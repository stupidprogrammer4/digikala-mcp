import asyncio

from pydantic import TypeAdapter

from src.app.comparison import build_comparison
from src.infra.gateways.base import ABCMarketGateway
from src.infra.http import GatewayError
from src.models import (
    AutocompleteResult,
    CategoryListResult,
    CategoryQuery,
    Location,
    Market,
    MarketError,
    MarketList,
    OfferListResult,
    ProductId,
    ProductResult,
    QueryText,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from src.models.comparison import ComparisonRequest, ComparisonResponse


class CatalogService:
    def __init__(self, gateways: list[ABCMarketGateway]):
        self.gateways = {gateway.market: gateway for gateway in gateways}

    def list_markets(self) -> MarketList:
        return MarketList.model_validate(
            {
                "markets": [
                    {
                        "market": gateway.market,
                        "capabilities": {
                            "search": "verified",
                            "categories": "verified" if gateway.supports_categories else "unknown",
                            "product_detail": "verified",
                            "autocomplete": "verified"
                            if gateway.supports_autocomplete
                            else "unknown",
                            "cart": "unknown",
                            "cart_read": "verified",
                            "shipping_quote": "unknown",
                        },
                        "last_verified": "2026-09-27",
                        "health_checked_now": False,
                        "location_required_by_api": False,
                    }
                    for gateway in self.gateways.values()
                ]
            }
        )

    async def search(
        self,
        query: SearchQuery,
        markets: list[Market] | None = None,
        location: Location | None = None,
    ) -> SearchResponse:
        selected = list(dict.fromkeys(markets if markets is not None else self.gateways))
        if not selected:
            raise ValueError("Select at least one market")

        async def one(market):
            try:
                result = await self.gateways[market].search(query, location)
                # Sponsored placements can violate upstream price filters or sorting.
                # Enforce bounds on this page; totals still describe upstream pagination.
                filtered = []
                for product in result.products:
                    price = product.price_rial
                    if query.min_price_rial is not None and (
                        price is None or price < query.min_price_rial
                    ):
                        continue
                    if query.max_price_rial is not None and (
                        price is None or price > query.max_price_rial
                    ):
                        continue
                    filtered.append(product)
                if len(filtered) != len(result.products):
                    result.warnings.append(
                        "Excluded unknown or out-of-budget prices; totals are upstream counts."
                    )
                if query.sort != "relevance":
                    known = [p for p in filtered if p.price_rial is not None]
                    unknown = [p for p in filtered if p.price_rial is None]
                    filtered = (
                        sorted(
                            known, key=lambda p: p.price_rial, reverse=query.sort == "price_desc"
                        )
                        + unknown
                    )
                result.products = filtered
                return result
            except GatewayError as exc:
                return SearchResult(
                    market=market, page=query.page, category_id=query.category_id, error=exc.error
                )

        return SearchResponse(results=await asyncio.gather(*(one(market) for market in selected)))

    async def list_categories(self, query: CategoryQuery | None = None) -> CategoryListResult:
        query = query or CategoryQuery()
        result = CategoryListResult(page=query.page, page_size=query.page_size)
        try:
            categories = await self.gateways[Market.DIGIKALA].list_categories()
        except GatewayError as exc:
            result.error = exc.error
            return result

        def normalized(text: str) -> str:
            text = text.translate(str.maketrans("يك", "یک")).replace("\u200c", " ")
            return " ".join(text.split()).casefold()

        needle = normalized(query.query) if query.query is not None else None
        matched = [
            c
            for c in categories
            if (not query.roots_only or c.parent_id is None)
            and (query.parent_id is None or c.parent_id == query.parent_id)
            and (
                needle is None
                or any(
                    needle in normalized(value)
                    for value in (c.title, c.title_en or "", c.code, c.category_id)
                )
            )
        ]
        matched.sort(key=lambda c: int(c.category_id))
        result.total_items = len(matched)
        result.total_pages = (len(matched) + query.page_size - 1) // query.page_size
        start = (query.page - 1) * query.page_size
        result.categories = matched[start : start + query.page_size]
        return result

    async def get_product(
        self, market: Market, product_id: str, location: Location | None = None
    ) -> ProductResult:
        TypeAdapter(ProductId).validate_python(product_id)
        try:
            return ProductResult(
                market=market, product=await self.gateways[market].get_product(product_id, location)
            )
        except GatewayError as exc:
            return ProductResult(market=market, error=exc.error)

    async def autocomplete(self, market: Market, query: str) -> AutocompleteResult:
        TypeAdapter(QueryText).validate_python(query)
        try:
            return await self.gateways[market].autocomplete(query)
        except GatewayError as exc:
            return AutocompleteResult(market=market, error=exc.error)

    async def list_offers(self, product_id: str, variant_id: str | None = None) -> OfferListResult:
        TypeAdapter(ProductId).validate_python(product_id)
        if variant_id is not None:
            TypeAdapter(ProductId).validate_python(variant_id)
        result = OfferListResult(product_id=product_id, variant_id=variant_id)
        detail = await self.get_product(Market.DIGIKALA, product_id)
        if detail.error is not None or detail.product is None:
            result.error = detail.error or MarketError(
                code="invalid_response", message="Product is missing"
            )
            return result

        product = detail.product
        result.url = product.url
        result.observed_at = product.observed_at
        result.warnings.append(
            "Offers cover the current product response, not a verified exhaustive seller list."
        )
        offers = [
            offer
            for offer in product.offers
            if variant_id is None or offer.variant_id == variant_id
        ]
        identifiers = [offer.offer_id for offer in offers if offer.offer_id is not None]
        if len(identifiers) != len(set(identifiers)):
            result.error = MarketError(
                code="ambiguous_offer", message="Upstream returned duplicate seller offer IDs"
            )
            return result
        result.offers = offers
        if variant_id is not None and not offers:
            result.warnings.append(
                "No offer matched the exact variant_id; no substitute was selected."
            )
        return result

    async def compare_offers(self, request: ComparisonRequest) -> ComparisonResponse:
        # Refresh each distinct product once per comparison, even for multiple sellers.
        keys = list(dict.fromkeys((s.market, s.product_id) for s in request.selections))
        products = await asyncio.gather(
            *(self.get_product(market, product_id, request.location) for market, product_id in keys)
        )
        return build_comparison(request, dict(zip(keys, products, strict=True)))

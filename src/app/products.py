"""Product content and seller comparisons over one public gateway."""

import asyncio

from pydantic import TypeAdapter

from src.app.comparison import build_comparison
from src.infra.http import GatewayError
from src.infra.http.gateways.base import ABCMarketGateway
from src.models.schemas.common import ProductId
from src.models.schemas.comparison import ComparisonRequest, ComparisonResponse, OfferSelection
from src.models.schemas.market import Market
from src.models.schemas.product import ProductResult
from src.models.schemas.product_content import (
    Page,
    ProductObservation,
    ProductSeller,
    QuestionListResult,
    QuestionSort,
    RatingResult,
    RecommendationResult,
    RecommendationSections,
    ReviewListResult,
    ReviewSort,
    SectionKey,
    SellerComparisonQuery,
    SellerListResult,
    VariantListResult,
    VariantType,
    VariantTypeListResult,
    VariantValue,
)
from src.models.schemas.research import (
    CategoryFiltersResult,
    PriceHistoryResult,
    ProductBatchQuery,
    ProductBatchResult,
    ProductComparisonQuery,
    ProductComparisonResult,
    ProductMediaResult,
    SpecificationComparison,
)


def observation(result: ProductObservation) -> dict:
    return result.model_dump(
        include={
            "product_id",
            "source_url",
            "observed_at",
            "cache_ttl_seconds",
            "warnings",
            "error",
        }
    )


class ProductService:
    def __init__(self, gateway: ABCMarketGateway):
        self.gateway = gateway

    async def variants(self, product_id: str) -> VariantListResult:
        TypeAdapter(ProductId).validate_python(product_id)
        try:
            return await self.gateway.product_variants(product_id)
        except GatewayError as exc:
            return VariantListResult(product_id=product_id, error=exc.error)

    async def variant_types(self, product_id: str) -> VariantTypeListResult:
        variants = await self.variants(product_id)
        result = VariantTypeListResult(**observation(variants))
        # Hash maps preserve first-seen order and keep value IDs separate from labels.
        dimensions: dict[tuple, VariantType] = {}
        values: dict[tuple, dict[tuple, VariantValue]] = {}
        for offer in variants.variants:
            for attr in offer.variant_attributes:
                key = (attr.label, attr.nature, attr.display_type)
                if key not in dimensions:
                    dimensions[key] = VariantType(
                        label=attr.label, nature=attr.nature, display_type=attr.display_type
                    )
                    values[key] = {}
                value_key = (attr.value_id, attr.value, attr.code)
                if value_key not in values[key]:
                    value = VariantValue(value_id=attr.value_id, title=attr.value, code=attr.code)
                    values[key][value_key] = value
                    dimensions[key].values.append(value)
                if offer.offer_id and offer.offer_id not in values[key][value_key].offer_ids:
                    values[key][value_key].offer_ids.append(offer.offer_id)
        result.types = list(dimensions.values())
        return result

    async def sellers(self, product_id: str) -> SellerListResult:
        variants = await self.variants(product_id)
        result = SellerListResult(**observation(variants))
        sellers: dict[str, ProductSeller] = {}
        for offer in variants.variants:
            if offer.seller_id is None:
                result.unidentified_offers.append(offer)
                continue
            if offer.seller_id not in sellers:
                sellers[offer.seller_id] = ProductSeller(
                    seller_id=offer.seller_id, name=offer.seller_name, rating=offer.seller_rating
                )
            sellers[offer.seller_id].offers.append(offer)
        result.sellers = list(sellers.values())
        return result

    async def ratings(self, product_id: str) -> RatingResult:
        TypeAdapter(ProductId).validate_python(product_id)
        try:
            product = await self.gateway.get_product(product_id)
            return RatingResult(
                product_id=product_id,
                rating=product.rating,
                observed_at=product.observed_at,
                source_url=f"https://api.digikala.com/product/v1/products/{product_id}/",
                warnings=[
                    "Product scores and distributions use a 0–100 scale; "
                    "individual review ratings use 0–5."
                ],
            )
        except GatewayError as exc:
            return RatingResult(product_id=product_id, error=exc.error)

    async def reviews(
        self, product_id: str, page: int = 1, sort: ReviewSort = "default"
    ) -> ReviewListResult:
        TypeAdapter(ProductId).validate_python(product_id)
        TypeAdapter(Page).validate_python(page)
        TypeAdapter(ReviewSort).validate_python(sort)
        try:
            return await self.gateway.product_reviews(product_id, page, sort)
        except GatewayError as exc:
            return ReviewListResult(product_id=product_id, page=page, sort=sort, error=exc.error)

    async def questions(
        self, product_id: str, page: int = 1, sort: QuestionSort = "created_at"
    ) -> QuestionListResult:
        TypeAdapter(ProductId).validate_python(product_id)
        TypeAdapter(Page).validate_python(page)
        TypeAdapter(QuestionSort).validate_python(sort)
        try:
            return await self.gateway.product_questions(product_id, page, sort)
        except GatewayError as exc:
            return QuestionListResult(product_id=product_id, page=page, sort=sort, error=exc.error)

    async def recommendation_sections(self, product_id: str) -> RecommendationSections:
        TypeAdapter(ProductId).validate_python(product_id)
        try:
            return await self.gateway.recommendation_sections(product_id)
        except GatewayError as exc:
            return RecommendationSections(product_id=product_id, error=exc.error)

    async def recommendations(
        self, product_id: str, section_key: str = "similar_products", limit: int = 10
    ) -> RecommendationResult:
        TypeAdapter(ProductId).validate_python(product_id)
        TypeAdapter(SectionKey).validate_python(section_key)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Limit must be an integer from 1 to 50")
        try:
            result = await self.gateway.product_recommendations(product_id, section_key)
            result.products = result.products[:limit]
            return result
        except GatewayError as exc:
            return RecommendationResult(
                product_id=product_id, section_key=section_key, error=exc.error
            )

    async def compare_sellers(self, query: SellerComparisonQuery) -> ComparisonResponse:
        request = ComparisonRequest(
            selections=[
                OfferSelection(
                    market=Market.DIGIKALA, product_id=query.product_id, offer_id=identifier
                )
                for identifier in query.offer_ids
            ]
        )
        try:
            variants = await self.gateway.product_variants(query.product_id)
            product = await self.gateway.get_product(query.product_id)
            # Prices/metrics come from the dedicated variants snapshot used by seller listing.
            product.offers = variants.variants
            assert variants.observed_at is not None
            product.observed_at = variants.observed_at
            detail = ProductResult(market=Market.DIGIKALA, product=product)
        except GatewayError as exc:
            detail = ProductResult(market=Market.DIGIKALA, error=exc.error)
        return build_comparison(request, {(Market.DIGIKALA, query.product_id): detail})

    async def batch(self, query: ProductBatchQuery) -> ProductBatchResult:
        async def read(identifier: str) -> tuple[str, ProductResult]:
            try:
                product = await self.gateway.get_product(identifier)
                return identifier, ProductResult(market=self.gateway.market, product=product)
            except GatewayError as exc:
                return identifier, ProductResult(market=self.gateway.market, error=exc.error)

        return ProductBatchResult(
            results=dict(
                await asyncio.gather(*(read(identifier) for identifier in query.product_ids))
            )
        )

    async def compare_products(self, query: ProductComparisonQuery) -> ProductComparisonResult:
        batch = await self.batch(query)
        labels = dict.fromkeys(
            label
            for result in batch.results.values()
            if result.product
            for label in result.product.specifications
        )
        rows = []
        for label in labels:
            values = {
                identifier: result.product.specifications.get(label) if result.product else None
                for identifier, result in batch.results.items()
            }
            known = {tuple(value) for value in values.values() if value}
            relation = (
                "unknown"
                if any(not v for v in values.values())
                else ("same" if len(known) == 1 else "different")
            )
            rows.append(SpecificationComparison(title=label, values=values, relation=relation))
        return ProductComparisonResult(results=batch.results, specifications=rows)

    async def media(self, product_id: str) -> ProductMediaResult:
        TypeAdapter(ProductId).validate_python(product_id)
        try:
            product = await self.gateway.get_product(product_id)
            return ProductMediaResult(
                product_id=product_id,
                media=product.media,
                observed_at=product.observed_at,
                source_url=f"https://api.digikala.com/product/v1/products/{product_id}/",
            )
        except GatewayError as exc:
            return ProductMediaResult(product_id=product_id, error=exc.error)

    async def filters(self, category_id: str) -> CategoryFiltersResult:
        TypeAdapter(ProductId).validate_python(category_id)
        try:
            return await self.gateway.category_filters(category_id)
        except GatewayError as exc:
            return CategoryFiltersResult(category_id=category_id, error=exc.error)

    async def history(self, product_id: str) -> PriceHistoryResult:
        TypeAdapter(ProductId).validate_python(product_id)
        try:
            return await self.gateway.product_price_history(product_id)
        except GatewayError as exc:
            return PriceHistoryResult(product_id=product_id, error=exc.error)

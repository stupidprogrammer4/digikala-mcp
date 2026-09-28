from datetime import datetime, timezone

from pydantic import TypeAdapter

from src.infra.cache import AsyncTTLCache, cached_catalog
from src.infra.http import GatewayError, HTTPConnection
from src.infra.http.gateways.base import ABCMarketGateway
from src.infra.http.gateways.parsing import checked_schema, items, price
from src.infra.http.gateways.product_parsing import (
    question,
    rating,
    recommended_product,
    review,
    seller_rating,
    variant_attributes,
)
from src.infra.http.gateways.research_parsing import category_filters, price_history, product_media
from src.models.schemas import (
    AutocompleteResult,
    Availability,
    Category,
    Location,
    Market,
    Offer,
    Product,
    ProductId,
    SearchQuery,
    SearchResult,
    Suggestion,
)
from src.models.schemas.product_content import (
    Page,
    Pagination,
    QuestionListResult,
    QuestionSort,
    RecommendationResult,
    RecommendationSection,
    RecommendationSections,
    ReviewListResult,
    ReviewSort,
    SectionKey,
    VariantListResult,
)
from src.models.schemas.research import CategoryFiltersResult, PriceHistoryResult
from src.models.schemas.trends import TrendSnapshot


def availability(status: str | None) -> Availability:
    if status == "marketable":
        return "available"
    if status == "out_of_stock":
        return "unavailable"
    return "unknown"


def parse_offer(raw):
    seller = raw.get("seller") or {}
    themes = variant_attributes(raw)
    attributes = {theme.label: theme.value for theme in themes}
    return Offer(
        variant_id=str(raw["id"]),
        offer_id=str(raw["id"]),
        seller_id=str(seller["id"]) if seller.get("id") is not None else None,
        seller_name=seller.get("title"),
        price_rial=price((raw.get("price") or {}).get("selling_price")),
        availability=availability(raw.get("status")),
        attributes=attributes,
        warranty=(raw.get("warranty") or {}).get("title_fa"),
        variant_attributes=themes,
        seller_rating=seller_rating(seller),
        lead_time_days=raw.get("lead_time"),
        shipment_description=(raw.get("shipment_methods") or {}).get("description"),
        order_limit=(raw.get("price") or {}).get("order_limit"),
    )


def parse_product(raw, *, detail=False):
    default = parse_offer(raw["default_variant"]) if raw.get("default_variant") else None
    offers = [parse_offer(v) for v in items(raw["variants"])] if detail else []
    offer_map = {offer.offer_id: offer for offer in offers}
    if default and default.offer_id not in offer_map:
        offers.insert(0, default)
    specs = {}
    for group in raw.get("specifications") or []:
        for attribute in group.get("attributes") or []:
            specs[attribute["title"]] = items(attribute["values"])
    product_id = str(raw["id"])
    return Product(
        market=Market.DIGIKALA,
        product_id=product_id,
        title=raw["title_fa"],
        url=f"https://www.digikala.com/product/dkp-{product_id}/",
        brand=(raw.get("brand") or {}).get("title_fa"),
        category_id=str(raw["category"]["id"])
        if (raw.get("category") or {}).get("id") is not None
        else None,
        rating=rating(raw),
        is_ad=(raw.get("properties") or {}).get("is_ad"),
        price_rial=default.price_rial if default else None,
        availability=availability(raw.get("status")),
        offers=offers,
        specifications=specs,
        media=product_media(raw) if detail else [],
    )


class DigikalaGateway(ABCMarketGateway):
    market = Market.DIGIKALA
    supports_autocomplete = True
    supports_categories = True
    supports_product_content = True
    supports_research = True

    def __init__(self, http: HTTPConnection, cache: AsyncTTLCache | None = None):
        super().__init__(http)
        self.cache = cache if cache is not None else AsyncTTLCache()

    @cached_catalog
    @checked_schema
    async def trends(self) -> TrendSnapshot:
        body = await self.http.request("GET", "/v1/")
        listing = self._data(body)["best_selling_products"]
        return TrendSnapshot(
            title=listing["title"],
            products=[parse_product(raw) for raw in items(listing["products"])],
        )

    @cached_catalog
    @checked_schema
    async def list_categories(self) -> list[Category]:
        body = await self.http.request(
            "GET",
            "/v1/dictionaries/",
            params={"types[0]": "category_tree", "hashes[0]": ""},
        )
        trees = [d for d in items(self._data(body)) if d["type"] == "category_tree"]
        if len(trees) != 1:
            raise ValueError("Expected one category tree")
        categories: dict[str, Category] = {}
        for row in items(trees[0]["data"]["data"]):
            raw = row["category"]
            category = Category(
                category_id=str(raw["id"]),
                title=raw["title_fa"],
                title_en=raw.get("title_en"),
                code=raw["code"],
                parent_id=str(row["parent_id"]) if row.get("parent_id") is not None else None,
            )
            previous = categories.get(category.category_id)
            if previous is not None and previous != category:
                raise ValueError("Conflicting category identities")
            categories[category.category_id] = category
        parents = {c.parent_id for c in categories.values()}
        for category in categories.values():
            category.has_children = category.category_id in parents
        return list(categories.values())

    @cached_catalog
    @checked_schema
    async def search(self, query: SearchQuery, location: Location | None = None) -> SearchResult:
        params: dict[str, str | int] = {
            "page": query.page,
            "sort": {"relevance": 22, "price_asc": 20, "price_desc": 21}[query.sort],
        }
        if query.query is not None:
            params["q"] = query.query
        if query.category_id is not None:
            params["categories[]"] = query.category_id
        if query.min_price_rial is not None:
            params["price[min]"] = query.min_price_rial
        if query.max_price_rial is not None:
            params["price[max]"] = query.max_price_rial
        if query.filters.values:
            assert query.category_id is not None
            available = await self.category_filters(query.category_id)
            allowed = {f.key: {o.value_id for o in f.options} for f in available.filters}
            for key, values in query.filters.values.items():
                if not set(values) <= allowed.get(key, set()):
                    raise GatewayError("invalid_filter", "Use IDs from get_category_filters")
                parameter = (
                    f"attributes[{key.removeprefix('attribute_')}]"
                    if key.startswith("attribute_")
                    else key
                )
                for index, value in enumerate(sorted(set(values))):
                    params[f"{parameter}[{index}]"] = value
        body = await self.http.request("GET", "/discovery/api/v2/search", params=params)
        data = self._data(body)
        listings = [
            w["data"] for w in items(data["widgets"]) if w.get("type") == "vertical_product_listing"
        ]
        if len(listings) != 1:
            raise ValueError("Expected one product listing")
        listing = listings[0]
        if query.category_id is not None:
            applied = items(listing["query_attributes"]["category_ids"])
            if [str(category_id) for category_id in applied] != [query.category_id]:
                raise GatewayError(
                    "category_filter_not_applied", "Digikala did not apply the requested category"
                )
        products = [
            parse_product(w["data"])
            for w in items(listing["widgets"])
            if w.get("type") == "product"
        ]
        pager = listing["pager"]
        return SearchResult(
            market=self.market,
            category_id=query.category_id,
            products=products,
            page=query.page,
            total_pages=pager["total_pages"],
            total_items=pager["total_items"],
            warnings=[
                "Filters were sent using storefront parameters; the response echoes "
                "only category IDs, not individual brand or attribute selections."
            ]
            if query.filters.values
            else [],
        )

    @cached_catalog
    @checked_schema
    async def get_product(self, product_id: str, location: Location | None = None) -> Product:
        TypeAdapter(ProductId).validate_python(product_id)
        body = await self.http.request("GET", f"/product/v1/products/{product_id}/")
        result = parse_product(self._data(body)["product"], detail=True)
        if result.product_id != product_id:
            raise ValueError("Product identity mismatch")
        return result

    @cached_catalog
    @checked_schema
    async def autocomplete(self, q: str) -> AutocompleteResult:
        body = await self.http.request("GET", "/discovery/api/v2/autocomplete", params={"q": q})
        return AutocompleteResult(
            market=self.market,
            suggestions=[
                Suggestion(
                    text=s["title"],
                    category=s.get("category_title"),
                    category_id=str(s["id"]) if s.get("type") == "category" else None,
                )
                for s in items(self._data(body)["auto_complete"])
            ],
        )

    @cached_catalog
    @checked_schema
    async def product_variants(self, product_id: str) -> VariantListResult:
        TypeAdapter(ProductId).validate_python(product_id)
        path = f"/v1/product/{product_id}/variants/"
        body = await self.http.request("GET", path)
        offers = [parse_offer(raw) for raw in items(self._data(body))]
        if len({o.offer_id for o in offers}) != len(offers):
            raise ValueError("Duplicate offer IDs")
        return VariantListResult(
            product_id=product_id,
            variants=offers,
            source_url=self.http.origin + path,
            observed_at=datetime.now(timezone.utc),
            warnings=["Coverage is the variants endpoint response, not all historical sellers."],
        )

    @cached_catalog
    @checked_schema
    async def product_reviews(
        self, product_id: str, page: int = 1, sort: ReviewSort = "default"
    ) -> ReviewListResult:
        TypeAdapter(ProductId).validate_python(product_id)
        TypeAdapter(Page).validate_python(page)
        TypeAdapter(ReviewSort).validate_python(sort)
        path = f"/v1/rate-review/products/{product_id}/"
        data = self._data(await self.http.request("GET", path, params={"page": page, "sort": sort}))
        pager = Pagination.model_validate(data["pager"])
        if pager.current_page != page:
            raise ValueError("Unexpected page")
        reviews = [review(raw, product_id) for raw in items(data["comments"])]
        if len({r.review_id for r in reviews}) != len(reviews):
            raise ValueError("Duplicate review IDs")
        return ReviewListResult(
            product_id=product_id,
            page=page,
            sort=sort,
            pager=pager,
            reviews=reviews,
            source_url=self.http.origin + path,
            observed_at=datetime.now(timezone.utc),
        )

    @cached_catalog
    @checked_schema
    async def product_questions(
        self, product_id: str, page: int = 1, sort: QuestionSort = "created_at"
    ) -> QuestionListResult:
        TypeAdapter(ProductId).validate_python(product_id)
        TypeAdapter(Page).validate_python(page)
        TypeAdapter(QuestionSort).validate_python(sort)
        path = f"/v1/product/{product_id}/questions/"
        data = self._data(await self.http.request("GET", path, params={"page": page, "sort": sort}))
        pager = Pagination.model_validate(data["pager"])
        if pager.current_page != page:
            raise ValueError("Unexpected page")
        questions = [question(raw) for raw in items(data["questions"])]
        if len({q.question_id for q in questions}) != len(questions):
            raise ValueError("Duplicate question IDs")
        return QuestionListResult(
            product_id=product_id,
            page=page,
            sort=sort,
            pager=pager,
            questions=questions,
            source_url=self.http.origin + path,
            observed_at=datetime.now(timezone.utc),
            warnings=["Answers include only those supplied on this question page."],
        )

    @cached_catalog
    @checked_schema
    async def recommendation_sections(self, product_id: str) -> RecommendationSections:
        TypeAdapter(ProductId).validate_python(product_id)
        product = await self.get_product(product_id)
        path = f"/product/v1/recommendations/{product_id}/section/"
        params = {"categoryId": product.category_id} if product.category_id else {}
        body = await self.http.request("GET", path, params=params)
        sections = [
            RecommendationSection.model_validate(s)
            for s in items(self._recommendation_data(body)["sections"])
        ]
        if len({s.key for s in sections}) != len(sections):
            raise ValueError("Duplicate section IDs")
        return RecommendationSections(
            product_id=product_id,
            sections=sections,
            source_url=self.http.origin + path,
            observed_at=datetime.now(timezone.utc),
        )

    @cached_catalog
    @checked_schema
    async def product_recommendations(
        self, product_id: str, section_key: str = "similar_products"
    ) -> RecommendationResult:
        TypeAdapter(ProductId).validate_python(product_id)
        TypeAdapter(SectionKey).validate_python(section_key)
        sections = await self.recommendation_sections(product_id)
        available = {s.key: s for s in sections.sections}
        if section_key not in available:
            raise GatewayError("section_not_found", "Select a key from recommendation sections")
        product = await self.get_product(product_id)
        params = {"key": section_key}
        if product.category_id:
            params["categoryId"] = product.category_id
        path = f"/product/v1/recommendations/{product_id}/items/"
        body = await self.http.request("GET", path, params=params)
        data = self._recommendation_data(body)
        products = [recommended_product(raw) for raw in items(data["products"])]
        return RecommendationResult(
            product_id=product_id,
            section_key=section_key,
            title=data.get("title"),
            available_sections=sections.sections,
            products=products,
            source_count=len(products),
            source_url=self.http.origin + path,
            observed_at=datetime.now(timezone.utc),
            warnings=[
                "Store recommendations in upstream order; not personalized advice. "
                "Refresh the exact product offer before cart actions."
            ],
        )

    @cached_catalog
    @checked_schema
    async def category_filters(self, category_id: str) -> CategoryFiltersResult:
        TypeAdapter(ProductId).validate_python(category_id)
        path = "/discovery/api/v2/search"
        body = await self.http.request("GET", path, params={"categories[]": category_id})
        listings = [
            w["data"]
            for w in items(self._data(body)["widgets"])
            if w.get("type") == "vertical_product_listing"
        ]
        if len(listings) != 1:
            raise ValueError("Expected one product listing")
        listing = listings[0]
        if [str(i) for i in items(listing["query_attributes"]["category_ids"])] != [category_id]:
            raise GatewayError("category_filter_not_applied", "Category was not applied")
        filters = listing["filters"]
        bounds = (filters.get("price") or {}).get("options") or {}
        return CategoryFiltersResult(
            category_id=category_id,
            filters=category_filters(filters),
            min_price_rial=bounds.get("min"),
            max_price_rial=bounds.get("max"),
            source_url=self.http.origin + path + "?categories[]=" + category_id,
            observed_at=datetime.now(timezone.utc),
            warnings=[
                "Only brand, color palette and attribute filters are projected. "
                "Price bounds are supplied by the upstream and may be global."
            ],
        )

    @cached_catalog
    @checked_schema
    async def product_price_history(self, product_id: str) -> PriceHistoryResult:
        TypeAdapter(ProductId).validate_python(product_id)
        path = f"/v1/product/{product_id}/price-chart/"
        data = self._data(await self.http.request("GET", path))
        return PriceHistoryResult(
            product_id=product_id,
            selection_title=data.get("chart_selection_title"),
            series=price_history(data),
            source_url=self.http.origin + path,
            observed_at=datetime.now(timezone.utc),
            warnings=[
                "Dates retain the upstream calendar text. Chart series do not establish "
                "exact offer identity or include shipping; gaps are not interpolated."
            ],
        )

    @staticmethod
    def _recommendation_data(body):
        if "status" in body and body["status"] != 200:
            raise GatewayError("upstream_error", "Digikala reported unsuccessful recommendations")
        data = body["data"]
        if not isinstance(data, dict):
            raise ValueError("Expected recommendation object")
        return data

    @staticmethod
    def _data(body):
        if body["status"] != 200:
            raise GatewayError("upstream_error", "Digikala reported an unsuccessful response")
        return body["data"]

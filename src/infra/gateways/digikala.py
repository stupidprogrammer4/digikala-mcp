from pydantic import TypeAdapter

from src.infra.gateways.base import ABCMarketGateway
from src.infra.gateways.parsing import checked_schema, items, price
from src.infra.http import GatewayError
from src.models import (
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


def availability(status: str | None) -> Availability:
    if status == "marketable":
        return "available"
    if status == "out_of_stock":
        return "unavailable"
    return "unknown"


def parse_offer(raw):
    seller = raw.get("seller") or {}
    attributes = {}
    for theme in raw.get("themes") or []:
        value = theme.get("value") or {}
        if theme.get("label") and value.get("title"):
            attributes[theme["label"]] = value["title"]
    if raw.get("color"):
        attributes.setdefault("رنگ", raw["color"]["title"])
    return Offer(
        variant_id=str(raw["id"]),
        offer_id=str(raw["id"]),
        seller_id=str(seller["id"]) if seller.get("id") is not None else None,
        seller_name=seller.get("title"),
        price_rial=price((raw.get("price") or {}).get("selling_price")),
        availability=availability(raw.get("status")),
        attributes=attributes,
        warranty=(raw.get("warranty") or {}).get("title_fa"),
    )


def parse_product(raw, *, detail=False):
    default = parse_offer(raw["default_variant"]) if raw.get("default_variant") else None
    offers = [parse_offer(v) for v in items(raw["variants"])] if detail else []
    if default and not any(o.offer_id == default.offer_id for o in offers):
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
        price_rial=default.price_rial if default else None,
        availability=availability(raw.get("status")),
        offers=offers,
        specifications=specs,
    )


class DigikalaGateway(ABCMarketGateway):
    market = Market.DIGIKALA
    supports_autocomplete = True
    supports_categories = True

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
        )

    @checked_schema
    async def get_product(self, product_id: str, location: Location | None = None) -> Product:
        TypeAdapter(ProductId).validate_python(product_id)
        body = await self.http.request("GET", f"/product/v1/products/{product_id}/")
        result = parse_product(self._data(body)["product"], detail=True)
        if result.product_id != product_id:
            raise ValueError("Product identity mismatch")
        return result

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

    @staticmethod
    def _data(body):
        if body["status"] != 200:
            raise GatewayError("upstream_error", "Digikala reported an unsuccessful response")
        return body["data"]

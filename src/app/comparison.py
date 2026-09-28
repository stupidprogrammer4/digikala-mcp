"""Compare observations without guessing cross-market product identity."""

from itertools import combinations
from typing import Literal

from src.models.schemas.comparison import (
    ComparisonItem,
    ComparisonRequest,
    ComparisonResponse,
    FieldDifference,
    OfferPairComparison,
    OfferSelection,
)
from src.models.schemas.market import Market, MarketError
from src.models.schemas.product import Offer, ProductResult


def select_offer(selection: OfferSelection, result: ProductResult) -> ComparisonItem:
    product = result.product
    if result.error is not None or product is None:
        return ComparisonItem(
            selection=selection,
            error=result.error
            or MarketError(code="invalid_response", message="Product is missing"),
        )
    item = ComparisonItem(
        selection=selection,
        title=product.title,
        url=product.url,
        brand=product.brand,
        specifications=product.specifications,
        observed_at=product.observed_at,
        location=product.location,
        location_is_default=product.location_is_default,
    )
    offers: dict[str | None, list[Offer]] = {}
    for offer in product.offers:
        offers.setdefault(offer.offer_id, []).append(offer)
    matches = offers.get(selection.offer_id, [])
    if len(matches) != 1:
        item.error = MarketError(
            code="offer_not_found" if not matches else "ambiguous_offer",
            message="Selected offer could not be uniquely resolved; no replacement was selected",
        )
        return item
    item.offer = matches[0]
    if selection.expected_price_rial is not None and item.offer.price_rial is not None:
        item.price_changed = selection.expected_price_rial != item.offer.price_rial
    return item


def compare_values(left: str | None, right: str | None) -> Literal["match", "different", "unknown"]:
    if not left or not right or not left.strip() or not right.strip():
        return "unknown"
    return "match" if left == right else "different"


def observed_fields(item: ComparisonItem) -> dict[str, list[str]]:
    fields = {f"specifications.{key}": value for key, value in item.specifications.items()}
    if item.title:
        fields["title"] = [item.title]
    if item.brand:
        fields["brand"] = [item.brand]
    if item.offer is not None:
        fields.update(
            {f"attributes.{key}": [value] for key, value in item.offer.attributes.items()}
        )
        if item.offer.warranty:
            fields["warranty"] = [item.offer.warranty]
        for key in (
            "seller_id",
            "seller_name",
            "lead_time_days",
            "shipment_description",
            "order_limit",
        ):
            value = getattr(item.offer, key)
            if value is not None:
                fields[key] = [str(value)]
        if item.offer.seller_rating is not None:
            for key, value in item.offer.seller_rating.model_dump(exclude_none=True).items():
                fields[f"seller_rating.{key}"] = [str(value)]
    return fields


def compare_pair(
    left_index: int, right_index: int, left: ComparisonItem, right: ComparisonItem
) -> OfferPairComparison:
    pair = OfferPairComparison(left_index=left_index, right_index=right_index, status="incomplete")
    a, b = left.offer, right.offer
    if left.error is not None or right.error is not None or a is None or b is None:
        pair.notes.append("At least one selected offer could not be refreshed; see item errors.")
        return pair
    pair.status = "compared"
    if (left.selection.market, left.selection.product_id) == (
        right.selection.market,
        right.selection.product_id,
    ):
        pair.identity = "same_listing"
    else:
        pair.notes.append("Product identity across these listings is unverified.")

    if a.attributes and b.attributes:
        shared = a.attributes.keys() & b.attributes.keys()
        if any(compare_values(a.attributes[k], b.attributes[k]) == "different" for k in shared):
            pair.attributes = "different"
        elif a.attributes.keys() == b.attributes.keys() and all(
            compare_values(a.attributes[k], b.attributes[k]) == "match" for k in shared
        ):
            pair.attributes = "match"
    pair.warranty = compare_values(a.warranty, b.warranty)
    if a.price_rial is not None and b.price_rial is not None:
        pair.price_difference_rial = b.price_rial - a.price_rial
    else:
        pair.notes.append("At least one item price is unknown; no price difference was calculated.")
    if a.availability != "available" or b.availability != "available":
        pair.notes.append("At least one offer is unavailable or has unverified availability.")
    left_fields, right_fields = observed_fields(left), observed_fields(right)
    pair.differences = [
        FieldDifference(field=key, left=left_fields.get(key), right=right_fields.get(key))
        for key in sorted(left_fields.keys() | right_fields.keys())
        if left_fields.get(key) != right_fields.get(key)
    ]
    return pair


def build_comparison(
    request: ComparisonRequest, products: dict[tuple[Market, str], ProductResult]
) -> ComparisonResponse:
    items = [select_offer(s, products[(s.market, s.product_id)]) for s in request.selections]
    return ComparisonResponse(
        items=items,
        pairs=[
            compare_pair(i, j, items[i], items[j]) for i, j in combinations(range(len(items)), 2)
        ],
    )

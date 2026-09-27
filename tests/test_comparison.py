import asyncio

import httpx
import pytest
from pydantic import ValidationError

from src.app.comparison import compare_pair
from src.models import Market, Offer
from src.models.comparison import ComparisonItem, ComparisonRequest, OfferSelection


def selection(offer_id: str, expected_price_rial: int | None = None) -> OfferSelection:
    return OfferSelection(
        market=Market.DIGIKALA,
        product_id="22672438",
        offer_id=offer_id,
        expected_price_rial=expected_price_rial,
    )


async def test_sellers_share_one_fresh_read_and_prices_are_rials(
    comparison_payload, catalog_factory
):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/product/v1/products/22672438/"
        return httpx.Response(200, json=comparison_payload())

    request = ComparisonRequest(
        selections=[selection("84016750", 27999900), selection("84016751", 20000000)],
    )
    response = await catalog_factory(handler).compare_offers(request)
    assert len(calls) == 1
    left, right = response.items
    assert left.price_changed is False
    assert right.price_changed is True
    assert left.observed_at == right.observed_at
    assert left.offer is not None and right.offer is not None
    assert left.offer.seller_id != right.offer.seller_id
    assert left.offer.variant_id != right.offer.variant_id
    assert left.offer.shipping_price_rial is None
    pair = response.pairs[0]
    assert pair.identity == "same_listing"
    assert pair.attributes == "match"
    assert pair.warranty == "match"
    assert pair.price_difference_rial == 50000


async def test_disappearing_offer_never_falls_back_to_default(comparison_payload, catalog_factory):
    service = catalog_factory(lambda r: httpx.Response(200, json=comparison_payload()))
    result = await service.compare_offers(
        ComparisonRequest(selections=[selection("84016750"), selection("removed-offer")])
    )
    assert result.items[0].offer is not None
    assert result.items[1].offer is None
    assert result.items[1].error is not None
    assert result.items[1].error.code == "offer_not_found"
    assert result.pairs[0].status == "incomplete"
    assert result.pairs[0].price_difference_rial is None


async def test_duplicate_upstream_offer_is_ambiguous(comparison_payload, catalog_factory):
    data = comparison_payload()
    vendors = data["data"]["product"]["variants"]
    vendors.append(vendors[0].copy())
    service = catalog_factory(lambda r: httpx.Response(200, json=data))
    result = await service.compare_offers(
        ComparisonRequest(selections=[selection("84016750"), selection("84016751")])
    )
    assert result.items[0].error is not None
    assert result.items[0].error.code == "ambiguous_offer"
    assert result.items[0].offer is None
    assert result.items[1].offer is not None


async def test_failures_are_isolated_and_products_fetch_concurrently(
    comparison_payload, catalog_factory
):
    second_started = asyncio.Event()

    async def handler(request):
        if request.url.path == "/product/v1/products/999/":
            await asyncio.wait_for(second_started.wait(), timeout=1)
            raise httpx.ReadTimeout("timeout", request=request)
        second_started.set()
        return httpx.Response(200, json=comparison_payload())

    request = ComparisonRequest(
        selections=[
            OfferSelection(market=Market.DIGIKALA, product_id="999", offer_id="84016750"),
            selection("84016750"),
            selection("84016751"),
        ]
    )
    result = await catalog_factory(handler).compare_offers(request)
    assert result.items[0].error is not None and result.items[0].error.code == "timeout"
    assert result.items[1].offer is not None and result.items[2].offer is not None
    assert [(p.left_index, p.right_index) for p in result.pairs] == [(0, 1), (0, 2), (1, 2)]
    assert [p.status for p in result.pairs] == ["incomplete", "incomplete", "compared"]


async def test_each_call_refreshes_price(comparison_payload, catalog_factory):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        data = comparison_payload()
        data["data"]["product"]["variants"][0]["price"]["selling_price"] += (calls - 1) * 1000
        return httpx.Response(200, json=data)

    service = catalog_factory(handler)
    request = ComparisonRequest(selections=[selection("84016750", 27999900), selection("84016751")])
    before = await service.compare_offers(request)
    after = await service.compare_offers(request)
    assert calls == 2
    assert before.items[0].price_changed is False
    assert after.items[0].price_changed is True
    assert after.items[0].offer is not None
    assert after.items[0].offer.price_rial == 28000900


def item(offer_id: str, **kwargs) -> ComparisonItem:
    return ComparisonItem(
        selection=selection(offer_id),
        title="Same product title",
        offer=Offer(offer_id=offer_id, **kwargs),
    )


def test_identical_names_across_listings_do_not_prove_identity():
    left = item("one", price_rial=100, attributes={"color": "black"}, warranty="12 months")
    right = item("two", price_rial=120, attributes={"color": "black"}, warranty="12 months")
    right.selection.product_id = "999"
    pair = compare_pair(0, 1, left, right)
    assert pair.identity == "unverified"
    assert pair.attributes == "match"
    assert pair.warranty == "match"
    assert pair.price_difference_rial == 20
    assert pair.notes


def test_color_capacity_and_warranty_differences_are_exposed():
    left = item("one", attributes={"color": "black", "storage": "128 GB"}, warranty="12 months")
    right = item("two", attributes={"color": "white", "storage": "256 GB"}, warranty="24 months")
    pair = compare_pair(0, 1, left, right)
    assert pair.attributes == "different"
    assert pair.warranty == "different"
    assert {d.field for d in pair.differences} == {
        "attributes.color",
        "attributes.storage",
        "warranty",
    }
    assert pair.price_difference_rial is None


@pytest.mark.parametrize("attributes", [{}, {"color": ""}, {"color": "black", "storage": "256 GB"}])
def test_missing_or_incomplete_variant_data_is_not_a_match(attributes):
    pair = compare_pair(
        0, 1, item("one", attributes={"color": "black"}), item("two", attributes=attributes)
    )
    assert pair.attributes == "unknown"
    assert pair.warranty == "unknown"


def test_missing_price_never_becomes_zero_and_unavailable_is_explicit():
    pair = compare_pair(
        0,
        1,
        item("one", price_rial=None, availability="unavailable"),
        item("two", price_rial=100, availability="available"),
    )
    assert pair.price_difference_rial is None
    assert any("unavailable" in note for note in pair.notes)


async def test_unknown_fresh_price_has_unknown_change_state(comparison_payload, catalog_factory):
    data = comparison_payload()
    data["data"]["product"]["variants"][0]["price"]["selling_price"] = 0
    service = catalog_factory(lambda r: httpx.Response(200, json=data))
    result = await service.compare_offers(
        ComparisonRequest(selections=[selection("84016750", 27999900), selection("84016751")])
    )
    assert result.items[0].price_changed is None
    assert result.pairs[0].price_difference_rial is None


@pytest.mark.parametrize(
    "selections",
    [
        [],
        [selection("one")],
        [selection("one"), selection("one", 123)],
        [selection(str(i)) for i in range(7)],
    ],
)
def test_invalid_selection_counts_and_duplicates(selections):
    with pytest.raises(ValidationError):
        ComparisonRequest(selections=selections)

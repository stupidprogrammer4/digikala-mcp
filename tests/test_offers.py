"""Seller listing contracts use public product fixtures; edge cases are explicit mutations."""

import copy

import httpx
import pytest
from pydantic import ValidationError

from src.models.schemas import OfferListResult


async def test_offers_preserve_sellers_variants_and_observation(
    comparison_payload, catalog_factory
):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/product/v1/products/22672438/"
        return httpx.Response(200, json=comparison_payload())

    result = await catalog_factory(handler).list_offers("22672438")
    assert isinstance(result, OfferListResult)
    assert len(calls) == 1
    assert result.error is None
    assert result.product_id == "22672438"
    assert result.variant_id is None
    assert result.url == "https://www.digikala.com/product/dkp-22672438/"
    assert result.observed_at is not None and result.observed_at.tzinfo is not None
    assert result.coverage == "product_response" and result.warnings
    left, right = result.offers
    assert (left.offer_id, right.offer_id) == ("84016750", "84016751")
    assert left.variant_id != right.variant_id
    assert left.seller_id != right.seller_id
    assert (left.price_rial, right.price_rial) == (27999900, 28049900)
    assert left.attributes["رنگ"] == "سفید" and left.warranty
    assert left.shipping_price_rial is None


async def test_variant_filter_never_falls_back_to_default(comparison_payload, catalog_factory):
    service = catalog_factory(lambda _: httpx.Response(200, json=comparison_payload()))
    result = await service.list_offers("22672438", "84016751")
    assert result.error is None
    assert result.variant_id == "84016751"
    assert [offer.offer_id for offer in result.offers] == ["84016751"]
    missing = await service.list_offers("22672438", "999")
    assert missing.error is None
    assert missing.offers == [] and missing.variant_id == "999"
    assert any("no substitute" in warning for warning in missing.warnings)


async def test_listing_does_not_infer_offer_stock_or_missing_price(payload, catalog_factory):
    data = payload("digikala-product")
    product = data["data"]["product"]
    product["default_variant"] = None
    product["variants"][0]["price"] = {}
    product["variants"][0].pop("status")
    service = catalog_factory(lambda _: httpx.Response(200, json=data))
    result = await service.list_offers("22672438")
    assert result.error is None
    assert result.offers[0].price_rial is None
    assert result.offers[0].availability == "unknown"


async def test_out_of_stock_offers_are_visible(payload, catalog_factory):
    data = payload("digikala-product")
    data["data"]["product"]["variants"][0]["status"] = "out_of_stock"
    result = await catalog_factory(lambda _: httpx.Response(200, json=data)).list_offers("22672438")
    assert result.offers[0].availability == "unavailable"


async def test_listing_reuses_observation_within_ttl(payload, catalog_factory):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        data = payload("digikala-product")
        data["data"]["product"]["variants"][0]["price"]["selling_price"] += calls * 100
        return httpx.Response(200, json=data)

    service = catalog_factory(handler)
    first = await service.list_offers("22672438")
    second = await service.list_offers("22672438")
    assert calls == 1
    assert first.offers[0].price_rial == 28000000
    assert second.offers[0].price_rial == 28000000
    assert first.observed_at is not None and second.observed_at is not None
    assert first.observed_at == second.observed_at


@pytest.mark.parametrize("status,code", [(404, "not_found"), (429, "rate_limited")])
async def test_listing_errors_do_not_look_like_verified_empty_results(
    catalog_factory, status, code
):
    result = await catalog_factory(
        lambda _: httpx.Response(status, text="private upstream body")
    ).list_offers("1")
    assert result.error is not None and result.error.code == code
    assert result.offers == []
    assert result.observed_at is None and result.url is None
    assert "private upstream body" not in result.model_dump_json()


async def test_missing_offer_schema_is_an_error(payload, catalog_factory):
    data = payload("digikala-product")
    del data["data"]["product"]["variants"]
    result = await catalog_factory(lambda _: httpx.Response(200, json=data)).list_offers("22672438")
    assert result.error is not None and result.error.code == "schema_changed"
    assert result.offers == [] and result.observed_at is None


async def test_empty_offer_list_is_valid(payload, catalog_factory):
    data = payload("digikala-product")
    data["data"]["product"]["variants"] = []
    data["data"]["product"]["default_variant"] = None
    result = await catalog_factory(lambda _: httpx.Response(200, json=data)).list_offers("22672438")
    assert result.error is None and result.offers == []
    assert result.observed_at is not None


async def test_ambiguous_offer_ids_are_not_silently_deduplicated(payload, catalog_factory):
    data = payload("digikala-product")
    variants = data["data"]["product"]["variants"]
    other = copy.deepcopy(variants[0])
    other["seller"]["id"] = 999
    variants.append(other)
    result = await catalog_factory(lambda _: httpx.Response(200, json=data)).list_offers("22672438")
    assert result.error is not None and result.error.code == "ambiguous_offer"
    assert result.offers == []


@pytest.mark.parametrize(
    "product_id,variant_id", [("../private", None), ("1", "../private"), ("1", "")]
)
async def test_invalid_identifiers_do_not_send_requests(catalog_factory, product_id, variant_id):
    def handler(request):
        pytest.fail("Invalid identifier should be rejected before networking")

    with pytest.raises(ValidationError):
        await catalog_factory(handler).list_offers(product_id, variant_id)

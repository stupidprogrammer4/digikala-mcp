"""Project public website payloads onto bounded product-content contracts."""

from src.infra.http.gateways.parsing import items, price
from src.models.schemas import Market, Offer, Product
from src.models.schemas.product_content import ProductAnswer, ProductQuestion, ProductReview
from src.models.schemas.product_info import ProductRating, SellerRating, VariantAttribute


def rating(raw) -> ProductRating | None:
    values = raw.get("rating")
    if values is None and not any(k in raw for k in ("comments_count", "questions_count")):
        return None
    values = values or {}
    suggestion = raw.get("suggestion") or {}
    return ProductRating(
        score_percent=values.get("rate"),
        rating_count=values.get("count"),
        distribution_percent=values.get("percentages") or {},
        recommendation_percent=suggestion.get("percentage"),
        recommendation_count=suggestion.get("count"),
        comments_count=raw.get("comments_count"),
        questions_count=raw.get("questions_count"),
    )


def seller_rating(seller) -> SellerRating | None:
    raw = seller.get("rating")
    if raw is None and seller.get("stars") is None:
        return None
    raw = raw or {}
    return SellerRating(
        satisfaction_percent=raw.get("total_rate"),
        rating_count=raw.get("total_count"),
        commitment_percent=raw.get("commitment"),
        no_return_percent=raw.get("no_return"),
        on_time_shipping_percent=raw.get("on_time_shipping"),
        stars=seller.get("stars"),
        grade=(seller.get("grade") or {}).get("label"),
    )


def variant_attributes(raw) -> list[VariantAttribute]:
    result = []
    for theme in items(raw.get("themes") or []):
        value = theme["value"]
        result.append(
            VariantAttribute(
                label=theme["label"],
                value=value["title"],
                value_id=str(value["id"]) if value.get("id") is not None else None,
                nature=value.get("nature"),
                display_type=theme.get("type"),
                code=value.get("code"),
            )
        )
    # Older product payloads only expose color or size, without a theme array.
    for nature, label in [("color", "رنگ"), ("size", "سایز")]:
        value = raw.get(nature)
        if value and not any(t.nature == nature or t.label == label for t in result):
            result.append(
                VariantAttribute(
                    label=label,
                    value=value["title"],
                    nature=nature,
                    value_id=str(value["id"]) if value.get("id") is not None else None,
                    code=value.get("hex_code"),
                )
            )
    return result


def review(raw, product_id: str) -> ProductReview:
    if raw.get("product_id") is not None and str(raw["product_id"]) != product_id:
        raise ValueError("Review product mismatch")
    reactions = raw.get("reactions") or {}
    buyer = raw.get("is_buyer")
    if buyer not in (None, 0, 1, False, True):
        raise ValueError("Unexpected buyer flag")
    return ProductReview(
        review_id=str(raw["id"]),
        title=raw.get("title"),
        body=raw["body"],
        rating=raw.get("rate"),
        is_buyer=bool(buyer) if buyer is not None else None,
        created_at_text=raw.get("created_at"),
        recommendation_status=raw.get("recommendation_status"),
        advantages=items(raw.get("advantages") or []),
        disadvantages=items(raw.get("disadvantages") or []),
        likes=reactions.get("likes"),
        dislikes=reactions.get("dislikes"),
    )


def question(raw) -> ProductQuestion:
    answers = []
    for answer in items(raw["answers"]):
        reactions = answer.get("reactions") or {}
        answers.append(
            ProductAnswer(
                answer_id=str(answer["id"]),
                text=answer["text"],
                created_at_text=answer.get("created_at"),
                responder_type=answer.get("type"),
                seller_id=str(answer["marketplace_seller_id"])
                if answer.get("marketplace_seller_id") is not None
                else None,
                likes=reactions.get("likes"),
                dislikes=reactions.get("dislikes"),
            )
        )
    if len({a.answer_id for a in answers}) != len(answers):
        raise ValueError("Duplicate answer IDs")
    return ProductQuestion(
        question_id=str(raw["id"]),
        text=raw["text"],
        answer_count=raw.get("answer_count"),
        created_at_text=raw.get("created_at"),
        answers=answers,
    )


def recommended_product(raw) -> Product:
    identifier = str(raw["product_id"])
    variants = items(raw["variants"])
    indexed = {str(v["variant_id"]): v for v in variants}
    if len(indexed) != len(variants):
        raise ValueError("Duplicate recommendation variants")
    default = indexed.get(str(raw.get("default_variant_id")))
    offers = []
    if default:
        pricing = default.get("pricing") or {}
        stock = pricing.get("pricing_stock") or {}
        known_stock = stock.get("marketable_stock")
        available = "unknown"
        if pricing.get("available_on_website") is False or known_stock == 0:
            available = "unavailable"
        elif pricing.get("available_on_website") is True and type(known_stock) is int:
            available = "available" if known_stock > 0 else "unknown"
        seller = default.get("seller") or {}
        themes = variant_attributes(default)
        offers.append(
            Offer(
                variant_id=str(default["variant_id"]),
                offer_id=str(default["variant_id"]),
                seller_id=str(seller["id"]) if seller.get("id") is not None else None,
                seller_name=seller.get("name"),
                price_rial=price(stock.get("selling_price")),
                availability=available,
                attributes={t.label: t.value for t in themes},
                variant_attributes=themes,
            )
        )
    return Product(
        market=Market.DIGIKALA,
        product_id=identifier,
        title=raw["title_fa"],
        url=f"https://www.digikala.com/product/dkp-{identifier}/",
        offers=offers,
        price_rial=offers[0].price_rial if offers else None,
        availability=offers[0].availability if offers else "unknown",
        rating=rating(raw),
        is_ad=raw.get("is_ad"),
        category_id=str(raw["category_id"]) if raw.get("category_id") is not None else None,
    )

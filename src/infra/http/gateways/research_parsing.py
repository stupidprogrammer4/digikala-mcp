"""Projections of observed storefront media, filter and chart contracts."""

from urllib.parse import urlsplit

from src.infra.http.gateways.parsing import items, price
from src.models.schemas.research import (
    CategoryFilter,
    FilterOption,
    MediaAsset,
    PriceHistoryPoint,
    PriceHistorySeries,
)


def media_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or url.hostname not in {"dkstatics-public.digikala.com", "video.digikala.com"}
        or url.username
        or url.password
        or url.port not in (None, 443)
    ):
        raise ValueError("Unexpected media origin")
    return value


def product_media(raw: dict) -> list[MediaAsset]:
    images = raw.get("images") or {}
    assets: dict[str, MediaAsset] = {}
    main = images.get("main")
    for entry in ([main] if main else []) + items(images.get("list", [])):
        for url in items(entry["url"]):
            url = media_url(url)
            if url not in assets:
                assets[url] = MediaAsset(kind="image", url=url, is_main=entry is main)
    for video in items(raw.get("videos") or []):
        url = media_url(video["url"])
        assets[url] = MediaAsset(
            kind="video",
            url=url,
            title=video.get("title"),
            cover_url=media_url(video["cover"]) if video.get("cover") else None,
        )
    return list(assets.values())


def category_filters(raw: dict) -> list[CategoryFilter]:
    result: dict[str, CategoryFilter] = {}

    def append(key: str, entry: dict) -> None:
        options: dict[str, FilterOption] = {}
        for option in items(entry.get("top_options", [])) + items(entry["options"]):
            identifier = str(option["id"])
            options[identifier] = FilterOption(
                value_id=identifier,
                title=option.get("title_fa") or option["title"],
                title_en=option.get("title_en"),
            )
        result[key] = CategoryFilter(key=key, title=entry["title"], options=list(options.values()))

    for key, entry in raw.items():
        if key in {"brands", "color_palettes"} or key.startswith("attribute_"):
            append(key, entry)
        elif key == "attributes":
            for attribute in items(entry["options"]):
                append(f"attribute_{attribute['id']}", attribute)
    return list(result.values())


def price_history(raw: dict) -> list[PriceHistorySeries]:
    result = []
    for series in items(raw["price_chart"]):
        points = []
        for point in items(series["history"]):
            points.append(
                PriceHistoryPoint(
                    date_text=point["day"],
                    selling_price_rial=price(point.get("selling_price")),
                    original_price_rial=price(point.get("rrp_price")),
                    is_marketable=point.get("is_marketable"),
                    seller_name=point.get("seller"),
                    warranty=point.get("product_warranty"),
                )
            )
        result.append(PriceHistorySeries(title=series["title"], points=points))
    return result

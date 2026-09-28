import os
from pathlib import Path

from src.models.schemas.cart import CartLimits


def cart_limits() -> CartLimits | None:
    amount = os.environ.get("INCART_CART_MAX_TOTAL_RIAL")
    count = os.environ.get("INCART_CART_MAX_ITEMS")
    if amount is None and count is None:
        return None
    if amount is None or count is None or not amount.isascii() or not count.isascii():
        raise ValueError("Set both INCART_CART_MAX_TOTAL_RIAL and INCART_CART_MAX_ITEMS")
    if not amount.isdecimal() or not count.isdecimal():
        raise ValueError("Cart limits must be nonnegative integer rials and units")
    return CartLimits(max_total_rial=int(amount), max_items=int(count))


def state_directory() -> Path:
    return Path(os.environ.get("INCART_STATE_DIR", str(Path.home() / ".local/share/incart-mcp")))


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")

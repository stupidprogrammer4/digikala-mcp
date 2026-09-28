"""Schema changes must not look like empty search results."""

from functools import wraps

from src.infra.http import GatewayError


def checked_schema(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise GatewayError(
                "schema_changed", "Market response no longer matches its contract"
            ) from exc

    return wrapped


def price(value, multiplier=1):
    if value is None or value == 0:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Expected integer price")
    return value * multiplier


def items(value):
    if not isinstance(value, list):
        raise TypeError("Expected an array")
    return value

import httpx
import pytest

from src.config.settings import cart_limits
from src.infra.http import GatewayError
from src.infra.http.session import SessionStore


class MemoryBackend:
    def __init__(self):
        self.value = None

    def get_password(self, *args):
        return self.value

    def set_password(self, service, entry, value):
        self.value = value

    def delete_password(self, *args):
        self.value = None


def test_session_roundtrip_never_persists_password(monkeypatch):
    backend = MemoryBackend()
    monkeypatch.setattr(SessionStore, "_backend", lambda self: backend)
    cookies = httpx.Cookies()
    cookies.set("session", "synthetic-token", domain=".digikala.com", path="/")
    cookies.set("irrelevant", "omit", domain="example.invalid", path="/")
    store = SessionStore()
    store.connect(cookies)
    loaded = SessionStore().load()
    assert loaded.cookies.get("session") == "synthetic-token"
    assert loaded.cookies.get("irrelevant") is None
    assert "synthetic-token" not in repr(loaded)
    assert backend.value is not None
    assert "password" not in backend.value
    assert loaded.connection_id == store.load().connection_id
    store.disconnect()
    with pytest.raises(GatewayError, match="login"):
        store.load()


def test_untrusted_keyring_is_not_used(monkeypatch):
    import keyring

    backend = MemoryBackend()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    with pytest.raises(GatewayError, match="supported OS keyring"):
        SessionStore().connect(httpx.Cookies())
    assert backend.value is None


@pytest.mark.parametrize(
    "amount,count", [("100", None), (None, "3"), ("1.5", "3"), ("-1", "3"), ("100", "true")]
)
def test_invalid_or_partial_limits_fail_startup(monkeypatch, amount, count):
    for name, value in [("INCART_CART_MAX_TOTAL_RIAL", amount), ("INCART_CART_MAX_ITEMS", count)]:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        cart_limits()

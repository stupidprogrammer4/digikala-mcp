"""Store session cookies in an OS keyring, never in the repository or a plaintext fallback."""

import json
from dataclasses import dataclass
from http.cookiejar import Cookie
from uuid import uuid4

import httpx
import keyring

from src.infra.http import GatewayError

SERVICE = "incart-mcp/digikala"
ENTRY = "session"
HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.digikala.com",
    "Referer": "https://www.digikala.com/",
    "X-Web-Optimize-Response": "1",
}


@dataclass(repr=False)
class AccountSession:
    connection_id: str
    cookies: httpx.Cookies


class SessionStore:
    def _backend(self):
        backend = keyring.get_keyring()
        if type(backend).__module__ not in {
            "keyring.backends.SecretService",
            "keyring.backends.kwallet",
            "keyring.backends.macOS",
            "keyring.backends.Windows",
        }:
            raise GatewayError("keyring_unavailable", "Configure a supported OS keyring backend")
        return backend

    def save(self, session: AccountSession) -> None:
        cookies = []
        for cookie in session.cookies.jar:
            if cookie.domain.lstrip(".") not in {"api.digikala.com", "digikala.com"}:
                continue
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": cookie.expires,
                }
            )
        try:
            self._backend().set_password(
                SERVICE,
                ENTRY,
                json.dumps({"connection_id": session.connection_id, "cookies": cookies}),
            )
        except GatewayError:
            raise
        except Exception:
            raise GatewayError(
                "keyring_unavailable", "Could not save session in OS keyring"
            ) from None

    def load(self) -> AccountSession:
        try:
            raw = self._backend().get_password(SERVICE, ENTRY)
            if raw is None:
                raise GatewayError("not_connected", "Run python -m src.account login locally first")
            data = json.loads(raw)
            cookies = httpx.Cookies()
            for c in data["cookies"]:
                if c["domain"].lstrip(".") not in {"api.digikala.com", "digikala.com"}:
                    raise ValueError("Unexpected cookie origin")
                cookies.jar.set_cookie(
                    Cookie(
                        version=0,
                        name=c["name"],
                        value=c["value"],
                        port=None,
                        port_specified=False,
                        domain=c["domain"],
                        domain_specified=True,
                        domain_initial_dot=c["domain"].startswith("."),
                        path=c["path"],
                        path_specified=True,
                        secure=True,
                        expires=c["expires"],
                        discard=c["expires"] is None,
                        comment=None,
                        comment_url=None,
                        rest={},
                        rfc2109=False,
                    )
                )
            cookies.jar.clear_expired_cookies()
            return AccountSession(connection_id=str(data["connection_id"]), cookies=cookies)
        except GatewayError:
            raise
        except Exception:
            raise GatewayError(
                "keyring_unavailable", "Could not load session from OS keyring"
            ) from None

    def disconnect(self) -> None:
        try:
            backend = self._backend()
            if backend.get_password(SERVICE, ENTRY) is not None:
                backend.delete_password(SERVICE, ENTRY)
        except GatewayError:
            raise
        except Exception:
            raise GatewayError("keyring_unavailable", "Could not remove local session") from None

    def connect(self, cookies: httpx.Cookies) -> None:
        self.save(AccountSession(connection_id=uuid4().hex, cookies=cookies))

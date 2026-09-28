from dataclasses import dataclass

import httpx

from src.infra.http.auth import check_login
from src.infra.http.session import HEADERS
from src.models.schemas.account import AccountLogin


@dataclass(repr=False)
class LoginOutcome:
    state: str
    reason: str | None
    cookies: httpx.Cookies


class PasswordLogin:
    def login(self, credentials: AccountLogin) -> LoginOutcome:
        with httpx.Client(headers=HEADERS) as client:
            outcome = check_login(
                client,
                credentials.username.get_secret_value(),
                credentials.password.get_secret_value(),
            )
            return LoginOutcome(
                str(outcome["state"]),
                str(outcome["reason"]) if outcome.get("reason") else None,
                client.cookies,
            )

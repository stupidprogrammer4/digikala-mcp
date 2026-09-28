import asyncio
import hashlib
import secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from src.app.cart import CartService
from src.app.replacement import CartReplacer
from src.infra.http import GatewayError
from src.infra.http.account_login import PasswordLogin
from src.infra.http.gateways.cart import CartGateway
from src.models.schemas.account import AccountConnection, AccountLogin


@dataclass(repr=False)
class ConnectedAccount:
    connection_id: str
    cookies: httpx.Cookies
    expires_at: datetime


GatewayFactory = Callable[[ConnectedAccount], AbstractAsyncContextManager[CartGateway]]


class AccountService:
    def __init__(self, login: PasswordLogin, gateways: GatewayFactory, replacer: CartReplacer):
        self.login_gateway = login
        self.gateways = gateways
        self.replacer = replacer
        self.sessions: dict[str, ConnectedAccount] = {}

    async def login(self, credentials: AccountLogin) -> AccountConnection:
        outcome = await asyncio.to_thread(self.login_gateway.login, credentials)
        if outcome.state != "connected":
            return AccountConnection.model_validate(
                {"state": outcome.state, "reason": outcome.reason}
            )
        now = datetime.now(timezone.utc)
        self.sessions = {k: v for k, v in self.sessions.items() if v.expires_at > now}
        if len(self.sessions) >= 100:
            raise GatewayError("session_capacity", "Too many active sessions")
        # The private host normalizes and owns the login identity. Never journal credentials.
        identity = hashlib.sha256(
            credentials.username.get_secret_value().strip().encode()
        ).hexdigest()
        token = secrets.token_urlsafe(32)
        expires = now + timedelta(minutes=15)
        self.sessions[token] = ConnectedAccount(identity, outcome.cookies, expires)
        return AccountConnection(state="connected", session_token=token, expires_at=expires)

    def require(self, token: str) -> ConnectedAccount:
        session = self.sessions.get(token)
        if session is None or session.expires_at <= datetime.now(timezone.utc):
            self.sessions.pop(token, None)
            raise GatewayError("session_expired", "Log in again through the trusted host")
        return session

    def logout(self, token: str) -> None:
        self.sessions.pop(token, None)

    def cart(self, token: str) -> CartService:
        # Validate at each tool call and again when opening its authenticated gateway.
        self.require(token)
        return CartService(
            lambda: self.gateways(self.require(token)), self.replacer.journal, self.replacer.limits
        )

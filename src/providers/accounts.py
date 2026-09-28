"""Account authentication and in-memory account session lifetime."""

from collections.abc import AsyncGenerator

from dishka import Provider, Scope, provide

from src.app.accounts import AccountService, GatewayFactory
from src.app.replacement import CartReplacer
from src.infra.http.account_login import PasswordLogin


class AccountProvider(Provider):
    scope = Scope.APP
    login = provide(PasswordLogin)

    @provide
    async def accounts(
        self, login: PasswordLogin, gateways: GatewayFactory, replacer: CartReplacer
    ) -> AsyncGenerator[AccountService, None]:
        service = AccountService(login, gateways, replacer)
        try:
            yield service
        finally:
            service.sessions.clear()

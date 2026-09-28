"""Real MCP services with a file-backed synthetic external store, no live credentials."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from dishka import Provider, Scope, provide

from src.app.accounts import AccountService
from src.app.replacement import CartReplacer
from src.config.settings import state_directory
from src.infra.db.cart_journal import CartJournal
from src.infra.http import GatewayError
from src.infra.http.account_login import LoginOutcome, PasswordLogin
from src.infra.http.auth import check_login
from src.infra.http.gateways.cart import CartGateway
from src.infra.http.session import HEADERS
from src.models.schemas.cart import CartItem, CartLimits, CartOffer, CartSnapshot
from src.models.schemas.product import Offer
from src.server import create_server


class SyntheticLogin(PasswordLogin):
    def login(self, credentials):
        username = credentials.username.get_secret_value()
        password = credentials.password.get_secret_value()
        if username not in {"alice", "bob"} or password != "synthetic-password":
            return LoginOutcome("not_connected", "invalid_password", httpx.Cookies())

        def handle(request):
            if request.url.path.endswith("authenticate/"):
                data = {"has_account": True, "has_password": True}
                headers = {}
            elif request.url.path.endswith("password/"):
                data = {}
                headers = {"Set-Cookie": f"test_user={username}; Secure; Path=/"}
            else:
                data = {"is_logged_in": request.headers.get("cookie") == f"test_user={username}"}
                headers = {}
            return httpx.Response(200, json={"status": 200, "data": data}, headers=headers)

        with httpx.Client(headers=HEADERS, transport=httpx.MockTransport(handle)) as client:
            result = check_login(client, username, password)
            return LoginOutcome(str(result["state"]), None, client.cookies)


def selected(identifier="11"):
    return Offer(
        offer_id=identifier,
        variant_id=identifier,
        seller_id="1",
        seller_name="Synthetic seller",
        price_rial=100,
        availability="available",
    )


class SyntheticStore(CartGateway):
    def __init__(self, identity, username):
        self.connection_id = identity
        self.path = state_directory() / (username + ".json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            old = CartItem(
                cart_item_id="old",
                product_id="99",
                title="Existing item",
                quantity=1,
                offer=selected("99"),
            )
            self.path.write_text(json.dumps({"items": [old.model_dump(mode="json")], "writes": 0}))

    async def read(self):
        return CartSnapshot.model_validate({"items": json.loads(self.path.read_text())["items"]})

    async def get_offer(self, product_id, offer_id):
        if product_id == "404":
            raise GatewayError("offer_unavailable", "Unavailable synthetic offer")
        return CartOffer(product_id=product_id, title="Jewelry", offer=selected(offer_id))

    async def mutate(self, plan):
        data = json.loads(self.path.read_text())
        if plan.action == "remove":
            data["items"] = [i for i in data["items"] if i["cart_item_id"] != plan.cart_item_id]
        else:
            assert plan.offer.offer_id is not None
            item = CartItem(
                cart_item_id=plan.offer.offer_id,
                product_id=plan.product_id,
                title=plan.title,
                quantity=1,
                offer=plan.offer,
            )
            data["items"].append(item.model_dump(mode="json"))
        data["writes"] += 1
        self.path.write_text(json.dumps(data))


@asynccontextmanager
async def gateway(session):
    yield SyntheticStore(session.connection_id, session.cookies.get("test_user"))


class SyntheticAccounts(Provider):
    @provide(scope=Scope.APP, override=True)
    async def accounts(self, journal: CartJournal) -> AsyncGenerator[AccountService, None]:
        service = AccountService(
            SyntheticLogin(),
            gateway,
            CartReplacer(journal, CartLimits(max_total_rial=50000000, max_items=3)),
        )
        try:
            yield service
        finally:
            service.sessions.clear()


if __name__ == "__main__":
    create_server(providers=[SyntheticAccounts()]).run(transport="stdio")

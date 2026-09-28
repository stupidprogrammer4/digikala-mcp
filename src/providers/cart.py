"""Cart services use a fresh Dishka database scope for each journal phase."""

from dishka import Provider, Scope, provide

from src.app.cart import CartGatewayFactory, CartService
from src.app.dependencies import JournalFactory
from src.app.replacement import CartReplacer
from src.config.settings import cart_limits


class CartProvider(Provider):
    scope = Scope.APP

    @provide
    def cart(self, gateway: CartGatewayFactory, journal: JournalFactory) -> CartService:
        return CartService(gateway, journal, cart_limits())

    @provide
    def replacer(self, journal: JournalFactory) -> CartReplacer:
        return CartReplacer(journal, cart_limits())

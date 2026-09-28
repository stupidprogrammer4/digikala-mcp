"""Catalog and product research services."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from dishka import Provider, Scope, provide

from src.app.catalog import CatalogService
from src.app.products import ProductService
from src.infra.http.gateways.digikala import DigikalaGateway

CatalogFactory = Callable[[], AbstractAsyncContextManager[CatalogService]]


class CatalogProvider(Provider):
    scope = Scope.APP

    @provide
    def catalog(self, gateway: DigikalaGateway) -> CatalogService:
        return CatalogService([gateway])

    @provide
    def products(self, catalog: CatalogService) -> ProductService:
        return catalog.products

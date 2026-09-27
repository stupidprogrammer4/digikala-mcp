from dataclasses import dataclass

from src.app.cart import CartService
from src.app.catalog import CatalogService


@dataclass
class Services:
    catalog: CatalogService
    cart: CartService

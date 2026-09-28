from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator

from src.models.schemas.base import Model
from src.models.schemas.cart import CartSnapshot
from src.models.schemas.common import Money, ProductId


class AccountLogin(Model):
    username: SecretStr
    password: SecretStr


class AccountConnection(Model):
    state: Literal["connected", "not_connected", "challenge_required"]
    session_token: str | None = Field(default=None, repr=False)
    expires_at: datetime | None = None
    reason: str | None = None


class SelectedCartOffer(Model):
    product_id: ProductId
    offer_id: ProductId
    seller_id: ProductId
    expected_price_rial: Money


class CartReplacement(Model):
    request_id: UUID
    items: list[SelectedCartOffer] = Field(min_length=1, max_length=3)
    max_total_rial: Money

    @model_validator(mode="after")
    def unique_offers(self):
        if len({item.offer_id for item in self.items}) != len(self.items):
            raise ValueError("Select distinct offers")
        return self


class CartReplacementResult(Model):
    request_id: UUID
    state: Literal["applied", "rejected", "uncertain"]
    reason: str | None = None
    cart: CartSnapshot | None = None


class AccountDisconnected(Model):
    disconnected: bool = True

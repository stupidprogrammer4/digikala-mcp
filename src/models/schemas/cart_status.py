"""Public operation status without credentials or internal account identifiers."""

from typing import Literal

from src.models.schemas.account import CartReplacementResult
from src.models.schemas.base import Model
from src.models.schemas.cart import CartOperation, CartPlan, PlanId


class CartOperationStatus(Model):
    operation_id: PlanId
    kind: Literal["change", "replacement"]
    state: Literal["prepared", "executing", "uncertain", "applied", "rejected"]
    plan: CartPlan | None = None
    result: CartOperation | CartReplacementResult | None = None
    expired: bool = False
    reason: str | None = None

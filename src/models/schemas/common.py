"""Shared value types. All money values are integer Iranian rials."""

from typing import Annotated, Literal

from pydantic import Field

from src.models.schemas.base import Model

QueryText = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
ProductId = Annotated[str, Field(pattern=r"^[0-9]{1,20}$")]
Money = Annotated[int, Field(ge=0, strict=True)]
Availability = Literal["available", "unavailable", "unknown"]


class Location(Model):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)

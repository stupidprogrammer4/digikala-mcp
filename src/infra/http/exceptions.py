"""Sanitized market gateway failures, independent of database persistence."""

from src.models.schemas import MarketError


class GatewayError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.error = MarketError(code=code, message=message, retryable=retryable)

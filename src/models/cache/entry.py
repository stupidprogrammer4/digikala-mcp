"""Internal cached values; never exposed as MCP schemas."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CacheEntry:
    expires_at: float
    value: Any

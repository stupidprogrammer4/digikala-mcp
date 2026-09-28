"""Bounded requests to a fixed market origin; no caller-provided URLs."""

import asyncio
from typing import Any

import httpx

from src.infra.http.exceptions import GatewayError


class HTTPConnection:
    def __init__(self, client: httpx.AsyncClient, origin: str):
        self.client = client
        self.origin = origin.rstrip("/")
        self._semaphore = asyncio.Semaphore(3)

    async def request(self, method: str, path: str, **kwargs: Any) -> dict:
        if not path.startswith("/") or path.startswith("//") or ".." in path:
            raise ValueError("Only fixed relative API paths are allowed")
        try:
            async with self._semaphore:
                response = await self.client.request(
                    method, self.origin + path, timeout=15, follow_redirects=False, **kwargs
                )
            if response.status_code == 404:
                raise GatewayError("not_found", "Product or upstream endpoint was not found")
            if response.status_code == 429:
                raise GatewayError("rate_limited", "Market rate limit reached", True)
            if response.status_code in (401, 403):
                raise GatewayError("access_denied", "Market denied access")
            if not 200 <= response.status_code < 300:
                raise GatewayError(
                    "upstream_error",
                    f"Market returned HTTP {response.status_code}",
                    response.status_code >= 500,
                )
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data
        except httpx.TimeoutException as exc:
            raise GatewayError("timeout", "Market request timed out", True) from exc
        except httpx.RequestError as exc:
            raise GatewayError("network_error", "Could not connect to market", True) from exc
        except ValueError as exc:
            raise GatewayError("invalid_response", "Market returned invalid JSON") from exc

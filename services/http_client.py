import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


async def http_get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        response = await client.get(url, params=params)
    return response


async def http_post(url: str, json_body: dict[str, Any] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        response = await client.post(url, json=json_body)
    return response
"""
Async geocoding utility using OpenStreetMap Nominatim API.

Provides address -> (latitude, longitude) resolution with automatic
rate-limiting (1 req/s as required by Nominatim's usage policy).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

NOMINATM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATM_USER_AGENT = "TailorsinAIAgent/1.0 (support@tailorsin.com)"
_RATE_LIMITER: asyncio.Lock | None = None
_last_request_time: float = 0.0


def _get_rate_limiter() -> asyncio.Lock:
    global _RATE_LIMITER  # noqa: PLW0603
    if _RATE_LIMITER is None:
        _RATE_LIMITER = asyncio.Lock()
    return _RATE_LIMITER


async def _enforce_rate_limit() -> None:
    """
    Ensure at least 1 second has passed since the last Nominatim request.
    """
    global _last_request_time  # noqa: PLW0603
    now = asyncio.get_event_loop().time()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)
    _last_request_time = asyncio.get_event_loop().time()


async def _nominatim_request(
    query: str,
    *,
    timeout: float = 10.0,
) -> tuple[float, float] | None:
    """Send a single Nominatim geocoding request and return (lat, lon) or None."""
    if not query:
        return None

    params: dict[str, str | int] = {
        "q": query,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }

    async with _get_rate_limiter():
        try:
            await _enforce_rate_limit()
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    NOMINATM_URL,
                    params=params,
                    headers={"User-Agent": NOMINATM_USER_AGENT},
                )
                response.raise_for_status()
                data = response.json()
        except Exception:
            logger.warning("Nominatim geocoding failed for query %r", query, exc_info=True)
            return None

    if not isinstance(data, list) or not data:
        logger.info("Nominatim returned no results for query %r", query)
        return None

    first = data[0]
    lat = first.get("lat")
    lon = first.get("lon")
    if lat is None or lon is None:
        return None

    try:
        return (float(lat), float(lon))
    except (TypeError, ValueError):
        return None


async def geocode_address(
    address_line: str,
    city: str,
    pincode: str,
    *,
    timeout: float = 10.0,
) -> tuple[float, float] | None:
    """
    Geocode an address using the Nominatim API with multiple fallback queries.

    Tries progressively broader queries:
      1. ``{address_line}, {city}, {pincode}``  (full address)
      2. ``{city}, {pincode}``                   (city + pincode)
      3. ``{pincode}``                            (pincode only - returns area centroid)

    Parameters
    ----------
    address_line : str
        Street / area / house number portion of the address.
    city : str
        City name.
    pincode : str
        6-digit Indian pincode.
    timeout : float
        HTTP request timeout in seconds.

    Returns
    -------
    tuple[float, float] | None
        ``(latitude, longitude)`` if any query succeeds, else ``None``.
    """
    queries: list[str] = []

    # 1. Full address
    full_parts = [part for part in [address_line, city, pincode] if part]
    if full_parts:
        queries.append(", ".join(full_parts))

    # 2. City + pincode (if different from full)
    city_pincode_parts = [part for part in [city, pincode] if part]
    if city_pincode_parts and city_pincode_parts != full_parts:
        queries.append(", ".join(city_pincode_parts))
    elif not full_parts and city_pincode_parts:
        queries.append(", ".join(city_pincode_parts))

    # 3. Pincode only (area-level centroid)
    if pincode:
        pincode_query = pincode
        if pincode_query not in queries:
            queries.append(pincode_query)

    for query in queries:
        result = await _nominatim_request(query, timeout=timeout)
        if result is not None:
            return result

    return None
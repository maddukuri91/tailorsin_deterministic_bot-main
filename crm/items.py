from dataclasses import dataclass

from services.http_client import http_get


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/items"


@dataclass
class BrowseCatalog:
    mens_wear: list[str]
    womens_wear: list[str]
    kids_wear: list[str]


def _normalize_spacing(value: str) -> str:
    return " ".join(value.strip().split())


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []

    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)

    return deduped


async def fetch_browse_catalog() -> BrowseCatalog:
    response = await http_get(BASE_URL)
    response.raise_for_status()

    payload = response.json()
    items = payload.get("data", [])

    mens_wear: list[str] = []
    womens_wear: list[str] = []
    kids_wear: list[str] = []

    for item in items:
        if item.get("deleted") not in (None, "0"):
            continue

        if item.get("vstatus") != "0":
            continue

        raw_name = item.get("iname")
        if not isinstance(raw_name, str):
            continue

        name = _normalize_spacing(raw_name)
        if name.startswith("MW - "):
            label = name.removeprefix("MW - ")
            if label.startswith("Kids"):
                kids_wear.append(f"Boys {label.removeprefix('Kids').strip()}")
            else:
                mens_wear.append(label)
            continue

        if name.startswith("WW - "):
            label = name.removeprefix("WW - ")
            if label.startswith("Kids"):
                kids_wear.append(f"Girls {label.removeprefix('Kids').strip()}")
            else:
                womens_wear.append(label)

    return BrowseCatalog(
        mens_wear=_dedupe_preserve_order(mens_wear),
        womens_wear=_dedupe_preserve_order(womens_wear),
        kids_wear=_dedupe_preserve_order(kids_wear),
    )
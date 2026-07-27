from dataclasses import dataclass

from services.http_client import http_get


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/itemswithprice.php"
CATEGORY_LABELS = {
    1: "Retail 24-25",
    2: "B2B 24-25",
    6: "Retail 25-26",
    7: "B2B 25-26",
}


@dataclass
class SubItemPrice:
    subitem_name: str
    price: str
    tax: str | None = None


@dataclass
class ItemWithPrice:
    item_name: str
    subitems: list[SubItemPrice]


@dataclass
class PriceCatalogResult:
    success: bool
    message: str
    items: list[ItemWithPrice]
    category_label: str


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


async def fetch_price_catalog(catid: int = 6) -> PriceCatalogResult:
    try:
        response = await http_get(BASE_URL, params={"catid": catid})
        payload = response.json() if response.content else {}
    except Exception:
        return PriceCatalogResult(
            success=False,
            message="Unable to fetch price catalogue right now. Please try again shortly.",
            items=[],
            category_label=CATEGORY_LABELS.get(catid, f"Category {catid}"),
        )

    if str(payload.get("status", "")).lower() != "success":
        return PriceCatalogResult(
            success=False,
            message=str(payload.get("message") or "Price catalogue is unavailable right now."),
            items=[],
            category_label=CATEGORY_LABELS.get(catid, f"Category {catid}"),
        )

    items_payload = payload.get("data") if isinstance(payload.get("data"), list) else []
    parsed_items: list[ItemWithPrice] = []

    for item in items_payload:
        if not isinstance(item, dict):
            continue

        if str(item.get("vstatus", "1")) != "1":
            continue

        item_name = _clean(item.get("iname"))
        if not item_name:
            continue

        subitems_payload = item.get("subitems") if isinstance(item.get("subitems"), list) else []
        parsed_subitems: list[SubItemPrice] = []

        for subitem in subitems_payload:
            if not isinstance(subitem, dict):
                continue

            subitem_name = _clean(subitem.get("subitem_name"))
            price = _clean(subitem.get("price"))
            if not subitem_name or not price:
                continue

            tax = _clean(subitem.get("tax")) or None
            parsed_subitems.append(SubItemPrice(subitem_name=subitem_name, price=price, tax=tax))

        if parsed_subitems:
            parsed_items.append(ItemWithPrice(item_name=item_name, subitems=parsed_subitems))

    detected_category_label = CATEGORY_LABELS.get(catid, f"Category {catid}")
    for item in items_payload:
        if not isinstance(item, dict):
            continue
        item_catid = _to_int(item.get("catid"))
        if item_catid is not None:
            detected_category_label = _clean(item.get("category_label")) or CATEGORY_LABELS.get(item_catid, detected_category_label)
            break

    if not parsed_items:
        return PriceCatalogResult(
            success=False,
            message="No priced items were found for the selected category.",
            items=[],
            category_label=detected_category_label,
        )

    return PriceCatalogResult(
        success=True,
        message="Price catalogue fetched successfully.",
        items=parsed_items,
        category_label=detected_category_label,
    )
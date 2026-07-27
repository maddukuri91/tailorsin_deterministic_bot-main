from dataclasses import dataclass
from datetime import date, timedelta

from services.http_client import http_get


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/alterationeligibleorders.php"
DEFAULT_WINDOW_DAYS = 30


@dataclass
class DeliveredOrder:
    order_id: int
    delivered_date: str
    item_summary: str


@dataclass
class DeliveredOrdersResult:
    success: bool
    message: str
    orders: list[DeliveredOrder]


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def _parse_date(value: object) -> date | None:
    text = _clean(value)
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return date.strptime(text[:10] if fmt.startswith("%Y") else text, fmt)
        except ValueError:
            continue

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _build_item_summary(item: dict) -> str:
    parts: list[str] = []
    for key in ("item", "garment", "product", "description", "items"):
        value = _clean(item.get(key))
        if value:
            parts.append(value)
    return " | ".join(parts)


async def fetch_delivered_orders(mobile: str, window_days: int = DEFAULT_WINDOW_DAYS) -> DeliveredOrdersResult:
    """Fetch recent delivered orders for a mobile number.

    Only orders delivered within the last ``window_days`` days are returned.
    """
    try:
        response = await http_get(BASE_URL, params={"mobile": mobile})
        data = response.json() if response.content else {}
    except Exception:
        return DeliveredOrdersResult(
            success=False,
            message="Unable to fetch your recent delivered orders right now. Please try again shortly.",
            orders=[],
        )

    if str(data.get("status", "")).lower() != "success":
        return DeliveredOrdersResult(
            success=False,
            message=str(data.get("message") or "No recent delivered orders were found."),
            orders=[],
        )

    cutoff = date.today() - timedelta(days=window_days)
    orders_payload = data.get("orders") if isinstance(data.get("orders"), list) else []
    if not orders_payload and isinstance(data.get("data"), list):
        orders_payload = data.get("data")

    orders: list[DeliveredOrder] = []
    for item in orders_payload:
        if not isinstance(item, dict):
            continue

        order_id = _to_int(item.get("order_id") or item.get("id"))
        if order_id is None:
            continue

        delivered_date_raw = _clean(
            item.get("delivered_date") or item.get("delivery_date") or item.get("date")
        )
        delivered_date = delivered_date_raw or "unknown date"

        parsed_date = _parse_date(delivered_date_raw)
        if parsed_date is not None and parsed_date < cutoff:
            continue

        orders.append(
            DeliveredOrder(
                order_id=order_id,
                delivered_date=delivered_date,
                item_summary=_build_item_summary(item),
            )
        )

    if not orders:
        return DeliveredOrdersResult(
            success=False,
            message=(
                f"No delivered orders were found in the last {window_days} days. "
                "Alteration pickups can only be scheduled for recently delivered orders."
            ),
            orders=[],
        )

    return DeliveredOrdersResult(
        success=True,
        message="Recent delivered orders fetched successfully.",
        orders=orders,
    )
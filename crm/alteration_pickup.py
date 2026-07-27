from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/alterationpickup.php"
MAX_NOTES_LENGTH = 500


@dataclass
class AlterationPickupResult:
    success: bool
    message: str
    order_id: int | None = None
    log_id: int | None = None


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


async def schedule_alteration_pickup(
    mobile: str,
    pickup_date: str,
    pickup_time: int,
    order_id: int | None = None,
    address_id: int | None = None,
    notes: str | None = None,
) -> AlterationPickupResult:
    payload: dict[str, object] = {
        "mobile": mobile,
        "pickup_date": pickup_date,
        "pickup_time": pickup_time,
    }

    if order_id is not None:
        payload["order_id"] = order_id

    if address_id is not None:
        payload["address_id"] = address_id

    cleaned_notes = (notes or "").strip()
    if cleaned_notes:
        if len(cleaned_notes) > MAX_NOTES_LENGTH:
            cleaned_notes = cleaned_notes[:MAX_NOTES_LENGTH].rstrip()
        payload["notes"] = cleaned_notes

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return AlterationPickupResult(
            success=False,
            message="Unable to schedule alteration pickup right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        response_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        return AlterationPickupResult(
            success=True,
            message=str(data.get("message") or "alteration pickup scheduled successfully"),
            order_id=_to_int(response_data.get("order_id")),
            log_id=_to_int(response_data.get("log_id")),
        )

    return AlterationPickupResult(
        success=False,
        message=str(data.get("message") or "Unable to schedule alteration pickup."),
    )
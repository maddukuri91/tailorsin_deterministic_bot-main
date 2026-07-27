from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/fabricdelivery.php"
DEFAULT_STORE_ID = 1
MAX_NOTES_LENGTH = 500


@dataclass
class FabricDeliveryResult:
    success: bool
    message: str
    alert_id: int | None = None


async def create_fabric_delivery_request(
    mobile: str,
    notes: str | None = None,
    store_id: int = DEFAULT_STORE_ID,
) -> FabricDeliveryResult:
    cleaned_notes = (notes or "").strip()
    if len(cleaned_notes) > MAX_NOTES_LENGTH:
        cleaned_notes = cleaned_notes[:MAX_NOTES_LENGTH].rstrip()

    payload: dict[str, object] = {
        "mobile": mobile,
        "store_id": store_id,
    }
    if cleaned_notes:
        payload["notes"] = cleaned_notes

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return FabricDeliveryResult(
            success=False,
            message="Unable to submit fabric delivery request right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        alert_id_raw = data.get("alert_id")
        alert_id = int(alert_id_raw) if str(alert_id_raw).isdigit() else None
        message = str(data.get("message") or "Fabric delivery request submitted successfully.")
        if alert_id is not None:
            message = f"{message} (Alert ID: {alert_id})"
        return FabricDeliveryResult(success=True, message=message, alert_id=alert_id)

    return FabricDeliveryResult(
        success=False,
        message=str(data.get("message") or "Unable to submit fabric delivery request."),
    )
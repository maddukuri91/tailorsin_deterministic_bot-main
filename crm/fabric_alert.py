from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/fabricalert.php"


@dataclass
class FabricAlertResult:
    success: bool
    message: str
    alert_id: int | None = None


async def raise_fabric_alert(mobile: str) -> FabricAlertResult:
    payload = {"mobile": mobile}

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return FabricAlertResult(
            success=False,
            message="Unable to raise fabric estimate request right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        alert_id_raw = data.get("alert_id")
        alert_id = int(alert_id_raw) if str(alert_id_raw).isdigit() else None
        message = str(data.get("message") or "Fabric estimate request has been submitted.")
        if alert_id is not None:
            message = f"{message} (Alert ID: {alert_id})"
        return FabricAlertResult(success=True, message=message, alert_id=alert_id)

    return FabricAlertResult(
        success=False,
        message=str(data.get("message") or "Unable to submit fabric estimate request."),
    )
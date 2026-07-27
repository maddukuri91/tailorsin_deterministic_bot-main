from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/cancelorder.php"
MAX_REASON_LENGTH = 500


@dataclass
class CancelOrderResult:
    success: bool
    message: str


async def cancel_current_order(mobile: str, order_id: int | None, reason: str) -> CancelOrderResult:
    cleaned_reason = reason.strip()
    if len(cleaned_reason) > MAX_REASON_LENGTH:
        cleaned_reason = cleaned_reason[:MAX_REASON_LENGTH].rstrip()

    payload: dict[str, object] = {
        "mobile": mobile,
        "reason": cleaned_reason,
    }
    if order_id is not None:
        payload["order_id"] = order_id

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return CancelOrderResult(
            success=False,
            message="Unable to cancel your order right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        return CancelOrderResult(
            success=True,
            message=str(data.get("message") or "Order cancelled successfully."),
        )

    return CancelOrderResult(
        success=False,
        message=str(data.get("message") or "Unable to cancel current order."),
    )
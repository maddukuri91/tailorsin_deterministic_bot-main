from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/modifyorder.php"
MAX_COMMENT_LENGTH = 1000


@dataclass
class ModifyOrderResult:
    success: bool
    message: str
    order_id: int | None = None
    client_id: int | None = None
    client_name: str | None = None
    client_whatsapp: str | None = None
    comment: str | None = None


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


async def modify_order(
    mobile: str,
    comment: str,
    order_id: int,
    chatby: str = "AI Assistant",
) -> ModifyOrderResult:
    cleaned_comment = comment.strip()
    if len(cleaned_comment) > MAX_COMMENT_LENGTH:
        cleaned_comment = cleaned_comment[:MAX_COMMENT_LENGTH].rstrip()

    payload: dict[str, object] = {
        "mobile": mobile,
        "comment": cleaned_comment,
        "order_id": order_id,
        "chatby": chatby,
    }

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return ModifyOrderResult(
            success=False,
            message="Unable to submit modification request right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        client_data = data.get("client") if isinstance(data.get("client"), dict) else {}
        return ModifyOrderResult(
            success=True,
            message=str(
                data.get("message")
                or f"your request has been recorded for order #{order_id}, our team will get back to you shortly"
            ),
            order_id=_to_int(data.get("order_id")),
            client_id=_to_int(client_data.get("id")),
            client_name=str(client_data.get("cname") or "") or None,
            client_whatsapp=str(client_data.get("whatsapp") or "") or None,
            comment=str(data.get("comment") or "") or None,
        )

    return ModifyOrderResult(
        success=False,
        message=str(data.get("message") or "Unable to submit modification request."),
    )
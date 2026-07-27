from dataclasses import dataclass
import logging

from services.http_client import http_post

from crm.schedule_pickup import SchedulePickupResult


BASE_URL = "https://crm.tailorsin.com/tailorsin-api//api/scheduleanotherpickup.php"
logger = logging.getLogger(__name__)


def _mask_mobile(mobile: str) -> str:
    digits_only = "".join(character for character in mobile if character.isdigit())
    if len(digits_only) <= 4:
        return digits_only
    return f"***{digits_only[-4:]}"


async def schedule_another_pickup(
    mobile: str,
    pickup_date: str,
    pickup_time: int,
    address_id: int | None = None,
) -> SchedulePickupResult:
    """Schedule an additional pickup/order for an existing (active) client.

    Uses the scheduleanotherpickup.php endpoint which requires an address_id,
    pickup date and a pickup time slot, mirroring the fresh pickup flow.
    """
    payload: dict[str, object] = {
        "mobile": mobile,
        "pickup_date": pickup_date,
        "pickup_time": pickup_time,
    }

    if address_id is not None:
        payload["address_id"] = address_id

    try:
        response = await http_post(BASE_URL, json_body=payload)
        status_code = response.status_code

        logger.info(
            "schedule_another_pickup request mobile=%s date=%s time=%s address_id=%s status_code=%s",
            _mask_mobile(mobile),
            pickup_date,
            pickup_time,
            address_id,
            status_code,
        )

        data = response.json() if response.content else {}
        message = str(data.get("message") or "Pickup scheduling failed.")

        if status_code == 409:
            return SchedulePickupResult(
                success=False,
                message=message,
                conflict_open_order=True,
            )

        if status_code == 200 and str(data.get("status", "")).lower() == "success":
            result = SchedulePickupResult(success=True, message=message)
            order_data = data.get("data") if isinstance(data.get("data"), dict) else {}
            order_id = order_data.get("order_id")
            if order_id is not None:
                result.message = f"{message} (Order ID: {order_id})"
            return result

        # 422: no saved address found — redirect client to add address first
        if status_code == 422 and str(data.get("action", "")).lower() == "add_address":
            return SchedulePickupResult(
                success=False,
                message=message,
                needs_address=True,
            )

        return SchedulePickupResult(success=False, message=message)
    except Exception as error:
        logger.exception(
            "schedule_another_pickup exception mobile=%s error=%s",
            _mask_mobile(mobile),
            error,
        )
        return SchedulePickupResult(
            success=False,
            message="Unable to schedule pickup right now. Please try again shortly.",
        )
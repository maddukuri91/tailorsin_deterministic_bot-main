from dataclasses import dataclass

from services.http_client import http_get, http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/bookappointment.php"
DEFAULT_STORE_ID = 1


@dataclass
class VisitAvailabilityResult:
    success: bool
    message: str
    slots: list[str]


@dataclass
class BookVisitResult:
    success: bool
    message: str
    appointment_id: int | None = None


@dataclass
class AppointmentHistoryItem:
    appointment_id: str
    bookdate_display: str
    booktime: str
    store_name: str
    status_label: str


@dataclass
class AppointmentHistoryResult:
    success: bool
    message: str
    customer_name: str | None
    appointments: list[AppointmentHistoryItem]


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def _extract_slot(entry: object) -> str | None:
    if isinstance(entry, str):
        candidate = _clean(entry)
        return candidate or None

    if not isinstance(entry, dict):
        return None

    blocked_status = str(entry.get("bookstatus") or entry.get("status") or "").strip().lower()
    if blocked_status in {"booked", "blocked", "unavailable"}:
        return None

    if entry.get("available") is False:
        return None

    if entry.get("is_booked") in {True, 1, "1", "true", "True"}:
        return None

    for key in ("booktime", "slot", "time", "time_slot"):
        candidate = _clean(entry.get(key))
        if candidate:
            return candidate

    return None


async def fetch_available_visit_slots(bookdate: str, store_id: int = DEFAULT_STORE_ID) -> VisitAvailabilityResult:
    try:
        response = await http_get(BASE_URL, params={"date": bookdate, "store_id": store_id})
        payload = response.json() if response.content else {}
    except Exception:
        return VisitAvailabilityResult(
            success=False,
            message="Unable to fetch available visit slots right now. Please try again shortly.",
            slots=[],
        )

    if str(payload.get("status", "")).lower() != "success":
        return VisitAvailabilityResult(
            success=False,
            message=str(payload.get("message") or "Could not fetch available slots."),
            slots=[],
        )

    data = payload.get("data") if isinstance(payload.get("data"), list) else []
    slots: list[str] = []
    seen: set[str] = set()

    for entry in data:
        nested_slots = entry.get("slots") if isinstance(entry, dict) and isinstance(entry.get("slots"), list) else None
        if nested_slots is not None:
            for nested_entry in nested_slots:
                slot = _extract_slot(nested_entry)
                if slot and slot not in seen:
                    seen.add(slot)
                    slots.append(slot)
            continue

        slot = _extract_slot(entry)
        if slot and slot not in seen:
            seen.add(slot)
            slots.append(slot)

    if not slots:
        return VisitAvailabilityResult(
            success=False,
            message="No slots are available for the selected date. Please choose another date.",
            slots=[],
        )

    return VisitAvailabilityResult(success=True, message="Slots fetched successfully.", slots=slots)


async def book_store_visit(
    mobile: str,
    bookdate: str,
    booktime: str,
    store_id: int = DEFAULT_STORE_ID,
) -> BookVisitResult:
    payload = {
        "mobile": mobile,
        "store_id": store_id,
        "bookdate": bookdate,
        "booktime": booktime,
    }

    try:
        response = await http_post(BASE_URL, json_body=payload)
        payload = response.json() if response.content else {}
    except Exception:
        return BookVisitResult(
            success=False,
            message="Unable to book store visit right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(payload.get("status", "")).lower() == "success":
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        appointment_id = data.get("appointment_id")
        message = str(payload.get("message") or "appointment booked successfully")
        if appointment_id is not None:
            message = f"{message} (Appointment ID: {appointment_id})"
        return BookVisitResult(success=True, message=message, appointment_id=appointment_id)

    return BookVisitResult(
        success=False,
        message=str(payload.get("message") or "Unable to book appointment for selected slot."),
    )


async def fetch_appointment_history(mobile: str) -> AppointmentHistoryResult:
    try:
        response = await http_get(BASE_URL, params={"mobile": mobile})
        payload = response.json() if response.content else {}
    except Exception:
        return AppointmentHistoryResult(
            success=False,
            message="Unable to fetch visit history right now. Please try again shortly.",
            customer_name=None,
            appointments=[],
        )

    if str(payload.get("status", "")).lower() != "success":
        return AppointmentHistoryResult(
            success=False,
            message=str(payload.get("message") or "Could not fetch visit history."),
            customer_name=None,
            appointments=[],
        )

    client_data = payload.get("client") if isinstance(payload.get("client"), dict) else {}
    customer_name = _clean(client_data.get("cname")) or None

    appointments_payload = payload.get("appointments") if isinstance(payload.get("appointments"), list) else []
    appointments: list[AppointmentHistoryItem] = []

    for appointment in appointments_payload:
        if not isinstance(appointment, dict):
            continue

        appointments.append(
            AppointmentHistoryItem(
                appointment_id=str(appointment.get("id") or ""),
                bookdate_display=_clean(appointment.get("bookdate_display")) or _clean(appointment.get("bookdate")),
                booktime=_clean(appointment.get("booktime")),
                store_name=_clean(appointment.get("store_name")) or "Store",
                status_label=_clean(appointment.get("bookstatus_label")) or "Unknown",
            )
        )

    if not appointments:
        return AppointmentHistoryResult(
            success=False,
            message="No appointments found for your mobile number.",
            customer_name=customer_name,
            appointments=[],
        )

    return AppointmentHistoryResult(
        success=True,
        message="Visit history fetched successfully.",
        customer_name=customer_name,
        appointments=appointments,
    )
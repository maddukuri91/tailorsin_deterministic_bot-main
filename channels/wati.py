"""
WATI (WhatsApp) channel integration with interactive list/button messages.

Provides a Telegram-like inline keyboard experience on WhatsApp using
WATI's interactive list and button message APIs.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Header, HTTPException

from config import settings
from services.conversation_service import IncomingMessage, OutgoingMessage, handle_incoming_message
from services.idempotency import claim_event, release_event


router = APIRouter(prefix="/wati", tags=["wati"])
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Interactive message type constants
# ──────────────────────────────────────────────
# WhatsApp interactive list messages support up to 10 rows per section
MAX_LIST_ROWS = 10
# WhatsApp interactive buttons support up to 3 buttons
MAX_BUTTONS = 3

_MAP_LOCATION_PATTERN = re.compile(
    r"(?:geo:|[?&](?:q|query|ll|center)=|@)"
    r"\s*([-+]?\d{1,2}(?:\.\d+)?)\s*(?:,|%2c)\s*"
    r"([-+]?\d{1,3}(?:\.\d+)?)",
    flags=re.IGNORECASE,
)

_WATI_DEBUG_REDACTED_KEYS = {
    "authorization",
    "apikey",
    "api_key",
    "body",
    "from",
    "phone",
    "phone_number",
    "senderphone",
    "text",
    "token",
    "waid",
    "whatsappnumber",
}

WATI_NAVIGATION_MARKUP: dict[str, Any] = {
    "inline_keyboard": [[
        {"text": "Main menu", "callback_data": "menu_main_menu"},
        {"text": "Human support", "callback_data": "menu_handover"},
    ]]
}


def _normalize_whatsapp_user_id(value: str | None) -> int | None:
    if value is None:
        return None

    digits_only = "".join(character for character in str(value) if character.isdigit())
    if not digits_only:
        return None

    return int(digits_only)


def _extract_text(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if isinstance(text, str):
        return text.strip()

    nested_text = payload.get("text", {})
    if isinstance(nested_text, dict):
        body = nested_text.get("body")
        if isinstance(body, str):
            return body.strip()

    message = payload.get("message")
    if isinstance(message, str):
        return message.strip()

    data = payload.get("data", {})
    if isinstance(data, dict):
        candidate = data.get("text") or data.get("body") or data.get("message")
        if isinstance(candidate, str):
            return candidate.strip()

    return ""


def _extract_interactive_response(payload: dict[str, Any]) -> str | None:
    """
    Extract the button/list selection from an interactive message response.

    WATI sends interactive responses in the following format:
    {
      "interactive": {
        "type": "list_reply" | "button_reply",
        "list_reply": { "id": "menu_order_status", "title": "Track my order" },
        "button_reply": { "id": "menu_order_status", "title": "Track my order" }
      }
    }
    """
    # WATI's current Message Received webhook places these fields at the top
    # level: `listReply`, `interactiveButtonReply`, and `buttonReply`. Older
    # integrations (and some providers) nest them inside `interactive`.
    interactive = payload.get("interactive")
    nested = interactive if isinstance(interactive, dict) else {}
    data = payload.get("data")
    nested_data = data if isinstance(data, dict) else {}

    candidates = (
        payload.get("listReply"),
        payload.get("interactiveButtonReply"),
        payload.get("buttonReply"),
        nested.get("list_reply") or nested.get("listReply"),
        nested.get("button_reply") or nested.get("buttonReply"),
        nested_data.get("listReply") or nested_data.get("list_reply"),
        nested_data.get("interactiveButtonReply") or nested_data.get("buttonReply"),
        # Some WATI tenants return the selected reply object directly as
        # `data`, rather than nesting it under a reply-type key.
        nested_data,
    )
    for reply in candidates:
        if not isinstance(reply, dict):
            continue
        # Preserve our original callback ID when WATI returns it. Other `id`
        # fields can be WATI-internal row/message IDs, so never route on them
        # ahead of the visible label.
        for key in ("id", "buttonId", "rowId", "selectedRowId", "selectedButtonId"):
            value = reply.get(key)
            if isinstance(value, str) and value.strip() and value.strip().startswith("menu_"):
                return value.strip()

        # WATI commonly returns the selected title/text. Those labels map to
        # the same intents as Telegram's callback data.
        for key in ("title", "text", "buttonText", "rowTitle"):
            value = reply.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        # Last resort for a provider-specific ID when no label is available.
        for key in ("id", "buttonId", "rowId", "selectedRowId", "selectedButtonId"):
            value = reply.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _normalize_wati_list_selection(text: str) -> str:
    """Remove WATI's display-only list-row description from a selection."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 2 and lines[1].casefold() == "tap to continue":
        return lines[0]
    return text


def _extract_contact_phone(payload: dict[str, Any]) -> str | None:
    contact = payload.get("contact")
    if isinstance(contact, dict):
        phone_number = contact.get("phone_number") or contact.get("phone")
        if isinstance(phone_number, str) and phone_number.strip():
            return phone_number.strip()

    data = payload.get("data", {})
    if isinstance(data, dict):
        phone_number = data.get("phone") or data.get("phone_number")
        if isinstance(phone_number, str) and phone_number.strip():
            return phone_number.strip()

    return None


def _extract_location(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """Extract a shared WhatsApp location from WATI webhook payloads.

    WATI tenants send location data in several forms, including nested
    dictionaries and a JSON-encoded ``data`` string. Decode only JSON-shaped
    strings so ordinary customer text is never interpreted as payload data.
    """
    candidates: list[dict[str, Any]] = []
    string_values: list[str] = []
    stack: list[Any] = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            candidates.append(value)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            string_values.append(value)
            compact_value = value.strip()
            if (
                len(compact_value) <= 16_384
                and compact_value[:1] in {"{", "["}
            ):
                try:
                    decoded = json.loads(compact_value)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, (dict, list)):
                    stack.append(decoded)

    for candidate in candidates:
        latitude = candidate.get(
            "latitude",
            candidate.get("lat", candidate.get("latitudeDegrees")),
        )
        longitude = candidate.get(
            "longitude",
            candidate.get(
                "lng",
                candidate.get("long", candidate.get("lon", candidate.get("longitudeDegrees"))),
            ),
        )
        try:
            lat_value = float(latitude)
            lng_value = float(longitude)
        except (TypeError, ValueError):
            pass
        else:
            if -90 <= lat_value <= 90 and -180 <= lng_value <= 180:
                return lat_value, lng_value

        # GeoJSON-style locations are occasionally returned as
        # {"coordinates": [longitude, latitude]}.
        coordinates = candidate.get("coordinates")
        if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            try:
                lng_value = float(coordinates[0])
                lat_value = float(coordinates[1])
            except (TypeError, ValueError):
                continue
            if -90 <= lat_value <= 90 and -180 <= lng_value <= 180:
                return lat_value, lng_value

    # Some WATI tenants provide a Google Maps / geo URI rather than separate
    # numeric fields. Parse coordinates only from an explicit map-location URI
    # so ordinary customer text can never be mistaken for a location.
    for value in string_values:
        match = _MAP_LOCATION_PATTERN.search(unquote(value))
        if not match:
            continue
        lat_value, lng_value = map(float, match.groups())
        if -90 <= lat_value <= 90 and -180 <= lng_value <= 180:
            return lat_value, lng_value

    return None, None


def _payload_key_paths(payload: Any, *, max_depth: int = 4) -> list[str]:
    """Return structural key paths only; never log customer values or IDs."""
    paths: list[str] = []
    stack: list[tuple[Any, str, int]] = [(payload, "", 0)]
    while stack and len(paths) < 80:
        value, prefix, depth = stack.pop()
        if not isinstance(value, dict) or depth >= max_depth:
            continue
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            if isinstance(child, dict):
                stack.append((child, path, depth + 1))
            elif isinstance(child, list):
                for item in child[:3]:
                    stack.append((item, f"{path}[]", depth + 1))
    return sorted(set(paths))


def _safe_wati_debug_payload(payload: Any) -> str:
    """Serialize diagnostics without customer content or credentials."""
    def redact(value: Any, key: str | None = None) -> Any:
        if key is not None and key.casefold() in _WATI_DEBUG_REDACTED_KEYS:
            return "[redacted]"
        if isinstance(value, dict):
            return {str(child_key): redact(child_value, str(child_key)) for child_key, child_value in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value[:20]]
        if isinstance(value, str) and len(value) > 500:
            return f"{value[:500]}…[truncated]"
        return value

    return json.dumps(redact(payload), default=str)[:4000]


def _is_location_event(payload: Any) -> bool:
    """Identify a location webhook without relying on one WATI envelope."""
    stack: list[Any] = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if str(value.get("type", "")).casefold() == "location":
                return True
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return False


def validate_wati_webhook_secret(header_secret: str | None) -> None:
    if not settings.wati_webhook_secret:
        return

    if header_secret != settings.wati_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


def parse_wati_update(update: dict[str, Any]) -> IncomingMessage | None:
    source_data = update.get("data") if isinstance(update.get("data"), dict) else update

    user_identifier = (
        source_data.get("waId")
        or source_data.get("whatsappNumber")
        or source_data.get("senderPhone")
        or source_data.get("from")
        or update.get("waId")
        or update.get("whatsappNumber")
        or update.get("senderPhone")
        or update.get("from")
    )

    user_id = _normalize_whatsapp_user_id(user_identifier)
    if user_id is None:
        return None

    # First try to extract interactive response (button/list tap)
    interactive_text = _extract_interactive_response(source_data if isinstance(source_data, dict) else update)
    if interactive_text is None and source_data is not update:
        # Preserve top-level WATI reply fields when `data` also exists.
        interactive_text = _extract_interactive_response(update)
    text = interactive_text or _extract_text(source_data if isinstance(source_data, dict) else update)
    text = _normalize_wati_list_selection(text)
    contact_phone = _extract_contact_phone(source_data if isinstance(source_data, dict) else update)
    location_lat, location_lng = _extract_location(source_data if isinstance(source_data, dict) else update)
    if location_lat is None or location_lng is None:
        location_lat, location_lng = _extract_location(update)
    message_type = str(
        (source_data if isinstance(source_data, dict) else update).get("type")
        or update.get("type", "")
    ).casefold()
    if message_type == "location" and (location_lat is None or location_lng is None):
        logger.warning(
            "WATI location event did not contain readable coordinates; payload=%s",
            _safe_wati_debug_payload(update),
        )
    interactive_message_types = {"interactive", "button", "list"}
    is_menu_selection = bool(interactive_text) or message_type in interactive_message_types

    return IncomingMessage(
        user_id=user_id,
        text=text,
        contact_phone=contact_phone,
        contact_user_id=user_id if contact_phone else None,
        source_user_id=user_id,
        location_lat=location_lat,
        location_lng=location_lng,
        is_start_command=text.casefold() in {"/start", "hi", "hello", "menu"},
        metadata={
            "platform": "wati",
            "raw_update": update,
            "message_id": source_data.get("messageId") or source_data.get("id") or update.get("messageId") or update.get("id"),
            "is_menu_selection": is_menu_selection,
            # Some WATI accounts represent an inbound map as a location event
            # with data=null. Preserve that fact so the conversation can give
            # an actionable map-link fallback instead of repeating the same
            # location request.
            "location_coordinates_unavailable": (
                message_type == "location"
                and (location_lat is None or location_lng is None)
            ),
        },
    )


# ──────────────────────────────────────────────
#  WATI API calls
# ──────────────────────────────────────────────

async def call_wati_api(
    endpoint: str,
    payload: dict[str, Any] | None = None,
    *,
    params: dict[str, str] | None = None,
) -> None:
    """Call a WATI API endpoint with the given payload."""
    if not settings.wati_base_url or not settings.wati_api_key:
        raise HTTPException(status_code=503, detail="WATI is not configured")

    url = f"{settings.wati_base_url.rstrip('/')}/api/v1/{endpoint.lstrip('/')}"
    # WATI's dashboard displays tokens with a "Bearer " label. Accept either
    # the raw token (the preferred .env format) or that copied form, but never
    # send the invalid "Bearer Bearer <token>" authorization header.
    api_key = settings.wati_api_key.strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload, params=params, headers=headers)
        response.raise_for_status()


async def fetch_latest_wati_location(user_id: int) -> tuple[float | None, float | None]:
    """Recover location coordinates omitted from a WATI webhook body.

    WATI stores the complete inbound message, even when a tenant's webhook
    event only contains a map notification. This fallback is called only for
    inbound location events that did not already include coordinates.
    """
    if not settings.wati_base_url or not settings.wati_api_key:
        return None, None

    api_key = settings.wati_api_key.strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    url = f"{settings.wati_base_url.rstrip('/')}/api/v1/getMessages/{user_id}"

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
            response = await client.get(
                url,
                params={"pageSize": "10", "pageNumber": "1"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            response_data = response.json()
    except Exception:
        logger.warning("WATI message-history lookup failed for a location event", exc_info=True)
        return None, None

    if not isinstance(response_data, (dict, list)):
        return None, None
    result = _extract_location(response_data)
    if result == (None, None):
        logger.warning(
            "WATI getMessages fallback returned no coordinates; response=%s",
            _safe_wati_debug_payload(response_data),
        )
    return result


# ──────────────────────────────────────────────
#  Interactive message builders
# ──────────────────────────────────────────────

def _extract_buttons_from_reply_markup(reply_markup: dict[str, Any]) -> list[dict[str, str]]:
    """
    Extract button definitions from either inline_keyboard or keyboard format.
    Returns a flat list of {text, callback_data} dicts.
    """
    buttons: list[dict[str, str]] = []

    # Try inline_keyboard first (Telegram style)
    inline_rows = reply_markup.get("inline_keyboard")
    if inline_rows:
        for row in inline_rows:
            for button in row:
                if button.get("text"):
                    buttons.append({
                        "text": button["text"],
                        "callback_data": button.get("callback_data", ""),
                    })
        return buttons

    # Fall back to keyboard (ReplyKeyboardMarkup)
    keyboard_rows = reply_markup.get("keyboard")
    if keyboard_rows:
        for row in keyboard_rows:
            for button in row:
                # A WhatsApp list cannot invoke the native location picker,
                # but it should still present a clear "Share current
                # location" action. The conversation service gives the
                # customer the platform-specific attachment instruction after
                # they choose it.
                if button.get("text") and not button.get("request_contact"):
                    buttons.append({
                        "text": button["text"],
                        "callback_data": "",
                    })
        return buttons

    return buttons


def _wati_menu_label(value: str) -> str:
    """Convert Telegram-style labels into WhatsApp-safe list/button labels."""
    label = value.strip()
    if label and not label[0].isalnum() and " " in label:
        label = label.split(" ", 1)[1].strip()
    return label[:24]


def _format_wati_text(value: str) -> str:
    """Use WhatsApp's single-asterisk bold convention, not Telegram **bold**."""
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", value.strip(), flags=re.DOTALL)


def _wati_markup(message: OutgoingMessage) -> dict[str, Any]:
    """Provide consistent navigation on free-text sub-flow messages."""
    if message.reply_markup is not None:
        return message.reply_markup
    return WATI_NAVIGATION_MARKUP if message.include_wati_navigation else {}


def _build_interactive_list_payload(
    user_id: int,
    text: str,
    buttons: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Build a WATI interactive list message payload.

    WhatsApp list messages support up to 10 rows. If there are more than 10
    options, they are split into multiple sections.
    """
    rows = []
    for button in buttons:
        label = _wati_menu_label(button["text"])
        # Strip emoji from the ID for cleaner callback handling
        callback_id = button.get("callback_data", "") or label
        rows.append({
            "id": callback_id,
            "title": label,
        })

    # Split into sections of MAX_LIST_ROWS
    sections = []
    for i in range(0, len(rows), MAX_LIST_ROWS):
        chunk = rows[i:i + MAX_LIST_ROWS]
        section_title = "Options" if len(rows) <= MAX_LIST_ROWS else f"Page {i // MAX_LIST_ROWS + 1}"
        sections.append({
            "title": section_title,
            "rows": chunk,
        })

    # WATI's list API uses its own schema. The recipient is sent as the
    # `whatsappNumber` query parameter by `send_wati_message`, not in JSON.
    return {
        "body": _format_wati_text(text)[:1024],
        "buttonText": "Explore options",
        "sections": sections,
    }


def _build_interactive_buttons_payload(
    user_id: int,
    text: str,
    buttons: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Build a WATI interactive buttons message payload.

    WhatsApp supports up to 3 buttons per message. If there are more than 3
    options, fall back to list message.
    """
    if len(buttons) > MAX_BUTTONS:
        return _build_interactive_list_payload(user_id, text, buttons)

    reply_buttons = []
    for button in buttons:
        label = _wati_menu_label(button["text"])
        # WhatsApp button title max 20 chars
        title = label[:20]
        # The WATI buttons API sends the button text back in the webhook.
        # Our router understands the display label as well as callback IDs.
        reply_buttons.append({"text": title})

    return {
        "body": _format_wati_text(text)[:1024],
        "buttons": reply_buttons,
    }


def _extract_buttons_text(reply_markup: dict[str, Any]) -> str:
    """Extract button labels from either inline_keyboard or keyboard format as text."""
    lines: list[str] = []

    # Try inline_keyboard first (Telegram style)
    inline_rows = reply_markup.get("inline_keyboard")
    if inline_rows:
        for row in inline_rows:
            labels = [button.get("text", "") for button in row if button.get("text")]
            if labels:
                lines.append(" | ".join(labels))
        return "\n".join(lines)

    # Fall back to keyboard (ReplyKeyboardMarkup)
    keyboard_rows = reply_markup.get("keyboard")
    if keyboard_rows:
        for row in keyboard_rows:
            labels = [button.get("text", "") for button in row if button.get("text")]
            if labels:
                lines.append(" | ".join(labels))
        return "\n".join(lines)

    return ""


def build_wati_payload(user_id: int, message: OutgoingMessage) -> dict[str, Any]:
    """
    Build the appropriate WATI API payload based on the reply_markup type.

    Priority:
    1. If inline_keyboard is present → use interactive list/buttons (Telegram-like)
    2. If keyboard with request_location → explain the WhatsApp alternative
    3. Otherwise → plain text message
    """
    text = _format_wati_text(message.text)
    effective_markup = _wati_markup(message)

    # WATI has no native location-request control. Keep the other choices
    # (manual entry, skip, navigation) interactive and add a short instruction
    # rather than discarding the whole keyboard.
    location_requested = False
    if message.reply_markup and message.reply_markup.get("keyboard"):
        for row in message.reply_markup.get("keyboard", []):
            for button in row:
                if button.get("request_location"):
                    location_requested = True
    if location_requested:
        text = f"{text}\n\nYou can also attach your location using WhatsApp's location option."

    # Check for inline_keyboard (Telegram-style) → use interactive list/buttons
    if effective_markup.get("inline_keyboard") or effective_markup.get("keyboard"):
        buttons = _extract_buttons_from_reply_markup(effective_markup)
        if buttons:
            # Use interactive buttons for 1-3 options, list for 4+
            if len(buttons) <= MAX_BUTTONS:
                return _build_interactive_buttons_payload(user_id, text, buttons)
            else:
                return _build_interactive_list_payload(user_id, text, buttons)

    # Fall back to text with button labels appended
    buttons_text = _extract_buttons_text(effective_markup)
    if buttons_text:
        text = f"{text}\n\n{buttons_text}"

    return {"messageText": text}


async def send_wati_message(user_id: int, message: OutgoingMessage) -> None:
    payload = build_wati_payload(user_id, message)

    # WATI has distinct endpoints and payload shapes for buttons and lists.
    # `sendInteractiveMessage` is not a WATI endpoint.
    if "buttons" in payload:
        await call_wati_api(
            "sendInteractiveButtonsMessage",
            payload,
            params={"whatsappNumber": str(user_id)},
        )
    elif "sections" in payload:
        await call_wati_api(
            "sendInteractiveListMessage",
            payload,
            params={"whatsappNumber": str(user_id)},
        )
    else:
        await call_wati_api(
            f"sendSessionMessage/{user_id}",
            params={"messageText": str(payload["messageText"])},
        )


async def process_wati_update(update: dict[str, Any]) -> dict[str, bool]:
    incoming_message = parse_wati_update(update)
    if incoming_message is None:
        return {"ok": True}

    if (
        _is_location_event(update)
        and (incoming_message.location_lat is None or incoming_message.location_lng is None)
    ):
        incoming_message.location_lat, incoming_message.location_lng = await fetch_latest_wati_location(
            incoming_message.user_id
        )

    metadata = incoming_message.metadata or {}
    event_id = str(metadata.get("message_id") or "")
    if not await claim_event("wati", event_id):
        return {"ok": True}

    try:
        outgoing_messages = await handle_incoming_message(incoming_message)
    except Exception:
        await release_event("wati", event_id)
        raise

    # Do not release the idempotency claim if outbound delivery fails after a
    # successful CRM/conversation action; replaying could duplicate an order.
    for outgoing_message in outgoing_messages:
        await send_wati_message(incoming_message.user_id, outgoing_message)

    return {"ok": True}


@router.post("/webhook")
async def wati_webhook(
    update: dict[str, Any],
    x_wati_webhook_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    validate_wati_webhook_secret(x_wati_webhook_secret)
    return await process_wati_update(update)

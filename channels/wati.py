"""
WATI (WhatsApp) channel integration with interactive list/button messages.

Provides a Telegram-like inline keyboard experience on WhatsApp using
WATI's interactive list and button message APIs.
"""

import re
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException

from config import settings
from services.conversation_service import IncomingMessage, OutgoingMessage, handle_incoming_message
from services.idempotency import claim_event, release_event


router = APIRouter(prefix="/wati", tags=["wati"])

# ──────────────────────────────────────────────
#  Interactive message type constants
# ──────────────────────────────────────────────
# WhatsApp interactive list messages support up to 10 rows per section
MAX_LIST_ROWS = 10
# WhatsApp interactive buttons support up to 3 buttons
MAX_BUTTONS = 3

WATI_NAVIGATION_MARKUP: dict[str, Any] = {
    "inline_keyboard": [[
        {"text": "Main menu", "callback_data": "menu_main_menu"},
        {"text": "Talk to an agent", "callback_data": "menu_handover"},
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
    interactive = payload.get("interactive")
    if not isinstance(interactive, dict):
        return None

    # Check for list reply
    list_reply = interactive.get("list_reply") or interactive.get("listReply")
    if isinstance(list_reply, dict):
        return list_reply.get("id") or list_reply.get("title")

    # Check for button reply
    button_reply = interactive.get("button_reply") or interactive.get("buttonReply")
    if isinstance(button_reply, dict):
        return button_reply.get("id") or button_reply.get("title")

    return None


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
    text = interactive_text or _extract_text(source_data if isinstance(source_data, dict) else update)
    contact_phone = _extract_contact_phone(source_data if isinstance(source_data, dict) else update)

    return IncomingMessage(
        user_id=user_id,
        text=text,
        contact_phone=contact_phone,
        contact_user_id=user_id if contact_phone else None,
        source_user_id=user_id,
        is_start_command=text.casefold() in {"/start", "hi", "hello", "menu"},
        metadata={
            "platform": "wati",
            "raw_update": update,
            "message_id": source_data.get("messageId") or source_data.get("id") or update.get("messageId") or update.get("id"),
        },
    )


# ──────────────────────────────────────────────
#  WATI API calls
# ──────────────────────────────────────────────

async def call_wati_api(endpoint: str, payload: dict[str, Any]) -> None:
    """Call a WATI API endpoint with the given payload."""
    if not settings.wati_base_url or not settings.wati_api_key:
        raise HTTPException(status_code=503, detail="WATI is not configured")

    url = f"{settings.wati_base_url.rstrip('/')}/api/v1/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {settings.wati_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()


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
                if button.get("text") and not button.get("request_contact") and not button.get("request_location"):
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
    return message.reply_markup or WATI_NAVIGATION_MARKUP


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
            "description": "Tap to continue",
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

    return {
        "whatsappNumber": str(user_id),
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": "tailorsin.com",
            },
            "body": {
                "text": _format_wati_text(text)[:1024],  # WhatsApp body max 1024 chars
            },
            "footer": {
                "text": "tailorsin.com",
            },
            "action": {
                "button": "Explore options",
                "sections": sections,
            },
        },
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
        callback_id = button.get("callback_data", "") or label
        # WhatsApp button title max 20 chars
        title = label[:20]
        reply_buttons.append({
            "type": "reply",
            "reply": {
                "id": callback_id,
                "title": title,
            },
        })

    return {
        "whatsappNumber": str(user_id),
        "interactive": {
            "type": "button",
            "body": {
                "text": _format_wati_text(text)[:1024],
            },
            "action": {
                "buttons": reply_buttons,
            },
        },
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
    2. If keyboard with request_location → use text + location button
    3. Otherwise → plain text message
    """
    text = _format_wati_text(message.text)
    effective_markup = _wati_markup(message)

    # Check for location request buttons
    if message.reply_markup and message.reply_markup.get("keyboard"):
        for row in message.reply_markup.get("keyboard", []):
            for button in row:
                if button.get("request_location"):
                    # WATI supports location request buttons via sendSessionMessage
                    payload = {
                        "whatsappNumber": str(user_id),
                        "messageText": text,
                    }
                    payload["buttons"] = [
                        {
                            "text": button.get("text", "📍 Share Location"),
                            "type": "location",
                        }
                    ]
                    return payload

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

    return {
        "whatsappNumber": str(user_id),
        "messageText": text,
    }


async def send_wati_message(user_id: int, message: OutgoingMessage) -> None:
    payload = build_wati_payload(user_id, message)

    # Determine which API endpoint to use
    if "interactive" in payload:
        # Use the interactive message endpoint
        await call_wati_api("sendInteractiveMessage", payload)
    else:
        # Use the standard session message endpoint
        await call_wati_api("sendSessionMessage", payload)


async def process_wati_update(update: dict[str, Any]) -> dict[str, bool]:
    incoming_message = parse_wati_update(update)
    if incoming_message is None:
        return {"ok": True}

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

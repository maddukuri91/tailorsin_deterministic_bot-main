import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from twilio.request_validator import RequestValidator
from xml.sax.saxutils import escape as xml_escape

from config import settings
from services.conversation_service import IncomingMessage, OutgoingMessage, handle_incoming_message
from services.idempotency import claim_event, release_event


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/twilio", tags=["twilio"])

# Content SIDs are reusable. Keeping this small cache avoids creating a new
# Content API resource every time the same menu is displayed by this worker.
_whatsapp_content_sids: dict[str, str] = {}
_twilio_send_lock = asyncio.Lock()
_next_twilio_send_at = 0.0
TWILIO_MIN_SEND_INTERVAL_SECONDS = 1.1
TWILIO_MAX_SEND_ATTEMPTS = 3

# WhatsApp does not have persistent keyboards. These two actions give every
# free-text step a predictable escape hatch without cluttering the main menu.
WHATSAPP_NAVIGATION_MARKUP: dict[str, Any] = {
    "inline_keyboard": [[
        {"text": "Main menu", "callback_data": "menu_main_menu"},
        {"text": "Talk to an agent", "callback_data": "menu_handover"},
    ]]
}


def validate_twilio_signature(url: str, signature: str | None, form_data: dict[str, Any]) -> None:
    """Reject a webhook that was not signed by Twilio when validation is enabled."""
    if not settings.twilio_validate_signature or not settings.twilio_auth_token:
        return

    if not signature or not RequestValidator(settings.twilio_auth_token).validate(url, form_data, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio webhook signature")


def _normalise_phone_number(value: str | None) -> str | None:
    """Return an E.164-like number without a channel prefix, or None if invalid."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits or None


def _is_whatsapp_address(value: str | None) -> bool:
    return str(value or "").strip().lower().startswith("whatsapp:")


def _normalise_button_selection(value: str | None) -> str:
    """Convert a Telegram-style callback payload into a router input."""
    selection = str(value or "").strip()
    if selection.startswith("menu_"):
        return selection[len("menu_"):]
    return selection


def parse_twilio_update(form_data: dict[str, Any]) -> IncomingMessage | None:
    """Parse Twilio webhook form data into IncomingMessage."""
    from_number = str(form_data.get("From") or "").strip()
    
    # Extract phone number (Twilio format: whatsapp:+919876543210 or +919876543210)
    normalised_number = _normalise_phone_number(from_number)
    if normalised_number is None:
        return None
    user_id = int(normalised_number)
    
    # Twilio sends button taps in ButtonPayload (stable hidden id) and
    # ButtonText. Prefer the payload so labels can change without breaking
    # routing. Plain typed messages continue to use Body.
    text = _normalise_button_selection(
        form_data.get("ButtonPayload") or form_data.get("ButtonText") or form_data.get("Body")
    )
    
    # Check for media (images, etc.)
    try:
        num_media = int(form_data.get("NumMedia", 0) or 0)
    except (TypeError, ValueError):
        num_media = 0
    
    return IncomingMessage(
        user_id=user_id,
        text=text,
        contact_phone=normalised_number,
        contact_user_id=user_id,
        source_user_id=user_id,
        is_start_command=text.lower() in {"/start", "hi", "hello", "menu"},
        metadata={
            "platform": "twilio",
            "num_media": num_media,
            "message_sid": form_data.get("MessageSid"),
            "from_address": from_number,
            # This is Twilio's configured sender that received the inbound
            # WhatsApp message. Reuse it for the reply to avoid sender mismatch.
            "to_address": str(form_data.get("To") or "").strip(),
            "is_whatsapp": _is_whatsapp_address(from_number),
            "button_payload": form_data.get("ButtonPayload"),
        },
    )




def build_twilio_response(messages: list[OutgoingMessage]) -> str:
    """Build TwiML response from outgoing messages."""
    # Twilio expects TwiML (XML) response
    # For multiple messages, we'll send the first one
    # Subsequent messages would need to be sent via REST API
    
    if not messages:
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    
    first_message = messages[0]
    text = xml_escape(first_message.text)
    
    # Convert reply_markup buttons to text menu for Twilio (SMS doesn't support buttons)
    if first_message.reply_markup:
        menu_text = _build_text_menu(first_message.reply_markup)
        if menu_text:
            text += "\n\n" + xml_escape(menu_text)
    
    # If there are more messages, append them (limited by Twilio)
    if len(messages) > 1:
        text += "\n\n---\n"
        for msg in messages[1:]:
            text += f"{xml_escape(msg.text)}\n"
    
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{text}</Message></Response>'
    return twiml


def _build_text_menu(reply_markup: dict[str, Any]) -> str:
    """Convert button markup to text menu for SMS platforms."""
    lines: list[str] = []
    lines.append("📋 Menu Options:")
    lines.append("")
    
    # Try inline_keyboard first (Telegram style)
    inline_rows = reply_markup.get("inline_keyboard")
    if inline_rows:
        counter = 1
        for row in inline_rows:
            for button in row:
                button_text = button.get("text", "")
                # Remove emoji prefix for cleaner SMS
                button_text = button_text.strip()
                if button_text:
                    lines.append(f"{counter}. {button_text}")
                    counter += 1
        return "\n".join(lines)
    
    # Fall back to keyboard (ReplyKeyboardMarkup)
    keyboard_rows = reply_markup.get("keyboard")
    if keyboard_rows:
        counter = 1
        for row in keyboard_rows:
            for button in row:
                button_text = button.get("text", "")
                button_text = button_text.strip()
                if button_text:
                    lines.append(f"{counter}. {button_text}")
                    counter += 1
        return "\n".join(lines)
    
    return ""


def _extract_menu_items(reply_markup: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten Telegram keyboard markup into Twilio list-picker items."""
    items: list[dict[str, str]] = []
    rows = reply_markup.get("inline_keyboard") or reply_markup.get("keyboard") or []
    for row in rows:
        for button in row:
            label = _whatsapp_menu_label(str(button.get("text") or ""))
            if not label or button.get("request_contact") or button.get("request_location"):
                continue
            payload = str(button.get("callback_data") or label).strip()
            items.append({"item": label[:24], "id": payload[:200], "description": "Select this option"})
    return items[:10]


def _whatsapp_menu_label(value: str) -> str:
    """Make a Telegram button label safe for WhatsApp's 24-character limit."""
    label = value.strip()
    # Telegram's buttons start with a decorative emoji. WhatsApp counts some
    # emoji as multiple characters, so remove that prefix before truncating.
    if label and not label[0].isalnum() and " " in label:
        label = label.split(" ", 1)[1].strip()
    return label[:24]


def _format_whatsapp_text(value: str) -> str:
    """Translate the small Markdown subset used by Telegram into WhatsApp text."""
    text = value.strip()
    # Telegram's **bold** is not WhatsApp formatting. Convert it to WhatsApp's
    # single-asterisk bold form, while preserving existing *bold* sections.
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=re.DOTALL)
    return text


def _whatsapp_markup(message: OutgoingMessage) -> dict[str, Any]:
    """Return explicit choices, or a consistent navigation pair for free text."""
    return message.reply_markup or WHATSAPP_NAVIGATION_MARKUP


def _build_whatsapp_content_types(message: OutgoingMessage) -> dict[str, Any] | None:
    """Build a consistent native WhatsApp UI for menus and sub-flow messages."""
    items = _extract_menu_items(_whatsapp_markup(message))
    if not items:
        return None

    body = _format_whatsapp_text(message.text)[:1024]
    types: dict[str, Any] = {"twilio/text": {"body": _message_body(message)}}
    if len(items) <= 3:
        # WhatsApp supports up to three visible quick-reply buttons during an
        # active customer-service session.
        types["twilio/quick-reply"] = {
            "body": body,
            "actions": [
                {"title": item["item"][:20], "id": item["id"]}
                for item in items
            ],
        }
    else:
        # Larger menus remain selectable through WhatsApp's native list UI.
        types["twilio/list-picker"] = {
            "body": body,
            "button": "Explore options",
            "items": items,
        }
    return types


async def _get_whatsapp_content_sid(message: OutgoingMessage) -> str | None:
    """Create (or reuse) the Twilio Content template that renders a menu."""
    content_types = _build_whatsapp_content_types(message)
    if content_types is None:
        return None

    fingerprint = hashlib.sha256(
        json.dumps(content_types, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if fingerprint in _whatsapp_content_sids:
        return _whatsapp_content_sids[fingerprint]

    # Unlike the Messages API, the Content API only accepts application/json
    # with lower-case field names. Form-encoding produces HTTP 415.
    payload = {
        "friendly_name": f"tailorsin_menu_{fingerprint[:12]}",
        "language": "en",
        "types": content_types,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://content.twilio.com/v1/Content",
            json=payload,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
        if response.is_error:
            # Twilio's response contains the field-level Content Template
            # validation message; preserve it in the application log.
            logger.error(
                "Twilio Content API rejected menu template (status=%s): %s",
                response.status_code,
                response.text,
            )
        response.raise_for_status()
        content_sid = str(response.json().get("sid") or "")

    if not content_sid:
        raise HTTPException(status_code=502, detail="Twilio Content API did not return a Content SID")
    _whatsapp_content_sids[fingerprint] = content_sid
    return content_sid


async def send_twilio_message(
    user_id: int,
    message: OutgoingMessage,
    *,
    is_whatsapp: bool | None = None,
    from_address: str | None = None,
) -> None:
    """Send a message via Twilio REST API (for follow-up messages)."""
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.warning("Twilio not configured, skipping message send")
        return
    
    to_number = str(user_id)
    from_number = (from_address or settings.twilio_phone_number).strip()
    if not from_number:
        raise HTTPException(status_code=503, detail="TWILIO_PHONE_NUMBER is required")
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    
    # Determine if WhatsApp or SMS
    use_whatsapp = settings.twilio_whatsapp_enabled if is_whatsapp is None else is_whatsapp
    if use_whatsapp:
        content_sid = await _get_whatsapp_content_sid(message)
        payload = {
            "To": f"whatsapp:+{to_number}",
            "From": _whatsapp_address(from_number),
        }
        if content_sid:
            # ContentSid is required for WhatsApp interactive list messages.
            payload["ContentSid"] = content_sid
        else:
            payload["Body"] = _message_body(message)
    else:
        # SMS - use text-based menu
        payload = {
            "To": f"+{to_number}",
            "From": from_number,
            "Body": _message_body(message),
        }
    
    await _post_twilio_message(url, payload)


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    """Use Twilio's retry hint where available, otherwise exponential backoff."""
    try:
        return max(float(response.headers.get("Retry-After", "")), TWILIO_MIN_SEND_INTERVAL_SECONDS)
    except ValueError:
        return TWILIO_MIN_SEND_INTERVAL_SECONDS * (2 ** attempt)


async def _post_twilio_message(url: str, payload: dict[str, str]) -> None:
    """Send one message at a time and recover from Twilio's 429 rate limit."""
    global _next_twilio_send_at

    async with _twilio_send_lock:
        wait_seconds = _next_twilio_send_at - time.monotonic()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        async with httpx.AsyncClient(timeout=15) as client:
            for attempt in range(TWILIO_MAX_SEND_ATTEMPTS):
                response = await client.post(
                    url,
                    data=payload,
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                )
                if response.status_code != 429:
                    _next_twilio_send_at = time.monotonic() + TWILIO_MIN_SEND_INTERVAL_SECONDS
                    response.raise_for_status()
                    return

                delay = _retry_after_seconds(response, attempt)
                logger.warning(
                    "Twilio rate limit hit; retrying outbound message in %.1f seconds (attempt %s/%s)",
                    delay,
                    attempt + 1,
                    TWILIO_MAX_SEND_ATTEMPTS,
                )
                await asyncio.sleep(delay)

        _next_twilio_send_at = time.monotonic() + TWILIO_MIN_SEND_INTERVAL_SECONDS
        raise HTTPException(status_code=503, detail="Twilio rate limit exceeded; please retry shortly")


def _whatsapp_address(number: str) -> str:
    """Format a configured Twilio WhatsApp sender exactly once."""
    clean = number.strip()
    if clean.lower().startswith("whatsapp:"):
        return clean
    return f"whatsapp:{clean}"


def _message_body(message: OutgoingMessage) -> str:
    """Twilio free-form WhatsApp messages do not support Telegram reply markup."""
    text = _format_whatsapp_text(message.text)
    if not message.reply_markup:
        return text
    menu_text = _build_text_menu(message.reply_markup)
    return f"{text}\n\n{menu_text}" if menu_text else text


def _build_whatsapp_buttons(reply_markup: dict[str, Any]) -> str | None:
    """Build WhatsApp interactive button list for Twilio."""
    # Twilio supports up to 3 buttons for WhatsApp
    # Format: "list: Title: option1|option2|option3"
    
    buttons = []
    
    # Try inline_keyboard first
    inline_rows = reply_markup.get("inline_keyboard")
    if inline_rows:
        for row in inline_rows:
            for button in row:
                button_text = button.get("text", "").strip()
                # Remove emoji prefix for cleaner button text
                button_text = button_text.split(" ", 1)[-1] if " " in button_text else button_text
                if button_text and len(buttons) < 3:
                    buttons.append(button_text)
    
    # Fall back to keyboard
    if not buttons:
        keyboard_rows = reply_markup.get("keyboard")
        if keyboard_rows:
            for row in keyboard_rows:
                for button in row:
                    button_text = button.get("text", "").strip()
                    if button_text and len(buttons) < 3:
                        buttons.append(button_text)
    
    if buttons:
        # Twilio WhatsApp list format
        return f'list: Choose an option: {"|".join(buttons)}'
    
    return None


async def process_twilio_update(form_data: dict[str, Any]) -> str:
    """Process incoming Twilio webhook and return TwiML response."""
    incoming_message = parse_twilio_update(form_data)
    
    if incoming_message is None:
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

    event_id = str(form_data.get("MessageSid") or "")
    if not await claim_event("twilio", event_id):
        logger.info("Ignoring duplicate Twilio MessageSid=%s", event_id)
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    
    try:
        outgoing_messages = await handle_incoming_message(incoming_message)
    except Exception:
        await release_event("twilio", event_id)
        raise

    if outgoing_messages:
        metadata = incoming_message.metadata or {}
        is_whatsapp = bool(metadata.get("is_whatsapp"))
        if is_whatsapp:
            # TwiML can only return text. Send every WhatsApp reply through
            # the Messages API so menus render as real selectable lists.
            for msg in outgoing_messages:
                await send_twilio_message(
                    incoming_message.user_id,
                    msg,
                    is_whatsapp=True,
                    from_address=str(metadata.get("to_address") or "") or None,
                )
            return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

        # SMS does not support interactive Content menus; return its first
        # reply immediately and use REST for any follow-up messages.
        twiml_response = build_twilio_response([outgoing_messages[0]])
        for msg in outgoing_messages[1:]:
            try:
                await send_twilio_message(
                    incoming_message.user_id,
                    msg,
                    is_whatsapp=False,
                    from_address=str(metadata.get("to_address") or "") or None,
                )
            except Exception:
                logger.exception("Failed to send follow-up Twilio message")
        return twiml_response

    return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/webhook")
async def twilio_webhook(request: Request) -> str:
    """Twilio webhook endpoint - accepts form data."""
    form_data = await request.form()
    form_dict = dict(form_data)
    
    webhook_url = settings.twilio_webhook_url or str(request.url)
    validate_twilio_signature(webhook_url, request.headers.get("X-Twilio-Signature"), form_dict)
    
    return await process_twilio_update(form_dict)


# Support legacy /webhook path (without /twilio prefix)
@router.post("/webhook/legacy")
async def twilio_webhook_legacy(request: Request) -> str:
    """Legacy webhook endpoint for backward compatibility."""
    form_data = await request.form()
    form_dict = dict(form_data)
    webhook_url = settings.twilio_webhook_url or str(request.url)
    validate_twilio_signature(webhook_url, request.headers.get("X-Twilio-Signature"), form_dict)
    return await process_twilio_update(form_dict)

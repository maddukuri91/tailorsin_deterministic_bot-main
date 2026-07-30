import logging
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException

from config import settings
from services.conversation_service import IncomingMessage, OutgoingMessage, handle_incoming_message
from services.idempotency import claim_event, release_event


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/telegram", tags=["telegram"])
webhook_router = APIRouter(prefix="", tags=["telegram_webhook"])
TELEGRAM_API_BASE_URL = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


def extract_message(update: dict[str, Any]) -> dict[str, Any] | None:
    return update.get("message") or update.get("edited_message")


def extract_callback_query(update: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a callback query from the update, if present."""
    return update.get("callback_query")


def validate_webhook_secret(path_secret: str | None, header_secret: str | None) -> None:
    # Skip validation if TELEGRAM_WEBHOOK_SECRET is not configured
    if not settings.telegram_webhook_secret:
        return
    
    if path_secret is not None:
        if path_secret != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
        return

    if header_secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


def parse_telegram_update(update: dict[str, Any]) -> IncomingMessage | None:
    message = extract_message(update)
    if not message:
        return None

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return None

    text = (message.get("text") or "").strip()
    contact = message.get("contact") or {}
    location = message.get("location") or {}

    lat = location.get("latitude")
    lng = location.get("longitude")
    try:
        location_lat = float(lat) if lat is not None else None
        location_lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        location_lat = None
        location_lng = None

    return IncomingMessage(
        user_id=chat_id,
        text=text,
        contact_phone=contact.get("phone_number"),
        contact_user_id=contact.get("user_id"),
        source_user_id=message.get("from", {}).get("id"),
        is_start_command=text == "/start",
        location_lat=location_lat,
        location_lng=location_lng,
        metadata={"platform": "telegram", "raw_update": update},
    )


def parse_callback_query_update(update: dict[str, Any]) -> IncomingMessage | None:
    """
    Parse a callback_query update from an inline button tap.
    Converts the callback_data (e.g. "menu_3") into a text message (e.g. "3")
    so the conversation service can process it normally.
    """
    callback_query = extract_callback_query(update)
    if not callback_query:
        return None

    data = callback_query.get("data", "")
    message = callback_query.get("message")
    if not message:
        return None

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return None

    # Convert callback_data like "menu_3" to "3"
    text = ""
    if data.startswith("menu_"):
        text = data[len("menu_"):]

    return IncomingMessage(
        user_id=chat_id,
        text=text,
        source_user_id=callback_query.get("from", {}).get("id"),
        is_start_command=False,
        metadata={
            "platform": "telegram",
            "raw_update": update,
            "callback_query_id": callback_query.get("id"),
            "is_menu_selection": data.startswith("menu_"),
        },
    )


async def call_telegram_api(method: str, payload: dict[str, Any]) -> None:
    if not settings.telegram_bot_token:
        # Do not fail application startup when Telegram is intentionally disabled.
        raise HTTPException(status_code=503, detail="Telegram is not configured")

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{TELEGRAM_API_BASE_URL}/{method}", json=payload)
        try:
            data = response.json()
        except ValueError:
            data = {"description": response.text}

        if response.is_error:
            # Convert transport/API failures into the channel's public error
            # type so send_telegram_message can retry without Markdown.
            detail = data.get("description") or data
            raise HTTPException(status_code=502, detail=f"Telegram API error: {detail}")

    if not data.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram API error: {data}")


async def answer_callback_query(callback_query_id: str) -> None:
    """Acknowledge a callback query to remove the loading indicator on the button."""
    try:
        await call_telegram_api("answerCallbackQuery", {"callback_query_id": callback_query_id})
    except Exception:
        logger.warning("Failed to answer callback query %s", callback_query_id)


async def send_telegram_message(chat_id: int, message: OutgoingMessage) -> None:
    # Try sending with Markdown first, fall back to plain text on parse errors.
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message.text,
        "parse_mode": "Markdown",
    }
    if message.reply_markup is not None:
        payload["reply_markup"] = message.reply_markup
    try:
        await call_telegram_api("sendMessage", payload)
    except HTTPException as exc:
        # If Telegram rejects the Markdown, retry as plain text.
        if exc.status_code == 502 and "can't parse entities" in str(exc.detail).lower():
            logger.warning(
                "send_telegram_message Markdown parse error for chat_id=%s, retrying as plain text",
                chat_id,
            )
            plain_payload = {
                "chat_id": chat_id,
                "text": message.text,
            }
            if message.reply_markup is not None:
                plain_payload["reply_markup"] = message.reply_markup
            await call_telegram_api("sendMessage", plain_payload)
        else:
            raise


async def process_telegram_update(update: dict[str, Any]) -> dict[str, bool]:
    # First try to parse as a callback query (inline button tap)
    incoming_message = parse_callback_query_update(update)
    callback_query_id = None
    if incoming_message:
        # Extract callback_query_id from metadata to answer it later
        metadata = incoming_message.metadata or {}
        callback_query_id = metadata.get("callback_query_id")
    else:
        # Fall back to regular message parsing
        incoming_message = parse_telegram_update(update)

    if incoming_message is None:
        return {"ok": True}

    event_id = str(update.get("update_id") or "")
    if not await claim_event("telegram", event_id):
        logger.info("Ignoring duplicate Telegram update_id=%s", event_id)
        return {"ok": True}

    try:
        outgoing_messages = await handle_incoming_message(incoming_message)
    except Exception:
        await release_event("telegram", event_id)
        raise

    # Answer the callback query first (removes loading state on button)
    if callback_query_id:
        await answer_callback_query(callback_query_id)

    for outgoing_message in outgoing_messages:
        try:
            await send_telegram_message(incoming_message.user_id, outgoing_message)
        except Exception:
            logger.exception(
                "process_telegram_update failed to send message to chat_id=%s",
                incoming_message.user_id,
            )

    return {"ok": True}


@router.post("/webhook")
async def telegram_webhook(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    validate_webhook_secret(None, x_telegram_bot_api_secret_token)
    return await process_telegram_update(update)


@router.post("/webhook/{secret}")
async def telegram_webhook_with_path_secret(secret: str, update: dict[str, Any]) -> dict[str, bool]:
    validate_webhook_secret(secret, None)
    return await process_telegram_update(update)


# Telegram sends POST requests to /webhook/telegram — add this route to match.
@webhook_router.post("/webhook/telegram")
async def telegram_webhook_alt(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    validate_webhook_secret(None, x_telegram_bot_api_secret_token)
    return await process_telegram_update(update)

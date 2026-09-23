from channels.telegram import (
    parse_callback_query_update,
    parse_telegram_update,
    process_telegram_update,
)
from services.conversation_service import OutgoingMessage

import asyncio
from unittest.mock import AsyncMock, patch


def test_telegram_parses_text_and_callback_updates():
    message = parse_telegram_update(
        {"message": {"chat": {"id": 10}, "from": {"id": 11}, "text": "hello"}}
    )
    callback = parse_callback_query_update(
        {
            "callback_query": {
                "id": "callback-1",
                "data": "menu_order_status",
                "from": {"id": 11},
                "message": {"chat": {"id": 10}},
            }
        }
    )

    assert message and message.user_id == 10 and message.text == "hello"
    assert callback and callback.user_id == 10 and callback.text == "order_status"
    assert callback.metadata and callback.metadata["is_menu_selection"] is True


def test_telegram_ignores_malformed_location_coordinates():
    message = parse_telegram_update(
        {
            "message": {
                "chat": {"id": 10},
                "location": {"latitude": "not-a-number", "longitude": "78.48"},
            }
        }
    )

    assert message is not None
    assert message.location_lat is None
    assert message.location_lng is None


def test_callback_tap_is_acknowledged_before_handling_and_reply_is_sent():
    """A menu button tap must be answered immediately, then handled and replied to."""
    from channels import telegram

    calls: list[tuple] = []

    async def fake_answer(callback_query_id):
        calls.append(("answer", callback_query_id))

    async def fake_handle(message):
        calls.append(("handle", message.text))
        return [OutgoingMessage(text="pricing menu")]

    async def fake_send(chat_id, outgoing):
        calls.append(("send", chat_id, outgoing.text))

    update = {
        "update_id": 424242,
        "callback_query": {
            "id": "cb-1",
            "data": "menu_pricing",
            "from": {"id": 7},
            "message": {"chat": {"id": 7}},
        },
    }

    # Use a private loop so the rest of the suite (which relies on
    # asyncio.get_event_loop()) keeps its existing policy state intact.
    loop = asyncio.new_event_loop()
    try:
        with (
            patch.object(telegram, "answer_callback_query", fake_answer),
            patch.object(telegram, "handle_incoming_message", fake_handle),
            patch.object(telegram, "send_telegram_message", fake_send),
            patch.object(telegram, "claim_event", AsyncMock(return_value=True)),
        ):
            result = loop.run_until_complete(process_telegram_update(update))
    finally:
        loop.close()

    assert result == {"ok": True}
    # The callback must be acknowledged first so the button never looks dead,
    # then the stripped callback_data ("menu_pricing" -> "pricing") is handled
    # and the reply is delivered to the same chat.
    assert calls == [
        ("answer", "cb-1"),
        ("handle", "pricing"),
        ("send", 7, "pricing menu"),
    ]

import asyncio

import pytest
from fastapi import HTTPException

from channels.telegram import parse_callback_query_update, parse_telegram_update
from channels.twilio import (
    _build_whatsapp_content_types,
    _whatsapp_menu_label,
    _retry_after_seconds,
    _message_body,
    _whatsapp_address,
    build_twilio_response,
    parse_twilio_update,
    validate_twilio_signature,
)
import channels.twilio as twilio
from config import settings
from services.conversation_service import OutgoingMessage


def test_telegram_parses_text_and_callback_updates():
    message = parse_telegram_update({"message": {"chat": {"id": 10}, "from": {"id": 11}, "text": "hello"}})
    callback = parse_callback_query_update({
        "callback_query": {
            "id": "callback-1",
            "data": "menu_3",
            "from": {"id": 11},
            "message": {"chat": {"id": 10}},
        }
    })

    assert message and message.user_id == 10 and message.text == "hello"
    assert callback and callback.user_id == 10 and callback.text == "3"
    assert callback.metadata and callback.metadata["is_menu_selection"] is True


def test_twilio_parses_whatsapp_and_rejects_invalid_sender():
    message = parse_twilio_update({"From": "whatsapp:+919876543210", "Body": "Hi", "NumMedia": "bad"})

    assert message is not None
    assert message.user_id == 919876543210
    assert message.contact_phone == "919876543210"
    assert message.metadata and message.metadata["is_whatsapp"] is True
    assert message.metadata["to_address"] == ""
    assert message.metadata["num_media"] == 0
    assert parse_twilio_update({"From": "not-a-number"}) is None


def test_twilio_uses_button_payload_for_menu_routing():
    message = parse_twilio_update({
        "From": "whatsapp:+919876543210",
        "Body": "Track my current order",
        "ButtonText": "Track my current order",
        "ButtonPayload": "menu_order_status",
    })

    assert message is not None
    assert message.text == "order_status"


def test_twilio_whatsapp_menu_uses_selectable_list_content():
    message = OutgoingMessage(
        text="Choose an option",
        reply_markup={"inline_keyboard": [[
            {"text": "Track my order", "callback_data": "menu_order_status"},
            {"text": "Chat with an agent", "callback_data": "menu_handover"},
        ]]},
    )

    types = _build_whatsapp_content_types(message)
    assert types is not None
    assert types["twilio/quick-reply"]["actions"][0]["id"] == "menu_order_status"


def test_twilio_whatsapp_large_menu_uses_selectable_list_content():
    message = OutgoingMessage(
        text="Choose an option",
        reply_markup={"inline_keyboard": [[
            {"text": "One", "callback_data": "menu_one"},
            {"text": "Two", "callback_data": "menu_two"},
            {"text": "Three", "callback_data": "menu_three"},
            {"text": "Four", "callback_data": "menu_four"},
        ]]},
    )

    types = _build_whatsapp_content_types(message)
    assert types is not None
    assert types["twilio/list-picker"]["button"] == "Explore options"
    assert types["twilio/list-picker"]["items"][0]["id"] == "menu_one"


def test_twilio_whatsapp_menu_label_removes_emoji_before_truncating():
    assert _whatsapp_menu_label("🔍 Track my current order") == "Track my current order"


def test_twilio_whatsapp_plain_subflow_has_consistent_navigation():
    types = _build_whatsapp_content_types(OutgoingMessage("Enter **your address**."))

    assert types is not None
    assert types["twilio/quick-reply"]["body"] == "Enter *your address*."
    assert [action["id"] for action in types["twilio/quick-reply"]["actions"]] == [
        "menu_main_menu", "menu_handover"
    ]
    assert [action["title"] for action in types["twilio/quick-reply"]["actions"]] == [
        "Main menu", "Human support"
    ]


def test_twilio_retry_delay_uses_header_or_backoff():
    retry_response = type("Response", (), {"headers": {"Retry-After": "2"}})()
    no_header_response = type("Response", (), {"headers": {}})()

    assert _retry_after_seconds(retry_response, 0) == 2
    assert _retry_after_seconds(no_header_response, 1) > 1


def test_whatsapp_updates_send_via_messages_api_not_text_twiml(monkeypatch):
    sent = []

    async def fake_handle(_message):
        return [OutgoingMessage("Choose", {"inline_keyboard": [[{"text": "One", "callback_data": "menu_one"}]]})]

    async def fake_send(user_id, message, *, is_whatsapp=None, from_address=None):
        sent.append((user_id, message.text, is_whatsapp, from_address))

    monkeypatch.setattr(twilio, "handle_incoming_message", fake_handle)
    monkeypatch.setattr(twilio, "send_twilio_message", fake_send)
    loop = asyncio.get_event_loop()
    response = loop.run_until_complete(twilio.process_twilio_update({
        "From": "whatsapp:+919876543210",
        "To": "whatsapp:+14155238886",
        "Body": "hi",
    }))

    assert sent == [(919876543210, "Choose", True, "whatsapp:+14155238886")]
    assert response == '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def test_twilio_response_escapes_content_and_includes_text_menu():
    message = OutgoingMessage(
        text="Use <this> & choose",
        reply_markup={"inline_keyboard": [[{"text": "First"}, {"text": "Second"}]]},
    )

    response = build_twilio_response([message])
    assert "Use &lt;this&gt; &amp; choose" in response
    assert "1. First" in response
    assert "2. Second" in response


def test_twilio_whatsapp_sender_and_body_are_valid_text():
    message = OutgoingMessage("Pick one", {"inline_keyboard": [[{"text": "One"}]]})

    assert _whatsapp_address("+14155550123") == "whatsapp:+14155550123"
    assert _whatsapp_address("whatsapp:+14155550123") == "whatsapp:+14155550123"
    assert "1. One" in _message_body(message)


def test_twilio_signature_validation_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", False)
    validate_twilio_signature("https://example.test/twilio/webhook", None, {})


def test_twilio_signature_validation_rejects_missing_signature(monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", "auth-token")

    with pytest.raises(HTTPException, match="Invalid Twilio webhook signature"):
        validate_twilio_signature("https://example.test/twilio/webhook", None, {})

from channels.wati import _is_location_event, _safe_wati_debug_payload, build_wati_payload, parse_wati_update
from services.conversation_service import OutgoingMessage


def test_wati_large_menu_is_a_clean_interactive_list():
    message = OutgoingMessage(
        "Choose **a service**.",
        {"inline_keyboard": [[
            {"text": "🔍 Track my current order", "callback_data": "menu_order_status"},
            {"text": "✏️ Modify my order", "callback_data": "menu_order_changes"},
            {"text": "🚫 Cancel my order", "callback_data": "menu_order_cancel"},
            {"text": "🏠 Update my address", "callback_data": "menu_address_update"},
        ]]},
    )

    payload = build_wati_payload(919876543210, message)
    assert payload["body"] == "Choose *a service*."
    assert "header" not in payload
    assert "footer" not in payload
    assert payload["buttonText"] == "Explore options"
    assert payload["sections"][0]["rows"][0]["title"] == "Track my current order"
    assert "description" not in payload["sections"][0]["rows"][0]


def test_wati_plain_subflow_has_navigation_buttons():
    payload = build_wati_payload(919876543210, OutgoingMessage("Enter your address."))
    buttons = payload["buttons"]

    assert "header" not in payload
    assert "footer" not in payload
    assert [button["text"] for button in buttons] == ["Main menu", "Human support"]


def test_wati_location_step_keeps_share_manual_skip_and_navigation_choices():
    payload = build_wati_payload(
        919876543210,
        OutgoingMessage(
            "Choose location.",
            {
                "keyboard": [
                    [{"text": "Share current location", "request_location": True}],
                    [{"text": "Enter location manually"}],
                    [{"text": "Skip location"}],
                    [{"text": "Main menu"}],
                    [{"text": "Human support"}],
                ]
            },
        ),
    )

    titles = [row["title"] for section in payload["sections"] for row in section["rows"]]
    assert "Share current location" in titles
    assert "Enter location manually" in titles
    assert "Skip location" in titles
    assert titles[-2:] == ["Main menu", "Human support"]


def test_wati_parses_camel_case_interactive_reply():
    message = parse_wati_update({
        "waId": "919876543210",
        "interactive": {"buttonReply": {"id": "menu_address_update", "title": "Update my address"}},
    })

    assert message is not None
    assert message.text == "menu_address_update"


def test_wati_parses_shared_location_from_nested_location_payload():
    message = parse_wati_update({
        "waId": "919876543210",
        "type": "location",
        "location": {"latitude": "17.3850", "longitude": "78.4867"},
    })

    assert message is not None
    assert message.location_lat == 17.3850
    assert message.location_lng == 78.4867


def test_wati_parses_shared_location_from_deep_message_payload():
    message = parse_wati_update({
        "waId": "919876543210",
        "data": {
            "message": {
                "locationMessage": {"lat": 17.3845, "lon": 78.4852},
            }
        },
    })

    assert message is not None
    assert message.location_lat == 17.3845
    assert message.location_lng == 78.4852


def test_wati_parses_shared_location_from_json_encoded_data():
    message = parse_wati_update({
        "waId": "919876543210",
        "type": "location",
        "data": '{"location":{"latitudeDegrees":17.3845,"longitudeDegrees":78.4852}}',
    })

    assert message is not None
    assert message.location_lat == 17.3845
    assert message.location_lng == 78.4852


def test_wati_parses_shared_location_from_google_maps_url():
    message = parse_wati_update({
        "waId": "919876543210",
        "type": "location",
        "data": {"mapUrl": "https://maps.google.com/?q=17.3845%2C78.4852"},
    })

    assert message is not None
    assert message.location_lat == 17.3845
    assert message.location_lng == 78.4852


def test_wati_parses_coordinates_from_google_maps_link_pasted_as_text():
    message = parse_wati_update({
        "waId": "919876543210",
        "type": "text",
        "text": "https://maps.google.com/?q=17.390022,78.366081",
    })

    assert message is not None
    assert message.location_lat == 17.390022
    assert message.location_lng == 78.366081


def test_wati_recognizes_nested_location_event_for_history_fallback():
    assert _is_location_event({"eventType": "messageReceived", "data": {"type": "location"}})
    assert not _is_location_event({"eventType": "messageReceived", "data": {"type": "text"}})


def test_wati_debug_payload_redacts_customer_content_and_credentials():
    logged = _safe_wati_debug_payload({
        "waId": "919876543210",
        "text": "customer message",
        "Authorization": "Bearer secret",
        "location": {"latitude": 17.3845, "longitude": 78.4852},
    })

    assert "919876543210" not in logged
    assert "customer message" not in logged
    assert "Bearer secret" not in logged
    assert "17.3845" in logged


def test_wati_parses_documented_top_level_list_reply():
    message = parse_wati_update({
        "id": "wati-event-1",
        "waId": "919876543210",
        "type": "interactive",
        "listReply": {"id": "menu_order_status", "title": "Track my order"},
    })

    assert message is not None
    assert message.text == "menu_order_status"
    assert message.metadata and message.metadata["is_menu_selection"] is True


def test_wati_parses_documented_top_level_button_reply_label():
    message = parse_wati_update({
        "id": "wati-event-2",
        "waId": "919876543210",
        "type": "interactive",
        "interactiveButtonReply": {"title": "Main menu"},
    })

    assert message is not None
    assert message.text == "Main menu"


def test_wati_strips_list_row_description_when_wati_returns_text_only():
    message = parse_wati_update({
        "id": "wati-event-3",
        "waId": "919876543210",
        "type": "interactive",
        "text": "View price catalog\nTap to continue",
    })

    assert message is not None
    assert message.text == "View price catalog"


def test_wati_parses_reply_object_directly_from_data():
    message = parse_wati_update({
        "id": "wati-event-4",
        "waId": "919876543210",
        "type": "interactive",
        "data": {"title": "View price catalog", "description": "Tap to continue"},
    })

    assert message is not None
    assert message.text == "View price catalog"


def test_wati_uses_visible_label_over_non_menu_internal_row_id():
    message = parse_wati_update({
        "id": "wati-event-5",
        "waId": "919876543210",
        "listReply": {"id": "row-481", "title": "View price catalog"},
    })

    assert message is not None
    assert message.text == "View price catalog"

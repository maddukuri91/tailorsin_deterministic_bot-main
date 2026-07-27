from channels.wati import build_wati_payload, parse_wati_update
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
    interactive = payload["interactive"]
    assert interactive["type"] == "list"
    assert interactive["body"]["text"] == "Choose *a service*."
    assert interactive["action"]["button"] == "Explore options"
    assert interactive["action"]["sections"][0]["rows"][0]["title"] == "Track my current order"


def test_wati_plain_subflow_has_navigation_buttons():
    payload = build_wati_payload(919876543210, OutgoingMessage("Enter your address."))
    buttons = payload["interactive"]["action"]["buttons"]

    assert payload["interactive"]["type"] == "button"
    assert [button["reply"]["id"] for button in buttons] == ["menu_main_menu", "menu_handover"]


def test_wati_parses_camel_case_interactive_reply():
    message = parse_wati_update({
        "waId": "919876543210",
        "interactive": {"buttonReply": {"id": "menu_address_update", "title": "Update my address"}},
    })

    assert message is not None
    assert message.text == "menu_address_update"

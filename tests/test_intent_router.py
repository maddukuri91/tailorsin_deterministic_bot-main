from conversation.intent_router import _strip_emoji, get_intent
from conversation.menu import get_menu_options


def test_direct_menu_number_match():
    client_type = "client"
    menu = get_menu_options(client_type)
    
    # Test first menu item
    assert get_intent(client_type, "1") == menu[0]["intent"]
    
    # Find address_update and test it exists
    address_intent = next((item["intent"] for item in menu if item["intent"] == "address_update"), None)
    assert address_intent is not None, "address_update should be in client menu"


def test_menu_label_match_case_insensitive():
    client_type = "active_client"
    label = get_menu_options(client_type)[0]["label"]
    assert get_intent(client_type, label) == "order_status"
    assert get_intent(client_type, f"1. {label}") == "order_status"


def test_footer_intent():
    # Test main menu shortcut
    assert get_intent("client", "0") == "main_menu"
    
    # Test handover by text and find its actual index
    assert get_intent("client", "handover") == "handover"
    menu = get_menu_options("client")
    handover_index = next((i+1 for i, item in enumerate(menu) if item["intent"] == "handover"), None)
    assert handover_index is not None, "handover should be in client menu"
    assert get_intent("client", str(handover_index)) == "handover"


def test_visit_history_special_input():
    assert get_intent("client", "visit history") == "visit_history"
    assert get_intent("client", "my appointments") == "visit_history"


def test_unknown_returns_none():
    assert get_intent("client", "banana") is None


def test_all_menu_options_resolve_to_known_intents():
    for client_type in ["active_client", "client", "new_user"]:
        menu = get_menu_options(client_type)
        # Test that each numeric index maps to the correct intent
        for index, item in enumerate(menu, start=1):
            assert get_intent(client_type, str(index)) == item["intent"]
        # Test that each intent name can be resolved
        for item in menu:
            assert get_intent(client_type, item["intent"]) == item["intent"]


def test_all_menu_options_accept_wati_shortened_labels():
    for client_type in ["active_client", "client", "new_user"]:
        for item in get_menu_options(client_type):
            plain_label = _strip_emoji(item["label"])
            assert get_intent(client_type, plain_label[:24]) == item["intent"]
            assert get_intent(client_type, plain_label[:20]) == item["intent"]


def test_wati_menu_labels_fit_with_icons_and_resolve():
    for client_type in ["active_client", "client", "new_user"]:
        for item in get_menu_options(client_type):
            # WATI allows a 20-character button title. The icon and separator
            # consume three characters, so the new labels never truncate.
            assert len(f"• {item['label']}") <= 20
            assert get_intent(client_type, item["label"]) == item["intent"]


def test_wati_quoted_navigation_reply_resolves_from_last_line():
    quoted_reply = "Please share a short note for fabric delivery.\nGo back to main menu"
    assert get_intent("client", quoted_reply) == "main_menu"
    assert get_intent("client", "Chat with a human ag") == "handover"

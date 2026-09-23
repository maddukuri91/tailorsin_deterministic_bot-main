from conversation.intent_router import _strip_emoji, get_intent
from conversation.menu import (
    MAIN_MENU_ID,
    SEGMENT_MENU_OPTIONS,
    get_follow_up_menu,
    get_menu_options,
)


def _all_menus():
    """Yield (segment, menu_id, options) for every configured menu."""
    for segment, menus in SEGMENT_MENU_OPTIONS.items():
        for menu_id, options in menus.items():
            yield segment, menu_id, options


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
    assert label == "Manage Orders"
    assert get_intent(client_type, label) == "manage_orders"
    assert get_intent(client_type, f"1. {label}") == "manage_orders"


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
    for segment, menu_id, options in _all_menus():
        # Each numeric index maps to the option visible in that menu.
        for index, item in enumerate(options, start=1):
            assert get_intent(segment, str(index), menu_id) == item["intent"]
        # Each intent name can be resolved from any menu.
        for item in options:
            assert get_intent(segment, item["intent"], menu_id) == item["intent"]


def test_nested_menu_numbering_never_leaks_into_the_main_menu():
    """A number means what the customer can see, not what the main menu holds."""
    # Main menu option 1 for an active client opens Manage Orders...
    assert get_intent("active_client", "1", MAIN_MENU_ID) == "manage_orders"
    # ...while option 1 of the nested menu it opens means Track Order.
    assert get_intent("active_client", "1", "active_client_orders") == "order_status"

    # A client's option 1 opens the New Order submenu, whose option 2 is Drop Fabric.
    assert get_intent("client", "1", MAIN_MENU_ID) == "new_order"
    assert get_intent("client", "2", "client_orders") == "fabric_delivery"


def test_all_menu_options_accept_wati_shortened_labels():
    for segment, menu_id, options in _all_menus():
        for item in options:
            plain_label = _strip_emoji(item["label"])
            assert get_intent(segment, plain_label[:24], menu_id) == item["intent"]
            assert get_intent(segment, plain_label[:20], menu_id) == item["intent"]


def test_wati_menu_labels_fit_with_icons_and_resolve():
    for segment, menu_id, options in _all_menus():
        for item in options:
            # WATI allows a 20-character button title. The icon and separator
            # consume three characters, so the new labels never truncate.
            assert len(f"• {item['label']}") <= 20
            assert get_intent(segment, item["label"], menu_id) == item["intent"]


def test_follow_up_menu_opens_only_from_outside_itself():
    # Reaching the action from the main menu reveals its nested menu.
    assert get_follow_up_menu("client", "new_order", MAIN_MENU_ID) == "client_orders"
    assert get_follow_up_menu("new_user", "about", MAIN_MENU_ID) == "new_user_about"
    assert get_follow_up_menu("new_user", "pricing", MAIN_MENU_ID) == "new_user_pricing"
    assert (
        get_follow_up_menu("active_client", "manage_orders", MAIN_MENU_ID)
        == "active_client_orders"
    )

    # "New Order" also appears inside the submenu it opens. Choosing it there
    # must run the order flow instead of reopening the same menu forever.
    assert get_follow_up_menu("client", "new_order", "client_orders") is None

    # Actions without a nested menu never return one.
    assert get_follow_up_menu("client", "handover", MAIN_MENU_ID) is None
    assert get_follow_up_menu("new_user", "register", MAIN_MENU_ID) is None


def test_follow_up_menus_are_scoped_to_their_own_segment():
    # "pricing" only nests for a new user; returning customers see pricing and
    # stay on their main menu.
    assert get_follow_up_menu("client", "pricing", MAIN_MENU_ID) is None
    assert get_follow_up_menu("active_client", "pricing", MAIN_MENU_ID) is None


def test_unknown_menu_id_falls_back_to_the_segment_main_menu():
    assert get_menu_options("client", "does-not-exist") == get_menu_options("client")
    # A nested menu from another segment is never shown.
    assert get_menu_options("client", "active_client_orders") == get_menu_options("client")


def test_wati_quoted_navigation_reply_resolves_from_last_line():
    quoted_reply = "Please share a short note for fabric delivery.\nGo back to main menu"
    assert get_intent("client", quoted_reply) == "main_menu"
    assert get_intent("client", "Chat with a human ag") == "handover"

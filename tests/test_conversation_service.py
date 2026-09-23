import asyncio

import pytest

import services.conversation_service as svc
from conversation.menu import (
    MAIN_MENU_ID,
    _icon,
    _item_icon,
    get_menu_inline_keyboard,
    get_menu_options,
)
from conversation.session import get_session, reset_session, save_session
from services.conversation_service import IncomingMessage

CHAT_ID = 70001


def make_message(text="", **kwargs):
    return IncomingMessage(user_id=CHAT_ID, text=text, **kwargs)


@pytest.fixture(autouse=True)
def fresh_session_and_stubs(monkeypatch):
    # In-memory session store (no Redis in tests)
    from conversation import session as session_mod

    if session_mod._get_redis() is None:
        session_mod._SESSIONS.clear()
    asyncio.get_event_loop().run_until_complete(reset_session(CHAT_ID))

    # Stub the CRM client lookup so no real HTTP is made.
    class _Profile:
        client_type = "client"
        customer_salutation = "Test User"

    async def fake_lookup(mobile):
        return _Profile()

    monkeypatch.setattr(svc, "lookup_customer_profile", fake_lookup)

    # Stub CRM write/reads used in menu flows.
    class _Addr:
        address_id = 11
        address1 = "1 Main St"
        city = "Hyderabad"
        pincode = "500001"
        is_main = True

    class _AddrResult:
        success = True
        message = "ok"
        customer_name = "Test User"
        addresses = [_Addr()]

    async def fake_fetch_addresses(mobile):
        return _AddrResult()

    monkeypatch.setattr(svc, "fetch_client_addresses", fake_fetch_addresses)
    yield


def run(msg):
    return asyncio.get_event_loop().run_until_complete(svc.handle_incoming_message(msg))


def set_client_type(monkeypatch, client_type, salutation="Test User"):
    """Start the conversation as the given customer segment."""

    class _Profile:
        pass

    profile = _Profile()
    profile.client_type = client_type
    profile.customer_salutation = salutation

    async def fake_lookup(mobile):
        return profile

    monkeypatch.setattr(svc, "lookup_customer_profile", fake_lookup)


def menu_labels(outgoing):
    """Return the visible labels of a menu message's inline keyboard."""
    return [
        button["text"]
        for row in outgoing.reply_markup["inline_keyboard"]
        for button in row
    ]


def expected_labels(client_type, menu_id):
    """Return the labels the menu definition says should be visible."""
    labels = [
        f"{_item_icon(option)} {option['label']}"
        for option in get_menu_options(client_type, menu_id)
    ]
    # Nested menus carry a back button that is not part of the menu data.
    if menu_id and menu_id != MAIN_MENU_ID:
        labels.append(f"{_icon('main_menu')} ← Back to main menu")
    return labels


def current_menu_id():
    session = asyncio.get_event_loop().run_until_complete(get_session(CHAT_ID))
    return session.current_menu


def test_start_command_returns_menu():
    out = run(make_message("/start"))
    assert out, "expected at least one outgoing message"
    assert "Welcome" in out[0].text or "menu" in out[0].text.lower()


def test_about_response_sends_the_complete_overview_in_one_provider_safe_message():
    responses = asyncio.get_event_loop().run_until_complete(
        svc.build_intent_response("about", "new_user")
    )

    assert responses is not None
    assert len(responses) == 1
    assert len(responses[0].text) <= 1024
    assert responses[0].text == svc.TAILORSIN_OVERVIEW_MESSAGE
    assert responses[0].reply_markup is not None


def test_selection_builders_always_include_standard_navigation():
    builders = [
        svc.build_selection_reply_markup(["One", "Two"]),
        svc.build_pickup_date_reply_markup(),
        svc.build_pickup_time_reply_markup(),
        svc.build_visit_slot_reply_markup(["10:00 AM", "2:00 PM"]),
        svc.build_number_selection_reply_markup(2, include_add_address=True),
        svc.build_location_choice_reply_markup(),
        svc.build_location_link_fallback_reply_markup(),
    ]

    for markup in builders:
        labels = [button["text"] for row in markup["keyboard"] for button in row]
        assert labels[-2:] == ["Main menu", "Human support"]


def test_unknown_text_prompts_menu():
    run(make_message("/start"))
    out = run(make_message("random gibberish"))
    texts = " ".join(o.text for o in out)
    assert "select one of the menu options below" in texts.lower()


def test_menu_zero_returns_main_menu():
    run(make_message("/start"))
    out = run(make_message("0"))
    # Should not crash and should produce a menu response
    assert out


def test_handover_intent_works():
    # Stub human handover to avoid HTTP
    async def fake_handover(mobile):
        class R:
            success = True
            message = "Agent notified."
        return R()

    svc.request_human_handover = fake_handover
    run(make_message("/start", contact_phone="9988776655"))
    out = run(make_message("9"))
    assert any("Agent notified" in o.text for o in out)


def test_wati_menu_button_routes_to_address_update(monkeypatch):
    """WhatsApp's menu_address_update payload must not reopen the main menu."""
    async def fake_addresses(mobile):
        return _AddrResult()

    async def fake_address_message(mobile):
        return "Saved addresses: reply 1 to add or manage an address."

    class _AddrResult:
        success = True
        addresses = []

    monkeypatch.setattr(svc, "fetch_client_addresses", fake_addresses)
    monkeypatch.setattr(svc, "build_address_list_message", fake_address_message)
    asyncio.get_event_loop().run_until_complete(reset_session(CHAT_ID))
    run(make_message("hi", is_start_command=True, contact_phone="9988776655", metadata={"platform": "wati"}))
    out = run(make_message("address_update", metadata={"platform": "wati", "is_menu_selection": True}))

    assert "Saved addresses" in out[0].text


def test_client_option_1_opens_nested_order_menu_then_runs_the_new_order_flow():
    # A client's option 1 is a gateway to the nested order menu, not the flow.
    run(make_message("/start", contact_phone="9988776655"))

    submenu = run(make_message("1"))

    assert len(submenu) == 1
    assert menu_labels(submenu[0]) == expected_labels("client", "client_orders")
    assert current_menu_id() == "client_orders"

    # Choosing "New Order" inside the nested menu runs the pickup flow, which
    # lists the addresses returned by the stubbed CRM.
    out = run(make_message("1"))
    texts = " ".join(o.text for o in out)
    assert "Saved addresses" in texts or "pickup" in texts.lower()


def test_tapping_new_order_twice_runs_the_flow_instead_of_reopening_the_menu():
    """The nested menu must not trap the customer in a loop.

    "New Order" appears both on the client main menu and inside the nested menu
    it opens. The second tap has to start the flow rather than reopen the menu.
    """
    run(make_message("/start", contact_phone="9988776655"))

    # First tap, from the main menu, opens the nested order menu.
    first = run(make_message(
        "new_order", metadata={"platform": "telegram", "is_menu_selection": True}
    ))
    assert menu_labels(first[0]) == expected_labels("client", "client_orders")
    assert current_menu_id() == "client_orders"

    # The same button inside that menu must start the order flow.
    second = run(make_message(
        "new_order", metadata={"platform": "telegram", "is_menu_selection": True}
    ))
    texts = " ".join(o.text for o in second)
    assert "Saved addresses" in texts or "pickup" in texts.lower()
    assert current_menu_id() == "main"


def test_client_nested_drop_fabric_runs_the_fabric_delivery_flow():
    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("1"))  # New Order -> nested order menu

    out = run(make_message("2"))  # Drop Fabric

    assert "fabric delivery" in out[0].text.lower()
    session = asyncio.get_event_loop().run_until_complete(get_session(CHAT_ID))
    assert session.awaiting_fabric_delivery_notes is True


def test_new_user_main_menu_is_how_it_works_catalogue_and_signup(monkeypatch):
    set_client_type(monkeypatch, "new_user")

    out = run(make_message("/start", contact_phone="9988776655"))

    assert menu_labels(out[0]) == expected_labels("new_user", "main")


def test_new_user_option_1_shows_overview_with_nested_catalogue_and_signup(monkeypatch):
    set_client_type(monkeypatch, "new_user")
    run(make_message("/start", contact_phone="9988776655"))

    out = run(make_message("1"))

    assert out[0].text == svc.TAILORSIN_OVERVIEW_MESSAGE
    assert menu_labels(out[0]) == expected_labels("new_user", "new_user_about")
    assert current_menu_id() == "new_user_about"


def test_new_user_option_2_shows_pricing_with_nested_estimate_and_visit(monkeypatch):
    set_client_type(monkeypatch, "new_user")
    run(make_message("/start", contact_phone="9988776655"))

    out = run(make_message("2"))

    assert "Price catalogue" in out[0].text
    assert menu_labels(out[0]) == expected_labels("new_user", "new_user_pricing")
    assert current_menu_id() == "new_user_pricing"


def test_new_user_option_3_starts_registration(monkeypatch):
    set_client_type(monkeypatch, "new_user")
    run(make_message("/start", contact_phone="9988776655"))

    out = run(make_message("3"))

    assert "full name" in out[0].text.lower()
    session = asyncio.get_event_loop().run_until_complete(get_session(CHAT_ID))
    assert session.awaiting_registration_name is True


def test_nested_number_is_read_from_the_menu_the_customer_sees(monkeypatch):
    """Inside the "How this Works" menu, "1" is Price Catalogue."""
    set_client_type(monkeypatch, "new_user")
    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("1"))  # How this Works -> nested menu

    out = run(make_message("1"))  # first option of the nested menu

    assert "Price catalogue" in out[0].text
    assert menu_labels(out[0]) == expected_labels("new_user", "new_user_pricing")


def test_active_client_option_1_opens_the_manage_orders_menu(monkeypatch):
    set_client_type(monkeypatch, "active_client")
    run(make_message("/start", contact_phone="9988776655"))

    out = run(make_message("1"))

    assert menu_labels(out[0]) == expected_labels("active_client", "active_client_orders")
    assert current_menu_id() == "active_client_orders"


def test_active_client_nested_track_order_runs_the_order_status_flow(monkeypatch):
    set_client_type(monkeypatch, "active_client")

    async def fake_order_status(mobile):
        return "Order #42 is being stitched."

    monkeypatch.setattr(svc, "build_order_status_response", fake_order_status)
    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("1"))  # Manage Orders -> nested menu

    out = run(make_message("1"))  # Track Order

    assert "Order #42" in out[0].text
    # The flow ends on the main menu, so the nested context is released.
    assert current_menu_id() == "main"


def test_active_client_nested_report_issue_is_the_fourth_option(monkeypatch):
    """Numbering follows Manage Orders, not the main menu it was opened from."""
    set_client_type(monkeypatch, "active_client")

    class _Delivered:
        success = False
        message = "No delivered orders found."
        orders = []

    async def fake_delivered(mobile):
        return _Delivered()

    monkeypatch.setattr(svc, "fetch_delivered_orders", fake_delivered)
    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("1"))  # Manage Orders -> nested menu

    out = run(make_message("4"))  # Report Issue, the 4th nested option

    assert "No delivered orders found." in out[0].text


def test_main_menu_clears_the_nested_menu_context(monkeypatch):
    set_client_type(monkeypatch, "active_client")
    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("1"))
    assert current_menu_id() == "active_client_orders"

    out = run(make_message("0"))

    assert current_menu_id() == "main"
    assert menu_labels(out[0]) == expected_labels("active_client", "main")


def test_pickup_address_add_enters_single_address_capture_step():
    async def prepare_pickup_address_step():
        session = await get_session(CHAT_ID)
        session.awaiting_pickup_address = True
        session.pending_pickup_date = "2026-08-01"
        session.pending_pickup_time = 1
        await save_session(session)

    asyncio.get_event_loop().run_until_complete(prepare_pickup_address_step())
    out = run(make_message("add"))

    assert len(out) == 1
    assert "address line" in out[0].text.lower()

    session = asyncio.get_event_loop().run_until_complete(get_session(CHAT_ID))
    assert session.awaiting_pickup_address is False
    assert session.awaiting_address_add_line is True
    assert session.address_needed_for_pickup is True


def test_wati_numbered_address_selection_advances_to_pickup_date():
    async def prepare_pickup_address_step():
        session = await get_session(CHAT_ID)
        session.awaiting_pickup_address = True
        session.pending_address_ordered_ids = [11]
        await save_session(session)

    asyncio.get_event_loop().run_until_complete(prepare_pickup_address_step())
    out = run(make_message("1", metadata={"platform": "wati", "is_menu_selection": True}))

    assert len(out) == 1
    assert "select a pickup date" in out[0].text.lower()

    session = asyncio.get_event_loop().run_until_complete(get_session(CHAT_ID))
    assert session.awaiting_pickup_address is False
    assert session.awaiting_pickup_date is True
    assert session.pending_pickup_address_id == 11


def test_invalid_address_delete_choice_repeats_address_selection_controls():
    async def prepare_delete_step():
        session = await get_session(CHAT_ID)
        session.awaiting_address_delete_id = True
        session.pending_address_list_ids = [11, 12]
        await save_session(session)

    asyncio.get_event_loop().run_until_complete(prepare_delete_step())
    out = run(make_message("3"))

    labels = [button["text"] for row in out[0].reply_markup["keyboard"] for button in row]
    assert labels == ["1", "2", "Main menu", "Human support"]


def test_invalid_order_choices_repeat_the_relevant_order_controls():
    async def prepare_order_steps():
        session = await get_session(CHAT_ID)
        session.awaiting_order_change_select = True
        session.pending_change_order_ids = [101, 102]
        await save_session(session)

    asyncio.get_event_loop().run_until_complete(prepare_order_steps())
    out = run(make_message("3"))

    labels = [button["text"] for row in out[0].reply_markup["keyboard"] for button in row]
    assert labels == ["1", "2", "Main menu", "Human support"]


def test_pickup_date_parsing():
    assert svc.parse_pickup_date_option("1") is not None
    assert svc.parse_pickup_date_option("not-a-date") is None


def test_pickup_time_parsing():
    assert svc.parse_pickup_time_option("1") == 1
    assert svc.parse_pickup_time_option("morning") == 1
    assert svc.parse_pickup_time_option("1. Morning (9 AM - 2") == 1
    assert svc.parse_pickup_time_option("Choose pickup time slot:\n2. Afternoon (2 PM -") == 2
    assert svc.parse_pickup_time_option("bogus") is None


def test_visit_slot_parsing():
    assert svc.parse_visit_slot_option("1", ["9 AM", "2 PM"]) == "9 AM"
    assert svc.parse_visit_slot_option("9 AM", ["9 AM", "2 PM"]) == "9 AM"
    assert svc.parse_visit_slot_option("zzz", ["9 AM"]) is None


def test_bulk_order_enquiry_intent_runs_the_flow_instead_of_the_fallback():
    """Regression: the menu emits bulk_order_enquiry, the handler checked bulk_orders."""
    run(make_message("/start", contact_phone="9988776655"))
    out = run(make_message("bulk_order_enquiry", metadata={"is_menu_selection": True}))

    assert out
    assert not any("Selected option" in o.text for o in out)
    assert "preferred contact" in out[0].text.lower()


def test_bulk_order_enquiry_yes_uses_the_session_number_as_secondary(monkeypatch):
    captured = {}

    async def fake_bulk_order(client_name, primary_no, secondary_no=None):
        captured["bulk"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = "Your bulk order enquiry has been submitted."

        return R()

    async def fake_handover(mobile):
        captured["handover"] = mobile

        class R:
            success = True
            message = "Agent notified."

        return R()

    monkeypatch.setattr(svc, "create_bulk_order_enquiry", fake_bulk_order)
    monkeypatch.setattr(svc, "request_human_handover", fake_handover)

    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("bulk_order_enquiry"))
    out = run(make_message("Yes"))

    assert captured["bulk"] == ("Test User", "9988776655", "9988776655")
    # The handover still runs so an agent is assigned in the CRM...
    assert captured["handover"] == "9988776655"
    # ...but only the enquiry confirmation is sent: the handover
    # acknowledgement repeats it. The client segment gets its own menu back.
    assert len(out) == 1
    assert "submitted" in out[0].text
    assert "Agent notified" not in out[0].text
    assert menu_labels(out[0]) == expected_labels("client", "main")


def test_fabric_estimate_collects_name_and_secondary_for_new_users(monkeypatch):
    set_client_type(monkeypatch, "new_user", salutation=None)
    captured = {}

    async def fake_register(client_name, primary_no, secondary_no=None):
        captured["registration"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = "You are registered successfully."

        return R()

    async def fake_fabric(client_name, primary_no, secondary_no=None):
        captured["fabric"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = "Fabric estimation request has been submitted."

        return R()

    async def fake_handover(mobile):
        captured["handover"] = mobile

        class R:
            success = True
            message = "Agent notified."

        return R()

    monkeypatch.setattr(svc, "register_new_client", fake_register)
    monkeypatch.setattr(svc, "custom_fabric_estimation", fake_fabric)
    monkeypatch.setattr(svc, "request_human_handover", fake_handover)

    run(make_message("/start", contact_phone="9988776655"))

    out = run(make_message("fabric_estimate"))
    assert "full name" in out[0].text.lower()

    out = run(make_message("John Doe"))
    assert "preferred contact" in out[0].text.lower()

    out = run(make_message("No"))
    assert "mobile number" in out[0].text.lower()

    out = run(make_message("9876543210"))

    # Registration still runs so the CRM can match the enquiry and handover...
    assert captured["registration"] == ("John Doe", "9988776655", "9876543210")
    assert captured["fabric"] == ("John Doe", "9988776655", "9876543210")
    assert captured["handover"] == "9988776655"
    # ...but a single confirmation is sent, and the new user keeps the new
    # user menu instead of being promoted to the client menu.
    assert len(out) == 1
    assert "submitted" in out[0].text
    assert "registered successfully" not in out[0].text
    assert "Agent notified" not in out[0].text
    assert menu_labels(out[0]) == expected_labels("new_user", "main")


def test_fabric_estimate_rejects_an_invalid_secondary_and_hides_handover_errors(monkeypatch):
    captured = {}

    async def fake_fabric(client_name, primary_no, secondary_no=None):
        captured["fabric"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = "Fabric estimation request has been submitted."

        return R()

    async def fake_handover(mobile):
        class R:
            success = False
            message = "client not found"

        return R()

    monkeypatch.setattr(svc, "custom_fabric_estimation", fake_fabric)
    monkeypatch.setattr(svc, "request_human_handover", fake_handover)

    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("fabric_estimate"))
    out = run(make_message("No"))
    assert "mobile number" in out[0].text.lower()

    out = run(make_message("12345"))
    assert "valid" in out[0].text.lower()
    assert "fabric" not in captured

    out = run(make_message("9876543210"))
    assert captured["fabric"] == ("Test User", "9988776655", "9876543210")
    # A failed handover must not bury the enquiry confirmation.
    texts = " ".join(o.text for o in out)
    assert "submitted" in texts
    assert "client not found" not in texts
    # The client segment gets the client menu back.
    assert len(out) == 1
    assert menu_labels(out[0]) == expected_labels("client", "main")


def test_nested_menu_shows_a_back_button_that_returns_to_the_main_menu(monkeypatch):
    from conversation.intent_router import get_intent

    set_client_type(monkeypatch, "new_user")
    back_label = f"{_icon('main_menu')} ← Back to main menu"

    # The back label resolves to the main menu intent on every channel: as a
    # reply-keyboard tap (WATI/Twilio) and as callback data (Telegram).
    assert get_intent("new_user", back_label) == "main_menu"
    assert get_intent("client", "main_menu") == "main_menu"

    run(make_message("/start", contact_phone="9988776655"))
    out = run(make_message("2"))  # Price Catalogue -> nested menu

    assert back_label in menu_labels(out[0])
    assert current_menu_id() == "new_user_pricing"

    out = run(make_message(back_label))
    assert menu_labels(out[0]) == expected_labels("new_user", "main")
    assert current_menu_id() == "main"

    # A Telegram inline tap arrives as the stripped callback payload.
    run(make_message("2"))
    out = run(make_message("main_menu", metadata={"platform": "telegram"}))
    assert menu_labels(out[0]) == expected_labels("new_user", "main")
    assert current_menu_id() == "main"


def test_new_user_enquiry_sends_one_confirmation_and_returns_the_new_user_menu(monkeypatch):
    """Registration and handover acknowledgements must not stack up, and the
    new user must get the new user menu back rather than the client menu."""
    set_client_type(monkeypatch, "new_user", salutation=None)
    captured = {}

    async def fake_register(client_name, primary_no, secondary_no=None):
        captured["registration"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = "client registered successfully"

        return R()

    async def fake_fabric(client_name, primary_no, secondary_no=None):
        captured["fabric"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = (
                "thanks! your custom fabric estimation request has been received, "
                "our team will get in touch with you shortly"
            )

        return R()

    async def fake_handover(mobile):
        captured["handover"] = mobile

        class R:
            success = True
            message = "handed over to human agent"

        return R()

    monkeypatch.setattr(svc, "register_new_client", fake_register)
    monkeypatch.setattr(svc, "custom_fabric_estimation", fake_fabric)
    monkeypatch.setattr(svc, "request_human_handover", fake_handover)

    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("fabric_estimate"))
    run(make_message("John Doe"))
    run(make_message("No"))
    out = run(make_message("9876543210"))

    assert captured["registration"] == ("John Doe", "9988776655", "9876543210")
    assert captured["fabric"] == ("John Doe", "9988776655", "9876543210")
    assert captured["handover"] == "9988776655"

    assert len(out) == 1
    assert "received" in out[0].text
    assert "registered successfully" not in out[0].text
    assert "handed over" not in out[0].text
    assert menu_labels(out[0]) == expected_labels("new_user", "main")
    assert current_menu_id() == "main"


def test_active_client_enquiry_confirmation_returns_the_active_client_menu(monkeypatch):
    set_client_type(monkeypatch, "active_client")
    captured = {}

    async def fake_fabric(client_name, primary_no, secondary_no=None):
        class R:
            success = True
            message = "Fabric estimation request has been submitted."

        return R()

    async def fake_handover(mobile):
        captured["handover"] = mobile

        class R:
            success = True
            message = "handed over to human agent"

        return R()

    monkeypatch.setattr(svc, "custom_fabric_estimation", fake_fabric)
    monkeypatch.setattr(svc, "request_human_handover", fake_handover)

    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("fabric_estimate"))  # salutation known -> contact prompt
    out = run(make_message("Yes"))

    assert captured["handover"] == "9988776655"
    assert len(out) == 1
    assert "submitted" in out[0].text
    assert "handed over" not in out[0].text
    assert menu_labels(out[0]) == expected_labels("active_client", "main")


def test_signup_asks_for_secondary_number_instead_of_email(monkeypatch):
    set_client_type(monkeypatch, "new_user", salutation=None)
    captured = {}

    async def fake_register(client_name, primary_no, secondary_no=None):
        captured["registration"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = "You are registered successfully."

        return R()

    monkeypatch.setattr(svc, "register_new_client", fake_register)

    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("3"))

    out = run(make_message("Jane Doe"))
    texts = " ".join(o.text for o in out).lower()
    assert "preferred contact" in texts
    assert "email" not in texts

    out = run(make_message("Yes"))
    assert captured["registration"] == ("Jane Doe", "9988776655", "9988776655")
    texts = [o.text for o in out]
    # Exactly one, non-repetitive signup confirmation — no duplicated
    # "Registration complete" sentence stacked on the CRM message.
    confirmations = [t for t in texts if "registered successfully" in t.lower()]
    assert len(confirmations) == 1
    assert "Registration complete" not in " ".join(texts)


def test_signup_no_path_prompts_for_the_secondary_number(monkeypatch):
    set_client_type(monkeypatch, "new_user", salutation=None)
    captured = {}

    async def fake_register(client_name, primary_no, secondary_no=None):
        captured["registration"] = (client_name, primary_no, secondary_no)

        class R:
            success = True
            message = "You are registered successfully."

        return R()

    monkeypatch.setattr(svc, "register_new_client", fake_register)

    run(make_message("/start", contact_phone="9988776655"))
    run(make_message("3"))
    run(make_message("Jane Doe"))

    out = run(make_message("No"))
    assert "mobile number" in out[0].text.lower()

    out = run(make_message("1234"))
    assert "valid" in out[0].text.lower()
    assert "registration" not in captured

    run(make_message("9876543210"))
    assert captured["registration"] == ("Jane Doe", "9988776655", "9876543210")


def test_schedule_pickup_option_uses_rider_icon_while_new_order_keeps_plus():
    """Only "Schedule Pick-up" overrides its shared new_order intent icon."""
    schedule_texts = [
        button["text"]
        for row in get_menu_inline_keyboard("client", "client_orders")
        for button in row
    ]
    assert "🛵 Schedule Pick-up" in schedule_texts

    main_texts = [
        button["text"]
        for row in get_menu_inline_keyboard("client")
        for button in row
    ]
    assert "➕ New Order" in main_texts

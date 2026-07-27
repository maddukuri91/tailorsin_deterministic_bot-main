import asyncio

import pytest

import services.conversation_service as svc
from conversation.session import reset_session
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


def test_start_command_returns_menu():
    out = run(make_message("/start"))
    assert out, "expected at least one outgoing message"
    assert "Welcome" in out[0].text or "menu" in out[0].text.lower()


def test_unknown_text_prompts_menu():
    run(make_message("/start"))
    out = run(make_message("random gibberish"))
    texts = " ".join(o.text for o in out)
    assert "choose one of the listed menu options" in texts


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


def test_menu_button_payload_routes_to_address_update(monkeypatch):
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
    run(make_message("hi", is_start_command=True, contact_phone="9988776655", metadata={"platform": "twilio"}))
    out = run(make_message("address_update", contact_phone="9988776655", metadata={"platform": "twilio"}))

    assert "Saved addresses" in out[0].text


def test_new_order_flow_with_address_lists_then_pickup_date():
    # "client" type maps option 1 -> new_order (fresh pickup). Provide a mobile.
    run(make_message("/start", contact_phone="9988776655"))
    # Choose option 1 (new order) - contact_phone is not sent on menu selections
    out = run(make_message("1"))
    texts = " ".join(o.text for o in out)
    # Because a stubbed address exists, it should ask to pick an address (numbered list).
    assert "Saved addresses" in texts or "pickup" in texts.lower()


def test_pickup_date_parsing():
    assert svc.parse_pickup_date_option("1") is not None
    assert svc.parse_pickup_date_option("not-a-date") is None


def test_pickup_time_parsing():
    assert svc.parse_pickup_time_option("1") == 1
    assert svc.parse_pickup_time_option("morning") == 1
    assert svc.parse_pickup_time_option("bogus") is None


def test_visit_slot_parsing():
    assert svc.parse_visit_slot_option("1", ["9 AM", "2 PM"]) == "9 AM"
    assert svc.parse_visit_slot_option("9 AM", ["9 AM", "2 PM"]) == "9 AM"
    assert svc.parse_visit_slot_option("zzz", ["9 AM"]) is None

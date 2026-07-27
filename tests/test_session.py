import asyncio

import pytest

from conversation.session import SessionState, get_session, reset_session, save_session


@pytest.fixture(autouse=True)
def clear_module_cache():
    # Ensure a clean in-memory store between tests when Redis is absent.
    from conversation import session as session_mod

    if session_mod._get_redis() is None:
        session_mod._SESSIONS.clear()
    else:
        # Best-effort flush if a redis client happens to be configured.
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(session_mod._get_redis().flushdb())
        except Exception:
            pass
    yield


def test_reset_creates_fresh_session():
    async def run():
        session = await reset_session(123)
        assert session.chat_id == 123
        assert session.client_type is None
        # retrieving same chat returns the persisted session
        again = await get_session(123)
        assert again.chat_id == 123

    asyncio.get_event_loop().run_until_complete(run())


def test_save_then_get_roundtrip_preserves_flags():
    async def run():
        session = await get_session(555)
        session.mobile = "9988776655"
        session.client_type = "active_client"
        session.awaiting_pickup_date = True
        session.pending_pickup_date = "2026-10-07"
        await save_session(session)

        loaded = await get_session(555)
        assert loaded.mobile == "9988776655"
        assert loaded.client_type == "active_client"
        assert loaded.awaiting_pickup_date is True
        assert loaded.pending_pickup_date == "2026-10-07"

    asyncio.get_event_loop().run_until_complete(run())


def test_from_dict_ignores_unknown_keys():
    state = SessionState.from_dict({"chat_id": 1, "bogus": "x", "mobile": "123"})
    assert state.chat_id == 1
    assert state.mobile == "123"
    assert not hasattr(state, "bogus")


def test_to_dict_roundtrip():
    state = SessionState(chat_id=9, client_type="client")
    restored = SessionState.from_dict(state.to_dict())
    assert restored.client_type == "client"
    assert restored.chat_id == 9
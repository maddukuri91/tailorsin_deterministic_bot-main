import asyncio
from uuid import uuid4

from services.idempotency import claim_event, release_event


def test_event_can_only_be_claimed_once_until_released():
    event_id = str(uuid4())
    loop = asyncio.get_event_loop()

    assert loop.run_until_complete(claim_event("test", event_id)) is True
    assert loop.run_until_complete(claim_event("test", event_id)) is False
    loop.run_until_complete(release_event("test", event_id))
    assert loop.run_until_complete(claim_event("test", event_id)) is True

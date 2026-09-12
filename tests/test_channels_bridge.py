from aiohttp.test_utils import TestClient, TestServer

from quorum.platform.channels_bridge import ChannelDelivery, bridge_app


async def test_bridge_rejects_unauthenticated_input():
    async with TestClient(TestServer(bridge_app(None, "secret"))) as client:
        response = await client.post("/delivery", json={})
        assert response.status == 401
        response = await client.post("/delivery", json={}, headers={"Authorization": "Bearer secret"})
        assert response.status == 400


def test_direct_channel_identity_is_exact_and_managed_keys_are_not_guessed():
    from datetime import UTC, datetime

    import pytest

    from quorum.domain.models import Message

    event = ChannelDelivery(conversation_key="C123::1700000000.000100", event_id="evt", actor_id="U123",
                            messages=[Message(id="1700000000.000100", user_id="U123", text="Question", at=datetime.now(UTC))])
    assert event.ref().key == "slack:C123:1700000000.000100"
    event.conversation_key = "opaque-managed-key"
    with pytest.raises(ValueError):
        event.ref()

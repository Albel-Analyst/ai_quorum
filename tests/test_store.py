from datetime import UTC, datetime

from quorum.domain.models import DecisionMemory, Message, ThreadRef, TrackedThread
from quorum.store.sqlite import Store


async def test_threads_decisions_dedup_and_cache():
    s = Store(":memory:")
    await s.open()
    ref = ThreadRef(platform="slack", channel_id="C1", thread_id="1.0")
    t = TrackedThread(ref=ref, author_id="U1", requested_by="U1")
    await s.save_thread(t)
    assert (await s.get_thread(ref.key)).author_id == "U1"
    assert len(await s.list_threads(statuses=["OBSERVING"])) == 1
    assert await s.list_threads(statuses=["voting"]) == []
    d = DecisionMemory(thread_key=ref.key, title="q", summary="s", decided_at=datetime.now(UTC), channel_id="C1")
    await s.save_decision(d)
    assert (await s.list_decisions(active_only=True))[0].title == "q"
    d.status = "superseded"
    await s.save_decision(d)
    assert await s.list_decisions(active_only=True) == []
    assert await s.seen("Ev1") is False and await s.seen("Ev1") is True
    await s.cache_messages(ref.key, [Message(id="2.0", user_id="U2", text="hi", at=datetime.now(UTC))])
    assert (await s.cached_messages(ref.key))[0]["text"] == "hi"
    await s.delete_cached_message(ref.key, "2.0")
    assert await s.cached_messages(ref.key) == []
    assert await s.incr("c") == 1 and await s.incr("c") == 2
    await s.close()

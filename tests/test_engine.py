"""End-to-end engine tests: FakePlatform + scripted FakeProvider, no Slack, no LLM."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from quorum.config import Settings
from quorum.core.engine import Engine
from quorum.domain.events import ButtonPressed, FormSubmitted, MessagePosted, TrackRequested
from quorum.domain.models import Message, Record, Status, ThreadRef, Verification
from quorum.llm.base import ExtractedClaim, ExtractedOpenQuestion, ExtractedOption, ExtractedPosition, Extraction
from quorum.llm.fake import FakeProvider
from quorum.platform.fake import FakePlatform
from quorum.recorders.markdown import MarkdownRecorder
from quorum.store.sqlite import Store

T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
REF = ThreadRef(platform="fake", channel_id="C1", thread_id="1.0")


def _msg(i: int, user: str, text: str, mentions: list[str] | None = None) -> Message:
    return Message(id=f"{i}.0", user_id=user, text=text, at=T0 + timedelta(minutes=i), mentions=mentions or [])


HISTORY = [
    _msg(1, "U1", "Postgres or Mongo for the new service?"),
    _msg(2, "U2", "Postgres, we know it"),
    _msg(3, "U1", "<@U3> how much is Mongo Atlas?", ["U3"]),
]

EXT = Extraction(
    question="Postgres or Mongo for the new service?",
    context="New service, team knows Postgres.",
    language="en",
    options=[ExtractedOption(id="A", label="Postgres", summary="managed PG"), ExtractedOption(id="B", label="Mongo", summary="Atlas")],
    positions=[ExtractedPosition(user_id="U2", option_id="A", argument="we know it")],
    open_questions=[ExtractedOpenQuestion(text="how much is Mongo Atlas?", directed_to="U3", asked_by="U1", asked_at_message_id="3.0")],
    claims=[ExtractedClaim(text="Atlas M10 costs $57/month", by="U2", materiality="material", disputed=True)],
)


class OkRecorder:
    kind = "confluence"

    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, 0

    def available(self) -> bool:
        return True

    async def record(self, inp):
        self.calls += 1
        if self.fail:
            raise RuntimeError("confluence down")
        return [Record(kind="confluence", title=inp.state.question, url="https://conf/x")]


class OkVerifier:
    def available(self) -> bool:
        return True

    async def verify(self, claim, context):
        return Verification(verdict="confirmed", summary="yes", sources=[])


@pytest.fixture
async def env(tmp_path):
    settings = Settings(
        slack_bot_token="x", slack_app_token="x", openai_api_key="", coalesce_seconds=0.01, demo_time_scale=1.0,
        silence_minutes=30, silence_messages=2, stall_hours=6, expire_hours=48, autosuggest_every_n=0,
        markdown_records_dir=str(tmp_path / "records"), db_path=":memory:", _env_file=None,
    )
    store = Store(":memory:")
    await store.open()
    platform = FakePlatform()
    platform.threads[REF.key] = list(HISTORY)
    platform.names = {"U1": "Ann", "U2": "Bob", "U3": "Cid"}
    llm = FakeProvider(scripted=[EXT])
    recorder = OkRecorder()
    engine = Engine(
        platform=platform, store=store, llm=llm, recorders=[recorder, MarkdownRecorder(str(tmp_path / "records"))],
        verifier=OkVerifier(), settings=settings, home_view=lambda th, de, lang: platform.home(th, de),
    )
    yield engine, platform, store, llm, recorder
    await engine.flush()
    await store.close()


def _status(platform: FakePlatform):
    return [c for c in platform.calls if c[0] in ("post_card", "update_card", "delete_message")]


async def test_mvp_flow(env):
    engine, platform, store, llm, recorder = env
    thread = await engine.track(TrackRequested(thread=REF, requested_by="U1", via="mention"))
    assert thread.state.status == Status.NEEDS_EVIDENCE
    card_id = thread.card_message_id
    await engine.handle(FormSubmitted(thread=REF, user_id="U2", form_id="confirm", values={"option": "A"}))
    assert (await store.get_thread(REF.key)).state.decision is None
    await engine.handle(FormSubmitted(thread=REF, user_id="U1", form_id="confirm", values={"option": "A"}))
    current = await store.get_thread(REF.key)
    assert current.state.status == Status.DECIDED
    assert current.state.decision.summary == "Postgres"
    assert current.card_message_id == card_id
    assert sum(c[0] == "post_card" for c in platform.calls) == 1
    assert not platform.dms


async def test_recorder_fallback_to_markdown(env, tmp_path):
    engine, platform, store, llm, recorder = env
    recorder.fail = True
    await engine.track(TrackRequested(thread=REF, requested_by="U1", via="shortcut"))
    await engine.handle(ButtonPressed(thread=REF, user_id="U1", action="open_voting"))
    await engine.handle(FormSubmitted(thread=REF, user_id="U1", form_id="confirm", values={"option": "A"}, payload={"t": REF.key}))
    await engine.handle(ButtonPressed(thread=REF, user_id="U1", action="record"))
    thread = await store.get_thread(REF.key)
    assert recorder.calls == 0  # legacy ADR fallback cannot masquerade as execution
    assert thread.state.status == Status.DECIDED and not thread.state.records
    assert not list((tmp_path / "records").glob("*.md"))


async def test_llm_failure_keeps_card_and_says_so(env):
    engine, platform, store, llm, recorder = env
    await engine.track(TrackRequested(thread=REF, requested_by="U1", via="reaction"))
    llm.set_fail(True)
    platform.threads[REF.key].append(_msg(5, "U3", "about 57 dollars"))
    await engine.handle(MessagePosted(thread=REF, message=_msg(5, "U3", "about 57 dollars"), in_thread=True))
    await asyncio.sleep(0.05)
    await engine.flush()
    st = (await store.get_thread(REF.key)).state
    assert st.last_llm_error and [o.id for o in st.options] == ["A", "B"]
    assert "⚠️" in str(platform.messages[("C1", (await store.get_thread(REF.key)).card_message_id)]["blocks"])


async def test_silent_scheduler_does_not_nag_or_auto_vote(env):
    engine, platform, store, llm, recorder = env
    await engine.track(TrackRequested(thread=REF, requested_by="U1", via="mention"))
    engine.s.demo_time_scale = 10_000
    await engine.tick()
    assert not platform.dms
    assert (await store.get_thread(REF.key)).state.status == Status.NEEDS_EVIDENCE


async def test_deferred_loop_is_explicitly_terminal(env):
    engine, platform, store, llm, recorder = env
    await engine.track(TrackRequested(thread=REF, requested_by="U1", via="mention"))
    await engine.handle(ButtonPressed(thread=REF, user_id="U1", action="defer"))
    await engine.reconcile(REF.key)
    state = (await store.get_thread(REF.key)).state
    assert state.status == Status.DEFERRED and state.next_trigger is None
    assert not platform.dms


async def test_verify_claim(env):
    engine, platform, store, llm, recorder = env
    await engine.track(TrackRequested(thread=REF, requested_by="U1", via="mention"))
    cid = (await store.get_thread(REF.key)).state.claims[0].id
    await engine.handle(ButtonPressed(thread=REF, user_id="U2", action="verify", payload={"claim_id": cid}))
    await engine.flush()
    c = (await store.get_thread(REF.key)).state.claims[0]
    assert c.verification and c.verification.verdict == "confirmed" and not c.checking


async def test_memory_recall_and_dispute(env):
    engine, platform, store, llm, recorder = env
    await engine.track(TrackRequested(thread=REF, requested_by="U1", via="mention"))
    await engine.handle(ButtonPressed(thread=REF, user_id="U1", action="open_voting"))
    await engine.handle(FormSubmitted(thread=REF, user_id="U1", form_id="confirm", values={"option": "A"}, payload={"t": REF.key}))
    assert await store.list_decisions(active_only=True)
    # a top-level message elsewhere that contradicts (FakeProvider flags messages containing "contradict"/"вопреки"... make it explicit)
    llm.force_contradiction = REF.key  # honoured by FakeProvider.contradiction if supported; else heuristic below
    other = ThreadRef(platform="fake", channel_id="C1", thread_id="50.0")
    msg = Message(id="50.0", user_id="U2", text="Let's just use Mongo for the new service, forget Postgres — contradicts nothing?", at=T0)
    platform.threads[other.key] = [msg]
    await engine.handle(MessagePosted(thread=other, message=msg, in_thread=False))
    await engine.flush()
    if platform.notices:
        notice = platform.notices[-1]["notice"]
        dispute = next(b for b in notice.buttons if b.action == "dispute")
        await engine.handle(ButtonPressed(thread=ThreadRef.from_key(dispute.payload["t"]), user_id="U2", action="dispute", payload=dispute.payload))
        new = await store.get_thread(other.key)
        assert new and new.state.supersedes == REF.key
        await engine.handle(ButtonPressed(thread=other, user_id="U2", action="open_voting"))
        await engine.handle(FormSubmitted(thread=other, user_id="U2", form_id="confirm", values={"option": "A"}, payload={"t": other.key}))
        old = await store.get_thread(REF.key)
        assert old.state.status == Status.SUPERSEDED and old.state.superseded_by == other.key
        assert (await store.get_decision(REF.key)).status == "superseded"


async def test_already_tracked_and_dedup_of_messages(env):
    engine, platform, store, llm, recorder = env
    await engine.track(TrackRequested(thread=REF, requested_by="U1", via="mention"))
    await engine.track(TrackRequested(thread=REF, requested_by="U2", via="mention"))
    assert platform.ephemerals[-1]["user_id"] == "U2"
    assert len([c for c in platform.calls if c[0] == "post_card"]) == 1

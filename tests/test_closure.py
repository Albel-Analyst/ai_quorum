"""A1–A15 offline acceptance tests. Test doubles are NOT live sponsor evidence."""
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from quorum.config import Settings
from quorum.core.engine import Engine
from quorum.domain import state_machine as sm
from quorum.domain.events import ButtonPressed, FormSubmitted, MessagePosted, TrackRequested
from quorum.domain.models import Message, Source, Status, ThreadRef, Verification
from quorum.llm.base import ExtractedClaim, ExtractedOption, ExtractedPosition, Extraction, ProposedCommitment
from quorum.llm.fake import FakeProvider
from quorum.platform.fake import FakePlatform
from quorum.recorders.ambiguous import ExternalTask
from quorum.store.sqlite import Store

REF = ThreadRef(platform="fake", channel_id="demo", thread_id="1.0")
PROMISE = "I'll migrate OTP by Friday."


class Tasks:
    def __init__(self):
        self.created = []
        self.reads = []
        self.status = "todo"
        self.fail = False
        self.lose_response = False

    async def create(self, thread, commitment):
        task = ExternalTask(id=str(uuid4()), url="https://example.test/task", status="todo")
        self.created.append(task)
        if self.lose_response:
            raise TimeoutError("write may have succeeded")
        return task

    async def get(self, record_id):
        self.reads.append(record_id)
        if self.fail:
            raise TimeoutError("offline")
        return self.created[0].model_copy(update={"status": self.status})

    async def recover(self, commitment_id):
        return self.created[0] if self.created else None


class Verifier:
    def available(self):
        return True

    async def verify(self, claim, context):
        return Verification(verdict="refuted", summary="Provider documentation contradicts the assertion.",
                            sources=[Source(title="Provider docs", url="https://example.test/docs")])


@pytest.fixture
async def env(tmp_path):
    store = Store(str(tmp_path / "quorum.db"))
    await store.open()
    platform, llm, tasks = FakePlatform(), FakeProvider(), Tasks()
    platform.threads[REF.key] = [Message(id="1.0", user_id="Alice", text="Choose OTP provider", at=datetime.now(UTC)),
                               Message(id="2.0", user_id="Bek", text=PROMISE, at=datetime.now(UTC))]
    ext = Extraction(question="Choose OTP provider", options=[ExtractedOption(id="A", label="Eskiz"),
                    ExtractedOption(id="B", label="Twilio")],
                    positions=[ExtractedPosition(user_id="Bek", option_id="B", argument="I prefer Twilio")],
                    decision_reached=True, decision_option_id="A", decision_by="Alice", decision_quote="Use Eskiz",
                    claims=[ExtractedClaim(text="Eskiz cannot support sender IDs", by="Alice", source_message_ids=["1.0"],
                                           materiality="material", disputed=True, confidence=0.9)],
                    commitments=[ProposedCommitment(owner="Bek", action="Migrate OTP", source_message_id="2.0",
                        quote=PROMISE, explicit_personal_promise=True, confidence=0.99)])
    llm.scripted = [ext]
    engine = Engine(platform=platform, store=store, llm=llm, recorders=[], verifier=Verifier(), tasks=tasks,
                    settings=Settings(_env_file=None, coalesce_seconds=100, closure_check_seconds=0.001))
    yield engine, platform, store, llm, tasks, ext
    await engine.flush()
    await store.close()


async def activate(env):
    engine, _, store, *_ = env
    await engine.track(TrackRequested(thread=REF, requested_by="Alice", via="mention"))
    return await store.get_thread(REF.key)


async def approve(env):
    engine, _, store, *_ = env
    thread = await activate(env)
    assert thread.state.status == Status.NEEDS_EVIDENCE
    cid = thread.state.claims[0].id
    await engine.handle(ButtonPressed(thread=REF, user_id="Alice", action="verify", payload={"claim_id": cid}))
    await engine.flush()
    await engine.handle(FormSubmitted(thread=REF, user_id="Alice", form_id="confirm", values={"option": "A"}))
    thread = await store.get_thread(REF.key)
    assert thread.state.status == Status.DECIDED
    c = thread.state.commitments[0]
    await engine.handle(FormSubmitted(thread=REF, user_id="Bek", form_id="commitment",
        payload={"t": REF.key, "commitment_id": c.id},
        values={"action": c.action, "owner": "Bek", "due": "2026-09-18", "mode": "observable"}))
    assert (await store.get_thread(REF.key)).state.status == Status.EXECUTING
    return c.id


async def record(env, cid):
    await env[0].handle(ButtonPressed(thread=REF, user_id="Bek", action="record_task", payload={"commitment_id": cid}))


async def test_a1_activation_isolation(env):
    await activate(env)
    other = ThreadRef(platform="fake", channel_id="demo", thread_id="99.0")
    before = (await env[2].get_thread(REF.key)).model_dump_json()
    await env[0].handle(MessagePosted(thread=other, in_thread=True,
                       message=Message(id="100.0", user_id="Mallory", text="Cancel", at=datetime.now(UTC))))
    await env[0].handle(FormSubmitted(thread=other, user_id="Alice", form_id="confirm",
                                     payload={"t": REF.key}, values={"option": "A"}))
    assert await env[2].get_thread(other.key) is None
    assert (await env[2].get_thread(REF.key)).model_dump_json() == before


async def test_a2_single_artifact_and_transient_update_failure(env):
    thread = await activate(env)
    for _ in range(10):
        await env[0].refresh(REF.key, force=True)
    assert (await env[2].get_thread(REF.key)).card_message_id == thread.card_message_id
    async def fail(*args):
        raise TimeoutError()
    env[1].update_card = fail
    with pytest.raises(TimeoutError):
        await env[0].refresh(REF.key, force=True)
    assert sum(name == "post_card" for name, _ in env[1].calls) == 1
    assert not env[1].dms and not env[1].notices


async def test_a3_decision_authority_and_dissent(env):
    await activate(env)
    await env[0].handle(FormSubmitted(thread=REF, user_id="Bek", form_id="confirm", values={"option": "A"}))
    assert (await env[2].get_thread(REF.key)).state.decision is None
    await approve(env)
    d = (await env[2].get_thread(REF.key)).state.decision
    assert d.confirmed_by == "Alice" and d.dissent[0].user_id == "Bek" and d.alternatives[0].id == "B"


@pytest.mark.parametrize("quote", ["We should migrate this Friday", "I could migrate", "Maybe somebody should migrate"])
async def test_a4_aspirations_are_not_commitments(env, quote):
    env[1].threads[REF.key][1].text = quote
    env[5].commitments[0].quote = quote
    assert not (await activate(env)).state.commitments


async def test_a5_evidence_separation(env):
    await approve(env)
    thread = await env[2].get_thread(REF.key)
    claim = thread.state.claims[0]
    assert claim.text == "Eskiz cannot support sender IDs" and claim.status == "contradicted"
    assert claim.source_message_ids == ["1.0"] and claim.external_sources
    blocks = str(env[1].card_blocks(thread))
    assert "Participant assertion" in blocks and "External evidence" in blocks


async def test_a6_a7_a9_persisted_write_readback_restart(env):
    cid = await approve(env)
    await record(env, cid)
    await env[2].close()
    await env[2].open()
    c = (await env[2].get_thread(REF.key)).state.commitments[0]
    assert c.external_record_id == env[4].created[0].id and c.external_url
    await env[0].reconcile(REF.key)
    assert env[4].reads == [c.external_record_id, c.external_record_id]


async def test_a8_denied_write(env):
    thread = await activate(env)
    cid = thread.state.commitments[0].id
    await env[0].handle(ButtonPressed(thread=REF, user_id="Bek", action="reject_commitment", payload={"commitment_id": cid}))
    await record(env, cid)
    assert not env[4].created
    assert (await env[2].get_thread(REF.key)).state.commitments[0].confirmation == "rejected"


@pytest.mark.parametrize("external", ["done", "cancelled", "blocked"])
async def test_a10_proof_before_any_intervention(env, external):
    cid = await approve(env)
    await record(env, cid)
    env[4].status = external
    await env[0].reconcile(REF.key)
    expected = {"done": Status.CLOSED, "cancelled": Status.CANCELLED, "blocked": Status.BLOCKED}[external]
    assert (await env[2].get_thread(REF.key)).state.status == expected
    assert not env[1].dms
    assert any(name == "fetch_thread" for name, _ in env[1].calls)


async def test_a11_no_closure_by_vibes_or_stale_source(env):
    cid = await approve(env)
    await record(env, cid)
    await env[0].reconcile(REF.key)
    state = (await env[2].get_thread(REF.key)).state
    assert state.status == Status.EXECUTING
    with pytest.raises(sm.IllegalTransition):
        sm.transition(state, Status.CLOSED)
    env[1].threads[REF.key][1].text = "That commitment was cancelled."
    env[4].status = "done"
    for _ in range(2):
        await env[0].reconcile(REF.key)
        assert (await env[2].get_thread(REF.key)).state.status == Status.BLOCKED


async def test_a12_failure_recovery_and_lost_write_response(env):
    cid = await approve(env)
    env[4].lose_response = True
    await record(env, cid)
    await record(env, cid)
    assert len(env[4].created) == 1
    assert (await env[2].get_thread(REF.key)).state.commitments[0].write_state == "uncertain"
    env[4].fail = True
    await env[0].reconcile(REF.key)
    assert (await env[2].get_thread(REF.key)).state.reconciliation_error
    env[4].fail, env[4].status = False, "done"
    await env[0].reconcile(REF.key)
    assert (await env[2].get_thread(REF.key)).state.status == Status.CLOSED


async def test_a13_replayed_events_do_not_duplicate_side_effects(env):
    cid = await approve(env)
    await record(env, cid)
    await record(env, cid)
    msg = Message(id="9.0", user_id="Bek", text="Still working", at=datetime.now(UTC))
    event = MessagePosted(thread=REF, in_thread=True, message=msg)
    await env[0].handle(event)
    count = (await env[2].get_thread(REF.key)).message_count
    await env[0].handle(event)
    assert (await env[2].get_thread(REF.key)).message_count == count
    assert len(env[4].created) == 1
    assert sum(name == "post_card" for name, _ in env[1].calls) == 1


async def test_a14_supersession_retires_obsolete_work(env):
    await approve(env)
    before = (await env[2].get_thread(REF.key)).state.decision.model_dump()
    await env[0]._supersede(REF.key, "fake:demo:other")
    state = (await env[2].get_thread(REF.key)).state
    assert state.status == Status.SUPERSEDED and state.commitments[0].status == "superseded"
    assert state.decision.model_dump() == before and state.next_trigger is None


@pytest.mark.parametrize("run", range(3))
async def test_a15_full_lifecycle_three_fresh_runs(env, run):
    cid = await approve(env)
    await record(env, cid)
    assert (await env[2].get_thread(REF.key)).state.status == Status.EXECUTING
    await env[2].close()
    await env[2].open()
    env[4].status = "done"
    await asyncio.sleep(0.005)
    await env[0].tick()
    state = (await env[2].get_thread(REF.key)).state
    assert state.status == Status.CLOSED and state.closed_at and state.next_trigger is None
    transitions = [event.state_after for event in state.audit if event.event_type == "transition"]
    for status in [Status.NEEDS_EVIDENCE, Status.DECIDED, Status.EXECUTING, Status.VERIFYING, Status.CLOSED]:
        assert status.value in transitions
    assert len(env[4].created) == 1 and len(env[4].reads) == 2
    assert sum(name == "post_card" for name, _ in env[1].calls) == 1


@pytest.mark.parametrize('mode,actor', [('attestable', 'Bek'), ('adjudicated', 'Alice')])
async def test_human_closure_requires_authorized_attestation(env, mode, actor):
    await activate(env)
    engine, _, store, *_ = env
    await engine.handle(FormSubmitted(thread=REF, user_id='Alice', form_id='confirm', values={'option': 'A'}))
    c = (await store.get_thread(REF.key)).state.commitments[0]
    await engine.handle(FormSubmitted(thread=REF, user_id='Bek', form_id='commitment',
        payload={'t': REF.key, 'commitment_id': c.id}, values={'action': c.action, 'owner': 'Bek',
        'due': '2026-09-18', 'mode': mode, 'expected': 'OTP works on the test handset'}))
    event = FormSubmitted(thread=REF, user_id='Mallory', form_id='attest',
        payload={'t': REF.key, 'commitment_id': c.id}, values={'evidence': 'Test completed on handset'})
    await engine.handle(event)
    assert not (await store.get_thread(REF.key)).state.closure_conditions[0].evidence
    event.user_id = actor
    await engine.handle(event)
    await engine.flush()
    assert (await store.get_thread(REF.key)).state.status == Status.CLOSED


async def test_replayed_check_is_noop_and_thread_change_blocks_closure(env):
    from quorum.llm.base import ProposedLoopControl
    cid = await approve(env)
    await record(env, cid)
    engine, platform, store, llm, tasks, ext = env
    event = ButtonPressed(event_id='check-1', thread=REF, user_id='Bek', action='check_now')
    await engine.handle(event)
    before = (await store.get_thread(REF.key)).model_dump_json()
    await engine.handle(event)
    assert (await store.get_thread(REF.key)).model_dump_json() == before
    control = Message(id='10.0', user_id='Alice', text='Cancel the migration', at=datetime.now(UTC))
    platform.threads[REF.key].append(control)
    ext.loop_control = ProposedLoopControl(intent='cancel', quote=control.text, source_message_id=control.id)
    tasks.status = 'done'
    await engine.reconcile(REF.key)
    assert (await store.get_thread(REF.key)).state.status == Status.BLOCKED


async def test_changed_assertion_does_not_inherit_verification(env):
    await approve(env)
    env[5].claims[0].text = 'Eskiz DOES support sender IDs'
    await env[0].refresh(REF.key, force=True)
    claims = (await env[2].get_thread(REF.key)).state.claims
    assert claims[0].verification is None
    assert claims[1].verification is not None


async def test_ambiguous_missing_credentials_never_falls_back_to_fake_success(env):
    cid = await approve(env)
    env[0].external_tasks = None
    await record(env, cid)
    state = (await env[2].get_thread(REF.key)).state
    assert state.reconciliation_error and not state.commitments[0].external_record_id
    assert state.status == Status.EXECUTING


async def test_card_creation_timeout_never_blindly_posts_again(env):
    async def lose_response(*args, **kwargs):
        raise TimeoutError('delivery uncertain')
    env[1].post_card = lose_response
    with pytest.raises(TimeoutError):
        await activate(env)
    thread = await env[0].track(TrackRequested(thread=REF, requested_by='Alice', via='mention'))
    assert thread.card_creation_pending and thread.card_message_id is None
    assert not env[1].messages


async def test_live_ambiguous_task_roundtrip(env):
    import os

    from quorum.config import settings
    from quorum.recorders.ambiguous import AmbiguousTasks
    if os.getenv('QUORUM_LIVE_AMBIGUOUS') != '1' or not settings.ambiguous_api_key:
        pytest.skip('Requires explicit live test opt-in and AMBIGUOUS_API_KEY; creates a labeled test task')
    client = AmbiguousTasks(settings.ambiguous_api_key, settings.ambiguous_mcp_url)
    await client.call('auth_whoami', {})
    env[0].external_tasks = client
    env[5].commitments[0].action = 'Quorum integration test — safe to archive'
    cid = await approve(env)
    await record(env, cid)
    c = (await env[2].get_thread(REF.key)).state.commitments[0]
    assert c.external_record_id, (await env[2].get_thread(REF.key)).state.reconciliation_error
    await env[2].close()
    await env[2].open()
    assert (await client.get(c.external_record_id)).id == c.external_record_id
    await client.call('update_task', {'id': c.external_record_id, 'status': 'done'})
    await env[0].reconcile(REF.key)
    assert (await env[2].get_thread(REF.key)).state.status == Status.CLOSED
    print('Real Ambiguous task:', c.external_record_id, 'Returned link:', c.external_url)


async def test_bot_seed_uses_real_activator_without_stealing_human_thread_authority(env):
    env[1].threads[REF.key][0].user_id = 'persona:Ann'
    thread = await activate(env)
    assert thread.author_id == 'Alice'
    assert env[0]._may_decide(thread, 'Alice')
    assert not env[0]._may_decide(thread, 'Mallory')


async def test_commitment_owner_cannot_assign_someone_else_without_decider(env):
    await activate(env)
    await env[0].handle(FormSubmitted(thread=REF, user_id='Alice', form_id='confirm', values={'option': 'A'}))
    c = (await env[2].get_thread(REF.key)).state.commitments[0]
    await env[0].handle(FormSubmitted(thread=REF, user_id='Bek', form_id='commitment',
        payload={'t': REF.key, 'commitment_id': c.id}, values={'action': c.action, 'owner': 'Alice',
        'due': '2026-09-18', 'mode': 'observable'}))
    c = (await env[2].get_thread(REF.key)).state.commitments[0]
    assert c.owner == 'Bek' and c.confirmation == 'pending'

"""FakeProvider behaviour. Offline, deterministic, no network."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from openai import APIConnectionError, APIStatusError
from openai._base_client import httpx2 as httpx  # openai 3.x vendors httpx under this name

from quorum.domain.models import (
    CardState,
    Decision,
    DecisionMemory,
    Message,
    OpenQuestion,
    Option,
    Position,
)
from quorum.llm import openai_provider
from quorum.llm.base import ContradictionCheck, Extraction, SourceSnippet
from quorum.llm.fake import FakeProvider
from quorum.llm.openai_provider import LLMError, OpenAIProvider
from quorum.llm.prompts import format_messages, state_for_prompt

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)

ANNA = "U_ANNA"
BEK = "U_BEK"
DIMA = "U_DIMA"
NAMES = {ANNA: "Anna", BEK: "Bekzod", DIMA: "Dima"}


def msg(i: int, user: str, text: str, *, is_bot: bool = False) -> Message:
    return Message(
        id=f"m{i}",
        user_id=user,
        user_name=NAMES.get(user, ""),
        text=text,
        at=T0 + timedelta(minutes=i),
        is_bot=is_bot,
    )


def thread() -> list[Message]:
    return [
        msg(1, ANNA, "Какую базу берём под новый сервис?"),
        msg(2, ANNA, "A) Postgres 17\nB) ClickHouse"),
        msg(3, BEK, "Я за A, он у нас уже есть и команда его знает"),
        msg(4, DIMA, "B быстрее на аналитике, но лицензия стоит 500 USD в месяц"),
        msg(5, ANNA, "<@U_BEK> сколько будет стоить миграция?"),
        msg(6, ANNA, "квorum card", is_bot=True),
    ]


# ---------------------------------------------------------------------------------------------
# heuristic mode
# ---------------------------------------------------------------------------------------------
async def test_heuristic_extraction_shape() -> None:
    fake = FakeProvider()
    messages = thread()
    out = await fake.extract(CardState(), messages[-2:], messages, NAMES)

    assert out.language == "ru"
    assert out.question.startswith("Какую базу")

    ids = [o.id for o in out.options]
    assert ids == ["A", "B"]
    assert "Postgres" in out.options[0].label

    by_user = {p.user_id: p for p in out.positions}
    assert set(by_user) == {ANNA, BEK, DIMA}          # the bot message is ignored
    assert by_user[BEK].option_id == "A"
    assert by_user[DIMA].option_id == "B"
    assert by_user[BEK].argument.startswith("Я за A")

    assert len(out.open_questions) == 1
    question = out.open_questions[0]
    assert question.directed_to == BEK
    assert question.asked_by == ANNA
    assert question.asked_at_message_id == "m5"

    assert [c.by for c in out.claims] == [DIMA]        # "500 USD" is the only checkable statement
    assert not out.decision_reached


async def test_heuristic_is_deterministic() -> None:
    messages = thread()
    first = await FakeProvider().extract(CardState(), messages, messages, NAMES)
    second = await FakeProvider().extract(CardState(), messages, messages, NAMES)
    assert first.model_dump() == second.model_dump()


async def test_generic_options_when_no_markers() -> None:
    messages = [
        msg(1, ANNA, "Where do we deploy the worker?"),
        msg(2, BEK, "Kubernetes in the aux cluster"),
        msg(3, DIMA, "A plain VM is enough"),
    ]
    out = await FakeProvider().extract(CardState(), messages, messages, NAMES)
    assert [o.id for o in out.options] == ["A", "B"]
    assert out.options[0].label == "Kubernetes in the aux cluster"
    assert out.language == "en"


async def test_decision_reached_detection() -> None:
    messages = [*thread()[:4], msg(7, ANNA, "ок, решено, берём A")]
    out = await FakeProvider().extract(CardState(), messages, messages, NAMES)
    assert out.decision_reached
    assert out.decision_option_id == "A"
    assert out.decision_by == ANNA
    assert out.decision_quote


async def test_english_decision_and_stakeholders() -> None:
    messages = [
        msg(1, ANNA, "Which queue? A) Redis B) Kafka"),
        msg(2, BEK, "Kafka scales, but ask <@U_SILENT> first"),
        msg(3, ANNA, "let's go with B then"),
    ]
    out = await FakeProvider().extract(CardState(), messages, messages, NAMES)
    assert out.decision_reached and out.decision_option_id == "B"
    assert out.stakeholders_mentioned == ["U_SILENT"]


async def test_empty_thread_degrades() -> None:
    out = await FakeProvider().extract(CardState(question="kept"), [], [], NAMES)
    assert out.question == "kept"
    assert out.options == []


# ---------------------------------------------------------------------------------------------
# scripted mode
# ---------------------------------------------------------------------------------------------
async def test_scripted_returns_in_order_and_repeats_last() -> None:
    scripted = [
        Extraction(question="first"),
        Extraction(question="second"),
    ]
    fake = FakeProvider(scripted)
    messages = thread()
    assert (await fake.extract(CardState(), messages, messages, NAMES)).question == "first"
    assert (await fake.extract(CardState(), messages, messages, NAMES)).question == "second"
    assert (await fake.extract(CardState(), messages, messages, NAMES)).question == "second"
    assert [c[0] for c in fake.calls] == ["extract"] * 3


async def test_scripted_result_is_a_copy() -> None:
    fake = FakeProvider([Extraction(question="q")])
    out = await fake.extract(CardState(), [], [], {})
    out.question = "mutated"
    again = await fake.extract(CardState(), [], [], {})
    assert again.question == "q"


# ---------------------------------------------------------------------------------------------
# the other protocol methods
# ---------------------------------------------------------------------------------------------
def decided_state() -> CardState:
    return CardState(
        question="Какую базу берём?",
        options=[Option(id="A", label="Postgres"), Option(id="B", label="ClickHouse")],
        positions=[
            Position(user_id=BEK, option_id="A", argument="уже есть"),
            Position(user_id=DIMA, option_id="B", argument="быстрее"),
        ],
        open_questions=[OpenQuestion(text="сколько стоит миграция?", directed_to=BEK)],
        decision=Decision(option_id="A", owner=ANNA),
        language="ru",
    )


async def test_write_record_keeps_dissent() -> None:
    fake = FakeProvider()
    draft = await fake.write_record(decided_state(), thread(), NAMES)
    assert draft.title
    assert [d.user_id for d in draft.dissent] == [DIMA]
    assert draft.owner == ANNA
    assert len(draft.follow_ups) == len(draft.follow_up_assignees)
    assert draft.follow_up_assignees == [BEK]


async def test_stalled_summary_points_at_the_open_question() -> None:
    line = await FakeProvider().stalled_summary(decided_state(), NAMES)
    assert f"<@{BEK}>" in line
    assert "миграция" in line


async def test_decision_brewing_needs_two_options() -> None:
    fake = FakeProvider()
    yes = await fake.decision_brewing(thread(), NAMES)
    assert yes.brewing and yes.confidence >= 0.7

    no = await fake.decision_brewing([msg(1, ANNA, "деплой прошёл")], NAMES)
    assert not no.brewing and no.confidence < 0.7


async def test_contradiction_ignores_questions() -> None:
    memory = [
        DecisionMemory(
            thread_key="slack:C1:1",
            title="Какую базу берём",
            summary="Postgres",
            option_label="Postgres",
            decided_at=T0,
            channel_id="C1",
        )
    ]
    fake = FakeProvider()
    assert not (await fake.contradiction(msg(9, BEK, "а мы точно на Postgres?"), memory)).contradicts

    hit = await fake.contradiction(msg(10, BEK, "ставлю не Postgres, а MySQL"), memory)
    assert hit.contradicts and hit.decision_thread_key == "slack:C1:1"

    assert not (await fake.contradiction(msg(11, BEK, "деплой прошёл"), [])).contradicts


async def test_judge_claim_with_and_without_sources() -> None:
    fake = FakeProvider()
    empty = await fake.judge_claim("Postgres 17 вышел в сентябре 2024", "", [])
    assert empty.verdict == "unclear"

    sources = [SourceSnippet(title="PostgreSQL 17", url="https://example.org/pg17", snippet="Released 2024-09-26")]
    judged = await fake.judge_claim("Postgres 17 вышел в сентябре 2024", "db choice", sources)
    assert judged.verdict == "confirmed"
    assert judged.supporting_urls == ["https://example.org/pg17"]


async def test_calls_are_recorded() -> None:
    fake = FakeProvider()
    messages = thread()
    await fake.extract(CardState(), messages[-1:], messages, NAMES)
    await fake.stalled_summary(decided_state(), NAMES)
    await fake.judge_claim("x", "", [])
    assert [name for name, _ in fake.calls] == ["extract", "stalled_summary", "judge_claim"]
    assert fake.calls[0][1] == {"new": 1, "total": len(messages)}


# ---------------------------------------------------------------------------------------------
# fail mode
# ---------------------------------------------------------------------------------------------
async def test_fail_mode_raises_llm_error_everywhere() -> None:
    fake = FakeProvider()
    fake.set_fail(True)
    messages = thread()
    state = decided_state()

    with pytest.raises(LLMError):
        await fake.extract(state, messages, messages, NAMES)
    with pytest.raises(LLMError):
        await fake.write_record(state, messages, NAMES)
    with pytest.raises(LLMError):
        await fake.stalled_summary(state, NAMES)
    with pytest.raises(LLMError):
        await fake.decision_brewing(messages, NAMES)
    with pytest.raises(LLMError):
        await fake.contradiction(messages[0], [])
    with pytest.raises(LLMError):
        await fake.judge_claim("c", "", [])

    assert len(fake.calls) == 6          # the attempt is recorded even when it fails

    fake.set_fail(False)
    assert (await fake.extract(state, messages, messages, NAMES)).question


# ---------------------------------------------------------------------------------------------
# prompt formatting (pure functions, no provider involved)
# ---------------------------------------------------------------------------------------------
def test_format_messages_shape_and_bot_filtering() -> None:
    out = format_messages(thread(), NAMES, new_ids={"m5"})
    lines = out.splitlines()
    headers = [ln for ln in lines if ln.startswith(("[m", "NEW [m"))]
    assert len(headers) == 5                                 # the bot message is gone
    assert "квorum card" not in out
    assert lines[0] == f"[m1] <@{ANNA}> (Anna) 2026-09-12T09:01+00:00: Какую базу берём под новый сервис?"
    assert headers[-1].startswith("NEW [m5] ")
    assert "<@U_BEK>" in headers[-1]                          # slack mentions survive
    assert "B) ClickHouse" in out                             # multi-line text keeps its structure


def test_state_for_prompt_drops_noise() -> None:
    state = decided_state()
    state.last_llm_error = "boom"
    dumped = state_for_prompt(state)
    assert "last_llm_error" not in dumped
    assert "updated_at" not in dumped
    assert '"votes"' not in dumped
    assert "Postgres" in dumped


# ---------------------------------------------------------------------------------------------
# OpenAIProvider failure handling (offline: the client is a stub, no network)
# ---------------------------------------------------------------------------------------------
class _Responses:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.kwargs: list[dict] = []

    async def parse(self, **kwargs):
        self.kwargs.append(kwargs)
        outcome = self.outcomes[min(len(self.kwargs) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Client:
    def __init__(self, outcomes: list[object]) -> None:
        self.responses = _Responses(outcomes)


class _Parsed:
    def __init__(self, parsed: object) -> None:
        self.output_parsed = parsed
        self.usage = SimpleNamespace(input_tokens=10, output_tokens=20)


def _transient() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))


def provider(outcomes: list[object]) -> OpenAIProvider:
    return OpenAIProvider(client=_Client(outcomes), model_fast="m-fast", model_smart="m-smart")


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_provider, "BACKOFF_BASE_S", 0.0)


async def test_provider_retries_transient_then_succeeds() -> None:
    ok = _Parsed(Extraction(question="q"))
    p = provider([_transient(), _transient(), ok])
    out = await p.extract(CardState(), [], thread(), NAMES)
    assert out.question == "q"
    assert len(p.client.responses.kwargs) == 3
    assert p.client.responses.kwargs[0]["model"] == "m-fast"


async def test_provider_gives_up_after_two_retries() -> None:
    p = provider([_transient()])
    with pytest.raises(LLMError) as err:
        await p.extract(CardState(), [], thread(), NAMES)
    assert "extract failed" in str(err.value)
    assert len(p.client.responses.kwargs) == openai_provider.MAX_ATTEMPTS == 3


async def test_provider_does_not_retry_permanent_errors() -> None:
    p = provider([ValueError("bad schema")])
    with pytest.raises(LLMError):
        await p.extract(CardState(), [], thread(), NAMES)
    assert len(p.client.responses.kwargs) == 1


async def test_provider_raises_on_missing_parsed_output() -> None:
    p = provider([_Parsed(None)])
    with pytest.raises(LLMError):
        await p.stalled_summary(decided_state(), NAMES)


async def test_provider_drops_reasoning_when_rejected() -> None:
    rejected = APIStatusError(
        "Unsupported parameter: 'reasoning' is not supported with this model",
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
        body=None,
    )
    p = provider([rejected, _Parsed(Extraction(question="q"))])
    out = await p.extract(CardState(), [], thread(), NAMES)
    assert out.question == "q"
    assert "reasoning" in p.client.responses.kwargs[0]
    assert "reasoning" not in p.client.responses.kwargs[1]
    assert p._reasoning_ok is False


async def test_provider_short_circuits_contradiction_without_memory() -> None:
    p = provider([_Parsed(None)])
    check = await p.contradiction(thread()[0], [])
    assert not check.contradicts
    assert p.client.responses.kwargs == []


async def test_provider_rejects_hallucinated_thread_key() -> None:
    bogus = _Parsed(ContradictionCheck(contradicts=True, decision_thread_key="slack:C9:9", why="x"))
    p = provider([bogus])
    memory = [
        DecisionMemory(thread_key="slack:C1:1", title="t", summary="s", decided_at=T0, channel_id="C1"),
    ]
    assert not (await p.contradiction(thread()[0], memory)).contradicts

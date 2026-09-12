"""Live smoke test against the real OpenAI API.

Skipped unless QUORUM_LIVE_LLM=1 (and an API key is configured), so the default suite stays offline:

    set -a; . ./.env; set +a; QUORUM_LIVE_LLM=1 uv run pytest tests/test_llm_live.py -q -s
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from quorum.config import settings
from quorum.domain.models import CardState, Message
from quorum.llm.openai_provider import OpenAIProvider

pytestmark = pytest.mark.skipif(
    os.getenv("QUORUM_LIVE_LLM") != "1" or not (settings.openai_api_key or os.getenv("OPENAI_API_KEY")),
    reason="live LLM test: set QUORUM_LIVE_LLM=1 and OPENAI_API_KEY",
)

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)

ANNA = "U01ANNA"
BEK = "U02BEK"
DIMA = "U03DIMA"
NAMES = {ANNA: "Аня", BEK: "Бекзод", DIMA: "Дима"}


def msg(i: int, user: str, text: str) -> Message:
    return Message(
        id=f"17578{i:05d}.000100", user_id=user, user_name=NAMES[user], text=text, at=T0 + timedelta(minutes=i)
    )


def russian_thread() -> list[Message]:
    return [
        msg(1, ANNA, "Ребята, какую базу берём под новый сервис заказов?"),
        msg(2, ANNA, "A) Postgres — то, что уже есть\nB) ClickHouse — если нужна аналитика"),
        msg(3, BEK, "Я за A: Postgres 17 вышел в сентябре 2024, транзакции нам важнее аналитики."),
        msg(4, DIMA, "B лучше на отчётах, у нас там 300 млн строк в месяц."),
        msg(5, ANNA, f"<@{BEK}> сколько времени займёт миграция схемы?"),
    ]


async def test_extract_russian_thread() -> None:
    provider = OpenAIProvider()
    messages = russian_thread()

    started = time.perf_counter()
    out = await provider.extract(CardState(), messages, messages, NAMES)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    print(f"\nextract() latency: {elapsed_ms} ms  (model={provider.model_fast})")
    print(out.model_dump_json(indent=2, exclude_none=True))

    assert out.language == "ru"
    assert out.question.strip()

    ids = [o.id for o in out.options]
    assert len(ids) >= 2, f"expected >=2 options, got {ids}"
    assert {"A", "B"} <= set(ids), f"option ids must stay A/B, got {ids}"

    positions = {p.user_id for p in out.positions}
    assert {BEK, DIMA} <= positions, f"missing positions, got {positions}"

    directed = [q for q in out.open_questions if q.directed_to]
    assert directed, f"expected a directed open question, got {out.open_questions}"
    assert directed[0].directed_to == BEK

    assert out.claims, "expected at least one checkable claim (Postgres 17 release date)"
    assert all(c.by in NAMES for c in out.claims)


async def test_classifier_and_stalled_summary() -> None:
    provider = OpenAIProvider()
    messages = russian_thread()

    brewing = await provider.decision_brewing(messages, NAMES)
    print(f"\ndecision_brewing: {brewing.model_dump()}")
    assert brewing.brewing and brewing.confidence >= 0.7

    chatter = [
        msg(6, ANNA, "деплой прошёл, всё зелёное"),
        msg(7, DIMA, "спасибо!"),
    ]
    quiet = await provider.decision_brewing(chatter, NAMES)
    print(f"decision_brewing (chatter): {quiet.model_dump()}")
    assert not (quiet.brewing and quiet.confidence >= 0.7)

    state = CardState(question="Какую базу берём под новый сервис заказов?", language="ru")
    line = await provider.stalled_summary(state, NAMES)
    print(f"stalled_summary: {line}")
    assert line.strip()

"""ADR renderer: deterministic markdown in both UI languages."""
from __future__ import annotations

from datetime import UTC, datetime

from quorum.domain.models import (
    CardState,
    Decision,
    Dissent,
    FollowUp,
    OpenQuestion,
    Option,
    Record,
    Status,
    ThreadRef,
    TrackedThread,
)
from quorum.render.markdown import render_adr, render_expired_note, slugify, title_from_markdown

NAMES = {"U1": "Alice", "U2": "Bob", "U3": "Clara"}


def decided_thread() -> tuple[TrackedThread, CardState]:
    state = CardState(
        question="Which database for the events service?",
        context="The events service outgrew SQLite; we need a target by Friday.",
        options=[
            Option(id="A", label="Postgres", summary="one more managed instance"),
            Option(id="B", label="ClickHouse", summary="columnar, fits the query shape"),
        ],
        open_questions=[OpenQuestion(text="Who owns the migration window?", directed_to="U3")],
        status=Status.DECIDED,
        participants=["U1", "U2", "U3"],
        records=[Record(kind="confluence", title="ADR: events db", url="https://wiki.example/x")],
        decision=Decision(
            option_id="B",
            summary="We go with ClickHouse for the events service.",
            rationale="The read pattern is analytical; Postgres would need a second copy anyway.",
            dissent=[Dissent(user_id="U2", argument="Operational cost of a second engine.")],
            owner="U1",
            decider="U1",
            confirmed_by="U1",
            confirmed_at=datetime(2026, 9, 12, 10, 30, tzinfo=UTC),
            consequences="One more engine to operate; the aggregation job gets simpler.",
            follow_ups=[
                FollowUp(text="Provision the cluster", assignee="U3"),
                FollowUp(text="Rewrite the ingestion job", issue_url="https://jira.example/browse/Q-2"),
            ],
        ),
        updated_at=datetime(2026, 9, 12, 11, 0, tzinfo=UTC),
    )
    thread = TrackedThread(
        ref=ThreadRef(platform="slack", channel_id="C1", thread_id="1757600000.1"),
        author_id="U1",
        requested_by="U1",
        permalink="https://slack.example/archives/C1/p1757600000",
        state=state,
    )
    return thread, state


def test_render_adr_english() -> None:
    thread, state = decided_thread()
    title, md = render_adr(thread, state, NAMES, lang="en")

    assert title == "ADR: Which database for the events service?"
    assert md.startswith("# ADR: Which database for the events service?\n")
    assert "**Date:** 2026-09-12" in md          # confirmed_at wins over updated_at
    assert "**Status:** Decided" in md
    assert "**Owner:** Alice" in md and "**Decider:** Alice" in md and "**Confirmed by:** Alice" in md
    assert thread.permalink in md

    for heading in ("## Context", "## Options", "## Decision", "## Rationale", "## Dissent",
                    "## Consequences", "## Follow-ups", "## Participants", "## Links"):
        assert heading in md, heading

    assert "- **B** — ClickHouse: columnar, fits the query shape — ✅ chosen" in md
    assert "- **A** — Postgres: one more managed instance\n" in md
    assert "- Bob: Operational cost of a second engine." in md
    assert "- [ ] Provision the cluster (Clara)" in md
    assert "- [ ] Rewrite the ingestion job (unassigned) — [https://jira.example/browse/Q-2]" in md
    assert "Alice, Bob, Clara" in md
    assert "- [ADR: events db](https://wiki.example/x)" in md
    assert "- Who owns the migration window? (→ Clara)" in md


def test_render_adr_russian_and_stable() -> None:
    thread, state = decided_thread()
    title_ru, ru = render_adr(thread, state, NAMES, lang="ru")
    _, en = render_adr(thread, state, NAMES, lang="en")

    assert title_ru.startswith("ADR: ")
    assert "**Статус:** Решено" in ru
    for heading in ("## Контекст", "## Варианты", "## Решение", "## Обоснование", "## Несогласие",
                    "## Последствия", "## Дальнейшие шаги", "## Участники", "## Ссылки"):
        assert heading in ru, heading
    assert "✅ выбрано" in ru
    assert ru != en

    # deterministic: same input, same bytes
    assert render_adr(thread, state, NAMES, lang="ru")[1] == ru


def test_render_adr_without_decision() -> None:
    thread, state = decided_thread()
    state.decision = None
    state.status = Status.DELIBERATING
    _, md = render_adr(thread, state, {}, lang="en")

    assert "## Decision" not in md and "## Follow-ups" not in md
    assert "**Status:** Deliberating" in md
    assert "- **A** — Postgres" in md and "✅" not in md
    assert "U1, U2, U3" in md          # unknown ids fall back to the id itself


def test_expired_note() -> None:
    _, state = decided_thread()
    state.status = Status.EXPIRED
    state.expired_summary = "Nobody came back after the incident week."
    note = render_expired_note(state, NAMES, "en")

    assert "**Expired without a decision**: Which database" in note
    assert "Nobody came back after the incident week." in note
    assert "- Who owns the migration window? (→ Clara)" in note

    note_ru = render_expired_note(state, NAMES, "ru")
    assert "Истекло без решения" in note_ru and "Открытые вопросы:" in note_ru

    state.open_questions = []
    assert "Nothing was left open." in render_expired_note(state, NAMES, "en")


def test_slug_and_title_helpers() -> None:
    assert slugify("ADR: Which database for the events service?") == "adr-which-database-for-the-events-service"
    assert slugify("Какую БД берём?") == "какую-бд-берём"
    assert slugify("   ") == "decision"
    assert title_from_markdown("# ADR: x\n\ntext\n") == "ADR: x"
    assert title_from_markdown("no heading", "fallback") == "fallback"

"""Slack Block Kit rendering.

Pure functions: domain objects in, Block Kit payloads out. No network, no LLM, no state. The card is rendered
from `CardState` deterministically — this module is the single place that decides what a card looks like.

Slack limits respected here: <=50 blocks per message, <=3000 chars per section text, <=5 elements per actions
block, <=2000 chars per button value, <=75 chars per button label.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from quorum.domain.models import (
    CardState,
    Claim,
    DecisionMemory,
    Status,
    ThreadRef,
    TrackedThread,
    now,
)
from quorum.domain.ui import Button, Form, Notice
from quorum.i18n import t

MAX_BLOCKS = 50
MAX_SECTION = 3000
MAX_ACTION_ELEMENTS = 5
MAX_BUTTON_LABEL = 75
NONE_VALUE = "__none__"          # Slack rejects an empty select value; this stands for "no option"

STATUS_EMOJI: dict[Status, str] = {
    Status.FRAMING: "📝",
    Status.DELIBERATING: "💬",
    Status.VOTING: "🗳",
    Status.DECIDED: "✅",
    Status.RECORDED: "📚",
    Status.STALLED: "🐌",
    Status.PARKED: "🅿️",
    Status.EXPIRED: "⌛",
    Status.SUPERSEDED: "🔁",
}

VERDICT_EMOJI = {"confirmed": "✅", "refuted": "❌", "unclear": "❔", "failed": "⚠️"}
CHECKING_EMOJI = "⏳"
NUDGED_EMOJI = "📩"
USER_EDIT_MARK = "✎"

PHASE_KEYS: dict[Status, str] = {
    Status.VOTING: "phase.voting",
    Status.DECIDED: "phase.decided",
    Status.RECORDED: "phase.recorded",
    Status.EXPIRED: "phase.expired",
}

_SLACK_USER_ID = re.compile(r"^[UWB][A-Z0-9]{2,}$")


# --------------------------------------------------------------------------------------------------
# tiny block builders
# --------------------------------------------------------------------------------------------------
def trim(text: str, limit: int = MAX_SECTION) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def section(text: str, accessory: dict[str, Any] | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "section", "text": {"type": "mrkdwn", "text": trim(text)}}
    if accessory:
        block["accessory"] = accessory
    return block


def context(*lines: str) -> dict[str, Any]:
    elements = [{"type": "mrkdwn", "text": trim(line, 2000)} for line in lines if line]
    return {"type": "context", "elements": elements[:10]}


def divider() -> dict[str, Any]:
    return {"type": "divider"}


def button(
    label: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    style: str = "default",
    url: str | None = None,
) -> dict[str, Any]:
    element: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": trim(label, MAX_BUTTON_LABEL), "emoji": True},
        "action_id": f"q:{action}",
        "value": json.dumps(payload or {}, ensure_ascii=False),
    }
    if style in ("primary", "danger"):
        element["style"] = style
    if url:
        element["url"] = url
    return element


def actions(elements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One or more actions blocks: Slack allows at most 5 elements each."""
    items = [e for e in elements if e]
    return [
        {"type": "actions", "elements": items[i : i + MAX_ACTION_ELEMENTS]}
        for i in range(0, len(items), MAX_ACTION_ELEMENTS)
    ]


def iter_buttons(blocks: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Every button element of a rendered message (actions elements + section accessories)."""
    for block in blocks:
        if block.get("type") == "actions":
            for element in block.get("elements", []):
                if element.get("type") == "button":
                    yield element
        accessory = block.get("accessory")
        if accessory and accessory.get("type") == "button":
            yield accessory


def slack_date(when: datetime, *, fmt: str = "{date_short} {time}") -> str:
    """Slack renders this in the reader's timezone; the fallback after `|` is what other clients show."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    epoch = int(when.timestamp())
    return f"<!date^{epoch}^{fmt}|{when.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}>"


def mention(user_id: str) -> str:
    return f"<@{user_id}>"


def who(user_id: str | None, names: dict[str, str] | None = None) -> str:
    """A real Slack id becomes a mention; anything else (tests, fake platform) falls back to the display name."""
    if not user_id:
        return "—"
    if _SLACK_USER_ID.match(user_id):
        return mention(user_id)
    return (names or {}).get(user_id) or user_id


def archive_url(thread_key: str) -> str:
    """`slack:<channel>:<ts>` -> https://slack.com/archives/<channel>/p<ts without dot>."""
    try:
        ref = ThreadRef.from_key(thread_key)
    except ValueError:
        return ""
    return f"https://slack.com/archives/{ref.channel_id}/p{ref.thread_id.replace('.', '')}"


def link(url: str, label: str) -> str:
    return f"<{url}|{label}>"


def phase_banner(status: Status, lang: str) -> str:
    """Text of the phase-change notice put on top of a re-posted card ('' when the phase is not announced)."""
    key = PHASE_KEYS.get(status)
    return t(lang, key) if key else ""


def _cap(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(blocks) <= MAX_BLOCKS:
        return blocks
    return blocks[: MAX_BLOCKS - 2] + [context("…"), blocks[-1]]


def _minutes_ago(when: datetime, lang: str) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (now() - when).total_seconds()
    if delta < 60:
        return t(lang, "just_now")
    return t(lang, "ago_min", n=int(delta // 60))


# --------------------------------------------------------------------------------------------------
# the card
# --------------------------------------------------------------------------------------------------
def render_card(
    thread: TrackedThread,
    state: CardState,
    *,
    lang: str,
    verifier_available: bool,
    recorders_available: bool,
    names: dict[str, str],
) -> tuple[list[dict[str, Any]], str]:
    """The one message Quorum keeps in the thread. Returns (blocks, fallback text)."""
    key = thread.key
    status = state.status
    blocks: list[dict[str, Any]] = []

    head = f"{STATUS_EMOJI.get(status, '•')} *{t(lang, f'status.{status.value}')}*"
    if state.deadline:
        head += f"   ⏱ {t(lang, 'deadline')}: {slack_date(state.deadline)}"
    blocks.append(section(head))

    if state.question:
        blocks.append(section(f"*{state.question}*"))
    if state.context:
        blocks.append(context(state.context))

    show_discussion = status not in (Status.DECIDED, Status.RECORDED, Status.SUPERSEDED)

    if show_discussion:
        blocks += _options_blocks(state, lang)
        blocks += _positions_blocks(state, lang, names)
        blocks += _open_questions_blocks(state, lang, names)

    if status is not Status.SUPERSEDED:
        blocks += _claims_blocks(state, lang, names, key=key, verifier_available=verifier_available)

    if state.decision_hint and status in (Status.FRAMING, Status.DELIBERATING, Status.STALLED):
        blocks += _hint_blocks(state, lang, names, key=key)

    if status is Status.VOTING:
        blocks += _voting_blocks(state, lang, names, key=key)
    elif status in (Status.DECIDED, Status.RECORDED):
        blocks += _decision_blocks(state, lang, names)
        if status is Status.RECORDED:
            blocks += _records_blocks(state, lang)
        elif recorders_available:
            blocks += actions([button(t(lang, "btn.record"), "record", {"t": key}, style="primary")])
    elif status is Status.STALLED:
        blocks.append(section(f"*{t(lang, 'section.stalled')}*\n{state.stalled_reason or '—'}"))
        blocks += actions(
            [
                button(t(lang, "btn.open_voting"), "open_voting", {"t": key}, style="primary"),
                button(t(lang, "btn.deadline"), "deadline", {"t": key}),
                button(t(lang, "btn.park"), "park", {"t": key}),
                button(t(lang, "btn.my_position"), "my_position", {"t": key}),
            ]
        )
    elif status is Status.PARKED:
        blocks += _parked_blocks(state, lang, names)
        blocks += actions([button(t(lang, "btn.unpark"), "unpark", {"t": key}, style="primary")])
    elif status is Status.EXPIRED:
        blocks.append(section(f"*{t(lang, 'section.expired')}*\n{state.expired_summary or '—'}"))
        blocks += actions([button(t(lang, "btn.unpark"), "unpark", {"t": key}, style="primary")])
    elif status is Status.SUPERSEDED:
        target = archive_url(state.superseded_by or "")
        line = f"*{t(lang, 'status.superseded')}*"
        if target:
            line += f" → {link(target, t(lang, 'btn.open_thread'))}"
        blocks.append(section(line))
    elif status in (Status.FRAMING, Status.DELIBERATING):
        blocks += actions(
            [
                button(t(lang, "btn.my_position"), "my_position", {"t": key}, style="primary"),
                button(t(lang, "btn.open_voting"), "open_voting", {"t": key}),
                button(t(lang, "btn.deadline"), "deadline", {"t": key}),
                button(t(lang, "btn.park"), "park", {"t": key}),
            ]
        )

    footer = f"{t(lang, 'updated')} {_minutes_ago(state.updated_at, lang)}"
    if state.last_llm_error:
        footer += f"   ⚠️ {t(lang, 'llm_failed')}"
    blocks.append(context(footer))

    text = trim(f"{state.question or t(lang, 'card.title')} — {t(lang, f'status.{status.value}')}", 300)
    return _cap(blocks), text


def _options_blocks(state: CardState, lang: str) -> list[dict[str, Any]]:
    if not state.options:
        return []
    lines = [f"*{t(lang, 'section.options')}*"]
    for option in state.options:
        line = f"*{option.id} · {option.label}*"
        if option.summary:
            line += f" — {option.summary}"
        lines.append(line)
    return [section("\n".join(lines))]


def _positions_blocks(state: CardState, lang: str, names: dict[str, str]) -> list[dict[str, Any]]:
    if not state.positions and not state.participants:
        return []
    lines = [f"*{t(lang, 'section.positions')}*"]
    for position in state.positions:
        option = state.option(position.option_id)
        where = f"{option.id}" if option else t(lang, "no_position")
        line = f"{who(position.user_id, names)} → {where}"
        if position.argument:
            line += f": {position.argument}"
        if position.source == "user":
            line += f" {USER_EDIT_MARK}"
        lines.append(line)
    for user_id in state.participants:
        if state.position_of(user_id) is None:
            lines.append(f"{who(user_id, names)} → {t(lang, 'no_position')}")
    if len(lines) == 1:
        return []
    return [section("\n".join(lines))]


def _open_questions_blocks(state: CardState, lang: str, names: dict[str, str]) -> list[dict[str, Any]]:
    questions = [q for q in state.unanswered() if not q.declined]
    if not questions:
        return []
    lines = [f"*{t(lang, 'section.open_questions')}*"]
    for question in questions:
        line = f"• {question.text}"
        if question.directed_to:
            line += f" (→ {who(question.directed_to, names)})"
        if question.nudged_at:
            line += f" {NUDGED_EMOJI}"
        lines.append(line)
    return [section("\n".join(lines))]


def _claim_badge(claim: Claim, lang: str) -> str:
    if claim.checking:
        return f"{CHECKING_EMOJI} {t(lang, 'verify.checking')}"
    if claim.verification:
        verdict = claim.verification.verdict
        return f"{VERDICT_EMOJI.get(verdict, '•')} {t(lang, f'verify.{verdict}')}"
    return ""


def _claims_blocks(
    state: CardState,
    lang: str,
    names: dict[str, str],
    *,
    key: str,
    verifier_available: bool,
) -> list[dict[str, Any]]:
    if not state.claims:
        return []
    blocks: list[dict[str, Any]] = [section(f"*{t(lang, 'section.claims')}*")]
    pending: list[dict[str, Any]] = []
    for index, claim in enumerate(state.claims, start=1):
        line = f"{index}. {claim.text} — {who(claim.by, names)}"
        badge = _claim_badge(claim, lang)
        if badge:
            line += f"  ·  {badge}"
        blocks.append(section(line))
        if claim.verification:
            extra = []
            if claim.verification.summary:
                extra.append(claim.verification.summary)
            sources = [link(s.url, s.title or s.url) for s in claim.verification.sources[:3]]
            if sources:
                extra.append(" · ".join(sources))
            if extra:
                blocks.append(context(*extra))
        elif verifier_available and not claim.checking:
            label = t(lang, "btn.verify")
            if len(state.claims) > 1:
                label = f"{label} {index}"
            pending.append(button(label, "verify", {"t": key, "claim_id": claim.id}))
    blocks += actions(pending)
    return blocks


def _hint_blocks(state: CardState, lang: str, names: dict[str, str], *, key: str) -> list[dict[str, Any]]:
    hint = state.decision_hint
    assert hint is not None
    option = state.option(hint.option_id)
    by = who(hint.by, names) if hint.by else "—"
    if option:
        text = t(lang, "hint.decision", option=f"{option.id} · {option.label}", by=by, quote=trim(hint.quote, 200))
    else:
        text = t(lang, "hint.decision_no_option", by=by, quote=trim(hint.quote, 200))
    confirm = button(
        t(lang, "btn.confirm"),
        "confirm",
        {"t": key, "option_id": hint.option_id} if hint.option_id else {"t": key},
        style="primary",
    )
    return [section(text, accessory=confirm)]


def _voting_blocks(state: CardState, lang: str, names: dict[str, str], *, key: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    vote_buttons = [
        button(t(lang, "btn.vote", label=option.id), "vote", {"t": key, "option_id": option.id})
        for option in state.options
    ]
    blocks += actions(vote_buttons)

    tally = state.tally()
    lines = [f"*{t(lang, 'section.votes')}*"]
    if tally:
        lines.append(" · ".join(t(lang, "tally", label=option_id, n=count) for option_id, count in tally.items()))
    lines.append(t(lang, "voted", n=len(state.votes), total=len(state.participants)))
    missing = state.voters_missing()
    if missing:
        lines.append(f"{t(lang, 'waiting_for')}: " + ", ".join(who(u, names) for u in missing))
    blocks.append(section("\n".join(lines)))

    blocks += actions(
        [
            button(t(lang, "btn.confirm"), "confirm", {"t": key}, style="primary"),
            button(t(lang, "btn.back_to_discussion"), "back_to_discussion", {"t": key}),
        ]
    )
    return blocks


def _decision_blocks(state: CardState, lang: str, names: dict[str, str]) -> list[dict[str, Any]]:
    decision = state.decision
    if decision is None:
        return []
    option = state.option(decision.option_id)
    lines = [f"*{t(lang, 'section.decision')}*"]
    if option:
        lines.append(f"*{option.id} · {option.label}*")
    if decision.summary:
        lines.append(decision.summary)
    if decision.rationale:
        lines.append(f"_{decision.rationale}_")
    blocks = [section("\n".join(lines))]

    if decision.dissent:
        blocks.append(
            section(
                f"*{t(lang, 'section.dissent')}*\n"
                + "\n".join(f"• {who(d.user_id, names)}: {d.argument}" for d in decision.dissent)
            )
        )
    if decision.follow_ups:
        follow_lines = []
        for follow_up in decision.follow_ups:
            line = f"• {follow_up.text}"
            if follow_up.assignee:
                line += f" — {who(follow_up.assignee, names)}"
            if follow_up.issue_url:
                line += f" {link(follow_up.issue_url, '↗')}"
            follow_lines.append(line)
        blocks.append(section(f"*{t(lang, 'section.follow_ups')}*\n" + "\n".join(follow_lines)))

    meta = []
    if decision.owner:
        meta.append(f"{t(lang, 'owner')}: {who(decision.owner, names)}")
    if decision.decider:
        meta.append(f"{t(lang, 'decider')}: {who(decision.decider, names)}")
    if decision.confirmed_by:
        meta.append(f"{t(lang, 'confirmed_by')} {who(decision.confirmed_by, names)}")
    if meta:
        blocks.append(context(" · ".join(meta)))
    return blocks


def _records_blocks(state: CardState, lang: str) -> list[dict[str, Any]]:
    if not state.records:
        return []
    lines = [f"*{t(lang, 'section.records')}*"]
    for record in state.records:
        lines.append(f"• {link(record.url, record.title or record.kind)} ({record.kind})")
    return [section("\n".join(lines))]


def _parked_blocks(state: CardState, lang: str, names: dict[str, str]) -> list[dict[str, Any]]:
    parked = state.parked
    if parked is None:
        return [section(f"*{t(lang, 'section.parked')}*")]
    lines = [f"*{t(lang, 'section.parked')}*", parked.reason or "—"]
    if parked.return_at:
        lines.append(f"⏱ {slack_date(parked.return_at)}")
    lines.append(f"{t(lang, 'confirmed_by')} {who(parked.by, names)}")
    return [section("\n".join(lines))]


# --------------------------------------------------------------------------------------------------
# notice / form / home
# --------------------------------------------------------------------------------------------------
def _notice_button(item: Button, thread_key: str | None) -> dict[str, Any]:
    payload = dict(item.payload)
    if thread_key:
        payload.setdefault("t", thread_key)
    return button(item.label, item.action, payload, style=item.style, url=item.url)


def render_notice(notice: Notice, *, thread_key: str | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [section(notice.text)]
    if notice.lines:
        blocks.append(context(*notice.lines))
    blocks += actions([_notice_button(b, thread_key) for b in notice.buttons])
    return _cap(blocks)


def _input_element(field: Any, lang: str) -> dict[str, Any]:
    element: dict[str, Any] = {"action_id": field.id}
    if field.kind == "select":
        element["type"] = "static_select"
        element["options"] = [
            {"text": {"type": "plain_text", "text": trim(label, 75), "emoji": True}, "value": value or NONE_VALUE}
            for value, label in field.options
        ]
        if field.initial is not None:
            wanted = field.initial or NONE_VALUE
            initial = next((o for o in element["options"] if o["value"] == wanted), None)
            if initial:
                element["initial_option"] = initial
    elif field.kind == "textarea":
        element["type"] = "plain_text_input"
        element["multiline"] = True
        if field.initial:
            element["initial_value"] = field.initial
    elif field.kind == "text":
        element["type"] = "plain_text_input"
        if field.initial:
            element["initial_value"] = field.initial
    elif field.kind == "date":
        element["type"] = "datepicker"
        if field.initial:
            element["initial_date"] = field.initial
    elif field.kind == "user":
        element["type"] = "users_select"
        if field.initial:
            element["initial_user"] = field.initial
    else:  # pragma: no cover - the Form contract has no other kinds
        element["type"] = "plain_text_input"
    if field.placeholder:
        element["placeholder"] = {"type": "plain_text", "text": trim(field.placeholder, 150), "emoji": True}
    return element


def render_form(form: Form, *, lang: str) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    if form.intro:
        blocks.append(section(form.intro))
    for field in form.fields:
        blocks.append(
            {
                "type": "input",
                "block_id": field.id,
                "optional": bool(field.optional),
                "label": {"type": "plain_text", "text": trim(field.label, 2000), "emoji": True},
                "element": _input_element(field, lang),
            }
        )
    return {
        "type": "modal",
        "callback_id": form.id,
        "private_metadata": json.dumps(form.payload, ensure_ascii=False),
        "title": {"type": "plain_text", "text": trim(form.title, 24), "emoji": True},
        "submit": {"type": "plain_text", "text": trim(form.submit_label or t(lang, "form.submit"), 24), "emoji": True},
        "blocks": _cap(blocks),
    }


def _home_lines(lines: list[str], per_block: int = 10) -> list[dict[str, Any]]:
    return [section("\n".join(lines[i : i + per_block])) for i in range(0, len(lines), per_block)]


def render_home(threads: list[TrackedThread], decisions: list[DecisionMemory], *, lang: str) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": trim(t(lang, "home.title"), 150), "emoji": True}}
    ]
    if not threads and not decisions:
        blocks.append(section(t(lang, "home.empty")))
        return {"type": "home", "blocks": _cap(blocks)}

    if threads:
        blocks.append(section(f"*{t(lang, 'home.active')}*"))
        lines = []
        for thread in threads[:30]:
            state = thread.state
            title = state.question or thread.root_text or thread.key
            url = thread.permalink or archive_url(thread.key)
            label = trim(title, 140)
            lines.append(
                f"{STATUS_EMOJI.get(state.status, '•')} *{t(lang, f'status.{state.status.value}')}* · "
                + (link(url, label) if url else label)
            )
        blocks += _home_lines(lines)

    if decisions:
        blocks.append(section(f"*{t(lang, 'home.decided')}*"))
        lines = []
        for memory in decisions[:30]:
            url = memory.record_url or memory.permalink or archive_url(memory.thread_key)
            label = trim(memory.title or memory.summary or memory.thread_key, 140)
            line = f"{memory.decided_at.date().isoformat()} · "
            if memory.option_label:
                line += f"*{memory.option_label}* · "
            line += link(url, label) if url else label
            if memory.status == "superseded":
                line += f" _({t(lang, 'home.superseded')})_"
            lines.append(line)
        blocks += _home_lines(lines)

    return {"type": "home", "blocks": _cap(blocks)}

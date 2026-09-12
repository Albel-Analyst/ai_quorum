"""Merge an LLM `Extraction` into the `CardState`. Deterministic; human-entered data wins over the model."""
from __future__ import annotations

import re
from datetime import datetime

from quorum.domain.models import CardState, Claim, DecisionHint, Message, OpenQuestion, Option, Position, Status
from quorum.llm.base import Extraction


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def _similar(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    wa, wb = set(na.split()), set(nb.split())
    return len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.6


def participants_of(messages: list[Message]) -> list[str]:
    seen: list[str] = []
    for m in messages:
        if not m.is_bot and m.user_id and m.user_id not in seen:
            seen.append(m.user_id)
    return seen


def merge(state: CardState, ext: Extraction, messages: list[Message], *, now: datetime) -> None:
    by_id = {m.id: m for m in messages}

    if ext.question.strip():
        state.question = ext.question.strip()
    if ext.context.strip():
        state.context = ext.context.strip()
    if ext.language:
        state.language = ext.language[:2].lower()

    # options: the LLM's list, but never drop an option that a human voted for or picked by hand
    protected = {v.option_id for v in state.votes} | {p.option_id for p in state.positions if p.source == "user" and p.option_id}
    new_options = [Option(id=o.id.strip().upper()[:2], label=o.label.strip(), summary=o.summary.strip()) for o in ext.options if o.label.strip()]
    ids = {o.id for o in new_options}
    for old in state.options:
        if old.id in protected and old.id not in ids:
            new_options.append(old)
            ids.add(old.id)
    state.options = new_options

    # positions: user-corrected ones survive; LLM ones are replaced
    kept = {p.user_id: p for p in state.positions if p.source == "user"}
    merged: list[Position] = []
    for p in ext.positions:
        if p.user_id in kept:
            merged.append(kept.pop(p.user_id))
            continue
        option_id = p.option_id.strip().upper()[:2] if p.option_id else None
        if option_id and option_id not in ids:
            option_id = None
        merged.append(Position(user_id=p.user_id, option_id=option_id, argument=p.argument.strip()[:300], source="llm"))
    merged.extend(kept.values())
    # one position per person: the model sometimes returns one per message — keep the latest, prefer one with an option
    by_user: dict[str, Position] = {}
    for p in merged:
        prev = by_user.get(p.user_id)
        if prev is None or prev.source != "user" and (p.option_id or not prev.option_id):
            by_user[p.user_id] = p if prev is None or p.argument else Position(user_id=p.user_id, option_id=p.option_id, argument=prev.argument, source=p.source)
    state.positions = list(by_user.values())

    # open questions: keep identity (id, asked_at, nudged) of the ones still open; new ones get asked_at from the message
    still_open: list[OpenQuestion] = []
    answered_texts = list(ext.answered_question_texts)
    for q in ext.open_questions:
        existing = next((o for o in state.open_questions if _similar(o.text, q.text)), None)
        if existing:
            existing.answered = False
            if q.directed_to and not existing.directed_to:
                existing.directed_to = q.directed_to
            still_open.append(existing)
            continue
        asked_at = now
        if q.asked_at_message_id and q.asked_at_message_id in by_id:
            asked_at = by_id[q.asked_at_message_id].at
        still_open.append(OpenQuestion(text=q.text.strip()[:300], directed_to=q.directed_to, asked_at=asked_at, asked_by=q.asked_by))
    open_ids = {q.id for q in still_open}
    for old in state.open_questions:
        if old.id not in open_ids and not old.answered:
            old.answered = True  # the LLM no longer lists it as open, or listed it as answered
        if old.id not in open_ids and old.answered:
            still_open.append(old)
    for txt in answered_texts:
        for q in still_open:
            if not q.answered and _similar(q.text, txt):
                q.answered = True
    state.open_questions = still_open

    # claims: keep verification results by text identity
    claims: list[Claim] = []
    for c in ext.claims:
        existing = next((o for o in state.claims if _similar(o.text, c.text)), None)
        claims.append(existing or Claim(text=c.text.strip()[:300], by=c.by))
    for old in state.claims:
        if old.verification and old not in claims:
            claims.append(old)  # a verified claim stays on the card even if the model stops listing it
    state.claims = claims[:8]

    # deadline typed in the thread; a deadline set by button is never overridden by the model
    if ext.deadline and state.deadline_source != "button":
        state.deadline = ext.deadline
        state.deadline_source = "text"

    # convergence hint: code renders a Confirm button; nothing is decided by the model
    if ext.decision_reached and state.status in (Status.FRAMING, Status.DELIBERATING, Status.STALLED):
        opt = ext.decision_option_id.strip().upper()[:2] if ext.decision_option_id else None
        state.decision_hint = DecisionHint(option_id=opt if opt in ids else None, by=ext.decision_by, quote=ext.decision_quote.strip()[:160])
    else:
        state.decision_hint = None

    # who is involved: derived from messages (code), widened by mentions and addressees
    state.participants = participants_of(messages)
    stakeholders = list(state.participants)
    for uid in [*ext.stakeholders_mentioned, *(q.directed_to for q in state.open_questions if q.directed_to)]:
        if uid and uid not in stakeholders:
            stakeholders.append(uid)
    for m in messages:
        for uid in m.mentions:
            if uid not in stakeholders:
                stakeholders.append(uid)
    state.stakeholders = stakeholders
    state.updated_at = now

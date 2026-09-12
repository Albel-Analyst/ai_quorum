"""ADR markdown renderer — the single source of the record text.

Deterministic, no LLM: the engine calls `render_adr` once and passes the result to every recorder through
`RecordInput.markdown`; recorders only convert or wrap it. Section headings follow the UI language: the keys that
already exist in `quorum.i18n` are reused, the ADR-only ones live in the small table below.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from quorum.domain.models import CardState, TrackedThread
from quorum.i18n import t

# ADR-only headings and labels (everything the card does not need, so it is not in i18n.STRINGS).
ADR_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "context": "Context",
        "rationale": "Rationale",
        "consequences": "Consequences",
        "participants": "Participants",
        "links": "Links",
        "open_questions": "Open questions",
        "date": "Date",
        "status": "Status",
        "thread": "Thread",
        "chosen": "chosen",
        "unassigned": "unassigned",
        "title_prefix": "ADR",
        "expired_title": "Expired without a decision",
        "expired_none": "Nothing was left open.",
        "none": "—",
    },
    "ru": {
        "context": "Контекст",
        "rationale": "Обоснование",
        "consequences": "Последствия",
        "participants": "Участники",
        "links": "Ссылки",
        "open_questions": "Открытые вопросы",
        "date": "Дата",
        "status": "Статус",
        "thread": "Тред",
        "chosen": "выбрано",
        "unassigned": "без исполнителя",
        "title_prefix": "ADR",
        "expired_title": "Истекло без решения",
        "expired_none": "Открытых вопросов не осталось.",
        "none": "—",
    },
}

# ADR heading -> i18n key, when the card already has the same word.
_I18N_HEADINGS = {
    "options": "section.options",
    "decision": "section.decision",
    "dissent": "section.dissent",
    "follow_ups": "section.follow_ups",
}


def _a(lang: str, key: str) -> str:
    table = ADR_STRINGS.get(lang) or ADR_STRINGS["en"]
    return table.get(key) or ADR_STRINGS["en"].get(key, key)


def h(lang: str, key: str) -> str:
    """Heading/label for an ADR section, from i18n when the card uses the same word."""
    i18n_key = _I18N_HEADINGS.get(key)
    if i18n_key:
        return t(lang, i18n_key)
    return _a(lang, key)


def _cap(text: str) -> str:
    """i18n labels are card-cased ("confirmed by"); ADR metadata wants a leading capital."""
    return text[:1].upper() + text[1:] if text else text


def _name(names: dict[str, str], user_id: str | None) -> str:
    if not user_id:
        return ""
    return names.get(user_id, user_id)


def _day(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def slugify(text: str, *, max_len: int = 60) -> str:
    """Filename-safe slug; transliterates nothing, keeps unicode letters, so Russian titles stay readable."""
    normalized = unicodedata.normalize("NFKC", text or "").strip().lower()
    slug = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_len].strip("-") or "decision"


def title_from_markdown(markdown: str, fallback: str = "Decision") -> str:
    """Recorders get only the rendered markdown; the ADR title is its first `# ` heading."""
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _section(out: list[str], heading: str, body: str | list[str]) -> None:
    lines = [body] if isinstance(body, str) else list(body)
    lines = [line for line in lines if line.strip()]
    if not lines:
        return
    out.append(f"## {heading}")
    out.append("")
    out.extend(lines)
    out.append("")


def render_adr(thread: TrackedThread, state: CardState, names: dict[str, str], *, lang: str) -> tuple[str, str]:
    """Render the decision as an ADR. Returns (title, markdown). Pure function: same input -> same bytes."""
    decision = state.decision
    question = (state.question or thread.root_text or "").strip()
    title = f"{_a(lang, 'title_prefix')}: {question}" if question else _a(lang, "title_prefix")

    out: list[str] = [f"# {title}", ""]

    # --- metadata line -------------------------------------------------------------------------
    at = (decision.confirmed_at if decision and decision.confirmed_at else state.updated_at)
    meta = [
        f"**{_a(lang, 'date')}:** {_day(at)}",
        f"**{_a(lang, 'status')}:** {t(lang, f'status.{state.status.value}')}",
    ]
    if thread.permalink:
        meta.append(f"**{_a(lang, 'thread')}:** [{thread.permalink}]({thread.permalink})")
    if decision:
        if decision.owner:
            meta.append(f"**{t(lang, 'owner')}:** {_name(names, decision.owner)}")
        if decision.decider:
            meta.append(f"**{t(lang, 'decider')}:** {_name(names, decision.decider)}")
        if decision.confirmed_by:
            meta.append(f"**{_cap(t(lang, 'confirmed_by'))}:** {_name(names, decision.confirmed_by)}")
    out.append(" · ".join(meta))
    out.append("")

    # --- body ----------------------------------------------------------------------------------
    _section(out, h(lang, "context"), state.context.strip())

    chosen_id = decision.option_id if decision else None
    options: list[str] = []
    for option in state.options:
        line = f"- **{option.id}** — {option.label}"
        if option.summary:
            line += f": {option.summary}"
        if chosen_id and option.id == chosen_id:
            line += f" — ✅ {_a(lang, 'chosen')}"
        options.append(line)
    _section(out, h(lang, "options"), options)

    if decision:
        _section(out, h(lang, "decision"), decision.summary.strip())
        _section(out, h(lang, "rationale"), decision.rationale.strip())
        _section(
            out,
            h(lang, "dissent"),
            [f"- {_name(names, d.user_id)}: {d.argument}" for d in decision.dissent],
        )
        _section(out, h(lang, "consequences"), decision.consequences.strip())

        follow_ups: list[str] = []
        for fu in decision.follow_ups:
            who = _name(names, fu.assignee) or _a(lang, "unassigned")
            line = f"- [ ] {fu.text} ({who})"
            if fu.issue_url:
                line += f" — [{fu.issue_url}]({fu.issue_url})"
            follow_ups.append(line)
        _section(out, h(lang, "follow_ups"), follow_ups)

    open_questions = [f"- {q.text}" + (f" (→ {_name(names, q.directed_to)})" if q.directed_to else "")
                      for q in state.unanswered()]
    _section(out, _a(lang, "open_questions"), open_questions)

    participants = ", ".join(_name(names, uid) for uid in state.participants)
    _section(out, _a(lang, "participants"), participants)

    links = [f"- [{r.title or r.kind}]({r.url})" for r in state.records if r.url]
    if thread.permalink:
        links.append(f"- [{_a(lang, 'thread')}]({thread.permalink})")
    _section(out, _a(lang, "links"), links)

    markdown = "\n".join(out).rstrip() + "\n"
    return title, markdown


def render_expired_note(state: CardState, names: dict[str, str], lang: str = "en") -> str:
    """Short markdown for an expired card: what stayed open. Used by the engine, never posted by a recorder."""
    out: list[str] = []
    question = state.question.strip()
    heading = _a(lang, "expired_title")
    out.append(f"**{heading}**" + (f": {question}" if question else ""))
    out.append("")

    if state.expired_summary.strip():
        out.append(state.expired_summary.strip())
        out.append("")

    unanswered = state.unanswered()
    if unanswered:
        out.append(f"{_a(lang, 'open_questions')}:")
        for q in unanswered:
            who = _name(names, q.directed_to)
            out.append(f"- {q.text}" + (f" (→ {who})" if who else ""))
    else:
        out.append(_a(lang, "expired_none"))

    return "\n".join(out).rstrip() + "\n"

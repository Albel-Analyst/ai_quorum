"""System prompts and prompt formatting for the LLM layer.

Nothing here talks to a network. `openai_provider.py` composes (system, user) pairs from these builders,
`fake.py` ignores them. Keeping the wording here makes the prompts diffable and testable.

Message wire format the model sees (one header line per message, bot messages omitted; a multi-line
message keeps its own line breaks after the header, which is how option lists like "A) ...\nB) ..."
survive into the prompt):

    [<message id>] <@U0C18LRS4SJ> (Display Name) 2026-09-12T10:22+00:00: text

Slack-style mentions inside the text (`<@U123>`) are kept verbatim so the model can resolve
"who is this question addressed to" and return the raw user id.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from quorum.config import settings
from quorum.domain.models import CardState, Message

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quorum.domain.models import DecisionMemory
    from quorum.llm.base import SourceSnippet

# The thread timezone assumption used to resolve relative deadlines ("by Wednesday").
DEFAULT_TZ = settings.timezone

# State keys the LLM has no business seeing: it never counts votes, never writes records,
# and its own bookkeeping fields would only be noise in the context.
STATE_EXCLUDE = {"votes", "records", "last_llm_ok_at", "last_llm_error", "updated_at"}


# --------------------------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------------------------
def _ts(at: datetime) -> str:
    moment = at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)
    return moment.isoformat(timespec="minutes")


def _display_name(message: Message, names: dict[str, str]) -> str:
    return names.get(message.user_id) or message.user_name or "unknown"


def format_messages(
    messages: Iterable[Message],
    names: dict[str, str],
    new_ids: Iterable[str] | None = None,
) -> str:
    """Render a transcript for the model. Bot messages (including Quorum's own card) are dropped.

    `new_ids` marks the messages that arrived since the last extraction with a leading `NEW `.
    Message text is kept verbatim (line breaks included) so lists of options stay readable.
    """
    fresh = set(new_ids or ())
    lines: list[str] = []
    for message in messages:
        if message.is_bot:
            continue
        marker = "NEW " if message.id in fresh else ""
        head = f"{marker}[{message.id}] <@{message.user_id}> ({_display_name(message, names)}) {_ts(message.at)}"
        lines.append(f"{head}: {message.text.strip()}")
    return "\n".join(lines)


def _prune(value: object) -> object:
    """Drop nulls and empty containers so the state JSON stays small."""
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None and v != [] and v != {} and v != ""}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def state_for_prompt(state: CardState) -> str:
    """Compact JSON of the current card, minus the fields the LLM does not need."""
    data = state.model_dump(mode="json", exclude=STATE_EXCLUDE)
    return json.dumps(_prune(data), ensure_ascii=False, separators=(",", ":"))


def _names_block(names: dict[str, str]) -> str:
    if not names:
        return ""
    known = ", ".join(f"{uid}={name}" for uid, name in sorted(names.items()))
    return f"\nPeople: {known}\n"


def _decisions_block(decisions: Iterable[DecisionMemory]) -> str:
    rows = []
    for d in decisions:
        rows.append(
            json.dumps(
                {
                    "thread_key": d.thread_key,
                    "title": d.title,
                    "summary": d.summary,
                    "option_label": d.option_label,
                    "decided_at": d.decided_at.isoformat(),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(rows) if rows else "(no recorded decisions)"


# --------------------------------------------------------------------------------------------
# system prompts
# --------------------------------------------------------------------------------------------
EXTRACT_SYSTEM = """\
You are the extraction engine of Quorum, a bot that never writes messages into a chat. It maintains ONE
decision card for a thread. You structure what people said. You never argue, never advise, never take a
side, never invent facts that are not in the thread.

You get: the current card state as JSON, and the full thread transcript. Lines prefixed with `NEW ` arrived
since the last extraction; the rest you have already seen. Return the WHOLE card content, not a diff:
re-state everything that is still true, plus what the new messages add or change.

Rules:

1. question — the decision being made, as one neutral question. No option is favoured by the wording.
   If the card already has a good question, keep it (only rewrite it if the thread clearly drifted).
2. context — 1-2 lines of background. Facts only, no opinions, no recommendations.
3. language — ISO 639-1 code of the language the people are writing in ("ru", "en", "uz", ...).
   Write EVERY text you produce (question, context, labels, arguments, questions, claims) in that language.
4. options — the alternatives actually on the table. Ids are stable single letters A, B, C...
   NEVER renumber: an option that already exists in the state keeps its id, even if its label improves.
   New options get the next free letter. Do not invent options nobody proposed. label = 2-6 words,
   summary = one line on what it means in practice.
5. positions — one per person who expressed a leaning or an argument. user_id is the raw platform id
   exactly as it appears in the transcript (U0C18LRS4SJ), never the display name, never `<@...>`.
   option_id = the option they lean to, or null if they only argued without picking one.
   argument = ONE line, in the author's own words and language, compressed but not reinterpreted.
   IMPORTANT: positions in the state with "source":"user" were typed by the human themselves through the
   form. Return them EXACTLY as they are (same option_id, same argument, keep source "user") unless that
   same person's own later message clearly changes their mind.
6. open_questions — questions that are still unanswered. If a question is addressed to a specific person
   ("@Bekzod how much does B cost?"), set directed_to to that person's user id (from the `<@U...>` mention).
   asked_by = the author's user id, asked_at_message_id = the id in square brackets of that message.
   Do not list rhetorical questions or questions that were already answered in the thread.
7. answered_question_texts — the texts of questions that were open in the state and got answered by the
   new messages. Copy the text from the state verbatim so the code can match them.
8. claims — statements a stranger could CHECK ON THE WEB: prices, versions, release dates, licence terms,
   published limits and quotas, third-party product facts. A web search must be able to settle it.
   NOT opinions ("A is nicer"). NOT anything about us: our data volumes, our traffic, our cluster, our
   deadlines ("у нас 300 млн строк в месяц", "our cluster has 3 nodes") are internal facts, not claims.
   NOT plans or intentions. If nothing is web-checkable, return [].
   by = the user id who said it.
9. deadline — only if the text states one. Resolve relative wording ("by Wednesday", "к пятнице") against
   the `now` given in the user message; the thread timezone is {tz} unless the messages say otherwise.
   Return an absolute UTC datetime. If no deadline is stated, null.
10. decision_reached — true ONLY if the thread itself converged ("ok, let's go with B", "решено, берём B").
    Then decision_option_id, decision_by (the user id who called it) and decision_quote (a short verbatim
    quote). A mere majority of opinions is NOT convergence. Nothing is decided by you: a human confirms.
11. stakeholders_mentioned — user ids that were mentioned in the thread but have not written anything.

Empty is better than wrong. If something is unclear, leave it out.\
""".replace("{tz}", DEFAULT_TZ)

RECORD_SYSTEM = """\
You write the decision record (ADR) for a decision that a human has just confirmed. You write it from the
ARGUMENTS in the thread, not from the vote count. A record that says "3 votes against 1" is a bad record;
a record that says why the chosen option won on the merits is a good one.

Write in the language of the thread (see `language` in the state).

- title — the decision as a short noun phrase, not a question.
- summary — one paragraph: what was decided, concretely.
- rationale — why: the arguments that carried it, and what trade-off was accepted.
- dissent — everyone who argued against the chosen option, with their argument in one line, in their own
  words. user_id = the raw platform id. Never soften or drop a dissent; an honest record keeps it.
- consequences — what changes because of this, including what is now closed for discussion.
- follow_ups — concrete next actions that fell out of the thread, imperative, one line each.
  follow_up_assignees — same length as follow_ups, the user id of the owner of each action or null.
- owner — the user id who owns the outcome, if the thread makes it clear; otherwise null.

Invent nothing. If there were no follow-ups, return an empty list.\
"""

STALLED_SYSTEM = """\
This decision card has gone quiet. In ONE short line (max ~140 characters), in the language of the card,
say what is actually blocking it: the unanswered question and who owes the answer, or the missing
information, or the disagreement that nobody has addressed. Name people by `<@user_id>`.
No greetings, no advice, no calls to action, no emoji. Just the blocker.\
"""

BREWING_SYSTEM = """\
You are a cheap gate. Given the last messages of a chat channel, decide whether the people are MAKING A
DECISION — weighing two or more alternatives, or asking the group to pick something. If yes, Quorum will
offer to track it, so a false positive is annoying and a miss is cheap.

Be conservative. brewing=true only when you can name the choice as one question, and at least two distinct
alternatives (or one proposal that someone can accept or reject) are visible in the text.

NOT a decision: status updates, incident chatter, questions with a factual answer, jokes, sharing links,
planning who does an already-decided task, small talk.

confidence — how sure you are, 0..1. Below 0.7 means the caller ignores you, so use it honestly.
question — if brewing, the choice as one neutral question in the language of the channel; otherwise "".\
"""

CONTRADICTION_SYSTEM = """\
You get one new chat message and a short list of decisions this team recorded earlier. Decide whether the
message goes AGAINST one of them.

Say contradicts=true only for a statement of intent or of fact that is incompatible with a recorded
decision ("I'm putting it on MySQL" when the team decided Postgres; "we don't have a retention policy" when
one was decided). Then set decision_thread_key to that decision and give one line in `why`, in the language
of the message.

Say contradicts=false for everything else, and this covers most messages: questions, thinking out loud,
discussing the decision, mentioning the topic, work that merely touches the same area, and anything about a
subject none of the decisions cover. When in doubt, false.\
"""

JUDGE_SYSTEM = """\
You judge one factual claim against the web sources you are given, and nothing else. You do not use your
own knowledge of the facts; if the sources do not settle it, the verdict is "unclear".

- confirmed — the sources state the claim (or something that entails it).
- refuted — the sources state something incompatible with the claim.
- unclear — the sources are silent, partial, contradictory, or only tangentially related.

summary — ONE line in the language of the claim: what the sources actually say, with the number/version/date
that matters. No hedging boilerplate.
supporting_urls — 2-3 urls, taken verbatim from the given sources, that a human should open first.\
"""


# --------------------------------------------------------------------------------------------
# user-message builders
# --------------------------------------------------------------------------------------------
def extract_user(
    state: CardState,
    new_messages: Iterable[Message],
    all_messages: Iterable[Message],
    names: dict[str, str],
    now: datetime | None = None,
) -> str:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    new_ids = [m.id for m in new_messages]
    transcript = format_messages(all_messages, names, new_ids=new_ids)
    return (
        f"now (UTC): {moment.isoformat(timespec='seconds')}  |  thread timezone: {DEFAULT_TZ}\n"
        f"{_names_block(names)}"
        f"\nCurrent card state (JSON):\n{state_for_prompt(state)}\n"
        f"\nThread transcript ({len(new_ids)} new message(s), marked NEW):\n{transcript or '(empty)'}\n"
    )


def record_user(state: CardState, messages: Iterable[Message], names: dict[str, str]) -> str:
    return (
        f"{_names_block(names)}"
        f"\nCard state (JSON), including the confirmed decision:\n{state_for_prompt(state)}\n"
        f"\nThread transcript:\n{format_messages(messages, names) or '(empty)'}\n"
        f"\nWrite the record."
    )


def stalled_user(state: CardState, names: dict[str, str]) -> str:
    return (
        f"{_names_block(names)}"
        f"\nCard state (JSON):\n{state_for_prompt(state)}\n"
        f"\nOne line: what blocks this decision."
    )


def brewing_user(messages: Iterable[Message], names: dict[str, str]) -> str:
    return (
        f"{_names_block(names)}"
        f"\nLast messages of the channel:\n{format_messages(messages, names) or '(empty)'}\n"
        f"\nIs a decision being made here?"
    )


def contradiction_user(message: Message, decisions: Iterable[DecisionMemory]) -> str:
    names = {message.user_id: message.user_name} if message.user_name else {}
    return (
        f"Recorded decisions (one JSON per line):\n{_decisions_block(decisions)}\n"
        f"\nNew message:\n{format_messages([message], names) or '(empty)'}\n"
        f"\nDoes it contradict one of the decisions?"
    )


def judge_user(claim: str, context: str, sources: Iterable[SourceSnippet]) -> str:
    blocks = [f"[{i}] {s.title}\n{s.url}\n{s.snippet}" for i, s in enumerate(sources, start=1)]
    body = "\n\n".join(blocks) if blocks else "(no sources found)"
    return (
        f"Claim: {claim}\n"
        f"Context of the discussion: {context or '(none)'}\n"
        f"\nSources:\n{body}\n"
        f"\nJudge the claim against these sources only."
    )

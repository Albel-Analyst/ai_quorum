"""Offline provider: same three protocols, no network, fully deterministic.

Two modes:

* **scripted** — `FakeProvider([extraction1, extraction2])` returns them in order and repeats the last one
  forever. This is how the engine tests drive a thread through a scenario.
* **heuristic** (default) — builds a plausible `Extraction` from the messages with dumb regexes. Good
  enough for the offline demo and for tests that only care that the pipeline is wired.

`set_fail(True)` turns every method into an `LLMError` so the degraded paths can be tested.
Every call is appended to `self.calls` as `(method_name, payload_dict)`.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from quorum.domain.models import CardState, DecisionMemory, Message
from quorum.llm.base import (
    ClaimJudgement,
    ContradictionCheck,
    DecisionBrewing,
    DecisionRecordDraft,
    ExtractedClaim,
    ExtractedOpenQuestion,
    ExtractedOption,
    ExtractedPosition,
    Extraction,
    SourceSnippet,
)
from quorum.llm.openai_provider import LLMError

LETTERS = "ABCDEFGHIJ"

MENTION_RE = re.compile(r"<@([A-Za-z0-9_]+)>")
# "A) label"
_OPT_PAREN_RE = re.compile(r"^\s*([A-Za-z])\s*\)\s*(.+?)\s*$")
# "option A: label" / "вариант B - label" / "вариант: label"
_OPT_WORD_RE = re.compile(r"^\s*(?:option|вариант|variant)\s*([A-Za-z])?\s*[:)\-–—.]?\s*(.+?)\s*$", re.IGNORECASE)
# a letter referenced inside running text: "A)", "option A", "вариант B", "за A"
_REF_RE = re.compile(r"(?:^|[\s(«\"'])(?:option|вариант|variant|за|for)?\s*([A-Za-z])(?:\)|\b)", re.IGNORECASE)

_CLAIM_MARKERS = (
    "$", "€", "₽", "%", "usd", "eur", "руб", "сум", "rub", "gb", "гб", "tb", "тб", "ms", "мс",
    "version", "версия", "версии", "v.", "release", "релиз", "вышел", "вышла", "released",
    "limit", "лимит", "rps", "qps", "req/s", "год", "года", "20",
)

_DECIDED_RE = (
    re.compile(r"let'?s\s+go\s+with\s+([A-Za-z])\b", re.IGNORECASE),
    re.compile(r"иде[мй]\s+(?:с|на|за)\s+([A-Za-z])\b", re.IGNORECASE),
    re.compile(r"бер[её]м\s+([A-Za-z])\b", re.IGNORECASE),
    re.compile(r"решено[,:\s]*(?:бер[её]м|иде[мй]\s+(?:с|на))?\s*([A-Za-z])?\b", re.IGNORECASE),
)

_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")


def _one_line(text: str, limit: int = 180) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _first_line(text: str) -> str:
    for raw in text.splitlines():
        if raw.strip():
            return _one_line(raw)
    return ""


def _language(messages: Iterable[Message]) -> str:
    return "ru" if any(_CYRILLIC_RE.search(m.text) for m in messages) else "en"


def _strip_mentions(text: str) -> str:
    return _one_line(MENTION_RE.sub("", text))


class FakeProvider:
    """Deterministic stand-in for `OpenAIProvider`."""

    def __init__(self, scripted: list[Extraction] | None = None) -> None:
        self.scripted: list[Extraction] = list(scripted or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._extract_n = 0
        self._fail = False

    def set_fail(self, fail: bool = True) -> None:
        """Make every subsequent call raise `LLMError`."""
        self._fail = fail

    def _record(self, method: str, **payload: Any) -> None:
        self.calls.append((method, payload))
        if self._fail:
            raise LLMError(f"FakeProvider is in fail mode ({method})")

    # -- Extractor ----------------------------------------------------------------------------
    async def extract(
        self,
        state: CardState,
        new_messages: list[Message],
        all_messages: list[Message],
        names: dict[str, str],
    ) -> Extraction:
        self._record("extract", new=len(new_messages), total=len(all_messages))
        if self.scripted:
            index = min(self._extract_n, len(self.scripted) - 1)
            self._extract_n += 1
            return self.scripted[index].model_copy(deep=True)
        return self._heuristic(state, all_messages)

    async def write_record(
        self, state: CardState, messages: list[Message], names: dict[str, str]
    ) -> DecisionRecordDraft:
        self._record("write_record", messages=len(messages))
        chosen = state.option(state.decision.option_id) if state.decision else None
        label = chosen.label if chosen else (state.options[0].label if state.options else state.question)
        dissent = [
            ExtractedPosition(user_id=p.user_id, option_id=p.option_id, argument=p.argument)
            for p in state.positions
            if chosen is not None and p.option_id is not None and p.option_id != chosen.id
        ]
        return DecisionRecordDraft(
            title=_one_line(state.question or label or "Decision", 120),
            summary=f"{label}." if label else "Decided.",
            rationale="; ".join(p.argument for p in state.positions if p.argument) or "No arguments recorded.",
            dissent=dissent,
            consequences="",
            follow_ups=[q.text for q in state.unanswered()],
            follow_up_assignees=[q.directed_to for q in state.unanswered()],
            owner=state.decision.owner if state.decision else None,
        )

    async def stalled_summary(self, state: CardState, names: dict[str, str]) -> str:
        self._record("stalled_summary")
        open_qs = state.unanswered()
        if open_qs:
            question = open_qs[0]
            who = f"<@{question.directed_to}> " if question.directed_to else ""
            return _one_line(f"{who}{question.text}", 140)
        undecided = [p.user_id for p in state.positions if p.option_id is None]
        if undecided:
            return _one_line("No option picked by " + ", ".join(f"<@{u}>" for u in undecided), 140)
        return "No new messages; nobody confirmed the decision."

    # -- Classifier ---------------------------------------------------------------------------
    async def decision_brewing(self, messages: list[Message], names: dict[str, str]) -> DecisionBrewing:
        self._record("decision_brewing", messages=len(messages))
        window = [m for m in messages if not m.is_bot][-15:]
        options = self._options(window, root_id=None)
        if len(options) >= 2:
            return DecisionBrewing(brewing=True, confidence=0.8, question=_first_line(window[0].text) if window else "")
        return DecisionBrewing(brewing=False, confidence=0.2, question="")

    async def contradiction(self, message: Message, decisions: list[DecisionMemory]) -> ContradictionCheck:
        self._record("contradiction", decisions=len(decisions))
        text = message.text.lower()
        if text.rstrip().endswith("?"):
            return ContradictionCheck(contradicts=False)
        for decision in decisions:
            label = (decision.option_label or "").strip().lower()
            if label and label not in text:
                continue
            if label and ("не " in text or "not " in text or "instead" in text or "вместо" in text):
                return ContradictionCheck(
                    contradicts=True,
                    decision_thread_key=decision.thread_key,
                    why=_one_line(f"goes against: {decision.title}", 140),
                )
        return ContradictionCheck(contradicts=False)

    # -- ClaimJudge ---------------------------------------------------------------------------
    async def judge_claim(self, claim: str, context: str, sources: list[SourceSnippet]) -> ClaimJudgement:
        self._record("judge_claim", claim=claim, sources=len(sources))
        if not sources:
            return ClaimJudgement(verdict="unclear", summary="No sources.", supporting_urls=[])
        return ClaimJudgement(
            verdict="confirmed",
            summary=_one_line(sources[0].snippet or sources[0].title, 140),
            supporting_urls=[s.url for s in sources[:3]],
        )

    # -- heuristics ---------------------------------------------------------------------------
    def _heuristic(self, state: CardState, messages: list[Message]) -> Extraction:
        human = [m for m in messages if not m.is_bot]
        if not human:
            return Extraction(question=state.question, language=state.language or "en")

        root = human[0]
        question = _first_line(root.text) or state.question
        options = self._options(human, root_id=root.id)
        option_ids = {o.id for o in options}
        speakers = [m.user_id for m in human]

        positions: list[ExtractedPosition] = []
        for user_id in dict.fromkeys(speakers):
            said = [m for m in human if m.user_id == user_id]
            positions.append(
                ExtractedPosition(
                    user_id=user_id,
                    option_id=self._referenced_option(said, option_ids),
                    argument=_strip_mentions(said[-1].text),
                )
            )

        open_questions = [
            ExtractedOpenQuestion(
                text=_one_line(m.text),
                directed_to=MENTION_RE.search(m.text).group(1),  # type: ignore[union-attr]
                asked_by=m.user_id,
                asked_at_message_id=m.id,
            )
            for m in human
            if m.text.rstrip().endswith("?") and MENTION_RE.search(m.text)
        ]

        claims = [
            ExtractedClaim(text=_strip_mentions(m.text), by=m.user_id)
            for m in human
            if self._is_claim(m.text)
        ]

        decided_by, decided_option, quote = self._decision(human, option_ids)
        mentioned = {uid for m in human for uid in MENTION_RE.findall(m.text)} | {
            uid for m in human for uid in m.mentions
        }

        return Extraction(
            question=question,
            context=_one_line(root.text, 200),
            language=_language(human),
            options=options,
            positions=positions,
            open_questions=open_questions,
            answered_question_texts=[],
            claims=claims,
            deadline=None,
            decision_reached=decided_by is not None,
            decision_option_id=decided_option,
            decision_by=decided_by,
            decision_quote=quote,
            stakeholders_mentioned=sorted(mentioned - set(speakers)),
        )

    @staticmethod
    def _options(messages: list[Message], root_id: str | None) -> list[ExtractedOption]:
        """Explicit "A) ..." / "option ..." markers first; otherwise two generic ones from the replies."""
        found: list[tuple[str | None, str]] = []
        for message in messages:
            for raw in message.text.splitlines():
                line = raw.strip(" -•*\t")
                if not line:
                    continue
                paren = _OPT_PAREN_RE.match(line)
                if paren and len(paren.group(1)) == 1 and paren.group(2):
                    found.append((paren.group(1).upper(), _one_line(paren.group(2), 60)))
                    continue
                word = _OPT_WORD_RE.match(line)
                if word and word.group(2):
                    letter = word.group(1).upper() if word.group(1) else None
                    found.append((letter, _one_line(word.group(2), 60)))

        options: list[ExtractedOption] = []
        seen_labels: set[str] = set()
        used: set[str] = set()
        for letter, label in found:
            key = label.lower()
            if key in seen_labels:
                continue
            oid = letter if letter and letter not in used else next(c for c in LETTERS if c not in used)
            used.add(oid)
            seen_labels.add(key)
            options.append(ExtractedOption(id=oid, label=label, summary=""))
        if options:
            return sorted(options, key=lambda o: o.id)

        generic: list[ExtractedOption] = []
        for message in messages:
            if message.id == root_id:
                continue
            label = _one_line(_strip_mentions(message.text), 60)
            if not label or any(o.label.lower() == label.lower() for o in generic):
                continue
            generic.append(ExtractedOption(id=LETTERS[len(generic)], label=label, summary=""))
            if len(generic) == 2:
                break
        return generic

    @staticmethod
    def _referenced_option(said: list[Message], option_ids: set[str]) -> str | None:
        """Last option letter the person referred to, if it is a real option."""
        picked: str | None = None
        for message in said:
            for match in _REF_RE.finditer(message.text):
                letter = match.group(1).upper()
                if letter in option_ids:
                    picked = letter
        return picked

    @staticmethod
    def _is_claim(text: str) -> bool:
        if not any(ch.isdigit() for ch in text):
            return False
        low = text.lower()
        return any(marker in low for marker in _CLAIM_MARKERS)

    @staticmethod
    def _decision(messages: list[Message], option_ids: set[str]) -> tuple[str | None, str | None, str]:
        for message in reversed(messages):
            for pattern in _DECIDED_RE:
                match = pattern.search(message.text)
                if not match:
                    continue
                letter = (match.group(1) or "").upper() if match.lastindex else ""
                option = letter if letter in option_ids else None
                return message.user_id, option, _one_line(match.group(0), 80)
        return None, None, ""

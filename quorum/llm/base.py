"""LLM contracts. The engine depends on these protocols; `quorum/llm/openai_provider.py` implements them.

Design rule: the LLM never writes the card. It gets (current state as JSON, new messages) and returns an
`Extraction` under a strict schema. Deterministic code merges it, runs the state machine and renders.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from quorum.domain.models import CardState, DecisionMemory, Message


class ExtractedOption(BaseModel):
    id: str = Field(description="Stable single-letter id: A, B, C... Keep ids of existing options unchanged.")
    label: str = Field(description="2-6 words")
    summary: str = Field(default="", description="One line: what it means in practice")


class ExtractedPosition(BaseModel):
    user_id: str = Field(description="Platform user id exactly as given in the messages, e.g. U0C18LRS4SJ")
    option_id: str | None = Field(default=None, description="Option id the person leans to, or null if unclear")
    argument: str = Field(default="", description="Their argument in one line, in their own words, same language")


class ExtractedOpenQuestion(BaseModel):
    text: str
    directed_to: str | None = Field(default=None, description="User id the question is addressed to, if any")
    asked_by: str | None = None
    asked_at_message_id: str | None = Field(default=None, description="id of the message where it was asked")


class ExtractedClaim(BaseModel):
    text: str = Field(description="A checkable statement about the outside world (prices, versions, dates, limits)")
    by: str = Field(description="user id")
    source_message_ids: list[str] = Field(default_factory=list)
    materiality: Literal["material", "incidental"] = "incidental"
    disputed: bool = False
    confidence: float = Field(default=0, ge=0, le=1)


class ProposedCommitment(BaseModel):
    owner: str
    action: str
    due_at: datetime | None = None
    source_message_id: str
    quote: str = Field(description="Verbatim personal commitment, never an aspiration or hypothetical")
    explicit_personal_promise: bool = False
    confidence: float = Field(default=0, ge=0, le=1)


class ProposedBlocker(BaseModel):
    description: str
    dependency_owner: str | None = None
    next_expected_event: str
    source_message_id: str


class ProposedLoopControl(BaseModel):
    intent: Literal["cancel", "defer", "supersede"]
    source_message_id: str
    quote: str


class Extraction(BaseModel):
    """What the extractor returns. Everything optional so that a bad turn degrades gracefully."""

    question: str = Field(description="The decision being made, as a neutral one-line question")
    context: str = Field(default="", description="1-2 lines of background, no opinions")
    language: str = Field(default="en", description="ISO 639-1 language of the thread, e.g. 'ru' or 'en'")
    options: list[ExtractedOption] = Field(default_factory=list)
    positions: list[ExtractedPosition] = Field(default_factory=list)
    open_questions: list[ExtractedOpenQuestion] = Field(default_factory=list, description="Still unanswered questions only")
    answered_question_texts: list[str] = Field(default_factory=list, description="Texts of previously open questions that got answered")
    claims: list[ExtractedClaim] = Field(default_factory=list)
    deadline: datetime | None = Field(default=None, description="Deadline if the thread states one ('by Wednesday'), absolute UTC")
    decision_reached: bool = Field(default=False, description="True if the thread itself converged on an option")
    decision_option_id: str | None = None
    decision_by: str | None = Field(default=None, description="user id who called it")
    decision_quote: str = Field(default="", description="short quote proving convergence")
    stakeholders_mentioned: list[str] = Field(default_factory=list, description="user ids mentioned but silent")
    commitments: list[ProposedCommitment] = Field(default_factory=list)
    blockers: list[ProposedBlocker] = Field(default_factory=list)
    decision_rationale: str = ""
    loop_control: ProposedLoopControl | None = None


class DecisionRecordDraft(BaseModel):
    """ADR-style record written from the arguments, not from the vote count."""

    title: str
    summary: str = Field(description="What was decided, one paragraph, same language as the thread")
    rationale: str = Field(description="Why, from the arguments")
    dissent: list[ExtractedPosition] = Field(default_factory=list, description="Who disagreed and with what argument")
    consequences: str = ""
    follow_ups: list[str] = Field(default_factory=list, description="Concrete next actions, each with an owner in brackets if known")
    follow_up_assignees: list[str | None] = Field(default_factory=list, description="user id per follow-up, same length as follow_ups")
    owner: str | None = Field(default=None, description="user id who owns the outcome")


class ContradictionCheck(BaseModel):
    contradicts: bool
    decision_thread_key: str | None = Field(default=None, description="thread_key of the contradicted decision")
    why: str = Field(default="", description="one line, same language as the message")


class DecisionBrewing(BaseModel):
    brewing: bool
    confidence: float = 0.0
    question: str = ""


class Extractor(Protocol):
    async def extract(self, state: CardState, new_messages: list[Message], all_messages: list[Message], names: dict[str, str]) -> Extraction: ...

    async def write_record(self, state: CardState, messages: list[Message], names: dict[str, str]) -> DecisionRecordDraft: ...

    async def stalled_summary(self, state: CardState, names: dict[str, str]) -> str: ...


class Classifier(Protocol):
    async def decision_brewing(self, messages: list[Message], names: dict[str, str]) -> DecisionBrewing: ...

    async def contradiction(self, message: Message, decisions: list[DecisionMemory]) -> ContradictionCheck: ...


Verdict = Literal["confirmed", "refuted", "unclear", "failed"]


class SourceSnippet(BaseModel):
    title: str
    url: str
    snippet: str


class ClaimJudgement(BaseModel):
    verdict: Literal["confirmed", "refuted", "unclear"]
    summary: str = Field(description="One line, same language as the claim: what the sources say")
    supporting_urls: list[str] = Field(default_factory=list, description="2-3 urls from the given sources that matter")


class ClaimJudge(Protocol):
    async def judge_claim(self, claim: str, context: str, sources: list[SourceSnippet]) -> ClaimJudgement: ...

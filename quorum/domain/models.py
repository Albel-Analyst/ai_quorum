"""Domain model of a tracked decision. Pure data, no platform, no LLM.

The card state is the single source of truth. The LLM returns an `Extraction` (the content part) and the
engine merges it into `CardState`; the status is owned by the state machine, never by the LLM.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Status(StrEnum):
    FRAMING = "framing"            # question is being understood, options not yet clear
    DELIBERATING = "deliberating"  # options and positions are forming
    VOTING = "voting"              # vote is open
    DECIDED = "decided"            # decision confirmed by a human
    RECORDED = "recorded"          # written to an external system (Confluence / Jira / markdown / canvas)
    STALLED = "stalled"            # no activity while deliberating; card shows what blocks
    PARKED = "parked"              # explicitly postponed: why and when to come back
    EXPIRED = "expired"            # stalled for too long; card lists what stayed open
    SUPERSEDED = "superseded"      # a later decision replaced this one


TERMINAL = {Status.RECORDED, Status.EXPIRED, Status.SUPERSEDED}
ACTIVE = {Status.FRAMING, Status.DELIBERATING, Status.VOTING, Status.STALLED}


class ThreadRef(BaseModel):
    """Identity of a conversation thread on a chat platform."""

    platform: str            # "slack" | "discord" | "fake"
    channel_id: str
    thread_id: str           # slack: root message ts; discord: thread channel id

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.channel_id}:{self.thread_id}"

    @classmethod
    def from_key(cls, key: str) -> ThreadRef:
        platform, channel_id, thread_id = key.split(":", 2)
        return cls(platform=platform, channel_id=channel_id, thread_id=thread_id)


class Message(BaseModel):
    """A normalized chat message. `user_name` is a display name for the LLM; `user_id` is what the card links."""

    id: str                                  # platform message id (slack ts)
    user_id: str
    user_name: str = ""
    text: str
    at: datetime
    is_bot: bool = False
    mentions: list[str] = Field(default_factory=list)   # user ids mentioned in the text


class Option(BaseModel):
    id: str                                  # stable short id: "A", "B", "C" ...
    label: str                               # 2-6 words
    summary: str = ""                        # one line: what it means in practice


class Position(BaseModel):
    user_id: str
    option_id: str | None = None             # None = user spoke but did not pick an option
    argument: str = ""                       # one line, in the author's words
    source: Literal["llm", "user"] = "llm"   # "user" = corrected through the "My position" form; LLM must not overwrite it
    at: datetime = Field(default_factory=now)


class OpenQuestion(BaseModel):
    id: str = Field(default_factory=lambda: new_id("q"))
    text: str
    directed_to: str | None = None           # user id the question is addressed to (a stakeholder), if any
    asked_at: datetime = Field(default_factory=now)
    asked_by: str | None = None
    answered: bool = False
    nudged_at: datetime | None = None        # one DM per question, never twice
    declined: bool = False                   # the addressee tapped "Not mine"


class Source(BaseModel):
    title: str
    url: str


class Verification(BaseModel):
    verdict: Literal["confirmed", "refuted", "unclear", "failed"]
    summary: str = ""
    sources: list[Source] = Field(default_factory=list)
    at: datetime = Field(default_factory=now)


class Claim(BaseModel):
    """A checkable statement about the outside world (not an opinion, not internal facts)."""

    id: str = Field(default_factory=lambda: new_id("c"))
    text: str
    by: str                                  # user id
    verification: Verification | None = None
    checking: bool = False                   # spinner state while the verifier runs


class Vote(BaseModel):
    user_id: str
    option_id: str
    at: datetime = Field(default_factory=now)


class Dissent(BaseModel):
    user_id: str
    argument: str


class FollowUp(BaseModel):
    text: str
    assignee: str | None = None              # user id
    issue_url: str | None = None             # filled by the Jira recorder


class Decision(BaseModel):
    option_id: str | None = None
    summary: str = ""                        # what was decided, one paragraph
    rationale: str = ""                      # why — written from the arguments, not from the vote count
    dissent: list[Dissent] = Field(default_factory=list)
    owner: str | None = None                 # user id who owns the outcome
    follow_ups: list[FollowUp] = Field(default_factory=list)
    decider: str | None = None               # who has the final say (thread author by default)
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    consequences: str = ""                   # ADR "consequences" paragraph


class ParkInfo(BaseModel):
    reason: str
    return_at: datetime | None = None
    by: str
    reminded: bool = False


class Record(BaseModel):
    kind: str                                # "confluence" | "jira" | "markdown" | "canvas"
    title: str
    url: str
    at: datetime = Field(default_factory=now)


class DecisionHint(BaseModel):
    """The LLM noticed the thread converged ("ok, let's go with B"). Code renders a Confirm button; nothing is decided yet."""

    option_id: str | None = None
    by: str | None = None
    quote: str = ""


class CardState(BaseModel):
    """Everything the card renders. Status is owned by the state machine (see state_machine.py)."""

    question: str = ""
    context: str = ""                        # 1-2 lines of background from the thread
    options: list[Option] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    deadline: datetime | None = None
    deadline_source: Literal["text", "button"] | None = None
    deadline_fired: bool = False             # the deadline already opened/closed the vote; do not fire twice
    all_voted_notified: bool = False         # the decider was already told that everyone voted
    status: Status = Status.FRAMING
    decision: Decision | None = None
    decision_hint: DecisionHint | None = None
    votes: list[Vote] = Field(default_factory=list)
    parked: ParkInfo | None = None
    records: list[Record] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)    # user ids who wrote in the thread (bots excluded)
    stakeholders: list[str] = Field(default_factory=list)    # participants + mentioned + addressees of open questions
    language: str = "en"                                     # language of the thread, detected by the LLM
    stalled_reason: str = ""
    expired_summary: str = ""
    superseded_by: str | None = None         # thread key of the newer decision
    supersedes: str | None = None            # thread key of the older decision this one disputes
    last_llm_ok_at: datetime | None = None
    last_llm_error: str | None = None
    updated_at: datetime = Field(default_factory=now)

    # ---- small helpers used by the renderer and the engine ---------------------------------
    def option(self, option_id: str | None) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)

    def position_of(self, user_id: str) -> Position | None:
        return next((p for p in self.positions if p.user_id == user_id), None)

    def vote_of(self, user_id: str) -> Vote | None:
        return next((v for v in self.votes if v.user_id == user_id), None)

    def tally(self) -> dict[str, int]:
        counts = {o.id: 0 for o in self.options}
        for v in self.votes:
            counts[v.option_id] = counts.get(v.option_id, 0) + 1
        return counts

    def unanswered(self) -> list[OpenQuestion]:
        return [q for q in self.open_questions if not q.answered]

    def voters_missing(self) -> list[str]:
        voted = {v.user_id for v in self.votes}
        return [u for u in self.participants if u not in voted]


class TrackedThread(BaseModel):
    """Persistent aggregate: a thread Quorum follows, plus where its card lives."""

    ref: ThreadRef
    author_id: str                           # who wrote the root message
    requested_by: str                        # who asked Quorum to track it
    card_message_id: str | None = None       # platform id of the card message (slack ts)
    root_text: str = ""
    permalink: str = ""
    state: CardState = Field(default_factory=CardState)
    last_seen_message_id: str | None = None  # last message the extractor has consumed
    message_count: int = 0
    last_activity_at: datetime = Field(default_factory=now)
    created_at: datetime = Field(default_factory=now)
    phase_reposts: int = 0

    @property
    def key(self) -> str:
        return self.ref.key


class DecisionMemory(BaseModel):
    """Row of the decisions journal (the "memory"). Short on purpose: all rows fit into one LLM context."""

    thread_key: str
    title: str                               # the question
    summary: str                             # what was decided, one-two lines
    option_label: str = ""
    decided_at: datetime
    channel_id: str
    permalink: str = ""
    record_url: str = ""
    owner: str | None = None
    status: Literal["active", "superseded"] = "active"
    superseded_by: str | None = None

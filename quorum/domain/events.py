"""Normalized inbound events. A platform adapter turns its raw payloads into these; the engine only sees these."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from quorum.domain.models import Message, ThreadRef


class InboundEvent(BaseModel):
    event_id: str | None = None


class TrackRequested(InboundEvent):
    """A human asked Quorum to follow a thread: @mention, message shortcut, or a ⚖️ reaction on the root."""

    thread: ThreadRef
    requested_by: str
    via: Literal["mention", "shortcut", "reaction", "suggestion", "dispute"]
    root_message: Message | None = None
    supersedes: str | None = None            # thread key of a decision this new thread disputes


class MessagePosted(InboundEvent):
    thread: ThreadRef                        # for a top-level message thread_id == message.id
    message: Message
    in_thread: bool                          # False = top-level channel message (memory recall / auto-suggest only)


class MessageChanged(InboundEvent):
    thread: ThreadRef
    message: Message


class MessageDeleted(InboundEvent):
    thread: ThreadRef
    message_id: str


class ButtonPressed(InboundEvent):
    """Any interactive tap: on the card, in an ephemeral, or in a DM. `thread` is resolved from the button payload."""

    thread: ThreadRef | None
    user_id: str
    action: str                              # e.g. "my_position", "vote", "open_voting", "confirm", "record", "verify", "not_me"
    payload: dict[str, Any] = Field(default_factory=dict)
    trigger_id: str | None = None            # needed to open a form
    message_id: str | None = None            # message that held the button
    channel_id: str | None = None
    response_url: str | None = None


class FormSubmitted(InboundEvent):
    thread: ThreadRef | None
    user_id: str
    form_id: str                             # e.g. "my_position", "deadline", "park", "confirm"
    values: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)   # private metadata echoed back


class HomeOpened(InboundEvent):
    user_id: str


Event = TrackRequested | MessagePosted | MessageChanged | MessageDeleted | ButtonPressed | FormSubmitted | HomeOpened

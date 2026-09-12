"""Recorder: writes a decision somewhere outside the chat. Only ever called after a human tap."""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from quorum.domain.models import CardState, Record, TrackedThread


class RecordInput(BaseModel):
    thread: TrackedThread
    state: CardState
    names: dict[str, str]          # user id -> display name
    markdown: str                  # the ADR rendered by quorum.render.markdown (single source of the text)


class Recorder(Protocol):
    kind: str                      # "confluence" | "jira" | "markdown" | "canvas"

    def available(self) -> bool: ...

    async def record(self, inp: RecordInput) -> list[Record]:
        """Write and return links. Raise on failure; the engine retries once and then falls back to markdown."""
        ...

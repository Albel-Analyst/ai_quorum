"""Verifier plugin: checks a claim against the outside world. Hidden from the card when unavailable."""
from __future__ import annotations

from typing import Protocol

from quorum.domain.models import Verification


class Verifier(Protocol):
    def available(self) -> bool: ...

    async def verify(self, claim_text: str, context: str) -> Verification: ...

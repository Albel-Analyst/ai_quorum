"""OpenAI implementation of the three LLM protocols (Extractor, Classifier, ClaimJudge).

One class does all of it because every call has the same shape: a system prompt from `prompts.py`,
a user message built from the card state and the transcript, and a strict pydantic schema parsed by the
Responses API (`client.responses.parse(..., text_format=Model)`).

Failure policy: nothing but `LLMError` ever leaves this module. Transient failures are retried twice with
exponential backoff; everything else fails fast. The engine renders a degraded card on `LLMError`.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, TypeVar

import structlog
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel

from quorum.config import settings
from quorum.domain.models import CardState, DecisionMemory, Message
from quorum.llm import prompts
from quorum.llm.base import (
    ClaimJudgement,
    ContradictionCheck,
    DecisionBrewing,
    DecisionRecordDraft,
    Extraction,
    SourceSnippet,
)

log = structlog.get_logger("llm")

T = TypeVar("T", bound=BaseModel)

MAX_ATTEMPTS = 3          # first try + 2 retries
BACKOFF_BASE_S = 0.6      # 0.6s, then 1.2s
BREWING_WINDOW = 15       # last N top-level messages the cheap classifier looks at

_TRANSIENT = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError, asyncio.TimeoutError)


class LLMError(RuntimeError):
    """Any LLM failure the engine has to survive: transport, rate limit, bad output, missing key."""


class _Line(BaseModel):
    """Single-line free text answers (the stalled summary) still go through a strict schema."""

    text: str = ""


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500 or exc.status_code == 429
    return False


def _tokens(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }


class OpenAIProvider:
    """Implements Extractor + Classifier + ClaimJudge against the OpenAI Responses API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        model_fast: str | None = None,
        model_smart: str | None = None,
        reasoning_effort: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model_fast = model_fast or settings.llm_model_fast
        self.model_smart = model_smart or settings.llm_model_smart
        self.reasoning_effort = reasoning_effort or settings.llm_reasoning_effort
        # Some models / gateways reject `reasoning`; the first rejection flips this off for the process.
        self._reasoning_ok = True
        if client is not None:
            self.client = client
        else:
            key = api_key if api_key is not None else settings.openai_api_key
            if not key:
                raise LLMError("openai_api_key is not configured")
            self.client = AsyncOpenAI(
                api_key=key,
                base_url=base_url if base_url is not None else settings.openai_base_url,
                timeout=timeout if timeout is not None else settings.llm_timeout_s,
            )

    # -- plumbing -----------------------------------------------------------------------------
    async def _parse(self, op: str, model: str, system: str, user: str, schema: type[T]) -> T:
        last: BaseException | None = None
        for attempt in range(MAX_ATTEMPTS):
            started = time.perf_counter()
            try:
                response = await self._call(model, system, user, schema)
            except Exception as exc:  # noqa: BLE001 - everything is normalized into LLMError
                last = exc
                took = int((time.perf_counter() - started) * 1000)
                transient = _is_transient(exc)
                log.warning(
                    "llm.call_failed",
                    op=op,
                    model=model,
                    attempt=attempt + 1,
                    duration_ms=took,
                    transient=transient,
                    error=f"{type(exc).__name__}: {exc}",
                )
                if not transient or attempt == MAX_ATTEMPTS - 1:
                    break
                await asyncio.sleep(BACKOFF_BASE_S * (2**attempt))
                continue

            took = int((time.perf_counter() - started) * 1000)
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                last = LLMError(f"{op}: model returned no parsed output (refusal or truncation)")
                log.warning("llm.no_output", op=op, model=model, duration_ms=took)
                break
            log.info("llm.ok", op=op, model=model, duration_ms=took, **_tokens(response))
            return parsed

        raise LLMError(f"{op} failed: {type(last).__name__}: {last}" if last else f"{op} failed")

    async def _call(self, model: str, system: str, user: str, schema: type[T]) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text_format": schema,
        }
        if self._reasoning_ok and self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        try:
            return await self.client.responses.parse(**kwargs)
        except (TypeError, APIStatusError) as exc:
            # `reasoning` unsupported by this model/gateway: drop it once and never send it again.
            if "reasoning" in kwargs and "reasoning" in str(exc).lower():
                log.info("llm.reasoning_unsupported", model=model)
                self._reasoning_ok = False
                kwargs.pop("reasoning")
                return await self.client.responses.parse(**kwargs)
            raise

    # -- Extractor ----------------------------------------------------------------------------
    async def extract(
        self,
        state: CardState,
        new_messages: list[Message],
        all_messages: list[Message],
        names: dict[str, str],
    ) -> Extraction:
        user = prompts.extract_user(state, new_messages, all_messages, names)
        return await self._parse("extract", self.model_fast, prompts.EXTRACT_SYSTEM, user, Extraction)

    async def write_record(
        self, state: CardState, messages: list[Message], names: dict[str, str]
    ) -> DecisionRecordDraft:
        user = prompts.record_user(state, messages, names)
        return await self._parse("write_record", self.model_smart, prompts.RECORD_SYSTEM, user, DecisionRecordDraft)

    async def stalled_summary(self, state: CardState, names: dict[str, str]) -> str:
        user = prompts.stalled_user(state, names)
        line = await self._parse("stalled_summary", self.model_fast, prompts.STALLED_SYSTEM, user, _Line)
        return line.text.strip()

    # -- Classifier ---------------------------------------------------------------------------
    async def decision_brewing(self, messages: list[Message], names: dict[str, str]) -> DecisionBrewing:
        window = [m for m in messages if not m.is_bot][-BREWING_WINDOW:]
        user = prompts.brewing_user(window, names)
        return await self._parse("decision_brewing", self.model_fast, prompts.BREWING_SYSTEM, user, DecisionBrewing)

    async def contradiction(self, message: Message, decisions: list[DecisionMemory]) -> ContradictionCheck:
        if not decisions:
            return ContradictionCheck(contradicts=False)
        user = prompts.contradiction_user(message, decisions)
        check = await self._parse(
            "contradiction", self.model_fast, prompts.CONTRADICTION_SYSTEM, user, ContradictionCheck
        )
        known = {d.thread_key for d in decisions}
        if check.contradicts and check.decision_thread_key not in known:
            # A hallucinated thread key would break the memory notice; treat it as no contradiction.
            log.warning("llm.unknown_thread_key", key=check.decision_thread_key)
            return ContradictionCheck(contradicts=False)
        return check

    # -- ClaimJudge ---------------------------------------------------------------------------
    async def judge_claim(self, claim: str, context: str, sources: list[SourceSnippet]) -> ClaimJudgement:
        user = prompts.judge_user(claim, context, sources)
        return await self._parse("judge_claim", self.model_fast, prompts.JUDGE_SYSTEM, user, ClaimJudgement)

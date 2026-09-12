"""Exa verifier: request shape, snippet mapping, and the never-raise contract."""
from __future__ import annotations

import json
import os
from typing import Self

import pytest

from quorum.llm.base import ClaimJudgement, SourceSnippet
from quorum.verifier.exa import ExaVerifier

CLAIM = "Slack canvases.create requires the canvases:write scope"
CONTEXT = "We are choosing where Quorum writes the decision record."

EXA_RESPONSE = {
    "requestId": "r1",
    "results": [
        {
            "id": "https://docs.slack.dev/reference/methods/canvases.create",
            "title": "canvases.create method",
            "url": "https://docs.slack.dev/reference/methods/canvases.create",
            "text": "long page text",
            "highlights": ["Required scope: canvases:write.", "Creates a standalone canvas."],
        },
        {"title": "Blog post", "url": "https://example.com/blog", "text": "text only, no highlights",
         "highlights": []},
        {"title": "Third", "url": "https://example.com/third", "text": "third text", "highlights": []},
        {"title": "Fourth", "url": "https://example.com/fourth", "text": "fourth text", "highlights": []},
        {"title": "No url", "url": "", "text": "dropped"},
    ],
}


class FakeResponse:
    def __init__(self, status: int, payload: dict | str) -> None:
        self.status = status
        self._payload = payload

    async def text(self) -> str:
        return self._payload if isinstance(self._payload, str) else json.dumps(self._payload)

    async def json(self) -> dict:
        return self._payload if isinstance(self._payload, dict) else json.loads(self._payload)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    closed = False

    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeJudge:
    def __init__(self, judgement: ClaimJudgement | Exception) -> None:
        self.judgement = judgement
        self.seen: list[SourceSnippet] = []

    async def judge_claim(self, claim: str, context: str, sources: list[SourceSnippet]) -> ClaimJudgement:
        self.seen = sources
        self.claim, self.context = claim, context
        if isinstance(self.judgement, Exception):
            raise self.judgement
        return self.judgement


def verifier(response: FakeResponse | Exception, judgement: ClaimJudgement | Exception) -> tuple:
    session = FakeSession(response)
    judge = FakeJudge(judgement)
    return ExaVerifier("key-123", judge, session=session), session, judge


def test_available() -> None:
    assert ExaVerifier("", FakeJudge(ClaimJudgement(verdict="unclear", summary=""))).available() is False
    assert ExaVerifier("k", FakeJudge(ClaimJudgement(verdict="unclear", summary=""))).available() is True


async def test_request_shape_and_verdict() -> None:
    judgement = ClaimJudgement(
        verdict="confirmed",
        summary="The docs list canvases:write as the required scope.",
        supporting_urls=["https://docs.slack.dev/reference/methods/canvases.create"],
    )
    v, session, judge = verifier(FakeResponse(200, EXA_RESPONSE), judgement)

    result = await v.verify(CLAIM, CONTEXT)

    call = session.calls[0]
    assert call["url"] == "https://api.exa.ai/search"
    assert call["headers"]["x-api-key"] == "key-123"
    assert call["json"] == {
        "query": CLAIM,
        "numResults": 5,
        "type": "auto",
        "contents": {
            "highlights": {"maxCharacters": 400, "numSentences": 3},
            "text": {"maxCharacters": 800},
        },
    }

    # snippets: highlights joined, text as the fallback, url-less results dropped
    assert [s.url for s in judge.seen] == [
        "https://docs.slack.dev/reference/methods/canvases.create",
        "https://example.com/blog",
        "https://example.com/third",
        "https://example.com/fourth",
    ]
    assert judge.seen[0].snippet == "Required scope: canvases:write. … Creates a standalone canvas."
    assert judge.seen[1].snippet == "text only, no highlights"
    assert judge.claim == CLAIM and judge.context == CONTEXT

    assert result.verdict == "confirmed"
    assert result.summary == judgement.summary
    assert [s.url for s in result.sources] == ["https://docs.slack.dev/reference/methods/canvases.create"]


async def test_falls_back_to_first_three_sources() -> None:
    v, _, _ = verifier(FakeResponse(200, EXA_RESPONSE), ClaimJudgement(verdict="unclear", summary="Nothing decisive."))
    result = await v.verify(CLAIM, CONTEXT)

    assert result.verdict == "unclear"
    assert len(result.sources) == 3


async def test_no_results_is_unclear() -> None:
    v, _, judge = verifier(FakeResponse(200, {"results": []}), ClaimJudgement(verdict="confirmed", summary=""))
    result = await v.verify(CLAIM, CONTEXT)

    assert result.verdict == "unclear"
    assert result.sources == []
    assert judge.seen == []            # the judge is not called without sources


async def test_http_error_never_raises() -> None:
    v, _, _ = verifier(FakeResponse(401, "unauthorized"), ClaimJudgement(verdict="confirmed", summary=""))
    result = await v.verify(CLAIM, CONTEXT)

    assert result.verdict == "failed"
    assert "Exa 401" in result.summary


async def test_network_error_never_raises() -> None:
    v, _, _ = verifier(OSError("connection reset"), ClaimJudgement(verdict="confirmed", summary=""))
    result = await v.verify(CLAIM, CONTEXT)

    assert result.verdict == "failed"
    assert "connection reset" in result.summary


async def test_judge_failure_never_raises() -> None:
    v, _, _ = verifier(FakeResponse(200, EXA_RESPONSE), RuntimeError("llm timeout"))
    result = await v.verify(CLAIM, CONTEXT)

    assert result.verdict == "failed"
    assert result.summary == "llm timeout"


@pytest.mark.skipif(os.getenv("QUORUM_LIVE_EXA") != "1", reason="live Exa call; set QUORUM_LIVE_EXA=1 and EXA_KEY")
async def test_live_exa_search() -> None:
    key = os.getenv("EXA_KEY", "")
    assert key, "EXA_KEY must be set for the live test"
    v = ExaVerifier(key, FakeJudge(ClaimJudgement(verdict="unclear", summary="live run")))
    try:
        result = await v.verify("Python 3.12 release date", "checking the request shape against the real API")
    finally:
        await v.aclose()

    assert result.verdict == "unclear", result.summary       # "failed" means the request shape broke
    assert result.sources, "the live search returned no sources"

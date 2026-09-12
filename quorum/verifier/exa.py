"""Exa verifier: search the web for a claim, then let the LLM judge the snippets. Never raises."""
from __future__ import annotations

import aiohttp
import structlog

from quorum.domain.models import Source, Verification
from quorum.llm.base import ClaimJudge, SourceSnippet

log = structlog.get_logger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)
SEARCH_URL = "https://api.exa.ai/search"
NUM_RESULTS = 5


class ExaVerifier:
    """`available()` follows EXA_KEY; a missing key hides the Verify button on the card."""

    def __init__(self, api_key: str, judge: ClaimJudge, session: aiohttp.ClientSession | None = None) -> None:
        self.api_key = api_key
        self.judge = judge
        self._session = session
        self._owns_session = session is None

    def available(self) -> bool:
        return bool(self.api_key)

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
            self._owns_session = True
        return self._session

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def verify(self, claim_text: str, context: str) -> Verification:
        try:
            snippets = await self.search(claim_text)
            if not snippets:
                return Verification(verdict="unclear", summary="No sources found.")
            judgement = await self.judge.judge_claim(claim_text, context, snippets)
            supporting = [s for s in snippets if s.url in set(judgement.supporting_urls) and s.snippet.strip()]
            chosen = supporting or snippets[:3]
            return Verification(
                verdict=judgement.verdict if supporting else "unclear",
                summary=judgement.summary,
                sources=[Source(title=s.title or s.url, url=s.url) for s in chosen],
            )
        except Exception as error:  # noqa: BLE001 - contract: the verifier never raises, it reports "failed"
            log.warning("verifier.exa.failed", error=str(error))
            return Verification(verdict="failed", summary=str(error)[:200])

    async def search(self, claim_text: str) -> list[SourceSnippet]:
        session = await self._http()
        body = {
            "query": claim_text,
            "numResults": NUM_RESULTS,
            "type": "auto",
            "contents": {
                "highlights": {"maxCharacters": 400, "numSentences": 3},
                "text": {"maxCharacters": 800},
            },
        }
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        async with session.post(SEARCH_URL, json=body, headers=headers, timeout=TIMEOUT) as resp:
            if resp.status >= 300:
                text = await resp.text()
                raise RuntimeError(f"Exa {resp.status}: {text[:200]}")
            data = await resp.json()

        snippets: list[SourceSnippet] = []
        for item in data.get("results", []) or []:
            url = item.get("url") or ""
            if not url:
                continue
            highlights = [h for h in (item.get("highlights") or []) if h]
            snippet = " … ".join(highlights) if highlights else (item.get("text") or "")
            snippets.append(SourceSnippet(title=item.get("title") or url, url=url, snippet=snippet.strip()))
        log.info("verifier.exa.searched", n=len(snippets))
        return snippets

"""Slack canvas recorder: the ADR as a standalone canvas document. Needs the `canvases:write` bot scope."""
from __future__ import annotations

import aiohttp
import structlog

from quorum.domain.models import Record
from quorum.recorders.base import RecordInput
from quorum.render.markdown import title_from_markdown

log = structlog.get_logger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)
API = "https://slack.com/api"
# Slack answers with one of these when the workspace/plan/app cannot create canvases; there is nothing to retry.
FATAL_ERRORS = {"missing_scope", "not_allowed", "not_allowed_token_type", "feature_not_enabled", "invalid_auth"}


class CanvasError(RuntimeError):
    """Canvas creation is impossible with the current token/plan (scope, feature flag)."""


class CanvasRecorder:
    """`canvases.create` with a markdown document; the record url is the canvas permalink."""

    kind = "canvas"

    def __init__(
        self,
        bot_token: str = "",
        enabled: bool = False,
        session: aiohttp.ClientSession | None = None,
        team_id: str = "",
    ) -> None:
        self.bot_token = bot_token
        self.enabled = enabled
        self._session = session
        self._owns_session = session is None
        self._team_id = team_id            # cached after the first auth.test

    def available(self) -> bool:
        return bool(self.enabled and self.bot_token)

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
            self._owns_session = True
        return self._session

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def record(self, inp: RecordInput) -> list[Record]:
        title = title_from_markdown(inp.markdown, inp.state.question or "Decision")
        data = await self._call(
            "canvases.create",
            {"title": title, "document_content": {"type": "markdown", "markdown": inp.markdown}},
        )
        canvas_id = data.get("canvas_id") or (data.get("canvas") or {}).get("id", "")
        team_id = await self._team()
        url = f"https://slack.com/docs/{team_id}/{canvas_id}"
        log.info("recorder.canvas.created", canvas_id=canvas_id, url=url)
        return [Record(kind=self.kind, title=title, url=url)]

    async def _team(self) -> str:
        if not self._team_id:
            data = await self._call("auth.test", {})
            self._team_id = data.get("team_id", "")
        return self._team_id

    async def _call(self, method: str, payload: dict) -> dict:
        session = await self._http()
        headers = {"Authorization": f"Bearer {self.bot_token}", "Content-Type": "application/json; charset=utf-8"}
        async with session.post(f"{API}/{method}", json=payload, headers=headers, timeout=TIMEOUT) as resp:
            if resp.status >= 300:
                text = await resp.text()
                raise RuntimeError(f"Slack {method} HTTP {resp.status}: {text[:200]}")
            data = await resp.json()
        if not data.get("ok"):
            error = str(data.get("error", "unknown_error"))
            if error in FATAL_ERRORS:
                raise CanvasError(
                    f"Slack {method} refused: {error}. Canvases need the `canvases:write` bot scope and a plan "
                    f"where canvases are enabled; set SLACK_CANVAS_ENABLED=false to hide this recorder."
                )
            raise RuntimeError(f"Slack {method} failed: {error}")
        return data

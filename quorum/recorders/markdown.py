"""Markdown recorder: the always-available fallback. Writes the ADR to a file, returns a file:// link."""
from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from quorum.domain.models import Record
from quorum.recorders.base import RecordInput
from quorum.render.markdown import slugify, title_from_markdown

log = structlog.get_logger(__name__)


class MarkdownRecorder:
    """`<dir>/<YYYY-MM-DD>-<slug>.md`. Never fails on configuration, only on the filesystem."""

    kind = "markdown"

    def __init__(self, dir: str = "records") -> None:   # `dir` mirrors the markdown_records_dir setting
        self.dir = Path(dir)

    def available(self) -> bool:
        return True

    async def record(self, inp: RecordInput) -> list[Record]:
        title = title_from_markdown(inp.markdown, inp.state.question or "Decision")
        day = (inp.state.decision.confirmed_at if inp.state.decision and inp.state.decision.confirmed_at
               else inp.state.updated_at).strftime("%Y-%m-%d")
        path = await asyncio.to_thread(self._write, day, title, inp.markdown)
        log.info("recorder.markdown.written", path=str(path))
        return [Record(kind=self.kind, title=title, url=path.as_uri())]

    def _write(self, day: str, title: str, markdown: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        base = f"{day}-{slugify(title)}"
        path = self.dir / f"{base}.md"
        n = 2
        while path.exists():
            path = self.dir / f"{base}-{n}.md"
            n += 1
        path.write_text(markdown, encoding="utf-8")
        return path.resolve()

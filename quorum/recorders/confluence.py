"""Confluence Cloud recorder: creates one page per decision (REST v1 `content`, storage XHTML body)."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from html import escape

import aiohttp
import structlog

from quorum.domain.models import Record
from quorum.recorders.base import RecordInput
from quorum.render.markdown import title_from_markdown

log = structlog.get_logger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\d+[.)]\s+(.*)$")
_CHECKBOX = re.compile(r"^[-*]\s+\[([ xX])\]\s+(.*)$")


def _inline(text: str) -> str:
    """Escape, then re-introduce the few inline constructs the ADR uses."""
    out = escape(text, quote=False)
    # group(2) is already escaped by the pass above; only the attribute quote still needs care
    out = _LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "&quot;")}">{m.group(1)}</a>', out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _CODE.sub(r"<code>\1</code>", out)
    return out


def md_to_storage(markdown: str) -> str:
    """Tiny markdown -> Confluence storage XHTML converter (headings, paragraphs, lists, checkboxes, bold, links)."""
    html: list[str] = []
    list_tag: str | None = None
    paragraph: list[str] = []

    def close_paragraph() -> None:
        if paragraph:
            html.append("<p>" + "<br/>".join(paragraph) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            html.append(f"</{list_tag}>")
            list_tag = None

    def open_list(tag: str) -> None:
        nonlocal list_tag
        if list_tag != tag:
            close_list()
            html.append(f"<{tag}>")
            list_tag = tag

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            close_paragraph()
            close_list()
            continue

        heading = _HEADING.match(line)
        if heading:
            close_paragraph()
            close_list()
            level = len(heading.group(1))
            html.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        if line.strip() in {"---", "***", "___"}:
            close_paragraph()
            close_list()
            html.append("<hr/>")
            continue

        checkbox = _CHECKBOX.match(line)
        if checkbox:
            close_paragraph()
            open_list("ul")
            mark = "☑ " if checkbox.group(1).lower() == "x" else "☐ "
            html.append(f"<li>{mark}{_inline(checkbox.group(2))}</li>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            close_paragraph()
            open_list("ul")
            html.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            close_paragraph()
            open_list("ol")
            html.append(f"<li>{_inline(numbered.group(1))}</li>")
            continue

        close_list()
        paragraph.append(_inline(line.strip()))

    close_paragraph()
    close_list()
    return "".join(html)


class ConfluenceRecorder:
    """POST {base_url}/rest/api/content with basic auth (email + API token)."""

    kind = "confluence"

    def __init__(
        self,
        base_url: str = "",
        email: str = "",
        api_token: str = "",
        space_key: str = "",
        parent_page_id: str = "",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.email = email
        self.api_token = api_token
        self.space_key = space_key
        self.parent_page_id = parent_page_id
        self._session = session
        self._owns_session = session is None

    def available(self) -> bool:
        return all([self.base_url, self.email, self.api_token, self.space_key])

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
            self._owns_session = True
        return self._session

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _payload(self, title: str, body: str) -> dict:
        payload: dict = {
            "type": "page",
            "title": title,
            "space": {"key": self.space_key},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        if self.parent_page_id:
            payload["ancestors"] = [{"id": str(self.parent_page_id)}]
        return payload

    async def record(self, inp: RecordInput) -> list[Record]:
        title = title_from_markdown(inp.markdown, inp.state.question or "Decision")
        body = md_to_storage(inp.markdown)
        try:
            result = await self._create(title, body)
        except _TitleTaken:
            stamped = f"{title} ({datetime.now(UTC).strftime('%Y-%m-%d %H:%M')})"
            log.info("recorder.confluence.title_taken", title=title, retry_title=stamped)
            result = await self._create(stamped, body)
            title = stamped

        url = self.base_url + (result.get("_links", {}) or {}).get("webui", "")
        log.info("recorder.confluence.created", title=title, url=url, page_id=result.get("id"))
        return [Record(kind=self.kind, title=title, url=url)]

    async def _create(self, title: str, body: str) -> dict:
        session = await self._http()
        headers = {"Authorization": aiohttp.encode_basic_auth(self.email, self.api_token)}
        url = f"{self.base_url}/rest/api/content"
        async with session.post(url, json=self._payload(title, body), headers=headers, timeout=TIMEOUT) as resp:
            text = await resp.text()
            if resp.status == 400 and "already exist" in text.lower():
                raise _TitleTaken(text[:200])
            if resp.status >= 300:
                raise RuntimeError(f"Confluence {resp.status}: {text[:300]}")
            return await resp.json()


class _TitleTaken(Exception):
    """Confluence rejects a duplicate title in the same space; we retry once with a timestamp."""

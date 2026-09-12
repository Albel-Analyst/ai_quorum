"""Jira recorder: one Task per follow-up. The ADR itself lives elsewhere; the issue links back to it."""
from __future__ import annotations

import aiohttp
import structlog

from quorum.domain.models import CardState, Record
from quorum.recorders.base import RecordInput
from quorum.render.markdown import title_from_markdown

log = structlog.get_logger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)
SUMMARY_MAX = 255


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


def _link(value: str, href: str) -> dict:
    return {"type": "text", "text": value, "marks": [{"type": "link", "attrs": {"href": href}}]}


def _paragraph(*nodes: dict) -> dict:
    return {"type": "paragraph", "content": list(nodes)}


class JiraRecorder:
    """POST {base_url}/rest/api/3/issue, basic auth (email + API token), issuetype Task."""

    kind = "jira"

    def __init__(
        self,
        base_url: str = "",
        email: str = "",
        api_token: str = "",
        project_key: str = "",
        session: aiohttp.ClientSession | None = None,
        account_ids: dict[str, str] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.email = email
        self.api_token = api_token
        self.project_key = project_key
        self._session = session
        self._owns_session = session is None
        # optional chat user id -> Jira accountId map; without it issues stay unassigned (the name goes in the body)
        self.account_ids = account_ids or {}

    def available(self, state: CardState | None = None) -> bool:
        """Configured; with a state given, also requires the decision to have follow-ups (nothing else to file)."""
        configured = all([self.base_url, self.email, self.api_token, self.project_key])
        if state is None:
            return configured
        return configured and bool(state.decision and state.decision.follow_ups)

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
        decision = inp.state.decision
        if not decision or not decision.follow_ups:
            return []

        adr_title = title_from_markdown(inp.markdown, inp.state.question or "Decision")
        adr_url = next((r.url for r in reversed(inp.state.records) if r.kind != self.kind and r.url), "")
        if not adr_url:
            adr_url = inp.thread.permalink

        records: list[Record] = []
        for follow_up in decision.follow_ups:
            assignee_name = inp.names.get(follow_up.assignee or "", follow_up.assignee or "")
            account_id = self.account_ids.get(follow_up.assignee or "")
            fields: dict = {
                "project": {"key": self.project_key},
                "summary": (follow_up.text or adr_title)[:SUMMARY_MAX],
                "issuetype": {"name": "Task"},
                "description": self._description(adr_title, adr_url, decision.summary, assignee_name),
            }
            if account_id:
                fields["assignee"] = {"id": account_id}

            key = await self._create(fields)
            url = f"{self.base_url}/browse/{key}"
            follow_up.issue_url = url                     # mutate the state: the card and the ADR show the issue
            records.append(Record(kind=self.kind, title=key, url=url))
            log.info("recorder.jira.created", key=key, url=url, summary=fields["summary"])

        return records

    def _description(self, adr_title: str, adr_url: str, decision_summary: str, assignee_name: str) -> dict:
        content: list[dict] = []
        if adr_url:
            content.append(_paragraph(_text("Decision record: "), _link(adr_title, adr_url)))
        else:
            content.append(_paragraph(_text(f"Decision record: {adr_title}")))
        if decision_summary:
            content.append(_paragraph(_text(decision_summary)))
        if assignee_name:
            content.append(_paragraph(_text(f"Owner (from the thread, not mapped to a Jira user): {assignee_name}")))
        return {"type": "doc", "version": 1, "content": content}

    async def _create(self, fields: dict) -> str:
        session = await self._http()
        headers = {"Authorization": aiohttp.encode_basic_auth(self.email, self.api_token), "Accept": "application/json"}
        url = f"{self.base_url}/rest/api/3/issue"
        async with session.post(url, json={"fields": fields}, headers=headers, timeout=TIMEOUT) as resp:
            text = await resp.text()
            if resp.status >= 300:
                raise RuntimeError(f"Jira {resp.status}: {text[:300]}")
            data = await resp.json()
        key = data.get("key")
        if not key:
            raise RuntimeError(f"Jira returned no issue key: {str(data)[:200]}")
        return key

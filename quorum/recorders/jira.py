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
# built-in localisations of the "Task" type on Jira Cloud sites, so a non-English site still gets a task, not an epic
TASK_ALIASES = frozenset({"task", "задание", "задача", "aufgabe", "tâche", "tarea", "tarefa", "attività", "taak", "タスク", "任务"})


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


def _link(value: str, href: str) -> dict:
    return {"type": "text", "text": value, "marks": [{"type": "link", "attrs": {"href": href}}]}


def _paragraph(*nodes: dict) -> dict:
    return {"type": "paragraph", "content": list(nodes)}


class JiraRecorder:
    """POST {base_url}/rest/api/3/issue, basic auth (email + API token).

    The issue type is resolved once per process through createmeta: Jira Cloud localises the built-in type names
    (a Russian site knows "Task" only as "Задание"), so we match `issue_type` case-insensitively against the
    project's standard types (hierarchy level 0: no epics, no sub-tasks) and send the id; with no match we try
    the known translations of "Task", then the first standard type.
    """

    kind = "jira"

    def __init__(
        self,
        base_url: str = "",
        email: str = "",
        api_token: str = "",
        project_key: str = "",
        session: aiohttp.ClientSession | None = None,
        account_ids: dict[str, str] | None = None,
        issue_type: str = "Task",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.email = email
        self.api_token = api_token
        self.project_key = project_key
        self.issue_type = issue_type or "Task"
        self._issue_type_ref: dict | None = None      # {"id": ...} once resolved, {"name": ...} as the fallback
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
                "issuetype": await self._issue_type(),
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

    def _headers(self) -> dict[str, str]:
        return {"Authorization": aiohttp.encode_basic_auth(self.email, self.api_token), "Accept": "application/json"}

    async def _issue_type(self) -> dict:
        """{"id": ...} of the project's issue type matching `issue_type`; cached. Falls back to {"name": ...}."""
        if self._issue_type_ref is not None:
            return self._issue_type_ref
        fallback = {"name": self.issue_type}
        url = f"{self.base_url}/rest/api/3/issue/createmeta/{self.project_key}/issuetypes"
        try:
            session = await self._http()
            async with session.get(url, headers=self._headers(), timeout=TIMEOUT) as resp:
                data = await resp.json() if resp.status < 300 else {}
        except Exception as exc:  # noqa: BLE001 - createmeta is an optimisation; the create call reports real errors
            log.warning("recorder.jira.createmeta_failed", error=str(exc))
            data = {}
        types = [t for t in (data.get("issueTypes") or []) if isinstance(t, dict) and t.get("id")]
        standard = [t for t in types if not t.get("subtask") and t.get("hierarchyLevel", 0) == 0]
        if not standard:
            self._issue_type_ref = fallback
            return fallback

        def names(t: dict) -> set[str]:
            return {str(t.get(k, "")).casefold() for k in ("name", "untranslatedName")} - {""}

        wanted = self.issue_type.casefold()
        match = next((t for t in standard if wanted in names(t)), None)
        if match is None and wanted in TASK_ALIASES:
            match = next((t for t in standard if names(t) & TASK_ALIASES), None)
        if match is None:
            match = standard[0]
            log.warning("recorder.jira.issue_type_fallback", wanted=self.issue_type, used=match.get("name"))
        self._issue_type_ref = {"id": str(match["id"])}
        log.info("recorder.jira.issue_type", name=match.get("name"), id=match["id"])
        return self._issue_type_ref

    async def _create(self, fields: dict) -> str:
        session = await self._http()
        headers = self._headers()
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

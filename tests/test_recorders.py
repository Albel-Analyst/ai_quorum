"""Recorders: file output, request shape of the HTTP ones, and registry filtering."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import aiohttp
import pytest

from quorum.domain.models import CardState, Decision, FollowUp, Option, Record, Status, ThreadRef, TrackedThread
from quorum.recorders.base import RecordInput
from quorum.recorders.canvas import CanvasError, CanvasRecorder
from quorum.recorders.confluence import ConfluenceRecorder, md_to_storage
from quorum.recorders.jira import JiraRecorder
from quorum.recorders.markdown import MarkdownRecorder
from quorum.recorders.registry import any_available, build_recorders, primary_available
from quorum.render.markdown import render_adr

NAMES = {"U1": "Alice", "U3": "Clara"}


# --------------------------------------------------------------------------------------------
# fake aiohttp session
# --------------------------------------------------------------------------------------------
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
    """Records every call; `responses` is a list consumed in order (or one response reused)."""

    closed = False

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._call("POST", url, **kwargs)

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        return self._call("GET", url, **kwargs)

    def _call(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


JIRA_TYPES_RU = FakeResponse(200, {"issueTypes": [                      # a real Russian-language site
    {"id": "10006", "name": "Эпик", "subtask": False, "hierarchyLevel": 1},
    {"id": "10007", "name": "Подзадача", "subtask": True, "hierarchyLevel": -1},
    {"id": "10008", "name": "Задание", "subtask": False, "hierarchyLevel": 0},
    {"id": "10009", "name": "История", "subtask": False, "hierarchyLevel": 0},
]})


def sample() -> RecordInput:
    state = CardState(
        question="Which database for the events service?",
        context="The events service outgrew SQLite.",
        options=[Option(id="A", label="Postgres"), Option(id="B", label="ClickHouse")],
        status=Status.DECIDED,
        participants=["U1", "U3"],
        decision=Decision(
            option_id="B",
            summary="We go with ClickHouse.",
            rationale="Analytical read pattern.",
            owner="U1",
            decider="U1",
            confirmed_by="U1",
            confirmed_at=datetime(2026, 9, 12, 10, 30, tzinfo=UTC),
            follow_ups=[
                FollowUp(text="Provision the cluster", assignee="U3"),
                FollowUp(text="Rewrite the ingestion job"),
            ],
        ),
        updated_at=datetime(2026, 9, 12, 11, 0, tzinfo=UTC),
    )
    thread = TrackedThread(
        ref=ThreadRef(platform="slack", channel_id="C1", thread_id="1757600000.1"),
        author_id="U1",
        requested_by="U1",
        permalink="https://slack.example/archives/C1/p1757600000",
        state=state,
    )
    _, md = render_adr(thread, state, NAMES, lang="en")
    return RecordInput(thread=thread, state=state, names=NAMES, markdown=md)


# --------------------------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------------------------
async def test_markdown_recorder_writes_file(tmp_path: Path) -> None:
    inp = sample()
    recorder = MarkdownRecorder(dir=str(tmp_path / "records"))
    assert recorder.available() is True

    records = await recorder.record(inp)

    assert len(records) == 1
    rec = records[0]
    assert rec.kind == "markdown"
    assert rec.title == "ADR: Which database for the events service?"
    assert rec.url.startswith("file://")

    path = Path(rec.url.removeprefix("file://"))
    assert path.name == "2026-09-12-adr-which-database-for-the-events-service.md"
    assert path.read_text(encoding="utf-8") == inp.markdown

    # a second record of the same decision does not overwrite the first
    second = await recorder.record(inp)
    assert second[0].url != rec.url
    assert second[0].url.endswith("-2.md")


# --------------------------------------------------------------------------------------------
# confluence
# --------------------------------------------------------------------------------------------
def test_md_to_storage() -> None:
    html = md_to_storage(
        "# Title\n\n"
        "**Date:** 2026-09-12 · [link](https://x.example?a=1&b=2)\n\n"
        "## Options\n\n"
        "- **A** — Postgres\n"
        "- [ ] do the thing\n"
        "- [x] done thing\n\n"
        "1. first\n"
        "2. second\n\n"
        "plain <b>escaped</b>\n"
    )
    assert "<h1>Title</h1>" in html
    assert "<h2>Options</h2>" in html
    assert '<a href="https://x.example?a=1&amp;b=2">link</a>' in html
    assert "<strong>Date:</strong>" in html
    assert "<ul><li><strong>A</strong> — Postgres</li><li>☐ do the thing</li><li>☑ done thing</li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html
    assert "<p>plain &lt;b&gt;escaped&lt;/b&gt;</p>" in html


async def test_confluence_recorder_request_and_url() -> None:
    session = FakeSession(FakeResponse(200, {"id": "42", "_links": {"webui": "/spaces/AN/pages/42/ADR"}}))
    recorder = ConfluenceRecorder(
        base_url="https://site.atlassian.net/wiki/",
        email="bot@example.com",
        api_token="tok",
        space_key="AN",
        parent_page_id="99",
        session=session,
    )
    assert recorder.available() is True

    records = await recorder.record(sample())

    call = session.calls[0]
    assert call["url"] == "https://site.atlassian.net/wiki/rest/api/content"
    assert call["headers"]["Authorization"] == aiohttp.encode_basic_auth("bot@example.com", "tok")
    body = call["json"]
    assert body["type"] == "page"
    assert body["title"] == "ADR: Which database for the events service?"
    assert body["space"] == {"key": "AN"}
    assert body["ancestors"] == [{"id": "99"}]
    assert body["body"]["storage"]["representation"] == "storage"
    assert "<h1>ADR: Which database for the events service?</h1>" in body["body"]["storage"]["value"]

    assert records[0].kind == "confluence"
    assert records[0].url == "https://site.atlassian.net/wiki/spaces/AN/pages/42/ADR"


async def test_confluence_retries_on_duplicate_title() -> None:
    session = FakeSession(
        FakeResponse(400, {"message": "A page with this title already exists"}),
        FakeResponse(200, {"id": "43", "_links": {"webui": "/pages/43"}}),
    )
    recorder = ConfluenceRecorder("https://site.atlassian.net/wiki", "e@x", "t", "AN", session=session)

    records = await recorder.record(sample())

    assert len(session.calls) == 2
    first, second = session.calls[0]["json"]["title"], session.calls[1]["json"]["title"]
    assert second.startswith(first) and second != first        # timestamp appended
    assert records[0].title == second


async def test_confluence_raises_on_error() -> None:
    session = FakeSession(FakeResponse(500, "boom"))
    recorder = ConfluenceRecorder("https://site.atlassian.net/wiki", "e@x", "t", "AN", session=session)
    with pytest.raises(RuntimeError, match="Confluence 500"):
        await recorder.record(sample())


def test_confluence_availability() -> None:
    assert ConfluenceRecorder().available() is False
    assert ConfluenceRecorder("https://w", "e", "t", "AN").available() is True
    assert ConfluenceRecorder("https://w", "e", "", "AN").available() is False


# --------------------------------------------------------------------------------------------
# jira
# --------------------------------------------------------------------------------------------
async def test_jira_one_issue_per_follow_up() -> None:
    types = FakeResponse(200, {"issueTypes": [{"id": "10001", "name": "Task"}, {"id": "10002", "name": "Sub-task", "subtask": True}]})
    session = FakeSession(types, FakeResponse(201, {"key": "Q-1"}), FakeResponse(201, {"key": "Q-2"}))
    recorder = JiraRecorder("https://site.atlassian.net/", "bot@example.com", "tok", "Q", session=session)
    inp = sample()
    inp.state.records.append(Record(kind="confluence", title="ADR", url="https://wiki.example/x"))

    records = await recorder.record(inp)

    assert [r.title for r in records] == ["Q-1", "Q-2"]
    assert [r.url for r in records] == ["https://site.atlassian.net/browse/Q-1",
                                        "https://site.atlassian.net/browse/Q-2"]
    assert [r.kind for r in records] == ["jira", "jira"]

    meta = session.calls[0]
    assert meta["method"] == "GET"
    assert meta["url"] == "https://site.atlassian.net/rest/api/3/issue/createmeta/Q/issuetypes"
    call = session.calls[1]
    assert call["url"] == "https://site.atlassian.net/rest/api/3/issue"
    assert call["headers"]["Authorization"] == aiohttp.encode_basic_auth("bot@example.com", "tok")
    fields = call["json"]["fields"]
    assert fields["project"] == {"key": "Q"}
    assert fields["issuetype"] == {"id": "10001"}     # resolved once through createmeta, sent by id
    assert len([c for c in session.calls if c["method"] == "GET"]) == 1   # cached for the second issue
    assert fields["summary"] == "Provision the cluster"
    assert "assignee" not in fields                    # no accountId mapping -> unassigned
    description = json.dumps(fields["description"], ensure_ascii=False)
    assert '"type": "doc"' in description
    assert "https://wiki.example/x" in description     # the ADR link
    assert "We go with ClickHouse." in description
    assert "Clara" in description                      # the name instead of an assignee field

    # the state now carries the issue urls, so the ADR/card can show them
    assert [fu.issue_url for fu in inp.state.decision.follow_ups] == [
        "https://site.atlassian.net/browse/Q-1",
        "https://site.atlassian.net/browse/Q-2",
    ]


async def test_jira_issue_type_on_localised_site() -> None:
    """A Russian Jira knows no "Task": the translated task type is picked, never the epic (level 1) or a sub-task."""
    session = FakeSession(JIRA_TYPES_RU, FakeResponse(201, {"key": "KAN-1"}))
    recorder = JiraRecorder("https://s", "e", "t", "KAN", session=session)
    inp = sample()
    inp.state.decision.follow_ups = [FollowUp(text="Provision the cluster")]

    await recorder.record(inp)
    assert session.calls[1]["json"]["fields"]["issuetype"] == {"id": "10008"}   # Задание == Task

    # an explicit name (any case) wins
    session = FakeSession(JIRA_TYPES_RU, FakeResponse(201, {"key": "KAN-2"}))
    recorder = JiraRecorder("https://s", "e", "t", "KAN", session=session, issue_type="история")
    await recorder.record(inp)
    assert session.calls[1]["json"]["fields"]["issuetype"] == {"id": "10009"}

    # an unknown name on a site without a task-like type: first standard type, epics and sub-tasks skipped
    session = FakeSession(JIRA_TYPES_RU, FakeResponse(201, {"key": "KAN-3"}))
    recorder = JiraRecorder("https://s", "e", "t", "KAN", session=session, issue_type="Bug")
    await recorder.record(inp)
    assert session.calls[1]["json"]["fields"]["issuetype"] == {"id": "10008"}


async def test_jira_issue_type_falls_back_to_name_without_createmeta() -> None:
    session = FakeSession(FakeResponse(403, "nope"), FakeResponse(201, {"key": "Q-3"}))
    recorder = JiraRecorder("https://s", "e", "t", "Q", session=session)
    inp = sample()
    inp.state.decision.follow_ups = [FollowUp(text="Provision the cluster")]

    await recorder.record(inp)
    assert session.calls[1]["json"]["fields"]["issuetype"] == {"name": "Task"}


async def test_jira_assignee_when_mapped() -> None:
    session = FakeSession(FakeResponse(201, {"key": "Q-9"}))
    recorder = JiraRecorder("https://s", "e", "t", "Q", session=session, account_ids={"U3": "acc-3"})
    inp = sample()
    inp.state.decision.follow_ups = [FollowUp(text="Provision the cluster", assignee="U3")]

    await recorder.record(inp)
    assert session.calls[-1]["json"]["fields"]["assignee"] == {"id": "acc-3"}


async def test_jira_availability_and_no_follow_ups() -> None:
    recorder = JiraRecorder("https://s", "e", "t", "Q", session=FakeSession(FakeResponse(201, {"key": "Q-1"})))
    assert recorder.available() is True
    inp = sample()
    assert recorder.available(inp.state) is True

    inp.state.decision.follow_ups = []
    assert recorder.available(inp.state) is False
    assert await recorder.record(inp) == []            # nothing to file, not an error

    assert JiraRecorder().available() is False


async def test_jira_raises_on_error() -> None:
    session = FakeSession(FakeResponse(403, "nope"))
    recorder = JiraRecorder("https://s", "e", "t", "Q", session=session)
    with pytest.raises(RuntimeError, match="Jira 403"):
        await recorder.record(sample())


# --------------------------------------------------------------------------------------------
# canvas
# --------------------------------------------------------------------------------------------
async def test_canvas_recorder() -> None:
    session = FakeSession(
        FakeResponse(200, {"ok": True, "canvas_id": "F123"}),
        FakeResponse(200, {"ok": True, "team_id": "T42"}),
    )
    recorder = CanvasRecorder(bot_token="xoxb-1", enabled=True, session=session)
    assert recorder.available() is True

    inp = sample()
    records = await recorder.record(inp)

    create = session.calls[0]
    assert create["url"] == "https://slack.com/api/canvases.create"
    assert create["headers"]["Authorization"] == "Bearer xoxb-1"
    assert create["json"]["title"] == "ADR: Which database for the events service?"
    assert create["json"]["document_content"] == {"type": "markdown", "markdown": inp.markdown}
    assert session.calls[1]["url"] == "https://slack.com/api/auth.test"

    assert records[0].kind == "canvas"
    assert records[0].url == "https://slack.com/docs/T42/F123"

    # team id is cached: the second record only calls canvases.create
    session.responses = [FakeResponse(200, {"ok": True, "canvas_id": "F999"})]
    await recorder.record(inp)
    assert [c["url"] for c in session.calls[2:]] == ["https://slack.com/api/canvases.create"]


async def test_canvas_missing_scope() -> None:
    session = FakeSession(FakeResponse(200, {"ok": False, "error": "missing_scope"}))
    recorder = CanvasRecorder(bot_token="xoxb-1", enabled=True, session=session)
    with pytest.raises(CanvasError, match="canvases:write"):
        await recorder.record(sample())


def test_canvas_availability() -> None:
    assert CanvasRecorder(bot_token="xoxb", enabled=False).available() is False
    assert CanvasRecorder(bot_token="", enabled=True).available() is False
    assert CanvasRecorder(bot_token="xoxb", enabled=True).available() is True


# --------------------------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------------------------
def fake_settings(**over: object) -> SimpleNamespace:
    base = {
        "confluence_base_url": "", "confluence_email": "", "confluence_api_token": "",
        "confluence_space_key": "", "confluence_parent_page_id": "",
        "jira_base_url": "", "jira_email": "", "jira_api_token": "", "jira_project_key": "",
        "slack_bot_token": "", "slack_canvas_enabled": False, "markdown_records_dir": "records",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_registry_markdown_only() -> None:
    recorders = build_recorders(fake_settings())
    assert [r.kind for r in recorders] == ["markdown"]
    assert any_available(recorders) is True
    assert primary_available(recorders) is False


def test_registry_priority_order() -> None:
    recorders = build_recorders(fake_settings(
        confluence_base_url="https://w", confluence_email="e", confluence_api_token="t",
        confluence_space_key="AN",
        jira_base_url="https://j", jira_email="e", jira_api_token="t", jira_project_key="Q",
        slack_bot_token="xoxb", slack_canvas_enabled=True,
    ))
    assert [r.kind for r in recorders] == ["confluence", "canvas", "jira", "markdown"]
    assert primary_available(recorders) is True


def test_registry_partial_config() -> None:
    recorders = build_recorders(fake_settings(
        jira_base_url="https://j", jira_email="e", jira_api_token="t", jira_project_key="Q",
        slack_bot_token="xoxb",           # canvas disabled by the flag
    ))
    assert [r.kind for r in recorders] == ["jira", "markdown"]
    assert primary_available(recorders) is True

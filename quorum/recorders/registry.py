"""Which recorders exist for this deployment. Priority order: confluence, canvas, jira, markdown."""
from __future__ import annotations

import structlog

from quorum.recorders.base import Recorder
from quorum.recorders.canvas import CanvasRecorder
from quorum.recorders.confluence import ConfluenceRecorder
from quorum.recorders.jira import JiraRecorder
from quorum.recorders.markdown import MarkdownRecorder

log = structlog.get_logger(__name__)

PRIORITY = ("confluence", "canvas", "jira", "markdown")


def build_recorders(settings) -> list[Recorder]:   # settings: quorum.config.Settings (kept loose for tests)
    """All configured recorders, in the order the engine should run them (markdown is always last and always on)."""
    candidates: list[Recorder] = [
        ConfluenceRecorder(
            base_url=settings.confluence_base_url,
            email=settings.confluence_email,
            api_token=settings.confluence_api_token,
            space_key=settings.confluence_space_key,
            parent_page_id=settings.confluence_parent_page_id,
        ),
        CanvasRecorder(bot_token=settings.slack_bot_token, enabled=settings.slack_canvas_enabled),
        JiraRecorder(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            project_key=settings.jira_project_key,
        ),
        MarkdownRecorder(dir=settings.markdown_records_dir),
    ]
    recorders = [r for r in candidates if r.available()]
    log.info("recorders.built", kinds=[r.kind for r in recorders])
    return recorders


def any_available(recorders: list[Recorder]) -> bool:
    """The card shows [Record] whenever at least one recorder exists (markdown counts)."""
    return bool(recorders)


def primary_available(recorders: list[Recorder]) -> bool:
    """True when something better than a local markdown file is configured."""
    return any(r.kind != "markdown" for r in recorders)

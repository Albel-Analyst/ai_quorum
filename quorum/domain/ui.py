"""Platform-neutral UI primitives. Renderers turn them into Block Kit (Slack) or components (Discord).

Only what the product needs: a card is rendered from CardState directly by the platform renderer; here are the
small things — a notice with buttons (ephemeral / DM / mini-card), and a form (modal)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Button(BaseModel):
    label: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    style: Literal["default", "primary", "danger"] = "default"
    url: str | None = None                   # link button: opens url, no event


class Notice(BaseModel):
    """Short message with buttons: ephemeral suggestion, DM nudge, memory recall mini-card."""

    text: str                                # markdown-ish (bold with *...*), single paragraph
    buttons: list[Button] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)     # optional extra lines under the text


class FormField(BaseModel):
    id: str
    label: str
    kind: Literal["select", "text", "textarea", "date", "user"]
    options: list[tuple[str, str]] = Field(default_factory=list)  # (value, label) for select
    optional: bool = False
    placeholder: str = ""
    initial: str | None = None


class Form(BaseModel):
    id: str                                  # callback id
    title: str
    submit_label: str
    fields: list[FormField]
    payload: dict[str, Any] = Field(default_factory=dict)  # echoed back on submit (thread key etc.)
    intro: str = ""

"""Raw Slack payloads -> normalized domain events. Pure functions, no network: everything here is unit-testable.

The adapter owns the transport (Bolt, Socket Mode); this module owns the shape of what arrives.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from quorum.domain.events import (
    ButtonPressed,
    FormSubmitted,
    MessageChanged,
    MessageDeleted,
    MessagePosted,
)
from quorum.domain.models import Message, ThreadRef

PLATFORM = "slack"
SHORTCUT_CALLBACK_ID = "track_with_quorum"
TRACK_REACTION = "scales"          # ⚖️
ACTION_PREFIX = "q:"
NONE_VALUE = "__none__"

#: message subtypes that carry a normal human message
TEXT_SUBTYPES = {None, "", "thread_broadcast", "file_share"}
#: subtypes we never turn into events (join/leave noise, our own posts, edit echoes)
IGNORED_SUBTYPES = {"bot_message", "channel_join", "channel_leave", "message_replied", "tombstone"}

#: `quorum/tools/seed_demo.py` posts a seeded thread under `username` (chat:write.customize); those messages
#: arrive as bot messages, and Quorum must read them as if a human wrote them.
PERSONA_PREFIX = "persona:"

_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")


def parse_personas(raw: str | None) -> set[str]:
    """`"Ann, Bob ,, Cid"` -> {"Ann", "Bob", "Cid"} (the `QUORUM_DEMO_PERSONAS` / `demo_personas` setting)."""
    return {name.strip() for name in (raw or "").split(",") if name.strip()}


def persona_of(payload: dict[str, Any], personas: set[str] | None) -> str | None:
    """The persona username of a bot message posted under one of `personas`, else None."""
    if not personas or not payload.get("bot_id"):
        return None
    username = (payload.get("username") or "").strip()
    return username if username in personas else None


def mentions_in(text: str) -> list[str]:
    seen: list[str] = []
    for user_id in _MENTION.findall(text or ""):
        if user_id not in seen:
            seen.append(user_id)
    return seen


def ts_to_dt(ts: str | float | None) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts), UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return datetime.now(UTC)


def thread_ref(channel_id: str, thread_id: str) -> ThreadRef:
    return ThreadRef(platform=PLATFORM, channel_id=channel_id, thread_id=thread_id)


def thread_key(channel_id: str, thread_id: str) -> str:
    return f"{PLATFORM}:{channel_id}:{thread_id}"


def _is_bot(payload: dict[str, Any], bot_user_id: str) -> bool:
    return bool(payload.get("bot_id")) or (bool(bot_user_id) and payload.get("user") == bot_user_id)


def to_message(
    payload: dict[str, Any],
    names_hint: dict[str, str] | None = None,
    *,
    is_bot: bool = False,
    personas: set[str] | None = None,
) -> Message:
    persona = persona_of(payload, personas)
    user_id = f"{PERSONA_PREFIX}{persona}" if persona else (payload.get("user") or payload.get("bot_id") or "")
    text = payload.get("text") or ""
    return Message(
        id=payload.get("ts") or "",
        user_id=user_id,
        user_name=persona or (names_hint or {}).get(user_id, ""),
        text=text,
        at=ts_to_dt(payload.get("ts")),
        is_bot=False if persona else is_bot,
        mentions=mentions_in(text),
    )


def message_event_to_event(
    event: dict[str, Any],
    bot_user_id: str,
    names_hint: dict[str, str] | None = None,
    *,
    personas: set[str] | None = None,
) -> MessagePosted | MessageChanged | MessageDeleted | None:
    """Normalize a `message` event. Returns None for anything Quorum must not react to.

    `personas` are `username`s of seeded demo messages (see `parse_personas`): they arrive as bot messages but
    are treated as humans (`user_id = "persona:<username>"`).
    """
    subtype = event.get("subtype")
    channel = event.get("channel") or ""

    if subtype == "message_changed":
        inner = event.get("message") or {}
        if persona_of(inner, personas) is None and _is_bot(inner, bot_user_id):
            return None
        ts = inner.get("ts") or ""
        thread_ts = inner.get("thread_ts")
        return MessageChanged(
            thread=thread_ref(channel, thread_ts or ts),
            message=to_message(inner, names_hint, personas=personas),
        )

    if subtype == "message_deleted":
        deleted_ts = event.get("deleted_ts") or ""
        previous = event.get("previous_message") or {}
        if persona_of(previous, personas) is None and _is_bot(previous, bot_user_id):
            return None
        thread_ts = previous.get("thread_ts")
        return MessageDeleted(
            thread=thread_ref(channel, thread_ts or deleted_ts),
            message_id=deleted_ts,
        )

    persona = persona_of(event, personas)

    if persona is None and subtype in IGNORED_SUBTYPES:
        return None

    if persona is not None or subtype in TEXT_SUBTYPES:
        if persona is None and _is_bot(event, bot_user_id):
            return None
        ts = event.get("ts") or ""
        thread_ts = event.get("thread_ts")
        return MessagePosted(
            thread=thread_ref(channel, thread_ts or ts),
            message=to_message(event, names_hint, personas=personas),
            in_thread=bool(thread_ts and thread_ts != ts),
        )

    return None


def app_mention_root(event: dict[str, Any], names_hint: dict[str, str] | None = None) -> tuple[ThreadRef, str, Message]:
    """(thread, requested_by, the mention message). A mention on a top-level message makes it the root."""
    ts = event.get("ts") or ""
    thread_ts = event.get("thread_ts")
    ref = thread_ref(event.get("channel") or "", thread_ts or ts)
    return ref, event.get("user") or "", to_message(event, names_hint)


def _parse_value(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _thread_from_payload(payload: dict[str, Any]) -> ThreadRef | None:
    key = payload.get("t")
    if not isinstance(key, str) or key.count(":") < 2:
        return None
    try:
        return ThreadRef.from_key(key)
    except ValueError:
        return None


def action_name(action_id: str) -> str:
    """`q:vote:A` -> `vote` (the suffix only makes the id unique inside a block; the payload carries the data)."""
    return action_id.removeprefix(ACTION_PREFIX).split(":", 1)[0]


def block_action_to_event(body: dict[str, Any], action: dict[str, Any]) -> ButtonPressed:
    payload = _parse_value(action.get("value"))
    container = body.get("container") or {}
    channel_id = container.get("channel_id") or (body.get("channel") or {}).get("id")
    message_id = container.get("message_ts") or (body.get("message") or {}).get("ts")
    return ButtonPressed(
        thread=_thread_from_payload(payload),
        user_id=(body.get("user") or {}).get("id", ""),
        action=action_name(action.get("action_id") or ""),
        payload=payload,
        trigger_id=body.get("trigger_id"),
        message_id=message_id,
        channel_id=channel_id,
        response_url=body.get("response_url"),
    )


def _field_value(state_value: dict[str, Any]) -> Any:
    kind = state_value.get("type")
    if kind == "static_select":
        selected = state_value.get("selected_option") or {}
        value = selected.get("value") or ""
        return "" if value == NONE_VALUE else value
    if kind == "multi_static_select":
        return [o.get("value") for o in state_value.get("selected_options") or []]
    if kind == "datepicker":
        return state_value.get("selected_date") or ""
    if kind == "timepicker":
        return state_value.get("selected_time") or ""
    if kind == "users_select":
        return state_value.get("selected_user") or ""
    if kind == "multi_users_select":
        return state_value.get("selected_users") or []
    if kind == "plain_text_input":
        return state_value.get("value") or ""
    if kind == "checkboxes":
        return [o.get("value") for o in state_value.get("selected_options") or []]
    return state_value.get("value") or ""


def view_values(view: dict[str, Any]) -> dict[str, Any]:
    """Flatten `view.state.values` ({block_id: {action_id: {...}}}) to {field_id: value}."""
    values: dict[str, Any] = {}
    for fields in ((view.get("state") or {}).get("values") or {}).values():
        for action_id, state_value in fields.items():
            values[action_id] = _field_value(state_value)
    return values


def view_submission_to_event(body: dict[str, Any]) -> FormSubmitted:
    view = body.get("view") or {}
    payload = _parse_value(view.get("private_metadata"))
    return FormSubmitted(
        thread=_thread_from_payload(payload),
        user_id=(body.get("user") or {}).get("id", ""),
        form_id=view.get("callback_id") or "",
        values=view_values(view),
        payload=payload,
    )


def shortcut_to_thread(body: dict[str, Any]) -> tuple[ThreadRef, str, str] | None:
    """Message shortcut `track_with_quorum` -> (thread, user_id, root ts). None for any other shortcut."""
    if (body.get("callback_id") or "") != SHORTCUT_CALLBACK_ID:
        return None
    message = body.get("message") or {}
    channel = (body.get("channel") or {}).get("id") or ""
    root = message.get("thread_ts") or message.get("ts") or body.get("message_ts") or ""
    if not channel or not root:
        return None
    return thread_ref(channel, root), (body.get("user") or {}).get("id", ""), root


def reaction_to_item(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """⚖️ on a message -> (channel, ts, user). None for any other reaction or item type."""
    if (event.get("reaction") or "") != TRACK_REACTION:
        return None
    item = event.get("item") or {}
    if item.get("type") != "message":
        return None
    channel, ts = item.get("channel") or "", item.get("ts") or ""
    if not channel or not ts:
        return None
    return channel, ts, event.get("user") or ""

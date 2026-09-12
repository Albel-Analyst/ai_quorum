"""Raw Slack payloads -> normalized events. Pure functions: no Bolt, no socket, no network.

The fixtures are trimmed copies of what Slack actually delivers (the fields Quorum reads, plus a bit of noise).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from quorum.config import Settings
from quorum.domain.events import (
    ButtonPressed,
    FormSubmitted,
    MessageChanged,
    MessageDeleted,
    MessagePosted,
)
from quorum.domain.models import Message, ThreadRef
from quorum.platform.slack import normalize as nz

BOT_USER = "U0QUORUM"
BOT_ID = "B0QUORUM"
CHANNEL = "C0DECIDE"
ROOT_TS = "1700000000.000100"
REPLY_TS = "1700000200.000300"
ANN, BOB, CID, DAN = "U0ANN", "U0BOB", "U0CID", "U0DAN"

ROOT = ThreadRef(platform="slack", channel_id=CHANNEL, thread_id=ROOT_TS)
KEY = ROOT.key
PERSONAS = {"Ann", "Bob", "Cid"}


def at(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), UTC)


# --------------------------------------------------------------------------------------------------
# message events
# --------------------------------------------------------------------------------------------------
def test_plain_channel_message() -> None:
    event = {
        "type": "message",
        "channel": CHANNEL,
        "channel_type": "channel",
        "user": ANN,
        "text": "Postgres or Mongo for the new service?",
        "ts": ROOT_TS,
        "event_ts": ROOT_TS,
        "team": "T0TEAM",
        "client_msg_id": "8e0a-1",
    }
    assert nz.message_event_to_event(event, BOT_USER) == MessagePosted(
        thread=ROOT,
        message=Message(
            id=ROOT_TS,
            user_id=ANN,
            user_name="",
            text="Postgres or Mongo for the new service?",
            at=at(ROOT_TS),
            is_bot=False,
            mentions=[],
        ),
        in_thread=False,
    )


def test_thread_reply_with_a_mention() -> None:
    event = {
        "type": "message",
        "channel": CHANNEL,
        "user": BOB,
        "text": "<@U0CID> how much is Atlas M10? <@U0CID> again",
        "ts": REPLY_TS,
        "thread_ts": ROOT_TS,
        "parent_user_id": ANN,
    }
    assert nz.message_event_to_event(event, BOT_USER, {BOB: "Bob"}) == MessagePosted(
        thread=ROOT,
        message=Message(
            id=REPLY_TS,
            user_id=BOB,
            user_name="Bob",
            text="<@U0CID> how much is Atlas M10? <@U0CID> again",
            at=at(REPLY_TS),
            is_bot=False,
            mentions=[CID],
        ),
        in_thread=True,
    )


def test_thread_broadcast_is_a_reply() -> None:
    event = {
        "type": "message",
        "subtype": "thread_broadcast",
        "channel": CHANNEL,
        "user": CID,
        "text": "decided: Postgres",
        "ts": "1700000300.000400",
        "thread_ts": ROOT_TS,
        "root": {"ts": ROOT_TS},
    }
    normalized = nz.message_event_to_event(event, BOT_USER)
    assert isinstance(normalized, MessagePosted)
    assert normalized.in_thread is True
    assert normalized.thread == ROOT
    assert normalized.message.id == "1700000300.000400"


def test_root_message_that_already_has_replies_is_not_in_thread() -> None:
    event = {"type": "message", "channel": CHANNEL, "user": ANN, "text": "root", "ts": ROOT_TS, "thread_ts": ROOT_TS}
    normalized = nz.message_event_to_event(event, BOT_USER)
    assert isinstance(normalized, MessagePosted)
    assert normalized.in_thread is False


def test_message_changed() -> None:
    event = {
        "type": "message",
        "subtype": "message_changed",
        "channel": CHANNEL,
        "ts": "1700000500.000000",
        "hidden": True,
        "message": {
            "type": "message",
            "user": BOB,
            "text": "Postgres, but with JSONB <@U0ANN>",
            "ts": REPLY_TS,
            "thread_ts": ROOT_TS,
            "edited": {"user": BOB, "ts": "1700000500.000000"},
        },
        "previous_message": {"type": "message", "user": BOB, "text": "Postgres", "ts": REPLY_TS, "thread_ts": ROOT_TS},
    }
    assert nz.message_event_to_event(event, BOT_USER) == MessageChanged(
        thread=ROOT,
        message=Message(
            id=REPLY_TS,
            user_id=BOB,
            user_name="",
            text="Postgres, but with JSONB <@U0ANN>",
            at=at(REPLY_TS),
            is_bot=False,
            mentions=[ANN],
        ),
    )


def test_message_deleted() -> None:
    event = {
        "type": "message",
        "subtype": "message_deleted",
        "channel": CHANNEL,
        "ts": "1700000600.000000",
        "hidden": True,
        "deleted_ts": REPLY_TS,
        "previous_message": {"type": "message", "user": BOB, "text": "oops", "ts": REPLY_TS, "thread_ts": ROOT_TS},
    }
    assert nz.message_event_to_event(event, BOT_USER) == MessageDeleted(thread=ROOT, message_id=REPLY_TS)


def test_deleted_top_level_message_keeps_its_own_ts_as_the_thread() -> None:
    event = {
        "type": "message",
        "subtype": "message_deleted",
        "channel": CHANNEL,
        "deleted_ts": ROOT_TS,
        "previous_message": {"type": "message", "user": ANN, "text": "root", "ts": ROOT_TS},
    }
    assert nz.message_event_to_event(event, BOT_USER) == MessageDeleted(thread=ROOT, message_id=ROOT_TS)


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(
            {
                "type": "message",
                "subtype": "bot_message",
                "channel": CHANNEL,
                "bot_id": "B0ZAPIER",
                "username": "Zapier",
                "text": "build finished",
                "ts": "1700000400.000500",
            },
            id="bot_message",
        ),
        pytest.param(
            {
                "type": "message",
                "channel": CHANNEL,
                "user": BOT_USER,
                "bot_id": BOT_ID,
                "text": "the card",
                "ts": "1700000400.000600",
                "thread_ts": ROOT_TS,
                "blocks": [{"type": "section"}],
            },
            id="our_own_card",
        ),
        pytest.param(
            {"type": "message", "channel": CHANNEL, "user": BOT_USER, "text": "no bot_id, still us", "ts": "1.0"},
            id="our_own_user_id",
        ),
        pytest.param(
            {
                "type": "message",
                "subtype": "channel_join",
                "channel": CHANNEL,
                "user": DAN,
                "text": "<@U0DAN> has joined the channel",
                "ts": "1700000400.000700",
            },
            id="channel_join",
        ),
        pytest.param(
            {"type": "message", "subtype": "channel_leave", "channel": CHANNEL, "user": DAN, "ts": "1.0"},
            id="channel_leave",
        ),
        pytest.param(
            {"type": "message", "subtype": "message_replied", "channel": CHANNEL, "hidden": True, "ts": "1.0"},
            id="message_replied",
        ),
        pytest.param(
            {"type": "message", "subtype": "channel_topic", "channel": CHANNEL, "user": ANN, "ts": "1.0"},
            id="unknown_subtype",
        ),
        pytest.param(
            {
                "type": "message",
                "subtype": "message_changed",
                "channel": CHANNEL,
                "message": {"user": BOT_USER, "bot_id": BOT_ID, "text": "card v2", "ts": "1.0"},
            },
            id="our_own_card_edited",
        ),
        pytest.param(
            {
                "type": "message",
                "subtype": "message_deleted",
                "channel": CHANNEL,
                "deleted_ts": "1.0",
                "previous_message": {"bot_id": "B0ZAPIER", "text": "gone", "ts": "1.0"},
            },
            id="bot_message_deleted",
        ),
    ],
)
def test_messages_quorum_must_ignore(event: dict[str, Any]) -> None:
    assert nz.message_event_to_event(event, BOT_USER) is None


def test_file_share_is_a_normal_message() -> None:
    event = {
        "type": "message",
        "subtype": "file_share",
        "channel": CHANNEL,
        "user": ANN,
        "text": "the benchmark",
        "ts": REPLY_TS,
        "thread_ts": ROOT_TS,
        "files": [{"id": "F1"}],
    }
    normalized = nz.message_event_to_event(event, BOT_USER)
    assert isinstance(normalized, MessagePosted)
    assert normalized.message.text == "the benchmark"


def test_ts_to_dt_survives_garbage() -> None:
    assert nz.ts_to_dt("1700000000.000100") == at(ROOT_TS)
    assert nz.ts_to_dt(None).tzinfo is UTC
    assert nz.ts_to_dt("not-a-ts").tzinfo is UTC


# --------------------------------------------------------------------------------------------------
# app_mention
# --------------------------------------------------------------------------------------------------
def test_app_mention_on_a_top_level_message_makes_it_the_root() -> None:
    event = {
        "type": "app_mention",
        "channel": CHANNEL,
        "user": ANN,
        "text": f"<@{BOT_USER}> track this: Postgres or Mongo?",
        "ts": ROOT_TS,
        "event_ts": ROOT_TS,
    }
    ref, requested_by, message = nz.app_mention_root(event)
    assert ref == ROOT
    assert requested_by == ANN
    assert message.id == ROOT_TS               # the mention *is* the root: the adapter passes it as root_message
    assert message.mentions == [BOT_USER]
    assert message.at == at(ROOT_TS)


def test_app_mention_on_a_reply_points_at_the_root() -> None:
    event = {
        "type": "app_mention",
        "channel": CHANNEL,
        "user": BOB,
        "text": f"<@{BOT_USER}> please follow",
        "ts": REPLY_TS,
        "thread_ts": ROOT_TS,
    }
    ref, requested_by, message = nz.app_mention_root(event)
    assert ref == ROOT
    assert requested_by == BOB
    assert message.id == REPLY_TS              # != ref.thread_id -> the adapter sends root_message=None


# --------------------------------------------------------------------------------------------------
# block_actions
# --------------------------------------------------------------------------------------------------
def block_actions_body(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "block_actions",
        "team": {"id": "T0TEAM"},
        "user": {"id": BOB, "username": "bob", "name": "bob"},
        "api_app_id": "A0QUORUM",
        "token": "verification-token",
        "container": {
            "type": "message",
            "message_ts": "1700000000.000200",
            "channel_id": CHANNEL,
            "is_ephemeral": False,
            "thread_ts": ROOT_TS,
        },
        "trigger_id": "8001.2002.abcdef",
        "channel": {"id": CHANNEL, "name": "decisions"},
        "message": {"type": "message", "ts": "1700000000.000200", "bot_id": BOT_ID, "text": "the card"},
        "response_url": "https://hooks.slack.com/actions/T0TEAM/8001/xyz",
        "actions": [action],
    }


def test_block_action_vote() -> None:
    action = {
        "type": "button",
        "action_id": "q:vote",
        "block_id": "abc",
        "text": {"type": "plain_text", "text": "Vote B"},
        "value": json.dumps({"t": KEY, "option_id": "B"}),
        "action_ts": "1700000900.000000",
    }
    assert nz.block_action_to_event(block_actions_body(action), action) == ButtonPressed(
        thread=ROOT,
        user_id=BOB,
        action="vote",
        payload={"t": KEY, "option_id": "B"},
        trigger_id="8001.2002.abcdef",
        message_id="1700000000.000200",
        channel_id=CHANNEL,
        response_url="https://hooks.slack.com/actions/T0TEAM/8001/xyz",
    )


def test_block_action_without_a_thread_payload() -> None:
    action = {"type": "button", "action_id": "q:track_no", "value": json.dumps({"x": 1})}
    pressed = nz.block_action_to_event(block_actions_body(action), action)
    assert pressed.thread is None
    assert pressed.payload == {"x": 1}
    assert pressed.action == "track_no"


@pytest.mark.parametrize("value", [None, "", "not json", "[1,2]", json.dumps({"t": "broken"})])
def test_block_action_with_an_unusable_value(value: str | None) -> None:
    action = {"type": "button", "action_id": "q:unpark", "value": value}
    pressed = nz.block_action_to_event(block_actions_body(action), action)
    assert pressed.thread is None
    assert pressed.action == "unpark"


def test_block_action_from_an_ephemeral_without_a_container_channel() -> None:
    action = {"type": "button", "action_id": "q:track_yes", "value": json.dumps({"t": KEY})}
    body = block_actions_body(action)
    body["container"] = {"type": "message", "is_ephemeral": True}
    pressed = nz.block_action_to_event(body, action)
    assert pressed.channel_id == CHANNEL       # falls back to body["channel"]["id"]
    assert pressed.message_id == "1700000000.000200"
    assert pressed.thread == ROOT


def test_action_name_strips_the_prefix() -> None:
    assert nz.action_name("q:my_position") == "my_position"
    assert nz.action_name("something_else") == "something_else"


# --------------------------------------------------------------------------------------------------
# view_submission
# --------------------------------------------------------------------------------------------------
def view_body(callback_id: str, values: dict[str, Any], *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "view_submission",
        "team": {"id": "T0TEAM"},
        "user": {"id": ANN, "username": "ann"},
        "trigger_id": "8001.2002.ghijkl",
        "view": {
            "id": "V0FORM",
            "type": "modal",
            "callback_id": callback_id,
            "private_metadata": json.dumps({"t": KEY} if payload is None else payload),
            "state": {"values": values},
            "hash": "1700000000.abcdef",
        },
    }


def test_view_submission_confirm_form() -> None:
    body = view_body(
        "confirm",
        {
            "option": {
                "option": {
                    "type": "static_select",
                    "selected_option": {"text": {"type": "plain_text", "text": "B · Mongo"}, "value": "B"},
                }
            },
            "owner": {"owner": {"type": "users_select", "selected_user": DAN}},
            "note": {"note": {"type": "plain_text_input", "value": "we accept the ops cost\nrevisit in Q1"}},
        },
    )
    assert nz.view_submission_to_event(body) == FormSubmitted(
        thread=ROOT,
        user_id=ANN,
        form_id="confirm",
        values={"option": "B", "owner": DAN, "note": "we accept the ops cost\nrevisit in Q1"},
        payload={"t": KEY},
    )


def test_view_submission_my_position_none_becomes_empty() -> None:
    body = view_body(
        "my_position",
        {
            "option": {
                "option": {
                    "type": "static_select",
                    "selected_option": {"text": {"type": "plain_text", "text": "No option yet"}, "value": nz.NONE_VALUE},
                }
            },
            "argument": {"argument": {"type": "plain_text_input", "value": "depends on the read pattern"}},
        },
    )
    submitted = nz.view_submission_to_event(body)
    assert submitted.form_id == "my_position"
    assert submitted.values == {"option": "", "argument": "depends on the read pattern"}


def test_view_submission_deadline_and_empty_optionals() -> None:
    body = view_body(
        "deadline",
        {
            "date": {"date": {"type": "datepicker", "selected_date": "2026-09-30"}},
            "time": {"time": {"type": "plain_text_input", "value": None}},
        },
    )
    submitted = nz.view_submission_to_event(body)
    assert submitted.values == {"date": "2026-09-30", "time": ""}


def test_view_submission_untouched_optional_fields() -> None:
    body = view_body(
        "park",
        {
            "reason": {"reason": {"type": "plain_text_input", "value": "waiting for the budget"}},
            "return": {"return": {"type": "datepicker", "selected_date": None}},
            "who": {"who": {"type": "users_select", "selected_user": None}},
            "pick": {"pick": {"type": "static_select", "selected_option": None}},
        },
    )
    submitted = nz.view_submission_to_event(body)
    assert submitted.values == {"reason": "waiting for the budget", "return": "", "who": "", "pick": ""}


def test_view_submission_without_private_metadata() -> None:
    body = view_body("my_position", {}, payload={})
    submitted = nz.view_submission_to_event(body)
    assert submitted.thread is None
    assert submitted.payload == {}
    assert submitted.values == {}


def test_view_values_handles_the_multi_variants() -> None:
    view = {
        "state": {
            "values": {
                "a": {"a": {"type": "multi_static_select", "selected_options": [{"value": "x"}, {"value": "y"}]}},
                "b": {"b": {"type": "multi_users_select", "selected_users": [ANN, BOB]}},
                "c": {"c": {"type": "checkboxes", "selected_options": [{"value": "z"}]}},
                "d": {"d": {"type": "timepicker", "selected_time": "18:00"}},
                "e": {"e": {"type": "email_text_input", "value": "a@b.c"}},
            }
        }
    }
    assert nz.view_values(view) == {"a": ["x", "y"], "b": [ANN, BOB], "c": ["z"], "d": "18:00", "e": "a@b.c"}


# --------------------------------------------------------------------------------------------------
# message shortcut & reaction
# --------------------------------------------------------------------------------------------------
def shortcut_body(message: dict[str, Any], *, callback_id: str = nz.SHORTCUT_CALLBACK_ID) -> dict[str, Any]:
    return {
        "type": "message_action",
        "callback_id": callback_id,
        "trigger_id": "8001.2002.mnopqr",
        "response_url": "https://hooks.slack.com/app/T0TEAM/8001/xyz",
        "team": {"id": "T0TEAM"},
        "user": {"id": BOB, "username": "bob"},
        "channel": {"id": CHANNEL, "name": "decisions"},
        "message_ts": message.get("ts"),
        "message": message,
    }


def test_shortcut_on_a_reply_tracks_the_root() -> None:
    body = shortcut_body({"type": "message", "user": CID, "ts": REPLY_TS, "thread_ts": ROOT_TS, "text": "reply"})
    assert nz.shortcut_to_thread(body) == (ROOT, BOB, ROOT_TS)


def test_shortcut_on_a_root_message() -> None:
    body = shortcut_body({"type": "message", "user": ANN, "ts": ROOT_TS, "text": "Postgres or Mongo?"})
    ref, user_id, root_ts = nz.shortcut_to_thread(body)
    assert (ref, user_id, root_ts) == (ROOT, BOB, ROOT_TS)
    # the adapter turns the message into root_message only when the shortcut was used on the root itself
    assert nz.to_message(body["message"]).id == root_ts


def test_shortcut_of_another_callback_id_is_ignored() -> None:
    body = shortcut_body({"type": "message", "ts": ROOT_TS}, callback_id="something_else")
    assert nz.shortcut_to_thread(body) is None


def test_shortcut_without_a_channel_is_ignored() -> None:
    body = shortcut_body({"type": "message", "ts": ROOT_TS})
    body["channel"] = {}
    assert nz.shortcut_to_thread(body) is None


def test_reaction_scales_tracks_the_message() -> None:
    event = {
        "type": "reaction_added",
        "user": BOB,
        "reaction": "scales",
        "item": {"type": "message", "channel": CHANNEL, "ts": ROOT_TS},
        "item_user": ANN,
        "event_ts": "1700000700.000000",
    }
    assert nz.reaction_to_item(event) == (CHANNEL, ROOT_TS, BOB)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "reaction_added", "user": BOB, "reaction": "eyes", "item": {"type": "message", "channel": CHANNEL, "ts": ROOT_TS}},
        {"type": "reaction_added", "user": BOB, "reaction": "scales", "item": {"type": "file", "file": "F1"}},
        {"type": "reaction_added", "user": BOB, "reaction": "scales", "item": {"type": "message", "ts": ROOT_TS}},
        {"type": "reaction_added", "user": BOB, "item": {"type": "message", "channel": CHANNEL, "ts": ROOT_TS}},
    ],
)
def test_reactions_quorum_must_ignore(event: dict[str, Any]) -> None:
    assert nz.reaction_to_item(event) is None


# --------------------------------------------------------------------------------------------------
# demo personas (`quorum/tools/seed_demo.py` posts under `username`)
# --------------------------------------------------------------------------------------------------
def persona_event(username: str, *, text: str = "Postgres, we know it", ts: str = REPLY_TS) -> dict[str, Any]:
    return {
        "type": "message",
        "subtype": "bot_message",
        "channel": CHANNEL,
        "bot_id": BOT_ID,
        "username": username,
        "icons": {"emoji": ":bust_in_silhouette:"},
        "text": text,
        "ts": ts,
        "thread_ts": ROOT_TS,
    }


def test_parse_personas() -> None:
    assert nz.parse_personas("Ann, Bob ,, Cid ") == {"Ann", "Bob", "Cid"}
    assert nz.parse_personas("") == set()
    assert nz.parse_personas(None) == set()
    assert nz.parse_personas("  ,  ") == set()


def test_persona_message_is_read_as_a_human() -> None:
    event = persona_event("Ann", text="Postgres, we know it <@U0DAN>")
    assert nz.message_event_to_event(event, BOT_USER, personas=PERSONAS) == MessagePosted(
        thread=ROOT,
        message=Message(
            id=REPLY_TS,
            user_id="persona:Ann",
            user_name="Ann",
            text="Postgres, we know it <@U0DAN>",
            at=at(REPLY_TS),
            is_bot=False,
            mentions=[DAN],
        ),
        in_thread=True,
    )


def test_persona_root_message_starts_the_thread() -> None:
    event = persona_event("Bob", text="Postgres or Mongo?", ts=ROOT_TS)
    event.pop("thread_ts")
    normalized = nz.message_event_to_event(event, BOT_USER, personas=PERSONAS)
    assert isinstance(normalized, MessagePosted)
    assert normalized.thread == ROOT
    assert normalized.in_thread is False
    assert normalized.message.user_id == "persona:Bob"


def test_persona_message_is_a_bot_message_without_the_setting() -> None:
    assert nz.message_event_to_event(persona_event("Ann"), BOT_USER) is None
    assert nz.message_event_to_event(persona_event("Ann"), BOT_USER, personas=set()) is None


def test_a_bot_that_is_not_a_persona_stays_ignored() -> None:
    assert nz.message_event_to_event(persona_event("Zapier"), BOT_USER, personas=PERSONAS) is None
    assert nz.message_event_to_event(persona_event(""), BOT_USER, personas=PERSONAS) is None
    # our own card carries no username at all
    card = {"type": "message", "channel": CHANNEL, "user": BOT_USER, "bot_id": BOT_ID, "text": "card", "ts": "1.0"}
    assert nz.message_event_to_event(card, BOT_USER, personas=PERSONAS) is None


def test_persona_message_edited_and_deleted() -> None:
    changed = {
        "type": "message",
        "subtype": "message_changed",
        "channel": CHANNEL,
        "message": {**persona_event("Cid", text="Mongo, actually"), "subtype": "bot_message"},
        "previous_message": persona_event("Cid"),
    }
    normalized = nz.message_event_to_event(changed, BOT_USER, personas=PERSONAS)
    assert isinstance(normalized, MessageChanged)
    assert normalized.message.user_id == "persona:Cid"
    assert normalized.message.is_bot is False
    assert normalized.thread == ROOT
    assert nz.message_event_to_event(changed, BOT_USER) is None

    deleted = {
        "type": "message",
        "subtype": "message_deleted",
        "channel": CHANNEL,
        "deleted_ts": REPLY_TS,
        "previous_message": persona_event("Cid"),
    }
    assert nz.message_event_to_event(deleted, BOT_USER, personas=PERSONAS) == MessageDeleted(
        thread=ROOT, message_id=REPLY_TS
    )
    assert nz.message_event_to_event(deleted, BOT_USER) is None


def test_to_message_persona_rules() -> None:
    raw = persona_event("Ann")
    assert nz.persona_of(raw, PERSONAS) == "Ann"
    assert nz.persona_of(raw, None) is None
    assert nz.persona_of({"user": ANN, "text": "x"}, PERSONAS) is None

    message = nz.to_message(raw, {"whatever": "ignored"}, is_bot=True, personas=PERSONAS)
    assert (message.user_id, message.user_name, message.is_bot) == ("persona:Ann", "Ann", False)
    plain = nz.to_message(raw, is_bot=True)
    assert (plain.user_id, plain.user_name, plain.is_bot) == (BOT_ID, "", True)


# --------------------------------------------------------------------------------------------------
# the adapter side of personas (no socket is opened: only the constructor and fetch_thread are exercised)
# --------------------------------------------------------------------------------------------------
class FakeSlackClient:
    """Just enough of AsyncWebClient for `fetch_thread` / `user_name`."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.user_calls: list[str] = []

    async def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        return {"messages": self.messages, "response_metadata": {}}

    async def users_info(self, *, user: str) -> dict[str, Any]:
        self.user_calls.append(user)
        return {"user": {"profile": {"display_name": f"name-of-{user}"}}}


def make_platform(demo_personas: str) -> Any:
    from quorum.platform.slack.adapter import SlackPlatform

    settings = Settings(slack_bot_token="xoxb-test", slack_app_token="xapp-test", demo_personas=demo_personas)
    return SlackPlatform("xoxb-test", "xapp-test", settings=settings, lang="en")


def test_adapter_reads_personas_from_the_settings() -> None:
    assert make_platform("Ann, Bob ,, Cid ").personas == PERSONAS
    assert make_platform("").personas == set()


async def test_fetch_thread_treats_personas_as_humans() -> None:
    platform = make_platform("Ann,Bob")
    platform.bot_user_id = BOT_USER
    client = FakeSlackClient(
        [
            {"type": "message", "user": ANN, "text": "root", "ts": ROOT_TS},
            persona_event("Ann", text="Postgres, we know it"),
            persona_event("Zapier", text="build finished", ts="1700000200.000400"),
            {"type": "message", "user": BOT_USER, "bot_id": BOT_ID, "text": "the card", "ts": "1700000200.000500"},
        ]
    )
    platform.client = client

    messages = await platform.fetch_thread(ROOT)
    assert [(m.user_id, m.user_name, m.is_bot) for m in messages] == [
        (ANN, f"name-of-{ANN}", False),
        ("persona:Ann", "Ann", False),
        (BOT_ID, "", True),
        (BOT_USER, "", True),
    ]
    assert client.user_calls == [ANN]           # no users.info lookup for a persona or a bot
    assert [m.id for m in messages] == sorted(m.id for m in messages)


async def test_persona_gets_a_display_name_without_a_slack_lookup() -> None:
    platform = make_platform("Ann,Bob")
    client = FakeSlackClient([])
    platform.client = client
    assert await platform.user_name("persona:Ann") == "Ann"
    assert await platform.user_name("") == ""
    assert client.user_calls == []
    assert await platform.user_name(ANN) == f"name-of-{ANN}"

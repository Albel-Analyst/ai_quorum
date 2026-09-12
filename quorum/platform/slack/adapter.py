"""Slack adapter: Bolt (async, Socket Mode) in, normalized events out; ChatPlatform methods for the engine.

Everything Slack-specific lives here and in `normalize.py` / `quorum.render.blockkit`. The engine never sees a
Slack payload, and this file never decides anything about the product — it only carries messages both ways.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError

from quorum.config import Settings
from quorum.domain.events import Event, HomeOpened, TrackRequested
from quorum.domain.models import CardState, Message, ThreadRef, TrackedThread
from quorum.domain.ui import Form, Notice
from quorum.platform.base import EventSink
from quorum.platform.slack import normalize as nz
from quorum.render.blockkit import (
    context as context_block,
)
from quorum.render.blockkit import (
    phase_banner,
    render_card,
    render_form,
    render_notice,
)

log = structlog.get_logger("slack")

Dedup = Any  # Callable[[str], Awaitable[bool]]


class SlackPlatform:
    """ChatPlatform over Slack. One card message per thread; everything else is ephemeral, DM or modal."""

    name = "slack"

    def __init__(self, bot_token: str, app_token: str, *, settings: Settings, lang: str) -> None:
        self.app = AsyncApp(token=bot_token, raise_error_for_unhandled_request=False)
        self.client = self.app.client
        self.app_token = app_token
        self.settings = settings
        self.lang = lang
        self.bot_user_id: str = ""
        # the engine flips these when the plugins report themselves available
        self.verifier_available: bool = False
        self.recorders_available: bool = False

        self._sink: EventSink | None = None
        self._dedup: Dedup | None = None
        self._handler: AsyncSocketModeHandler | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._names: dict[str, str] = {}
        self._register()

    # ---- wiring ---------------------------------------------------------------------------------
    def bind(self, sink: EventSink, dedup: Dedup) -> None:
        """`dedup(event_id)` returns True when the event was already processed (Slack retries deliveries)."""
        self._sink = sink
        self._dedup = dedup

    async def start(self) -> None:
        auth = await self.client.auth_test()
        self.bot_user_id = auth["user_id"]
        self._handler = AsyncSocketModeHandler(self.app, self.app_token)
        await self._handler.connect_async()
        log.info("slack.connected", bot_user_id=self.bot_user_id, team=auth.get("team"))

    async def stop(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()
            self._handler = None
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    # ---- inbound --------------------------------------------------------------------------------
    def _emit(self, event: Event) -> None:
        """Hand off to the engine without blocking Bolt's listener (Slack expects an ack within 3 s)."""
        if self._sink is None:
            log.warning("slack.no_sink", event=type(event).__name__)
            return
        task = asyncio.create_task(self._deliver(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, event: Event) -> None:
        try:
            assert self._sink is not None
            await self._sink.handle(event)
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception:
            log.exception("slack.sink_failed", event=type(event).__name__)

    async def _seen(self, body: dict[str, Any]) -> bool:
        event_id = body.get("event_id")
        if not event_id or self._dedup is None:
            return False
        try:
            return bool(await self._dedup(event_id))
        except Exception:
            log.exception("slack.dedup_failed", event_id=event_id)
            return False

    def _register(self) -> None:
        app = self.app

        @app.event("app_mention")
        async def on_app_mention(body: dict[str, Any], event: dict[str, Any], ack: Any) -> None:
            await ack()
            try:
                if await self._seen(body):
                    return
                ref, user_id, message = nz.app_mention_root(event)
                root = message if message.id == ref.thread_id else None
                self._emit(TrackRequested(thread=ref, requested_by=user_id, via="mention", root_message=root))
            except Exception:
                log.exception("slack.app_mention_failed")

        @app.event("message")
        async def on_message(body: dict[str, Any], event: dict[str, Any], ack: Any) -> None:
            await ack()
            try:
                if await self._seen(body):
                    return
                normalized = nz.message_event_to_event(event, self.bot_user_id)
                if normalized is not None:
                    self._emit(normalized)
            except Exception:
                log.exception("slack.message_failed")

        @app.event("reaction_added")
        async def on_reaction(body: dict[str, Any], event: dict[str, Any], ack: Any) -> None:
            await ack()
            try:
                if await self._seen(body):
                    return
                item = nz.reaction_to_item(event)
                if item is None:
                    return
                channel, ts, user_id = item
                root_ts, root_message = await self._resolve_root(channel, ts)
                self._emit(
                    TrackRequested(
                        thread=nz.thread_ref(channel, root_ts),
                        requested_by=user_id,
                        via="reaction",
                        root_message=root_message,
                    )
                )
            except Exception:
                log.exception("slack.reaction_failed")

        @app.event("app_home_opened")
        async def on_home(event: dict[str, Any], ack: Any) -> None:
            await ack()
            try:
                if event.get("tab") not in (None, "home"):
                    return
                self._emit(HomeOpened(user_id=event.get("user") or ""))
            except Exception:
                log.exception("slack.home_failed")

        @app.shortcut(nz.SHORTCUT_CALLBACK_ID)
        async def on_shortcut(ack: Any, body: dict[str, Any]) -> None:
            await ack()
            try:
                parsed = nz.shortcut_to_thread(body)
                if parsed is None:
                    return
                ref, user_id, root_ts = parsed
                message = body.get("message") or {}
                root = nz.to_message(message) if message.get("ts") == root_ts else None
                self._emit(TrackRequested(thread=ref, requested_by=user_id, via="shortcut", root_message=root))
            except Exception:
                log.exception("slack.shortcut_failed")

        @app.action(re.compile(r"^q:"))
        async def on_action(ack: Any, body: dict[str, Any], action: dict[str, Any]) -> None:
            await ack()
            try:
                if action.get("url"):
                    return  # link button: Slack already opened it, there is nothing to decide
                self._emit(nz.block_action_to_event(body, action))
            except Exception:
                log.exception("slack.action_failed", action_id=action.get("action_id"))

        @app.view(re.compile(r".*"))
        async def on_view(ack: Any, body: dict[str, Any]) -> None:
            await ack()
            try:
                self._emit(nz.view_submission_to_event(body))
            except Exception:
                log.exception("slack.view_failed")

    async def _resolve_root(self, channel: str, ts: str) -> tuple[str, Message | None]:
        """A reaction can land on a reply: ask Slack which thread the message belongs to."""
        try:
            resp = await self._call(self.client.conversations_replies, channel=channel, ts=ts, limit=1)
            messages = resp.get("messages") or []
        except SlackApiError as err:
            log.warning("slack.replies_failed", channel=channel, ts=ts, error=str(err))
            return ts, None
        if not messages:
            return ts, None
        first = messages[0]
        root_ts = first.get("thread_ts") or first.get("ts") or ts
        root = nz.to_message(first, is_bot=self._is_bot(first)) if first.get("ts") == root_ts else None
        return root_ts, root

    # ---- outbound: Slack calls ---------------------------------------------------------------------
    async def _call(self, method: Any, **kwargs: Any) -> Any:
        try:
            return await method(**kwargs)
        except SlackApiError as err:
            if (err.response.get("error") or "") == "ratelimited":
                delay = float((err.response.headers or {}).get("Retry-After", 1))
                log.warning("slack.ratelimited", method=getattr(method, "__name__", "?"), retry_after=delay)
                await asyncio.sleep(delay)
                return await method(**kwargs)
            raise

    def _is_bot(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("bot_id")) or payload.get("user") == self.bot_user_id

    async def _names_for(self, state: CardState) -> dict[str, str]:
        ids: list[str] = []
        for user_id in (
            list(state.participants)
            + list(state.stakeholders)
            + [p.user_id for p in state.positions]
            + [q.directed_to for q in state.open_questions if q.directed_to]
            + [c.by for c in state.claims]
        ):
            if user_id and user_id not in ids:
                ids.append(user_id)
        return {user_id: await self.user_name(user_id) for user_id in ids}

    async def _render(self, thread: TrackedThread, state: CardState) -> tuple[list[dict[str, Any]], str]:
        return render_card(
            thread,
            state,
            lang=self.lang,
            verifier_available=self.verifier_available,
            recorders_available=self.recorders_available,
            names=await self._names_for(state),
        )

    # ---- ChatPlatform ------------------------------------------------------------------------------
    async def post_card(self, thread: TrackedThread, state: CardState, *, broadcast: bool = False) -> str:
        blocks, text = await self._render(thread, state)
        banner = phase_banner(state.status, self.lang)
        if banner:
            blocks = [context_block(banner), *blocks][:50]
        resp = await self._call(
            self.client.chat_postMessage,
            channel=thread.ref.channel_id,
            thread_ts=thread.ref.thread_id,
            blocks=blocks,
            text=text,
            reply_broadcast=broadcast,
            unfurl_links=False,
            unfurl_media=False,
        )
        return str(resp["ts"])

    async def update_card(self, thread: TrackedThread, state: CardState) -> None:
        if not thread.card_message_id:
            log.warning("slack.update_without_card", thread=thread.key)
            return
        blocks, text = await self._render(thread, state)
        await self._call(
            self.client.chat_update,
            channel=thread.ref.channel_id,
            ts=thread.card_message_id,
            blocks=blocks,
            text=text,
        )

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        try:
            await self._call(self.client.chat_delete, channel=channel_id, ts=message_id)
        except SlackApiError as err:
            if (err.response.get("error") or "") in ("message_not_found", "cant_delete_message"):
                log.info("slack.delete_skipped", channel=channel_id, ts=message_id, error=err.response.get("error"))
                return
            raise

    async def ephemeral(
        self,
        channel_id: str,
        user_id: str,
        notice: Notice,
        *,
        thread_id: str | None = None,
    ) -> None:
        key = nz.thread_key(channel_id, thread_id) if thread_id else None
        await self._call(
            self.client.chat_postEphemeral,
            channel=channel_id,
            user=user_id,
            blocks=render_notice(notice, thread_key=key),
            text=notice.text,
            thread_ts=thread_id,
        )

    async def dm(self, user_id: str, notice: Notice) -> str | None:
        opened = await self._call(self.client.conversations_open, users=user_id)
        channel_id = (opened.get("channel") or {}).get("id")
        if not channel_id:
            return None
        resp = await self._call(
            self.client.chat_postMessage,
            channel=channel_id,
            blocks=render_notice(notice, thread_key=None),
            text=notice.text,
            unfurl_links=False,
        )
        return str(resp["ts"])

    async def open_form(self, trigger_id: str, form: Form) -> None:
        # trigger ids expire after 3 seconds: this must be the first thing done after the button ack
        await self._call(self.client.views_open, trigger_id=trigger_id, view=render_form(form, lang=self.lang))

    async def post_notice(self, channel_id: str, thread_id: str, notice: Notice) -> str:
        resp = await self._call(
            self.client.chat_postMessage,
            channel=channel_id,
            thread_ts=thread_id,
            blocks=render_notice(notice, thread_key=nz.thread_key(channel_id, thread_id)),
            text=notice.text,
            unfurl_links=False,
        )
        return str(resp["ts"])

    async def fetch_thread(self, thread: ThreadRef) -> list[Message]:
        messages: list[Message] = []
        cursor: str | None = None
        while True:
            resp = await self._call(
                self.client.conversations_replies,
                channel=thread.channel_id,
                ts=thread.thread_id,
                limit=200,
                cursor=cursor,
            )
            for raw in resp.get("messages") or []:
                is_bot = self._is_bot(raw)
                message = nz.to_message(raw, is_bot=is_bot)
                if message.user_id and not is_bot:
                    message.user_name = await self.user_name(message.user_id)
                messages.append(message)
            cursor = ((resp.get("response_metadata") or {}).get("next_cursor") or "") or None
            if not cursor:
                break
        messages.sort(key=lambda m: float(m.id or 0))
        return messages

    async def user_name(self, user_id: str) -> str:
        if not user_id:
            return ""
        cached = self._names.get(user_id)
        if cached:
            return cached
        name = user_id
        try:
            resp = await self._call(self.client.users_info, user=user_id)
            profile = (resp.get("user") or {}).get("profile") or {}
            name = profile.get("display_name") or profile.get("real_name") or user_id
        except SlackApiError as err:
            log.info("slack.user_info_failed", user=user_id, error=str(err))
        self._names[user_id] = name
        return name

    async def permalink(self, channel_id: str, message_id: str) -> str:
        try:
            resp = await self._call(self.client.chat_getPermalink, channel=channel_id, message_ts=message_id)
            return str(resp.get("permalink") or "")
        except SlackApiError as err:
            log.info("slack.permalink_failed", channel=channel_id, ts=message_id, error=str(err))
            return ""

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        try:
            await self._call(self.client.reactions_add, channel=channel_id, timestamp=message_id, name=emoji.strip(":"))
        except SlackApiError as err:
            if (err.response.get("error") or "") == "already_reacted":
                return
            log.info("slack.reaction_failed", channel=channel_id, ts=message_id, error=str(err))

    async def publish_home(self, user_id: str, view: dict[str, Any]) -> None:
        await self._call(self.client.views_publish, user_id=user_id, view=view)

    async def update_form_error(self, form_id: str, errors: dict[str, str]) -> None:
        # Slack shows field errors only in the response to a view_submission, which Bolt already acked.
        log.info("slack.form_error", form_id=form_id, errors=errors)

"""Authenticated loopback bridge from the pinned Channels direct Slack adapter.

Channels owns ingress/history/subscription; the existing Slack Web API adapter
keeps the canonical card and modals. Only one process owns Socket Mode.
"""
from __future__ import annotations

import hmac
import re

from aiohttp import web
from pydantic import BaseModel, Field, ValidationError

from quorum.domain.events import TrackRequested
from quorum.domain.models import Message, ThreadRef
from quorum.platform.slack.normalize import block_action_to_event, view_submission_to_event


class ChannelDelivery(BaseModel):
    conversation_key: str
    event_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    activate: bool = False
    messages: list[Message] = Field(min_length=1)

    def ref(self) -> ThreadRef:
        # Verified against channels-slack@0.9.2 conversationKeyOf, NOT a managed-key parser.
        match = re.fullmatch(r"([CG][A-Z0-9]+)::(\d+\.\d+)", self.conversation_key)
        if not match:
            raise ValueError("Expected a direct Slack Channels thread identity")
        return ThreadRef(platform="slack", channel_id=match[1], thread_id=match[2])


def bridge_app(engine, token: str) -> web.Application:
    if not token:
        raise ValueError("QUORUM_BRIDGE_TOKEN is required")

    @web.middleware
    async def authorize(request, handler):
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            raise web.HTTPUnauthorized()
        try:
            return await handler(request)
        except (ValueError, ValidationError) as exc:
            raise web.HTTPBadRequest(text="Invalid Channels payload") from exc

    async def delivery(request):
        event = ChannelDelivery.model_validate(await request.json())
        ref = event.ref()
        async with engine._lock(f"channel:{ref.key}"):
            thread = await engine.store.get_thread(ref.key)
            if thread and thread.channels_conversation_key not in {None, event.conversation_key}:
                raise web.HTTPConflict()
            if await engine.store.event_processed(event.event_id):
                return web.json_response({"tracked": thread is not None})
            if not thread and event.activate:
                thread = await engine.track(TrackRequested(thread=ref, requested_by=event.actor_id, via="mention"))
            if not thread:
                return web.json_response({"tracked": False})
            async with engine._lock(ref.key):
                thread = await engine.store.get_thread(ref.key)
                thread.channels_conversation_key = event.conversation_key
                thread.last_activity_at = max(m.at for m in event.messages)
                await engine.store.save_thread(thread)
                await engine.store.replace_messages(ref.key, event.messages)
            await engine.refresh(ref.key, force=True, current_messages=event.messages)
            await engine.store.seen(event.event_id)
            return web.json_response({"tracked": True})

    async def interaction(request):
        body = await request.json()
        if body.get("type") == "block_actions":
            for action in body.get("actions", []):
                await engine.handle(block_action_to_event(body, action))
        elif body.get("type") == "view_submission":
            await engine.handle(view_submission_to_event(body))
        else:
            raise web.HTTPBadRequest()
        return web.json_response({"received": True})

    app = web.Application(middlewares=[authorize], client_max_size=1024 * 1024)
    app.router.add_post("/delivery", delivery)
    app.router.add_post("/interaction", interaction)
    return app

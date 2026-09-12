"""ChatPlatform: the only thing the engine knows about a messenger.

post / update / delete a card; ephemeral; dm; open_form; fetch_thread; names & links. Events flow the other way
through `EventSink` (the engine). A new messenger = a new adapter + renderer, not a new agent.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from quorum.domain.events import Event
from quorum.domain.models import CardState, Message, ThreadRef, TrackedThread
from quorum.domain.ui import Form, Notice


class EventSink(Protocol):
    async def handle(self, event: Event) -> None: ...


@runtime_checkable
class ChatPlatform(Protocol):
    name: str
    bot_user_id: str

    # --- the card (one message per thread, edited in place; re-posted on phase change) ----------
    async def post_card(self, thread: TrackedThread, state: CardState, *, broadcast: bool = False) -> str:
        """Post the card into the thread. Returns the platform message id."""
        ...

    async def update_card(self, thread: TrackedThread, state: CardState) -> None: ...

    async def delete_message(self, channel_id: str, message_id: str) -> None: ...

    # --- quiet channels: nobody else sees these ------------------------------------------------
    async def ephemeral(self, channel_id: str, user_id: str, notice: Notice, *, thread_id: str | None = None) -> None: ...

    async def dm(self, user_id: str, notice: Notice) -> str | None: ...

    async def open_form(self, trigger_id: str, form: Form) -> None: ...

    # --- the one exception: a small notice inside a thread (memory recall) ---------------------
    async def post_notice(self, channel_id: str, thread_id: str, notice: Notice) -> str: ...

    # --- reading -------------------------------------------------------------------------------
    async def fetch_thread(self, thread: ThreadRef) -> list[Message]:
        """Full thread history, oldest first, root included, bot messages included (is_bot=True)."""
        ...

    async def user_name(self, user_id: str) -> str: ...

    async def permalink(self, channel_id: str, message_id: str) -> str: ...

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...

    async def publish_home(self, user_id: str, view: dict[str, Any]) -> None: ...

    async def update_form_error(self, form_id: str, errors: dict[str, str]) -> None: ...

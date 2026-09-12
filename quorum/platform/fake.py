"""In-memory ChatPlatform for tests: records every call and renders real Block Kit through `quorum.render.blockkit`.

Engine tests assert on `calls` / `messages` / `dms` / `ephemerals` / `forms_opened` / `notices`; because the card
goes through the real renderer, they also prove the blocks stay well-formed.
"""
from __future__ import annotations

from typing import Any

from quorum.domain.models import CardState, DecisionMemory, Message, ThreadRef, TrackedThread
from quorum.domain.ui import Form, Notice
from quorum.render.blockkit import context as context_block
from quorum.render.blockkit import phase_banner, render_card, render_form, render_home, render_notice


class FakePlatform:
    """Everything the engine can do to a chat, remembered instead of sent."""

    name = "fake"

    def __init__(self, *, lang: str = "en", verifier_available: bool = True, recorders_available: bool = True) -> None:
        self.lang = lang
        self.bot_user_id = "UBOT"
        self.verifier_available = verifier_available
        self.recorders_available = recorders_available

        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: dict[tuple[str, str], dict[str, Any]] = {}
        self.threads: dict[str, list[Message]] = {}          # thread key -> history the test sets
        self.names: dict[str, str] = {}
        self.dms: list[dict[str, Any]] = []
        self.ephemerals: list[dict[str, Any]] = []
        self.forms_opened: list[dict[str, Any]] = []
        self.notices: list[dict[str, Any]] = []
        self.home_views: list[dict[str, Any]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.form_errors: list[tuple[str, dict[str, str]]] = []
        self._seq = 0

    # ---- helpers ------------------------------------------------------------------------------
    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    def _next_ts(self) -> str:
        self._seq += 1
        return f"1700000000.{self._seq * 100:06d}"

    def _render(self, thread: TrackedThread, state: CardState) -> tuple[list[dict[str, Any]], str]:
        names = {user_id: self.names.get(user_id, user_id) for user_id in self._known_ids(state)}
        return render_card(
            thread,
            state,
            lang=self.lang,
            verifier_available=self.verifier_available,
            recorders_available=self.recorders_available,
            names=names,
        )

    @staticmethod
    def _known_ids(state: CardState) -> list[str]:
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
        return ids

    def card_blocks(self, thread: TrackedThread) -> list[dict[str, Any]]:
        """Blocks of the card currently posted for `thread` (empty when there is none)."""
        if not thread.card_message_id:
            return []
        return self.messages.get((thread.ref.channel_id, thread.card_message_id), {}).get("blocks", [])

    # ---- ChatPlatform -------------------------------------------------------------------------
    async def post_card(self, thread: TrackedThread, state: CardState, *, broadcast: bool = False) -> str:
        blocks, text = self._render(thread, state)
        banner = phase_banner(state.status, self.lang)
        if banner:
            blocks = [context_block(banner), *blocks][:50]
        ts = self._next_ts()
        self.messages[(thread.ref.channel_id, ts)] = {
            "blocks": blocks,
            "text": text,
            "thread_ts": thread.ref.thread_id,
            "broadcast": broadcast,
            "deleted": False,
        }
        self._record("post_card", thread=thread.key, status=state.status, broadcast=broadcast, ts=ts)
        return ts

    async def update_card(self, thread: TrackedThread, state: CardState) -> None:
        blocks, text = self._render(thread, state)
        key = (thread.ref.channel_id, thread.card_message_id or "")
        existing = self.messages.get(key) or {"thread_ts": thread.ref.thread_id, "deleted": False}
        existing.update({"blocks": blocks, "text": text})
        self.messages[key] = existing
        self._record("update_card", thread=thread.key, status=state.status, ts=thread.card_message_id)

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        message = self.messages.get((channel_id, message_id))
        if message is not None:
            message["deleted"] = True
        self._record("delete_message", channel_id=channel_id, message_id=message_id)

    async def ephemeral(
        self,
        channel_id: str,
        user_id: str,
        notice: Notice,
        *,
        thread_id: str | None = None,
    ) -> None:
        key = f"fake:{channel_id}:{thread_id}" if thread_id else None
        item = {
            "channel_id": channel_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "notice": notice,
            "blocks": render_notice(notice, thread_key=key),
        }
        self.ephemerals.append(item)
        self._record("ephemeral", channel_id=channel_id, user_id=user_id, text=notice.text, thread_id=thread_id)

    async def dm(self, user_id: str, notice: Notice) -> str | None:
        ts = self._next_ts()
        self.dms.append({"user_id": user_id, "notice": notice, "blocks": render_notice(notice), "ts": ts})
        self._record("dm", user_id=user_id, text=notice.text, ts=ts)
        return ts

    async def open_form(self, trigger_id: str, form: Form) -> None:
        self.forms_opened.append({"trigger_id": trigger_id, "form": form, "view": render_form(form, lang=self.lang)})
        self._record("open_form", trigger_id=trigger_id, form_id=form.id)

    async def post_notice(self, channel_id: str, thread_id: str, notice: Notice) -> str:
        ts = self._next_ts()
        blocks = render_notice(notice, thread_key=f"fake:{channel_id}:{thread_id}")
        self.notices.append({"channel_id": channel_id, "thread_id": thread_id, "notice": notice, "blocks": blocks, "ts": ts})
        self.messages[(channel_id, ts)] = {"blocks": blocks, "text": notice.text, "thread_ts": thread_id, "deleted": False}
        self._record("post_notice", channel_id=channel_id, thread_id=thread_id, text=notice.text, ts=ts)
        return ts

    async def fetch_thread(self, thread: ThreadRef) -> list[Message]:
        self._record("fetch_thread", thread=thread.key)
        return list(self.threads.get(thread.key, []))

    async def user_name(self, user_id: str) -> str:
        return self.names.get(user_id, user_id)

    async def permalink(self, channel_id: str, message_id: str) -> str:
        self._record("permalink", channel_id=channel_id, message_id=message_id)
        return f"https://fake.slack.test/archives/{channel_id}/p{message_id.replace('.', '')}"

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.reactions.append((channel_id, message_id, emoji))
        self._record("add_reaction", channel_id=channel_id, message_id=message_id, emoji=emoji)

    async def publish_home(self, user_id: str, view: dict[str, Any]) -> None:
        self.home_views.append({"user_id": user_id, "view": view})
        self._record("publish_home", user_id=user_id)

    async def update_form_error(self, form_id: str, errors: dict[str, str]) -> None:
        self.form_errors.append((form_id, errors))
        self._record("update_form_error", form_id=form_id, errors=errors)

    # ---- convenience for tests ------------------------------------------------------------------
    def home(self, threads: list[TrackedThread], decisions: list[DecisionMemory]) -> dict[str, Any]:
        return render_home(threads, decisions, lang=self.lang)

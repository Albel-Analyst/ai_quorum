"""The engine: normalized events in, one living card out.

Everything platform-specific is behind `ChatPlatform`; everything LLM-specific behind the `llm` protocols.
Per-thread lock + coalescing window; the LLM never sees two bursts of the same thread concurrently.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from quorum.config import Settings
from quorum.core.merge import merge, participants_of
from quorum.domain import state_machine as sm
from quorum.domain.events import (
    ButtonPressed,
    Event,
    FormSubmitted,
    HomeOpened,
    MessageChanged,
    MessageDeleted,
    MessagePosted,
    TrackRequested,
)
from quorum.domain.models import (
    Decision,
    DecisionMemory,
    Dissent,
    FollowUp,
    Message,
    Record,
    Status,
    ThreadRef,
    TrackedThread,
    Verification,
    now,
)
from quorum.domain.ui import Button, Form, FormField, Notice
from quorum.i18n import t
from quorum.llm.base import ClaimJudge, Classifier, Extractor
from quorum.platform.base import ChatPlatform
from quorum.recorders.base import Recorder, RecordInput
from quorum.render.markdown import render_adr, render_expired_note
from quorum.store.sqlite import Store
from quorum.verifier.base import Verifier

log = structlog.get_logger("engine")


class LLMFailed(Exception):
    pass


class Engine:
    def __init__(
        self,
        *,
        platform: ChatPlatform,
        store: Store,
        llm: Extractor | Classifier | ClaimJudge,
        recorders: list[Recorder],
        verifier: Verifier | None,
        settings: Settings,
        home_view: Callable[[list[TrackedThread], list[DecisionMemory], str], dict[str, Any]] | None = None,
        sleep: Callable[[float], Any] | None = None,
    ):
        self.platform = platform
        self.store = store
        self.llm: Any = llm
        self.recorders = recorders
        self.verifier = verifier if verifier and verifier.available() else None
        self.s = settings
        self.lang = settings.lang
        self.home_view = home_view
        self._sleep = sleep or asyncio.sleep
        self._locks: dict[str, asyncio.Lock] = {}
        self._debounce: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._names: dict[str, str] = {}
        self._buffers: dict[str, list[Message]] = {}   # passive classifier buffers (untracked threads / channels)
        self.tz = ZoneInfo(settings.timezone)
        self.recorders_available = bool(recorders)
        with contextlib.suppress(AttributeError):
            platform.verifier_available = self.verifier is not None  # type: ignore[attr-defined]
            platform.recorders_available = self.recorders_available  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ plumbing
    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def names_for(self, ids: list[str]) -> dict[str, str]:
        for uid in ids:
            if uid and uid not in self._names:
                try:
                    self._names[uid] = await self.platform.user_name(uid)
                except Exception:  # noqa: BLE001
                    self._names[uid] = uid
        return {uid: self._names.get(uid, uid) for uid in ids if uid}

    def _elapsed(self, since: datetime) -> timedelta:
        return (now() - since) * self.s.demo_time_scale

    @staticmethod
    def _thread_url(ref: ThreadRef) -> str:
        return f"https://slack.com/archives/{ref.channel_id}/p{ref.thread_id.replace('.', '')}"

    # ------------------------------------------------------------------ events
    async def handle(self, event: Event) -> None:
        try:
            match event:
                case TrackRequested():
                    await self.track(event)
                case MessagePosted():
                    await self._on_message(event)
                case MessageChanged():
                    await self._on_changed(event)
                case MessageDeleted():
                    await self._on_deleted(event)
                case ButtonPressed():
                    await self._on_button(event)
                case FormSubmitted():
                    await self._on_form(event)
                case HomeOpened():
                    await self.publish_home(event.user_id)
        except Exception:
            log.exception("engine.event_failed", event=type(event).__name__)

    # ------------------------------------------------------------------ tracking
    async def track(self, ev: TrackRequested) -> TrackedThread | None:
        key = ev.thread.key
        async with self._lock(key):
            existing = await self.store.get_thread(key)
            if existing and existing.card_message_id:
                if ev.via in ("mention", "shortcut"):
                    await self.platform.ephemeral(ev.thread.channel_id, ev.requested_by, Notice(text=t(self.lang, "notice.already")), thread_id=ev.thread.thread_id)
                return existing
            messages = await self.platform.fetch_thread(ev.thread)
            humans = [m for m in messages if not m.is_bot]
            root = messages[0] if messages else ev.root_message
            thread = TrackedThread(
                ref=ev.thread,
                author_id=(root.user_id if root else ev.requested_by),
                requested_by=ev.requested_by,
                root_text=(root.text if root else ""),
                message_count=len(humans),
                last_activity_at=(humans[-1].at if humans else now()),
            )
            with contextlib.suppress(Exception):
                thread.permalink = await self.platform.permalink(ev.thread.channel_id, ev.thread.thread_id)
            thread.state.question = (root.text.strip().splitlines()[0][:200] if root and root.text.strip() else "…")
            thread.state.participants = participants_of(humans)
            thread.state.supersedes = ev.supersedes
            await self.store.cache_messages(key, messages)
            thread.card_message_id = await self.platform.post_card(thread, thread.state)
            await self.store.save_thread(thread)
            with contextlib.suppress(Exception):
                await self.platform.add_reaction(ev.thread.channel_id, ev.thread.thread_id, "scales")
            log.info("track.started", key=key, via=ev.via, messages=len(humans))
        await self.refresh(key, force=True)
        return await self.store.get_thread(key)

    async def _on_message(self, ev: MessagePosted) -> None:
        if ev.message.is_bot:
            return
        key = ev.thread.key
        thread = await self.store.get_thread(key) if ev.in_thread else None
        if thread is None:
            await self._passive(ev)
            return
        await self.store.cache_messages(key, [ev.message])
        thread.last_activity_at = now()
        thread.message_count += 1
        await self.store.save_thread(thread)
        self._schedule_refresh(key)

    async def _on_changed(self, ev: MessageChanged) -> None:
        key = ev.thread.key
        if await self.store.get_thread(key) is None:
            return
        await self.store.cache_messages(key, [ev.message])
        self._schedule_refresh(key, force=True)

    async def _on_deleted(self, ev: MessageDeleted) -> None:
        key = ev.thread.key
        if await self.store.get_thread(key) is None:
            return
        await self.store.delete_cached_message(key, ev.message_id)
        self._schedule_refresh(key, force=True)

    def _schedule_refresh(self, key: str, *, force: bool = False) -> None:
        """Coalesce a burst of messages into one LLM call."""
        old = self._debounce.pop(key, None)
        if old and not old.done():
            old.cancel()

        async def _later() -> None:
            try:
                await self._sleep(self.s.coalesce_seconds)
            except asyncio.CancelledError:
                return
            self._debounce.pop(key, None)
            await self.refresh(key, force=force)

        self._debounce[key] = self._spawn(_later())

    async def flush(self) -> None:
        """Test helper: run every pending debounce/background task now."""
        for task in list(self._debounce.values()):
            task.cancel()
        self._debounce.clear()
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ the core loop
    async def _messages(self, key: str) -> list[Message]:
        raw = await self.store.cached_messages(key)
        msgs = [Message.model_validate(r) for r in raw]
        msgs.sort(key=lambda m: float(m.id) if m.id.replace(".", "").isdigit() else 0.0)
        return msgs

    async def refresh(self, key: str, *, force: bool = False) -> None:
        async with self._lock(key):
            thread = await self.store.get_thread(key)
            if thread is None or thread.state.status in (Status.RECORDED, Status.SUPERSEDED):
                return
            messages = await self._messages(key)
            humans = [m for m in messages if not m.is_bot]
            last = thread.last_seen_message_id
            new = [m for m in humans if last is None or float(m.id) > float(last)]
            if not new and not force:
                return
            state = thread.state
            names = await self.names_for(sorted({m.user_id for m in humans} | set(state.stakeholders) | {u for m in humans for u in m.mentions}))
            try:
                ext = await self.llm.extract(state, new, humans, names)
            except Exception as e:  # noqa: BLE001 — LLM down: the card keeps the previous state and says so
                state.last_llm_error = f"{type(e).__name__}: {e}"[:200]
                log.warning("refresh.llm_failed", key=key, error=state.last_llm_error)
                await self.store.save_thread(thread)
                await self._render(thread, phase_change=False)
                return
            merge(state, ext, humans, now=now())
            state.last_llm_error = None
            state.last_llm_ok_at = now()
            if humans:
                thread.last_seen_message_id = humans[-1].id
            if state.status == Status.PARKED and new:
                sm.unpark(state)
            changed = sm.settle_after_extraction(state)
            phase = changed and state.status in sm.PHASE_CHANGE
            await self.store.save_thread(thread)
            await self._render(thread, phase_change=phase)
            log.info("refresh.done", key=key, status=state.status.value, options=len(state.options), positions=len(state.positions))

    async def _render(self, thread: TrackedThread, *, phase_change: bool, broadcast: bool = False) -> None:
        """Edit in place; on a phase change re-post at the end of the thread and delete the old card."""
        state = thread.state
        if thread.card_message_id is None or phase_change:
            old = thread.card_message_id
            thread.card_message_id = await self.platform.post_card(thread, state, broadcast=broadcast)
            thread.phase_reposts += 1
            await self.store.save_thread(thread)
            if old:
                with contextlib.suppress(Exception):
                    await self.platform.delete_message(thread.ref.channel_id, old)
            return
        try:
            await self.platform.update_card(thread, state)
        except Exception as e:  # noqa: BLE001 — card gone (deleted by a human)? re-post it
            log.warning("render.update_failed", key=thread.key, error=str(e)[:200])
            thread.card_message_id = await self.platform.post_card(thread, state)
            await self.store.save_thread(thread)

    # ------------------------------------------------------------------ buttons
    async def _on_button(self, ev: ButtonPressed) -> None:
        action, key = ev.action, (ev.thread.key if ev.thread else None)
        log.info("button", action=action, user=ev.user_id, key=key)
        if action == "track_yes" and ev.thread:
            await self.track(TrackRequested(thread=ev.thread, requested_by=ev.user_id, via="suggestion"))
            return
        if action == "track_no" and key:
            await self.store.incr(f"dismissed:{key}")
            return
        if action == "dispute" and ev.thread:
            channel = ev.payload.get("channel_id") or ev.channel_id
            root = ev.payload.get("message_id") or ev.message_id
            if channel and root:
                new_ref = ThreadRef(platform=ev.thread.platform, channel_id=channel, thread_id=root)
                await self.track(TrackRequested(thread=new_ref, requested_by=ev.user_id, via="dispute", supersedes=ev.thread.key))
            return
        if key is None:
            return
        thread = await self.store.get_thread(key)
        if thread is None:
            return
        state = thread.state
        lang = self.lang

        # forms are opened outside the lock: a trigger_id lives 3 seconds
        if action == "my_position" and ev.trigger_id:
            await self.platform.open_form(ev.trigger_id, self._form_my_position(thread, ev.user_id))
            return
        if action == "deadline" and ev.trigger_id:
            await self.platform.open_form(ev.trigger_id, self._form_deadline(thread))
            return
        if action == "park" and ev.trigger_id:
            await self.platform.open_form(ev.trigger_id, self._form_park(thread))
            return
        if action == "confirm" and ev.trigger_id:
            if not self._may_decide(thread, ev.user_id):
                await self._ephemeral(thread, ev.user_id, t(lang, "notice.only_author"))
                return
            preset = ev.payload.get("option_id") or sm.leading_option(state) or (state.decision_hint.option_id if state.decision_hint else None)
            await self.platform.open_form(ev.trigger_id, self._form_confirm(thread, preset))
            return

        async with self._lock(key):
            thread = await self.store.get_thread(key) or thread
            state = thread.state
            phase = False
            if action == "vote":
                if state.status != Status.VOTING:
                    await self._ephemeral(thread, ev.user_id, t(lang, "notice.not_voting"))
                    return
                sm.cast_vote(state, ev.user_id, str(ev.payload.get("option_id")))
            elif action == "open_voting":
                if not state.options:
                    await self._ephemeral(thread, ev.user_id, t(lang, "notice.no_options"))
                    return
                phase = sm.open_voting(state)
            elif action == "back_to_discussion":
                sm.reopen_discussion(state)
                state.votes = []
            elif action == "unpark":
                sm.unpark(state)
                thread.last_activity_at = now()
            elif action == "record":
                if not self._may_decide(thread, ev.user_id):
                    await self._ephemeral(thread, ev.user_id, t(lang, "notice.only_author"))
                    return
                if state.status != Status.DECIDED:
                    await self._ephemeral(thread, ev.user_id, t(lang, "notice.need_decision"))
                    return
                await self._record(thread, ev.user_id)
                return
            elif action == "verify":
                await self._start_verification(thread, str(ev.payload.get("claim_id")), ev.user_id)
                return
            elif action == "not_me":
                qid = str(ev.payload.get("question_id"))
                for q in state.open_questions:
                    if q.id == qid:
                        q.declined = True
                        if q.directed_to == ev.user_id:
                            q.directed_to = None
                await self.store.save_thread(thread)
                await self.platform.dm(ev.user_id, Notice(text=t(lang, "notice.nudge_done")))
                await self._render(thread, phase_change=False)
                return
            else:
                log.warning("button.unknown", action=action)
                return
            await self.store.save_thread(thread)
            await self._render(thread, phase_change=phase)
            if action == "vote" and sm.everyone_voted(state) and not state.all_voted_notified:
                state.all_voted_notified = True
                await self.store.save_thread(thread)
                await self._dm_decider(thread, self._vote_closed_text(thread))

    def _may_decide(self, thread: TrackedThread, user_id: str) -> bool:
        decider = thread.state.decision.decider if thread.state.decision else None
        return user_id in {thread.author_id, thread.requested_by, decider}

    async def _ephemeral(self, thread: TrackedThread, user_id: str, text: str) -> None:
        with contextlib.suppress(Exception):
            await self.platform.ephemeral(thread.ref.channel_id, user_id, Notice(text=text), thread_id=thread.ref.thread_id)

    async def _dm_decider(self, thread: TrackedThread, text: str) -> None:
        decider = (thread.state.decision.decider if thread.state.decision and thread.state.decision.decider else None) or thread.author_id
        with contextlib.suppress(Exception):
            await self.platform.dm(decider, Notice(text=text, buttons=[Button(label=t(self.lang, "btn.open_thread"), action="open_thread", url=thread.permalink or self._thread_url(thread.ref))]))

    def _vote_closed_text(self, thread: TrackedThread) -> str:
        state = thread.state
        lead = state.option(sm.leading_option(state))
        tally = " · ".join(t(self.lang, "tally", label=o.id, n=n) for o, n in ((state.option(k), v) for k, v in state.tally().items()) if o)
        head = f"*{state.question}* — {t(self.lang, 'voted', n=len(state.votes), total=len(state.participants))}: {tally}"
        return head + (f"\n→ {lead.id} · {lead.label}" if lead else "")

    # ------------------------------------------------------------------ forms
    def _form_my_position(self, thread: TrackedThread, user_id: str) -> Form:
        st, lang = thread.state, self.lang
        current = st.position_of(user_id)
        opts = [("", t(lang, "form.my_position.none"))] + [(o.id, f"{o.id} · {o.label}") for o in st.options]
        return Form(
            id="my_position",
            title=t(lang, "form.my_position.title"),
            submit_label=t(lang, "form.submit"),
            intro=st.question,
            payload={"t": thread.key},
            fields=[
                FormField(id="option", label=t(lang, "form.my_position.option"), kind="select", options=opts, initial=(current.option_id or "") if current else ""),
                FormField(id="argument", label=t(lang, "form.my_position.argument"), kind="text", optional=True, initial=(current.argument if current else None)),
            ],
        )

    def _form_deadline(self, thread: TrackedThread) -> Form:
        lang = self.lang
        return Form(
            id="deadline",
            title=t(lang, "form.deadline.title"),
            submit_label=t(lang, "form.submit"),
            intro=thread.state.question,
            payload={"t": thread.key},
            fields=[
                FormField(id="date", label=t(lang, "form.deadline.date"), kind="date"),
                FormField(id="time", label="HH:MM", kind="text", optional=True, placeholder="18:00"),
            ],
        )

    def _form_park(self, thread: TrackedThread) -> Form:
        lang = self.lang
        return Form(
            id="park",
            title=t(lang, "form.park.title"),
            submit_label=t(lang, "form.submit"),
            intro=thread.state.question,
            payload={"t": thread.key},
            fields=[
                FormField(id="reason", label=t(lang, "form.park.reason"), kind="text"),
                FormField(id="return", label=t(lang, "form.park.return"), kind="date", optional=True),
            ],
        )

    def _form_confirm(self, thread: TrackedThread, preset: str | None) -> Form:
        st, lang = thread.state, self.lang
        return Form(
            id="confirm",
            title=t(lang, "form.confirm.title"),
            submit_label=t(lang, "form.submit"),
            intro=st.question,
            payload={"t": thread.key},
            fields=[
                FormField(id="option", label=t(lang, "form.confirm.option"), kind="select", options=[(o.id, f"{o.id} · {o.label}") for o in st.options], initial=preset),
                FormField(id="owner", label=t(lang, "form.confirm.owner"), kind="user", optional=True),
                FormField(id="note", label=t(lang, "form.confirm.note"), kind="textarea", optional=True),
            ],
        )

    def _parse_local(self, date_s: str | None, time_s: str | None, *, default_time: str = "18:00") -> datetime | None:
        """'YYYY-MM-DD' + optional 'HH:MM' in the configured timezone -> aware UTC datetime."""
        if not date_s:
            return None
        clock = (time_s or "").strip() or default_time
        try:
            hh, mm = (int(x) for x in clock.split(":")[:2])
        except ValueError:
            hh, mm = (int(x) for x in default_time.split(":"))
        y, m, d = (int(x) for x in date_s.split("-"))
        return datetime(y, m, d, hh, mm, tzinfo=self.tz).astimezone(UTC)

    async def _on_form(self, ev: FormSubmitted) -> None:
        key = ev.payload.get("t") or (ev.thread.key if ev.thread else None)
        if not key:
            return
        async with self._lock(key):
            thread = await self.store.get_thread(key)
            if thread is None:
                return
            state, v = thread.state, ev.values
            phase = False
            if ev.form_id == "my_position":
                from quorum.domain.models import Position

                option_id = (v.get("option") or "") or None
                state.positions = [p for p in state.positions if p.user_id != ev.user_id] + [
                    Position(user_id=ev.user_id, option_id=option_id, argument=(v.get("argument") or "")[:300], source="user")
                ]
                if ev.user_id not in state.participants:
                    state.participants.append(ev.user_id)
                state.updated_at = now()
            elif ev.form_id == "deadline":
                state.deadline = self._parse_local(v.get("date"), v.get("time"))
                state.deadline_source = "button"
                state.deadline_fired = False
                state.updated_at = now()
            elif ev.form_id == "park":
                sm.park(state, by=ev.user_id, reason=(v.get("reason") or "")[:300], return_at=self._parse_local(v.get("return"), "10:00"))
            elif ev.form_id == "confirm":
                if not self._may_decide(thread, ev.user_id):
                    await self._ephemeral(thread, ev.user_id, t(self.lang, "notice.only_author"))
                    return
                await self._confirm(thread, by=ev.user_id, option_id=v.get("option") or None, owner=v.get("owner") or None, note=v.get("note") or "")
                return
            else:
                return
            await self.store.save_thread(thread)
            await self._render(thread, phase_change=phase)

    # ------------------------------------------------------------------ decision & record
    async def _confirm(self, thread: TrackedThread, *, by: str, option_id: str | None, owner: str | None, note: str) -> None:
        state = thread.state
        decision = state.decision or Decision()
        decision.owner = owner or decision.owner or thread.author_id
        decision.decider = decision.decider or thread.author_id
        if note:
            decision.summary = note
        sm.confirm_decision(state, by=by, option_id=option_id, decision=decision)
        await self.store.save_thread(thread)
        await self._render(thread, phase_change=True)
        # write the record text from the arguments (slow model) — the card already says "Decided"
        try:
            humans = [m for m in await self._messages(thread.key) if not m.is_bot]
            names = await self.names_for(sorted(set(state.stakeholders) | {m.user_id for m in humans}))
            draft = await self.llm.write_record(state, humans, names)
            decision.summary = draft.summary or decision.summary or (state.option(option_id).label if state.option(option_id) else "")
            decision.rationale = draft.rationale
            decision.consequences = draft.consequences
            decision.dissent = [Dissent(user_id=d.user_id, argument=d.argument) for d in draft.dissent if d.user_id]
            assignees = list(draft.follow_up_assignees) + [None] * len(draft.follow_ups)
            decision.follow_ups = [FollowUp(text=txt, assignee=assignees[i]) for i, txt in enumerate(draft.follow_ups)]
            if draft.owner and not owner:
                decision.owner = draft.owner
        except Exception as e:  # noqa: BLE001
            log.warning("confirm.record_draft_failed", error=str(e)[:200])
            if not decision.summary:
                opt = state.option(option_id)
                decision.summary = f"{opt.id} · {opt.label}" if opt else state.question
            decision.dissent = [Dissent(user_id=p.user_id, argument=p.argument) for p in state.positions if p.option_id and p.option_id != option_id]
        await self.store.save_thread(thread)
        await self._render(thread, phase_change=False)
        await self._remember(thread)
        if state.supersedes:
            await self._supersede(state.supersedes, thread.key)

    async def _remember(self, thread: TrackedThread) -> None:
        st = thread.state
        if not st.decision:
            return
        opt = st.option(st.decision.option_id)
        mem = DecisionMemory(
            thread_key=thread.key,
            title=st.question,
            summary=st.decision.summary[:400],
            option_label=(f"{opt.id} · {opt.label}" if opt else ""),
            decided_at=st.decision.confirmed_at or now(),
            channel_id=thread.ref.channel_id,
            permalink=thread.permalink or self._thread_url(thread.ref),
            record_url=next((r.url for r in st.records if r.kind in ("confluence", "canvas", "markdown")), ""),
            owner=st.decision.owner,
        )
        await self.store.save_decision(mem)

    async def _supersede(self, old_key: str, new_key: str) -> None:
        old = await self.store.get_thread(old_key)
        if old and sm.can(old.state, Status.SUPERSEDED):
            sm.supersede(old.state, new_key)
            await self.store.save_thread(old)
            with contextlib.suppress(Exception):
                await self._render(old, phase_change=False)
        mem = await self.store.get_decision(old_key)
        if mem:
            mem.status = "superseded"
            mem.superseded_by = new_key
            await self.store.save_decision(mem)

    async def _record(self, thread: TrackedThread, by: str) -> None:
        state = thread.state
        names = await self.names_for(sorted(set(state.stakeholders) | {state.decision.owner or "", by}))
        _title, md = render_adr(thread, state, names, lang=state.language or self.lang)
        inp = RecordInput(thread=thread, state=state, names=names, markdown=md)
        written: list[Record] = []
        errors: list[str] = []
        primaries = [r for r in self.recorders if r.kind != "markdown" and r.available()]
        for rec in primaries:
            for attempt in (1, 2):
                try:
                    written += await rec.record(inp)
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("record.failed", kind=rec.kind, attempt=attempt, error=str(e)[:200])
                    if attempt == 2:
                        errors.append(f"{rec.kind}: {str(e)[:120]}")
        if not written:
            md_rec = next((r for r in self.recorders if r.kind == "markdown"), None)
            if md_rec:
                try:
                    written += await md_rec.record(inp)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"markdown: {str(e)[:120]}")
        if not written:
            await self.platform.dm(by, Notice(text=t(self.lang, "notice.record_failed", error="; ".join(errors)), lines=[md[:2800]]))
            return
        state.records.extend(written)
        sm.mark_recorded(state)
        await self.store.save_thread(thread)
        await self._render(thread, phase_change=True, broadcast=True)   # the channel gets the outcome
        await self._remember(thread)
        links = ", ".join(f"<{r.url}|{r.kind}: {r.title}>" for r in written)
        text = t(self.lang, "notice.recorded", links=links) + (f"\n⚠️ {'; '.join(errors)}" if errors else "")
        with contextlib.suppress(Exception):
            await self.platform.dm(by, Notice(text=text))
        log.info("record.done", key=thread.key, records=[r.kind for r in written])

    # ------------------------------------------------------------------ verification
    async def _start_verification(self, thread: TrackedThread, claim_id: str, user_id: str) -> None:
        if self.verifier is None:
            await self._ephemeral(thread, user_id, t(self.lang, "notice.verify_unavailable"))
            return
        claim = next((c for c in thread.state.claims if c.id == claim_id), None)
        if claim is None or claim.checking:
            return
        claim.checking = True
        await self.store.save_thread(thread)
        await self._render(thread, phase_change=False)
        self._spawn(self._finish_verification(thread.key, claim_id))

    async def _finish_verification(self, key: str, claim_id: str) -> None:
        thread = await self.store.get_thread(key)
        claim = next((c for c in thread.state.claims if c.id == claim_id), None) if thread else None
        if thread is None or claim is None:
            return
        try:
            result = await self.verifier.verify(claim.text, thread.state.question)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            result = Verification(verdict="failed", summary=str(e)[:200])
        async with self._lock(key):
            thread = await self.store.get_thread(key)
            if thread is None:
                return
            for c in thread.state.claims:
                if c.id == claim_id:
                    c.verification = result
                    c.checking = False
            await self.store.save_thread(thread)
            await self._render(thread, phase_change=False)

    # ------------------------------------------------------------------ passive: memory recall + auto-suggest
    def _watched(self, channel_id: str) -> bool:
        allowed = [c.strip() for c in self.s.watch_channels.split(",") if c.strip()]
        return not allowed or channel_id in allowed

    async def _passive(self, ev: MessagePosted) -> None:
        if not self._watched(ev.thread.channel_id):
            return
        msg = ev.message
        # 1) memory: does this contradict a recorded decision?
        if len(msg.text) >= 20:
            decisions = await self.store.list_decisions(active_only=True)
            decisions = [d for d in decisions if d.thread_key != ev.thread.key]
            if decisions:
                self._spawn(self._recall(ev, decisions))
        # 2) auto-suggest: cheap classifier every N messages of an untracked thread / channel
        if self.s.autosuggest_every_n > 0:
            buf_key = ev.thread.key if ev.in_thread else f"{ev.thread.platform}:{ev.thread.channel_id}:top"
            buf = self._buffers.setdefault(buf_key, [])
            buf.append(msg)
            del buf[:-15]
            n = await self.store.incr(f"seen:{buf_key}")
            if n % self.s.autosuggest_every_n == 0:
                self._spawn(self._suggest(ev, list(buf)))

    async def _recall(self, ev: MessagePosted, decisions: list[DecisionMemory]) -> None:
        try:
            check = await self.llm.contradiction(ev.message, decisions)
        except Exception as e:  # noqa: BLE001
            log.warning("recall.llm_failed", error=str(e)[:200])
            return
        if not check.contradicts or not check.decision_thread_key:
            return
        d = next((x for x in decisions if x.thread_key == check.decision_thread_key), None)
        if d is None:
            return
        root = ev.thread.thread_id if ev.in_thread else ev.message.id
        notice = Notice(
            text=t(self.lang, "notice.contradiction", date=d.decided_at.strftime("%d.%m.%Y"), title=d.title, why=check.why),
            buttons=[
                Button(label="🔗", action="open_thread", url=d.record_url or d.permalink),
                Button(label=t(self.lang, "btn.dispute"), action="dispute", payload={"t": d.thread_key, "channel_id": ev.thread.channel_id, "message_id": root}),
            ],
        )
        with contextlib.suppress(Exception):
            await self.platform.post_notice(ev.thread.channel_id, root, notice)
            log.info("recall.posted", decision=d.thread_key, channel=ev.thread.channel_id)

    async def _suggest(self, ev: MessagePosted, buf: list[Message]) -> None:
        root_id = ev.thread.thread_id if ev.in_thread else ev.message.id
        ref = ThreadRef(platform=ev.thread.platform, channel_id=ev.thread.channel_id, thread_id=root_id)
        if await self.store.get_thread(ref.key) is not None:
            return
        async with self.store.db.execute("SELECT value FROM counters WHERE name=?", (f"dismissed:{ref.key}",)) as cur:
            if await cur.fetchone():
                return
        names = await self.names_for(sorted({m.user_id for m in buf}))
        try:
            verdict = await self.llm.decision_brewing(buf, names)
        except Exception as e:  # noqa: BLE001
            log.warning("suggest.llm_failed", error=str(e)[:200])
            return
        if not verdict.brewing or verdict.confidence < 0.7:
            return
        author = ev.message.user_id
        if ev.in_thread:
            with contextlib.suppress(Exception):
                msgs = await self.platform.fetch_thread(ref)
                if msgs:
                    author = msgs[0].user_id
        notice = Notice(
            text=t(self.lang, "notice.suggest", question=verdict.question or "…"),
            buttons=[
                Button(label=t(self.lang, "btn.track_yes"), action="track_yes", payload={"t": ref.key}, style="primary"),
                Button(label=t(self.lang, "btn.track_no"), action="track_no", payload={"t": ref.key}),
            ],
        )
        with contextlib.suppress(Exception):
            await self.platform.ephemeral(ref.channel_id, author, notice, thread_id=root_id if ev.in_thread else None)
            log.info("suggest.posted", key=ref.key, to=author)

    # ------------------------------------------------------------------ App Home
    async def publish_home(self, user_id: str) -> None:
        if self.home_view is None:
            return
        threads = await self.store.list_threads(statuses=[s.value for s in (Status.FRAMING, Status.DELIBERATING, Status.VOTING, Status.STALLED, Status.PARKED, Status.DECIDED)])
        decisions = await self.store.list_decisions()
        with contextlib.suppress(Exception):
            await self.platform.publish_home(user_id, self.home_view(threads, decisions, self.lang))

    # ------------------------------------------------------------------ timers (called by the scheduler)
    async def tick(self) -> None:
        active = await self.store.list_threads(statuses=[s.value for s in (Status.FRAMING, Status.DELIBERATING, Status.VOTING, Status.STALLED, Status.PARKED)])
        for thread in active:
            try:
                await self._tick_thread(thread.key)
            except Exception:
                log.exception("tick.failed", key=thread.key)

    async def _tick_thread(self, key: str) -> None:
        async with self._lock(key):
            thread = await self.store.get_thread(key)
            if thread is None:
                return
            st, lang = thread.state, self.lang
            messages = [m for m in await self._messages(key) if not m.is_bot]
            dirty, phase = False, False

            # a) silent stakeholder: one DM per question, never twice
            for q in st.open_questions:
                if q.answered or not q.directed_to or q.nudged_at or q.declined:
                    continue
                after = [m for m in messages if m.at > q.asked_at]
                if any(m.user_id == q.directed_to for m in after):
                    q.answered = True
                    dirty = True
                    continue
                others = [m for m in after if m.user_id != q.directed_to]
                if self._elapsed(q.asked_at) >= timedelta(minutes=self.s.silence_minutes) or len(others) >= self.s.silence_messages:
                    q.nudged_at = now()
                    dirty = True
                    url = thread.permalink or self._thread_url(thread.ref)
                    with contextlib.suppress(Exception):
                        await self.platform.dm(
                            q.directed_to,
                            Notice(
                                text=t(lang, "notice.nudge", channel=thread.ref.channel_id, question=st.question),
                                lines=[f"• {q.text}"],
                                buttons=[
                                    Button(label=t(lang, "btn.open_thread"), action="open_thread", url=url),
                                    Button(label=t(lang, "btn.not_me"), action="not_me", payload={"t": key, "question_id": q.id}),
                                ],
                            ),
                        )
                    log.info("nudge.sent", key=key, to=q.directed_to)

            # b) deadline: opens the vote (reversible, safe); in Voting it closes it (DM the decider)
            if st.deadline and not st.deadline_fired and now() >= st.deadline:
                if st.status in (Status.FRAMING, Status.DELIBERATING, Status.STALLED) and st.options:
                    phase = sm.open_voting(st)
                    st.deadline_fired = dirty = True
                    await self._dm_decider(thread, f"{t(lang, 'notice.deadline_vote')}\n*{st.question}*")
                elif st.status == Status.VOTING:
                    st.deadline_fired = dirty = True
                    await self._dm_decider(thread, self._vote_closed_text(thread))

            # c) stalled: nothing happens while deliberating
            if st.status in (Status.DELIBERATING, Status.FRAMING) and self._elapsed(thread.last_activity_at) >= timedelta(hours=self.s.stall_hours):
                reason = ""
                try:
                    reason = await self.llm.stalled_summary(st, await self.names_for(st.stakeholders))
                except Exception:  # noqa: BLE001
                    reason = "; ".join(q.text for q in st.unanswered())[:300]
                sm.stall(st, reason)
                dirty = True
                open_q = "; ".join(q.text for q in st.unanswered()) or "—"
                with contextlib.suppress(Exception):
                    await self.platform.dm(
                        thread.author_id,
                        Notice(
                            text=t(lang, "notice.stalled", question=st.question, open=open_q),
                            buttons=[Button(label=t(lang, "btn.open_thread"), action="open_thread", url=thread.permalink or self._thread_url(thread.ref))],
                        ),
                    )

            # d) expired: stalled for too long
            elif st.status == Status.STALLED and self._elapsed(st.updated_at) >= timedelta(hours=self.s.expire_hours):
                names = await self.names_for(st.stakeholders)
                sm.expire(st, render_expired_note(st, names, lang))
                dirty, phase = True, True
                open_q = "; ".join(q.text for q in st.unanswered()) or "—"
                with contextlib.suppress(Exception):
                    await self.platform.dm(thread.author_id, Notice(text=t(lang, "notice.expired", open=open_q)))

            # e) parked: remind the author when the return date comes
            elif st.status == Status.PARKED and st.parked and st.parked.return_at and not st.parked.reminded and now() >= st.parked.return_at:
                st.parked.reminded = dirty = True
                with contextlib.suppress(Exception):
                    await self.platform.dm(
                        thread.author_id,
                        Notice(
                            text=t(lang, "notice.parked_return", question=st.question, reason=st.parked.reason),
                            buttons=[
                                Button(label=t(lang, "btn.unpark"), action="unpark", payload={"t": key}, style="primary"),
                                Button(label=t(lang, "btn.open_thread"), action="open_thread", url=thread.permalink or self._thread_url(thread.ref)),
                            ],
                        ),
                    )

            if dirty:
                await self.store.save_thread(thread)
                await self._render(thread, phase_change=phase)


class Scheduler:
    """Runs `engine.tick()` every N seconds and prunes the dedup table once an hour."""

    def __init__(self, engine: Engine, interval_s: float):
        self.engine, self.interval = engine, interval_s
        self._stop = asyncio.Event()

    async def run(self) -> None:
        n = 0
        while not self._stop.is_set():
            try:
                await self.engine.tick()
                n += 1
                if n % max(1, int(3600 / self.interval)) == 0:
                    await self.engine.store.prune_seen()
            except Exception:
                log.exception("scheduler.tick_failed")
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)

    def stop(self) -> None:
        self._stop.set()

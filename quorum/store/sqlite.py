"""SQLite persistence: tracked threads (JSON), decisions journal, seen event ids, per-channel counters."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import aiosqlite

from quorum.domain.models import DecisionMemory, TrackedThread

_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    key TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    status TEXT NOT NULL,
    json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    thread_key TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_events (
    event_id TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS message_cache (
    thread_key TEXT NOT NULL,
    message_id TEXT NOT NULL,
    json TEXT NOT NULL,
    PRIMARY KEY (thread_key, message_id)
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "store not opened"
        return self._db

    # --- threads --------------------------------------------------------------------------------
    async def save_thread(self, t: TrackedThread) -> None:
        await self.db.execute(
            "INSERT INTO threads(key, channel_id, status, json, updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, json=excluded.json, updated_at=excluded.updated_at",
            (t.key, t.ref.channel_id, t.state.status.value, t.model_dump_json(), datetime.now(UTC).isoformat()),
        )
        await self.db.commit()

    async def get_thread(self, key: str) -> TrackedThread | None:
        async with self.db.execute("SELECT json FROM threads WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return TrackedThread.model_validate_json(row["json"]) if row else None

    async def list_threads(self, *, statuses: list[str] | None = None, channel_id: str | None = None) -> list[TrackedThread]:
        q, args = "SELECT json FROM threads", []
        conds = []
        if statuses:
            conds.append(f"status IN ({','.join('?' * len(statuses))})")
            args += statuses
        if channel_id:
            conds.append("channel_id=?")
            args.append(channel_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY updated_at DESC"
        async with self.db.execute(q, args) as cur:
            rows = await cur.fetchall()
        return [TrackedThread.model_validate_json(r["json"]) for r in rows]

    async def delete_thread(self, key: str) -> None:
        await self.db.execute("DELETE FROM threads WHERE key=?", (key,))
        await self.db.execute("DELETE FROM message_cache WHERE thread_key=?", (key,))
        await self.db.commit()

    # --- decisions journal (memory) -------------------------------------------------------------
    async def save_decision(self, d: DecisionMemory) -> None:
        await self.db.execute(
            "INSERT INTO decisions(thread_key, channel_id, status, decided_at, json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(thread_key) DO UPDATE SET status=excluded.status, decided_at=excluded.decided_at, json=excluded.json",
            (d.thread_key, d.channel_id, d.status, d.decided_at.isoformat(), d.model_dump_json()),
        )
        await self.db.commit()

    async def get_decision(self, thread_key: str) -> DecisionMemory | None:
        async with self.db.execute("SELECT json FROM decisions WHERE thread_key=?", (thread_key,)) as cur:
            row = await cur.fetchone()
        return DecisionMemory.model_validate_json(row["json"]) if row else None

    async def list_decisions(self, *, active_only: bool = False, channel_id: str | None = None) -> list[DecisionMemory]:
        q, args = "SELECT json FROM decisions", []
        conds = []
        if active_only:
            conds.append("status='active'")
        if channel_id:
            conds.append("channel_id=?")
            args.append(channel_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY decided_at DESC"
        async with self.db.execute(q, args) as cur:
            rows = await cur.fetchall()
        return [DecisionMemory.model_validate_json(r["json"]) for r in rows]

    # --- dedup ----------------------------------------------------------------------------------
    async def seen(self, event_id: str) -> bool:
        """Returns True if this event id was already processed (and records it otherwise)."""
        try:
            await self.db.execute("INSERT INTO seen_events(event_id, seen_at) VALUES(?,?)", (event_id, datetime.now(UTC).isoformat()))
            await self.db.commit()
            return False
        except aiosqlite.IntegrityError:
            return True

    async def prune_seen(self, older_than: timedelta = timedelta(days=1)) -> None:
        cutoff = (datetime.now(UTC) - older_than).isoformat()
        await self.db.execute("DELETE FROM seen_events WHERE seen_at < ?", (cutoff,))
        await self.db.commit()

    # --- counters -------------------------------------------------------------------------------
    async def incr(self, name: str) -> int:
        await self.db.execute(
            "INSERT INTO counters(name, value) VALUES(?,1) ON CONFLICT(name) DO UPDATE SET value=value+1", (name,)
        )
        await self.db.commit()
        async with self.db.execute("SELECT value FROM counters WHERE name=?", (name,)) as cur:
            row = await cur.fetchone()
        return int(row["value"])

    async def reset_counter(self, name: str) -> None:
        await self.db.execute("INSERT INTO counters(name, value) VALUES(?,0) ON CONFLICT(name) DO UPDATE SET value=0", (name,))
        await self.db.commit()

    # --- message cache (so the extractor does not refetch the thread every burst) ---------------
    async def cache_messages(self, thread_key: str, messages: list) -> None:
        await self.db.executemany(
            "INSERT OR REPLACE INTO message_cache(thread_key, message_id, json) VALUES(?,?,?)",
            [(thread_key, m.id, m.model_dump_json()) for m in messages],
        )
        await self.db.commit()

    async def delete_cached_message(self, thread_key: str, message_id: str) -> None:
        await self.db.execute("DELETE FROM message_cache WHERE thread_key=? AND message_id=?", (thread_key, message_id))
        await self.db.commit()

    async def cached_messages(self, thread_key: str) -> list[dict]:
        async with self.db.execute("SELECT json FROM message_cache WHERE thread_key=? ORDER BY message_id", (thread_key,)) as cur:
            rows = await cur.fetchall()
        return [json.loads(r["json"]) for r in rows]

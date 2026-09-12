"""Ambiguous Tasks via the live MCP schema captured in docs/ambiguous-tools.json.

No synthetic record URLs and no automatic retries of writes. A timeout may mean
the task was created: the engine persists that ambiguity before calling us.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse
from uuid import UUID

import aiohttp
from pydantic import BaseModel

from quorum.domain.models import Commitment, TrackedThread


class ExternalTask(BaseModel):
    id: str
    status: str
    url: str | None = None
    description: str = ""


class AmbiguousTasks:
    def __init__(self, token: str, endpoint: str = "https://app.ambiguous.ai/mcp",
                 assignees: dict[str, str] | None = None):
        self.token, self.endpoint = token, endpoint
        self.assignees = assignees or {}
        self.schemas: dict = {}

    async def rpc(self, method: str, params: dict) -> dict:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:  # noqa: SIM117
            async with session.post(self.endpoint, headers=headers,
                                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Ambiguous HTTP {response.status}")
                raw = await response.text()
        if raw.lstrip().startswith(("event:", "data:")):
            packets = [json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith("data:")]
            packet = next((p for p in packets if p.get("id") == 1), {})
        else:
            packet = json.loads(raw)
        if "error" in packet or "result" not in packet:
            raise RuntimeError("Ambiguous MCP request failed")
        return packet["result"]

    async def discover(self) -> dict:
        await self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "quorum", "version": "0.1"}})
        result = await self.rpc("tools/list", {})
        self.schemas = {tool["name"]: tool["inputSchema"] for tool in result["tools"]}
        for name in ("create_task", "get_task", "list_tasks"):
            if name not in self.schemas:
                raise RuntimeError(f"Ambiguous does not expose {name}")
        return self.schemas

    async def call(self, name: str, arguments: dict):
        if not self.token:
            raise RuntimeError("AMBIGUOUS_API_KEY is required for workspace operations")
        if not self.schemas:
            await self.discover()
        schema = self.schemas.get(name)
        if not schema or set(arguments) - set(schema.get("properties", {})):
            raise RuntimeError("Ambiguous tool schema changed; inspect tools/list before writing")
        if set(schema.get("required", [])) - set(arguments):
            raise RuntimeError("Missing required Ambiguous tool arguments")
        result = await self.rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"Ambiguous {name} failed; no success recorded")
        data = result.get("structuredContent")
        if data is None:
            texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
            if len(texts) != 1:
                raise RuntimeError("Ambiguous returned no unambiguous structured result")
            data = json.loads(texts[0])
        return data

    @staticmethod
    def task(data: dict) -> ExternalTask:
        # MCP versions may wrap a resource; reject unknown shapes rather than inventing fields.
        data = data.get("task", data.get("data", data))
        task = ExternalTask.model_validate(data)
        UUID(task.id)
        if task.url and urlparse(task.url).scheme != "https":
            raise ValueError("Ambiguous returned an unsafe task link")
        return task

    async def create(self, thread: TrackedThread, commitment: Commitment) -> ExternalTask:
        args = {"title": commitment.action[:255], "description": (
            f"Quorum loop {thread.state.id}; commitment {commitment.id}\n"
            f"Thread: {thread.permalink}\nOwner (Slack): {commitment.owner}\n"
            f"Decision: {thread.state.decision.summary}\n"
            f"Closure: {next(c for c in thread.state.closure_conditions if c.id == commitment.closure_condition_id).expected_state}"
        )}
        if commitment.due_at:
            args["due_date"] = commitment.due_at.date().isoformat()
        if commitment.owner in self.assignees:
            args["assignee_id"] = str(UUID(self.assignees[commitment.owner]))
        return self.task(await self.call("create_task", args))

    async def get(self, record_id: str) -> ExternalTask:
        task = self.task(await self.call("get_task", {"id": str(UUID(record_id))}))
        if task.id != record_id:
            raise RuntimeError("Ambiguous returned a different task ID")
        return task

    async def recover(self, commitment_id: str) -> ExternalTask | None:
        data = await self.call("list_tasks", {"q": commitment_id, "limit": 200, "show_archived": "true"})
        items = data if isinstance(data, list) else data.get("tasks", data.get("data", []))
        if not isinstance(items, list):
            raise TypeError("Unknown Ambiguous list response; write remains uncertain")
        matches = [self.task(item) for item in items if commitment_id in (item.get("description") or "")]
        if len(matches) > 1:
            raise RuntimeError("Multiple tasks match this commitment; human reconciliation required")
        return matches[0] if matches else None

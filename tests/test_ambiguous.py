"""Contract checks against the captured live schema, not claims of live writes."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from quorum.recorders.ambiguous import AmbiguousTasks


async def test_mcp_schema_boundary_and_record_identity():
    client = AmbiguousTasks("test-token")
    client.schemas = {t["name"]: t["inputSchema"] for t in json.loads(
        Path("docs/ambiguous-tools.json").read_text())["tools"]}
    record_id = str(uuid4())
    calls = []

    async def rpc(method, params):
        calls.append((method, params))
        return {"content": [{"type": "text", "text": json.dumps({"task": {"id": record_id, "status": "todo"}})}]}

    client.rpc = rpc
    result = await client.get(record_id)
    assert result.id == record_id and result.url is None  # never synthesize a vendor link
    assert calls[0][1] == {"name": "get_task", "arguments": {"id": record_id}}
    with pytest.raises(RuntimeError, match="schema changed"):
        await client.call("create_task", {"title": "Example", "invented_idempotency_key": "x"})
    with pytest.raises(RuntimeError, match="required"):
        await client.call("create_task", {})
    with pytest.raises(RuntimeError, match="different task"):
        await client.get(str(uuid4()))


async def test_mcp_error_is_not_success():
    client = AmbiguousTasks("test-token")
    client.schemas = {"get_task": {"properties": {"id": {}}, "required": ["id"]}}

    async def rpc(method, params):
        return {"isError": True, "content": [{"type": "text", "text": "Denied"}]}

    client.rpc = rpc
    with pytest.raises(RuntimeError, match="no success recorded"):
        await client.get(str(uuid4()))

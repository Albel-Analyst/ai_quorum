# Quorum

**Slack remembers what the team said. Quorum remembers what still has to become true.**

Mention Quorum in a Slack thread. One living OpenLoop card carries the conversation through
uncertainty, a human-confirmed decision, approved commitments, external task execution,
and a later evidence check. **Decided is not closed.**

The existing Python engine, SQLite store, structured OpenAI extraction, Slack Block Kit
renderer and Exa verifier are preserved. The build contract is
`.local/Quorum_Build_Specification.docx`; the initial comparison is [docs/AUDIT.md](docs/AUDIT.md).
The previous decision-card documentation is archived in [docs/LEGACY_DECISION_CARD.md](docs/LEGACY_DECISION_CARD.md).

```text
Slack / CopilotKit Channels
  → sourced structured proposals → guarded OpenLoop reducer → SQLite
  → human approval → Ambiguous create → persist actual ID → read back
  → scheduled wake / Check now → latest Slack + current task → verified closure
```

The canonical card stays at the same Slack timestamp. It shows the decision and dissent,
material assertions separately from Exa evidence, owner/due date/closure condition,
blockers, external references and the next scheduled check. Most events only edit it.

## Run

```bash
uv sync
cp .env.example .env  # only on a fresh checkout; do not overwrite existing credentials
make run             # existing Python Bolt / Socket Mode transport
make check
```

Configure Slack/OpenAI/Exa as described in [docs/SLACK_APP.md](docs/SLACK_APP.md).
Set `AMBIGUOUS_API_KEY` from the intended workspace's Connect instructions to enable
real task execution. No key means a visible unavailable state, never a fallback task.
An optional `AMBIGUOUS_ASSIGNEES` JSON mapping links Slack IDs to verified workspace UUIDs;
without it, the Slack owner is recorded in task context and the external task stays unassigned.

`Record in Ambiguous` is a separate approval after the commitment contract is confirmed.
The adapter uses discovered `create_task`, `get_task` and `list_tasks` schemas, captured in
[docs/ambiguous-tools.json](docs/ambiguous-tools.json). It never synthesizes task URLs.
A timeout persists an uncertain write; `Check now` searches for the existing task before
reading it. An absent search result does not authorize a blind second write.

## CopilotKit transport

See [channels/README.md](channels/README.md). This optional process uses the official starter's
pinned Channels/runtime pair and the supported direct Slack adapter because this repository
already owns its Slack installation and tokens. It replaces Python Socket Mode ingress;
it does not replace the working reducer. Use exactly one ingress process per Slack app.
Native controls currently use the existing Slack Block Kit approval modals. SDK-managed
interrupt/resume approvals are **not implemented** and should not be claimed in the pitch.

## Checks and demonstration

```bash
make check
uv run pytest tests/test_closure.py -k a15 -v       # three fresh OFFLINE lifecycle runs
npm ci --prefix channels --ignore-scripts
npm run check --prefix channels
npm test --prefix channels
uv run python -m quorum.tools.live_smoke --channel C_YOUR_DEMO_CHANNEL
```

The Slack smoke command posts clearly labeled diagnostic threads and proves one card survives
multiple edits and real read-back. It does **not** claim human approval or external completion.
The [demo runbook](docs/DEMO.md) describes the real workflow and the remaining live gates.

Tests use doubles unless explicitly opted into live access:

```bash
QUORUM_LIVE_LLM=1 uv run pytest tests/test_llm_live.py -q
QUORUM_LIVE_EXA=1 uv run python -c "from dotenv import load_dotenv; load_dotenv('.env'); import pytest; raise SystemExit(pytest.main(['tests/test_exa.py','-q']))"
QUORUM_LIVE_AMBIGUOUS=1 uv run pytest tests/test_closure.py -k live_ambiguous -v -s
```

The last command creates and completes one labeled real test task; Slack/model inputs in
that test are doubles. Full live judging readiness requires three real human-driven runs
in Slack with Ambiguous and Channels connected.

## Operational limits

Run one Python engine against a SQLite database. Per-thread locks are process-local.
`Check now` and the scheduler re-read current Slack and task state. Failed reads suppress
closure; changed/deleted commitment sources require human review. No reminder DMs are sent.
A deferred loop stops checking; reopen work in a new thread. Supersession records the old
history and retires old obligations when the replacement decision is confirmed.

After an ambiguous initial card-post timeout, automatic reposting is suppressed to prevent
duplicates. Inspect Slack and recover the known card timestamp before retrying. Externally
cancelled tasks are retired, not counted as completed. Observable closure currently means
**the linked task is `done`**, not independent proof that a deployment or real-world migration succeeded.

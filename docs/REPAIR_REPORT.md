# Quorum repair report — 2026-09-12

## 1. Audit summary

Read `.local/Quorum_Build_Specification.docx` before repair. Preserved SQLite,
the existing structured OpenAI extraction boundary, Slack transport and native
Block Kit controls, Exa search, and compatible domain imports. The initial
requirement-by-requirement classification is in [AUDIT.md](AUDIT.md).

## 2. Meaningful gaps

The original lifecycle stopped at recording a decision. It lacked durable
commitments, explicit completion criteria and external execution read-back.
Phase changes could repost cards; timers acted on cached state. Neither
Ambiguous nor CopilotKit Channels was integrated. Confirmed text and fuzzy
claim matching could carry interpretations or evidence beyond what was approved.

## 3. Changes made

| Files/modules | Purpose |
| --- | --- |
| `quorum/domain/models.py`, `state_machine.py` | Authoritative OpenLoop, obligations, evidence, audit history and guarded closure; compatible CardState alias |
| `quorum/llm/{base,prompts}.py`, `core/merge.py` | Sourced structured proposals, conservative commitment handling, preserved human authority and dissent |
| `quorum/core/engine.py`, `store/sqlite.py` | Latest-thread reconciliation, durable next checks and external IDs, approval gates, write uncertainty handling, cancellation and supersession |
| `quorum/render/blockkit.py`, `platform/slack/normalize.py`, `domain/events.py` | One mutable card, contextual controls and stable interaction IDs |
| `quorum/recorders/ambiguous.py`, `docs/ambiguous-tools.json` | Adapter based on discovered real MCP schemas: create, persist ID, read, recover uncertain writes |
| `quorum/verifier/exa.py` | Evidence-backed verdicts with assertions and sources kept separate |
| `channels/`, `quorum/platform/channels_bridge.py` | Pinned Channels direct Slack ingress, scoped SDK history and authenticated local bridge |
| `quorum/{app,config}.py`, `.env.example` | Wire services without inventing credentials or success |
| `tests/test_closure.py`, `test_ambiguous.py`, `test_channels_bridge.py`, existing regression tests | A1–A15 and failure/authority regressions |
| `quorum/tools/live_smoke.py`, README and docs | Repeatable real Slack transport checks and honest demo instructions |

## 4. Architecture after repair

Slack directly, or through CopilotKit Channels → current thread history →
structured model proposals → deterministic OpenLoop reducer → SQLite →
approved external task creation → persisted wake → current Slack and task
read-back → evidence-gated closure → edit the original card.

`DECIDED` does not close a loop. Observable completion requires current external
evidence; human completion requires the appropriate explicit attestation or
adjudication. Failed tools and changed source messages keep the loop unresolved.

## 5. Sponsor usage

- **CopilotKit:** In Channels mode the SDK owns ingress, thread identity,
  subscriptions and history. Existing Slack APIs update the durable card and
  supply native approval controls. An authenticated SDK session is unproven;
  SDK-native interrupt/resume flows are not implemented. The fallback Python
  transport does not count as CopilotKit usage.
- **Ambiguous:** Its persisted task ID and later current task state drive
  observable completion. Public MCP discovery worked; workspace authentication
  returned 401. No real task has yet been created. The schema does not promise
  a task URL, so the implementation never constructs a fake one.
- **Exa:** Authorized verification supplies inspectable evidence for material,
  disputed external claims. Live search and a real OpenAI evidence judgment
  succeeded; insufficient evidence remains unresolved.

## 6. Tests and observed evidence

- `make check`: Ruff passed; **250 passed, 4 skipped**. The three clean A15
  lifecycles include persistence/reload and scheduled external reconciliation,
  using explicit platform/model/task doubles.
- `npm run check --prefix channels`: passed.
- `npm test --prefix channels`: passed, one delivery normalization test.
- Channels dependency audit after lockfile updates: **zero vulnerabilities**.
- `git diff --check`: passed.
- Live OpenAI extraction/classification: two tests passed.
- Live Exa test file with `.env` loaded: eight tests passed. Separate live
  Exa + OpenAI judgment returned PostgreSQL primary-source license evidence.
- `uv run python -m quorum.tools.live_smoke --channel C0C1B6KANQN`:
  **three real Slack runs passed**, each preserving one card through four edits
  and read-back. These diagnostic cards end CANCELLED, not fake CLOSED.

## 7. Remaining blockers and limits

Set `AMBIGUOUS_API_KEY` in local `.env` to enable actual workspace operations.
For Channels, set `CHANNEL_CODE`, `INTELLIGENCE_API_KEY`, a random
`QUORUM_BRIDGE_TOKEN`, and `CHANNELS_ENABLED=true`; run the two processes in
[channels/README.md](../channels/README.md). Keep secrets out of chat and git.
The Slack demo channel is already known: `#quorum-demo`, `C0C1B6KANQN`.

The full live sponsor lifecycle remains unproven. Complete three real
Slack → human approval → Ambiguous write → later external change → CLOSED
runs before claiming readiness. Automated acceptance tests do not replace this.
Unknown initial card-post outcomes require provider inspection; unknown task
writes are recovered by marker, never blindly retried. Deployment targets one
core process. Optional webhooks and closure documents were not added.

## 8. Judging self-assessment

| Criterion | Score | Concrete evidence needed for 5 |
| --- | --- | --- |
| Core Requirements & Functionality | 3/5 | Three real full lifecycles ending in verified closure |
| Innovation & Theme Alignment | 4/5 | Judges observe delayed closure in the original live thread |
| Technical Execution & Integration | 3/5 | Authenticated Channels, real Ambiguous round trip, live failure recovery |
| Usefulness & Agentic Experience | 4/5 | A teammate completes the flow unassisted and understands what counts as done |

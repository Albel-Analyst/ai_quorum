# Closure demo and evidence gates

The contract is `.local/Quorum_Build_Specification.docx`. Use a fresh thread for
each run. Never edit SQLite to make the demo advance.

## Two-minute story

1. In `#quorum-demo`, two actual participants debate Eskiz vs Twilio. Mention
   Quorum once. The same OpenLoop card remains throughout the demo.
2. Make a decision-critical, externally disputed assertion. Click **Verify**.
   Exa sources appear separately from the assertion. An insufficient result
   stays unresolved; do not substitute a canned success.
3. State the decision. The thread author clicks **Confirm / Edit**, reviews the
   chosen option and arguments, and confirms. Show dissent remains visible.
4. The owner explicitly promises work: “I'll migrate OTP by Friday.” Click
   **Confirm commitment**, review owner/date and select **Ambiguous task is done**.
   This is the agreed machine-observable criterion, not proof of deployment.
5. Click **Record in Ambiguous**. Open the actual returned link if the tool supplies
   one; otherwise show the returned task ID in Ambiguous. Do not invent a URL.
6. Stop talking. Restart the Python process if desired. Change that same task to
   `done` in Ambiguous with a second actor. Leave the database untouched.
7. Wait for the persisted check, or click **Check now**. The engine re-reads Slack
   and the task. The original card becomes **CLOSED** with timestamp and evidence.
8. In a separate fresh thread, reject the commitment or omit the Ambiguous key.
   Show that no external record or completed state is invented.

Run this three times with real people approving the native controls before
claiming full live readiness. The current automated three-run lifecycle uses
explicit test doubles and is not proof of this live gate.

## Commands

```bash
make check
uv run pytest tests/test_closure.py -k a15 -v
npm run check --prefix channels
npm test --prefix channels
# Explicit real test-task write; needs AMBIGUOUS_API_KEY
QUORUM_LIVE_AMBIGUOUS=1 uv run pytest tests/test_closure.py -k live_ambiguous -v -s
# Real transport check, clearly labeled test data, three new diagnostic threads
uv run python -m quorum.tools.live_smoke --channel C0C1B6KANQN
```

Use normal `make run` for Python Slack transport, or follow `channels/README.md`
to put the SDK in charge of ingress. Do not run two Socket Mode clients for one app.

## Observed live evidence — 2026-09-12

- Slack auth succeeded; `#quorum-demo` is `C0C1B6KANQN` and the bot is a member.
- Three real Slack transport runs passed: one card, four edits, read-back of the
  same timestamp. Diagnostic cards are deliberately **CANCELLED**, not CLOSED.
  Local details are in `.local/slack-smoke.json`.
- Live OpenAI extraction/classification tests passed.
- Live Exa search passed. Exa + the real OpenAI judge confirmed the PostgreSQL
  license claim with `postgresql.org/about/licence/`, its press FAQ and wiki FAQ.
- Ambiguous's public MCP `initialize` and `tools/list` succeeded. `auth_whoami`
  without a credential returned HTTP 401. No real task has been created.
- Channels TypeScript and local normalization checks passed. No authenticated
  Channels session has been observed.

## Deliberate limits / recovery

- A failed task create may have succeeded remotely. The write intent is saved
  before the call; **Check now** searches by commitment marker and reads back a
  recovered ID. If no match is found, retain uncertainty. Do not click a blind retry.
- An initial card-post timeout also retains uncertainty rather than duplicating
  the artifact. Inspect the provider result before recovering the known timestamp.
- Changed/deleted commitment source text or a replacement-decision proposal blocks
  closure pending human review, even if the previous external task says done.
- Cancel/defer stops local monitoring. It does not silently delete external tasks.
- Supersede starts tracking a replacement Slack thread; confirming its decision
  retires the old obligations and preserves the old decision and dissent.
- No reminder DMs, automatic votes, manager escalations, extra dashboards or
  closure-document exports are part of the repaired primary path.

## Judging evidence still required

| Criterion | Current evidence score | What would justify 5 |
| --- | --- | --- |
| Core functionality | 3/5 | Three real Slack → human approval → Ambiguous → later read → CLOSED runs |
| Innovation / environment | 4/5 | Judges observe delayed external closure in the original live thread |
| Technical integration | 3/5 | Authenticated Channels ingress and real Ambiguous create/read, plus a live failure recovery |
| Usefulness / agent experience | 4/5 | A teammate completes the whole flow unassisted and understands the closure criterion |

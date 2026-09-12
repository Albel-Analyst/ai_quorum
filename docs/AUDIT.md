# Build-contract audit — 2026-09-12

Authority: `.local/Quorum_Build_Specification.docx`, sections 5–9, 11–16;
the mission's A1–A15 numbering is used for new acceptance tests.
Baseline: 220 tests passed, 3 live tests skipped. Existing `.gitignore` edits preserved.

| Requirement | Existing implementation | Status | Problem | Required change |
| --- | --- | --- | --- | --- |
| Durable authoritative OpenLoop | SQLite JSON CardState + TrackedThread | PARTIAL | Decision card only; no obligations or evidence contract | Extend existing aggregate, retain imports/data compatibility |
| Structured proposals | Pydantic Responses extraction + deterministic merge | GOOD | Working extraction boundary | Preserve; add sourced commitment proposals |
| Guarded lifecycle | Framing → Decided → Recorded | CONFLICTING | Recording ends tracking | Add execution, verification, evidence-gated closure |
| Human decision authority | Confirm modal + author check | PARTIAL | Draft changes confirmed text after approval | Freeze reviewed text, preserve dissent and alternatives |
| One live artifact | Block Kit + chat.update | CONFLICTING | Phase changes repost; any update error duplicates | Edit stable card; surface errors without speculative repost |
| Rehydrate / proof before reminder | Cached messages + timer DMs | CONFLICTING | Stale evidence; no external reads | Fetch latest thread and external records on wake; silence on failure |
| Event idempotency | SQLite event IDs in Slack adapter | PARTIAL | Interactive writes and crash ambiguity unguarded | Durable write intent, persisted returned IDs, no blind write retry |
| Ambiguous Tasks | Absent | MISSING | No execution system | Live tools/list inspected: create_task, get_task, list_tasks, update_task exist; auth required for workspace operations |
| CopilotKit Channels | Absent; Python Bolt transport | MISSING | No sponsor SDK | Preserve Slack fallback; integrate supported Channels only with verified API |
| Exa material evidence | Search + structured judge, sources | PARTIAL | Fuzzy identity can transfer proof; unsupported verdict fallback | Exact assertion identity; cited-source requirement; material/disputed gate |
| Commitment / closure condition | ADR follow-up strings | MISSING | No owner approval, due date, execution evidence | Sourced proposals + reviewed closure contract |
| Cancellation / defer / supersession | Park and cross-thread journal | PARTIAL | Old work not retired | Explicit terminal states + history and retired commitments |
| Restart and scheduling | SQLite + scheduler | PARTIAL | Stops at recorded; triggers implicit | Persist next check and reconciliation timestamps |
| Voting, passive recall, multi-recorders | Working broad legacy features | OVERBUILT | Competes with opt-in closure story | Keep compatibility modules; remove from primary demo path |
| Full demo | Decision → ADR tests | PARTIAL | No external closure; no sponsor round trip | A1–A15 regression suite plus separately gated real integration proof |

Live boundaries: `.env` contains Slack/OpenAI/Exa credentials, but no Ambiguous
or CopilotKit credentials. Public Ambiguous MCP schema discovery succeeds without
authentication. Real task creation must not be represented as passed until an
authenticated create/read/later-read run has succeeded. Channels documentation
explicitly limits managed MessageRef reuse across deliveries; use supported Slack
edits for the persistent card (spec section 18 permits this).

<div align="center">

# ⚖️ Quorum

**An agent with no lines.**
One living decision card. Buttons instead of replies. Inside the thread where your team is already deciding.

[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Slack · Socket Mode](https://img.shields.io/badge/Slack-Socket%20Mode-4A154B?logo=slack&logoColor=white)](docs/SLACK_APP.md)
[![OpenAI structured output](https://img.shields.io/badge/LLM-gpt--5.4-000000?logo=openai&logoColor=white)](quorum/llm)
[![tests](https://img.shields.io/badge/tests-pytest%20%C2%B7%20no%20Slack%20needed-2ea44f)](tests)
[![docker compose](https://img.shields.io/badge/run-docker%20compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)

</div>

---

Teams decide things in Slack threads — and then the decision evaporates. Who was for what? Was it actually decided?
Who owns it? Where is it written? Three weeks later someone quietly does the opposite.

Quorum sits in that thread. It **never replies**. It keeps exactly **one message** — a decision card — and edits it as
people talk. It structures the argument instead of joining it: the question, the options, who stands where and why,
what is still open, which facts can be checked, and the deadline. Everything else it does is quiet: a button, an
ephemeral hint, a private DM. Anything that leaves the chat — Confluence, Jira, a public ping — happens only after a
human taps.

> **Every tracked thread ends.** Decided · Parked (why, and when to come back) · Expired (what stayed open).
> Nothing dies silently.

## What it looks like

```
┌──────────────────────────────────────────────────────────────────┐
│ 🗳 Voting · ⏱ Wed 18:00                                          │
│ Postgres or Mongo for the new service?                           │
│                                                                  │
│ Options                                                          │
│  A · Postgres — managed RDS, the team knows it                   │
│  B · Mongo Atlas — flexible schema, higher ops cost              │
│                                                                  │
│ Positions                                                        │
│  @ann → A: "we know it, boring is good"                          │
│  @bob → B ✎: "cheaper ops for us"                                │
│  @cid — no position yet                                          │
│                                                                  │
│ Open questions                                                   │
│  • How much is Atlas M10 in our region? → @cid 📩                │
│                                                                  │
│ Claims to verify                                                 │
│  • "RDS db.t3.medium is ~$60/month" ✅ confirmed · aws.amazon.com │
│                                                                  │
│  [Vote A]  [Vote B]     2/3 voted · A: 1 · B: 1                  │
│  [Confirm decision]  [Back to discussion]                        │
│ updated 1 min ago                                                │
└──────────────────────────────────────────────────────────────────┘
```

## How it works

```mermaid
flowchart LR
    S[Slack thread] -- @Quorum · shortcut · ⚖️ --> A[Adapter<br/>Bolt · Socket Mode]
    A -- normalized events --> E[Engine<br/>per-thread lock · coalescing]
    E -- state JSON + new messages --> L[LLM extractor<br/>strict schema]
    L -- Extraction --> E
    E -- CardState --> R[Renderer<br/>Block Kit · deterministic]
    R -- chat.update --> S
    E -. one DM, never twice .-> DM[Silent stakeholder]
    E -- after a tap --> P[Recorders<br/>Confluence · Canvas · Jira · markdown]
    E -- after a tap --> V[Verifier<br/>Exa]
    E --> M[(Decisions journal)]
    M -. contradiction? .-> S
```

**The card is a state machine.** `Framing → Deliberating → Voting → Decided → Recorded`, with `Stalled`, `Parked`,
`Expired` on the side and `Superseded` when a later decision replaces it. The LLM never writes the card: it receives
the current state as JSON plus the new messages and returns a new state under a strict schema. Deterministic code
merges it (human corrections win), runs the transitions and renders Block Kit. The card is stable, diffable and tested
without Slack — another messenger is another renderer, not another agent.

| Moment | What Quorum does | What it never does |
|---|---|---|
| Someone mentions it, uses *Track with Quorum*, or reacts ⚖️ | Reads the thread, posts the card at once, fills it in ~5 s | Post a greeting |
| The argument goes on | Edits the card after each burst of messages (4 s coalescing) | Reply in the thread |
| The model misattributed you | *My position* → a modal; your correction is marked ✎ and the model cannot overwrite it | Argue back |
| Someone states a fact | Marks it as a claim; *Verify* runs Exa + a judge, shows a badge and 2–3 sources | Verify on its own |
| A question was addressed to X and X went silent | One DM to X with *Open thread* / *Not mine* | A second DM, a public ping |
| Nobody writes for hours | `Stalled`: the card shows what blocks; the author gets a DM | Let it rot |
| "By Wednesday" | Extracts the deadline; when it comes, opens the vote (reversible, safe) | Decide |
| Everyone voted | DMs the author: confirm | Treat the tally as the decision |
| *Confirm* | Writes the record from the arguments, not the count: what, why, who disagreed, owner, follow-ups | Skip the dissent |
| *Record* | ADR page in Confluence (or Canvas / Jira / markdown), final card broadcast to the channel | Write anywhere without a tap |
| Someone contradicts a past decision | A small notice in that thread: *Link* / *Dispute* → a new thread that supersedes the old one | Stay quiet about it |

## Run it in five minutes

1. **Slack app** — create it from [docs/slack_manifest.yaml](docs/slack_manifest.yaml) (App → *From a manifest*), generate an
   app-level token with `connections:write`, install to the workspace. Details: [docs/SLACK_APP.md](docs/SLACK_APP.md).
2. **Keys** — `make setup` creates `.env` from [.env.example](.env.example); fill in the three required values.
3. **Check** — `make env` verifies every credential live and tells you which plugins will be on.
4. **Start** — `make up` (docker compose, Socket Mode: no public URL, no ngrok; state in a docker volume, ADRs in `./records`).
   On a laptop instead: `make install && make run`.
5. **Use** — `/invite @Quorum` into a channel, mention it in a thread (or react ⚖️, or use the *Track with Quorum* shortcut).

| Variable | Required | What it does |
|---|---|---|
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | yes | bot (`xoxb-`) and Socket Mode (`xapp-`) tokens |
| `OPENAI_API_KEY` | yes* | extractor / record writer; `LLM_MODEL_FAST`, `LLM_MODEL_SMART`, `OPENAI_BASE_URL` optional. *Empty → deterministic `FakeProvider` (`make fake`) |
| `EXA_KEY` | no | fact verification plugin; without it the *Verify* button is not rendered |
| `ATLASSIAN_EMAIL`, `ATLASSIAN_TOKEN`, `JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `CONFLUENCE_SPACE_KEY` | no | Confluence ADR page + Jira tasks for follow-ups; without them → markdown ADR in `records/` |
| `SLACK_CANVAS_ENABLED` | no | record decisions as Slack Canvases (scope `canvases:write`) |
| `QUORUM_LANG` | no | `en` / `ru` chrome of the card (content follows the thread language) |
| `TIMEZONE` | no | for "by Wednesday" and the date pickers (default `Asia/Tashkent`) |
| `COALESCE_SECONDS`, `SILENCE_MINUTES`, `SILENCE_MESSAGES`, `STALL_HOURS`, `EXPIRE_HOURS`, `AUTOSUGGEST_EVERY_N` | no | behaviour knobs, see `.env.example` |
| `DEMO_TIME_SCALE`, `DEMO_PERSONAS` | no | demo: speed up timers ×N; treat seeded personas (`Ann,Bob,Cid`) as humans |
| `WATCH_CHANNELS` | no | restrict passive features (memory recall, auto-suggest) to these channel ids |

Demo script with exact messages: [docs/DEMO.md](docs/DEMO.md). Team guide (RU): [DEVELOPMENT.md](DEVELOPMENT.md).
`make seed CH=… SCENARIO=db|release|vendor|contradict MENTION=…` posts a realistic argument by three personas;
`make track CH=… TS=… BY=…` starts tracking a thread from the CLI and `make act … ACTION=confirm OPT=A` drives card actions (both run inside the container; `make reset-db` wipes the state).

## Engineering notes

- **Core without Slack.** Domain model, state machine, merge, engine and renderer are plain Python with tests on a
  fake platform and a fake LLM (`make test`).
- **`ChatPlatform` protocol** — `post_card / update_card / ephemeral / dm / open_form / fetch_thread`; Discord has every
  primitive (threads, components, ephemeral interactions, DMs), so the abstraction is honest.
- **Bolt + Socket Mode**, ack within 3 s, hand-off to the engine in a task; **dedup by `event_id`** (Slack retries),
  `message_changed` / `message_deleted` re-extract; **per-thread queue + coalescing** (no `chat.update` races).
- **Failure handling you can see.** LLM down → the card stays and says *last update failed*; Exa down → *could not
  verify*; Confluence down → retry, then markdown, then the markdown in a DM.
- **Slack cannot move a message.** The card is edited in place; it is re-posted (old one deleted) only on a phase
  change — vote opened, decision confirmed, recorded — so the change is the notification.
- **Plugins, not core.** Exa, Confluence, Jira, Canvas each hide themselves when their key is empty.

## Layout

```
quorum/domain      models · state machine · events · UI primitives
quorum/core        engine (coalescing, buttons/forms, timers, memory) · merge
quorum/llm         protocols · OpenAI structured-output provider · offline fake
quorum/render      Block Kit renderer · ADR markdown
quorum/platform    ChatPlatform protocol · slack/ (Bolt) · fake (tests) · discord/ (next)
quorum/recorders   confluence · canvas · jira · markdown
quorum/verifier    exa
quorum/store       sqlite
docs/              SLACK_APP · CONTRACTS · ACCESS · DEMO
```

<div align="center"><sub>Built for the AI Tinkerers hackathon, Tashkent, September 2026.</sub></div>

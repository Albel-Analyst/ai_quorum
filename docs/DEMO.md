# Demo script — what to type, who taps what (≈4 minutes)

Roles: **Presenter** (Aleksei, `U0C18LRS4SJ`, thread author on screen), **Colleague** (second human account, e.g. xzoqpozx
`U0C0VGBV815`). The personas Ann / Bob / Cid are seeded by the bot (`chat:write.customize`) and count as humans thanks to
`DEMO_PERSONAS=Ann,Bob,Cid`. Channel: `#quorum-demo` (`C0C1B6KANQN`), bot `@quorum` is a member.

## Before recording

```bash
make up                                                   # bot in docker compose (Socket Mode, no public URL)
# .env: DEMO_TIME_SCALE=10  -> "30 min of silence" fires after 3 min, "6 h stall" after 36 min
make seed CH=C0C1B6KANQN SCENARIO=db MENTION=U0C18LRS4SJ  # a fresh 6-message argument by Ann/Bob/Cid
# optional prep without clicking (runs inside the container, shares its state):
make track CH=C0C1B6KANQN TS=<root ts> BY=U0C18LRS4SJ
make act CH=… TS=… BY=U0C18LRS4SJ ACTION=confirm OPT=A
make reset-db                                             # wipe tracked threads / journal between rehearsals
```
Never point host-side tools at the container's SQLite (Docker Desktop bind mounts corrupt it); `make seed` is safe — it only posts to Slack.
Open Slack on the Presenter's account, keep the DM list visible on the left (notifications arrive there).
Optional: pre-open Confluence space and Jira board tabs (recorder links go there when Atlassian is configured).

## Take 1 — from a thread to a living card (0:00–1:00)

| Step | Who | Type / tap | What the audience sees |
|---|---|---|---|
| 1 | Presenter | Open the seeded thread «Folks, we need to pick the database for the new billing service…» — scroll it, say: *"a normal argument: two options, one question nobody answered"* | 6 plain messages |
| 2 | Presenter | Reply in the thread: `@quorum` | The card appears instantly (question only), ~5 s later it fills: **Deliberating**, deadline *Wed*, options **A · Postgres / B · Mongo**, positions Ann/Bob/Cid, **Open questions: "how much is Atlas M10?" → @Presenter**, **Claims to verify** ×3 |
| 3 | Presenter | Say: *"Quorum never replies. This is its only message, and it edits it."* | |

## Take 2 — people correct the machine, facts get checked (1:00–1:45)

| Step | Who | Type / tap | What the audience sees |
|---|---|---|---|
| 4 | Colleague | Reply in the thread: `I'm with Bob here. Postgres, but only if we get RDS reserved pricing.` | ~5 s: a new line in **Positions**: `@Colleague → A: …` |
| 5 | Colleague | Tap **My position** → pick **B · Mongo**, argument `Cheaper ops for a small team` → Save | The position flips to `@Colleague → B ✎` (✎ = corrected by a human; the model will not overwrite it) |
| 6 | Presenter | Tap **Verify 2** (the "$60/month" claim) | Badge ⏳ *checking…* → ✅/❌/❔ with 2–3 source links under the claim, separated from what was "said in the thread" |

## Take 3 — the silent stakeholder and the deadline (1:45–2:30)

| Step | Who | Type / tap | What the audience sees |
|---|---|---|---|
| 7 | Presenter | Point at the DM from **quorum** (it arrived right after tracking, because the question addressed to the Presenter stayed unanswered): *"In #quorum-demo they are waiting for you: … [Open thread] [Not mine]"* | One DM, never a second one, no public ping |
| 8 | Presenter | Reply in the thread: `Atlas M10 in eu-central is $57/month, so under Cid's $70 line.` | The open question disappears; a new claim "$57/month" appears |
| 9 | Presenter | Tap **Set deadline** → today's date, time = two minutes from now → Save | Header shows ⏱ the new deadline. *"When it comes, the vote opens by itself — reversible, safe."* (If you do not want to wait, tap **Open vote** yourself in step 10.) |

## Take 4 — vote, confirm, record (2:30–3:30)

| Step | Who | Type / tap | What the audience sees |
|---|---|---|---|
| 10 | — / Presenter | Wait for the deadline (or tap **Open vote**) | The card is **re-posted at the end of the thread** with the banner *🗳 Vote opened*; the old card is deleted. Buttons **Vote A / Vote B**, tally, "voted 0/5" |
| 11 | Presenter | Tap **Vote A** | `A: 1` |
| 12 | Colleague | Tap **Vote B**, then tap **Vote A** (re-vote) | tally follows; *"one tap = one vote, you can change your mind"* |
| 13 | Presenter | Tap **Confirm decision** → modal: option **A** preselected, owner = Colleague, note empty → Save | Card re-posted: *✅ Decision confirmed*. **Decision**: what was decided, **why** (from the arguments, not the count), **Dissent**: Cid with his argument, **Owner**, **Follow-ups**. *"The vote is a snapshot of positions; the decision is a human's tap."* |
| 14 | Presenter | Tap **Record** | Card re-posted with *📚 Decision recorded* and **broadcast to the channel** (the thread is where you argue, the channel gets the outcome). DM to the Presenter with the links. With Atlassian configured: open the **Confluence** page (ADR: context, options, decision, consequences, dissent) and the **Jira** tasks created for the follow-ups; without it: the markdown file in `records/` |

## Take 5 — memory (3:30–4:00)

| Step | Who | Type / tap | What the audience sees |
|---|---|---|---|
| 15 | Colleague | New **top-level** message in `#quorum-demo`: `Starting the billing service scaffold today — going with Mongo Atlas, it is just faster for me.` (or `make seed … SCENARIO=contradict`) | A small notice appears **in that message's thread**: *"This seems to contradict a decision from 12.09.2026: Postgres or Mongo… [🔗] [Dispute]"* — the only time Quorum speaks first |
| 16 | Colleague | Tap **Dispute** | A new card in that thread; the old card gets *Superseded* when this one is confirmed |
| 17 | Presenter | Open the **quorum** App Home | The decisions journal: in progress / decided / superseded, with links |

## Spare threads (already seeded, already tracked)

- «Release plan for the new checkout: big-bang Friday or gradual rollout…» — deadline Thursday, question to the Presenter about the payment provider, claim about the March outage.
- «Error tracking for the mobile app: Sentry or GlitchTip…» — 5 claims (pricing), question about event volume.

Use them for a second take or to show **Park**: tap **Park** → reason `waiting for the Q4 budget`, return date tomorrow → the card shows *Parked* with the reason; the author gets a DM when the date comes.

## What to say if something breaks

- LLM down → the card stays as it was, footer says *⚠️ last update failed, showing the previous state*.
- Exa down → the claim shows *could not verify*.
- Confluence/Jira down → retry once, then a markdown ADR in `records/`, then the markdown in a DM.
- Two instances running (laptop + compose) split the Socket Mode events: keep exactly one.

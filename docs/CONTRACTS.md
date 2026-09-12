# Internal contracts (read before touching another module)

- `quorum/domain/models.py` — CardState & co. Status is owned by `domain/state_machine.py`; the LLM never sets it.
- `quorum/domain/events.py` — normalized events the engine consumes. Adapters produce them.
- `quorum/domain/ui.py` — Notice (text+buttons) and Form (modal spec). Renderers turn them into platform payloads.
- `quorum/platform/base.py` — ChatPlatform protocol (post_card/update_card/delete_message/ephemeral/dm/open_form/post_notice/fetch_thread/...).
- `quorum/llm/base.py` — Extractor / Classifier / ClaimJudge protocols and the strict output schemas.
- `quorum/recorders/base.py`, `quorum/verifier/base.py` — plugin protocols; `available()` hides the feature when unconfigured.
- `quorum/store/sqlite.py` — persistence; `seen(event_id)` is the dedup primitive.
- `quorum/i18n.py` — `t(lang, key, **kw)`; the chrome follows `QUORUM_LANG`, the LLM content follows the thread language.

## Button / form vocabulary (engine <-> renderer <-> adapter)

Button `action` names (Slack action_id = `q:<action>`; value = JSON payload, always with `"t": <thread key>` when the
button belongs to a thread):

| action | payload | who | effect |
|---|---|---|---|
| `my_position` | t | anyone | open form `my_position` |
| `vote` | t, option_id | anyone | cast/replace vote (only in Voting) |
| `open_voting` | t | anyone | Deliberating/Stalled -> Voting (phase change: card re-posted) |
| `back_to_discussion` | t | anyone | Voting -> Deliberating |
| `confirm` | t, option_id? | author/decider | open form `confirm` (option preselected = leading vote or hint) |
| `record` | t | author/decider | run recorders; Decided -> Recorded (phase change) |
| `deadline` | t | anyone | open form `deadline` |
| `park` | t | anyone | open form `park` |
| `unpark` | t | anyone | Parked/Expired -> Deliberating |
| `verify` | t, claim_id | anyone | run verifier on the claim |
| `not_me` | t, question_id | DM addressee | mark question declined, never nudge again |
| `track_yes` / `track_no` | t | ephemeral addressee | start tracking / dismiss |
| `dispute` | t (old decision thread key), channel_id, message_id | anyone (memory notice) | start a new tracked thread that supersedes the old decision |
| `open_thread` | url | — | link button, no event |

Form ids and field ids (`FormSubmitted.form_id`, `values`):

| form | fields |
|---|---|
| `my_position` | `option` (select, value = option id or `""` for none), `argument` (text) |
| `deadline` | `date` (date, `YYYY-MM-DD`), `time` (text `HH:MM`, optional) |
| `park` | `reason` (text), `return` (date, optional) |
| `confirm` | `option` (select), `owner` (user, optional), `note` (textarea, optional) |

Every form's `payload` carries `{"t": thread_key}` and is echoed back in `FormSubmitted.payload`.

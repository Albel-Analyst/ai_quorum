# Slack app setup

Quorum runs in **Socket Mode**: the process dials out to Slack, so it works from a laptop or a docker-compose
container with no public URL, ingress, TLS or ngrok.

## Option A — create a fresh app from the manifest (2 minutes, recommended)

1. https://api.slack.com/apps → **Create New App** → *From a manifest* → pick the workspace → paste `docs/slack_manifest.yaml`.
2. **Basic Information → App-Level Tokens → Generate** with scope `connections:write` → this is `SLACK_APP_TOKEN` (`xapp-…`).
3. **Install App → Install to Workspace** → copy **Bot User OAuth Token** → `SLACK_BOT_TOKEN` (`xoxb-…`).
4. In every channel you want to demo in: `/invite @Quorum`.
5. `make env` verifies both tokens.

## Option B — reuse an existing app

The hackathon workspace already has the app **hackhatonwal** (bot user `U0C0VDXBGUX`) from the WALrus project; the tokens in
`.env` belong to it. To use it for Quorum, open its config page and make sure the manifest below is a subset of what is
installed. Compared with what WALrus needed, Quorum additionally requires:

| Where | Add |
|---|---|
| OAuth & Permissions → Bot Token Scopes | `chat:write`, `reactions:read`, `reactions:write`, `users:read`, `im:write`, `channels:history`, `groups:history`, `channels:read`, `groups:read`, `commands`, (optional, for Slack Canvas records) `canvases:write`, (optional, for `seed_demo` and channel bootstrap) `channels:manage`, `channels:join`, `chat:write.customize` |
| Event Subscriptions → Bot events | `app_mention`, `message.channels`, `message.groups`, `reaction_added`, `app_home_opened` |
| Interactivity & Shortcuts | Interactivity **On**; **Create New Shortcut → On messages**: name `Track with Quorum`, callback id `track_with_quorum` |
| App Home | **Home Tab** enabled |

After changing scopes press **Reinstall to Workspace** and copy the (possibly new) bot token.

**One Slack app = one running Quorum.** Socket Mode load-balances events across connections, so a second instance
(another laptop, a pod in a cluster, an old WALrus deployment with the same tokens) silently steals half of the events.
Scale everything else to zero before a demo.

## Demo data

`uv run python -m quorum.tools.seed_demo --channel <C…> --scenario db --mention <your user id>` posts a realistic
six-message argument under three personas (needs `chat:write.customize`; set `DEMO_PERSONAS=Ann,Bob,Cid` so the adapter
counts them as humans). Then mention `@Quorum` in that thread. `--scenario contradict` posts a top-level message that
contradicts the recorded decision, to show memory recall.

## Manifest

See `docs/slack_manifest.yaml` (kept in sync with `quorum/platform/slack/adapter.py`).

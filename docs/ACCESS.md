# Access and connections

No secrets in this file. Real values live in the git-ignored `.env` (see `.env.example` for every variable);
`make env` checks all of them live.

## Slack

Workspace **Hackathon** (`hackathon-rro2244.slack.com`), team `T0C1AHML1L2`. The tokens currently in `.env` belong to the app
**hackhatonwal** (bot user `U0C0VDXBGUX`, bot id `B0C1EEVN7T6`) created for the WALrus project. Its installed scopes as of
2026-09-12: `app_mentions:read chat:write im:write reactions:write assistant:write channels:history groups:history im:history
im:read reactions:read users:read channels:read`; events: `app_mention`, `message.channels`. What is still missing for Quorum
(`reaction_added`, `app_home_opened`, `message.groups`, the message shortcut, `groups:read`) is listed in `docs/SLACK_APP.md`;
or create a dedicated app from `docs/slack_manifest.yaml`.

Channels: `#general` C0C0V882SKH, `#random` C0C1AHN3LBC, `#hackhaton` C0C1E989KDJ, `#walrus-ops` C0C1EJS7FN0 (bot is a member).
Humans: Aleksei Bel `U0C18LRS4SJ`, xzoqpozx `U0C0VGBV815`.

Health checks:

```bash
set -a; . ./.env; set +a
curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" https://slack.com/api/auth.test | jq .
curl -s -I -H "Authorization: Bearer $SLACK_BOT_TOKEN" https://slack.com/api/auth.test | grep -i x-oauth-scopes
curl -s -X POST -H "Authorization: Bearer $SLACK_APP_TOKEN" https://slack.com/api/apps.connections.open | jq .ok
```

Gotchas: after adding a scope → Reinstall to Workspace (token may change). Slack does not deliver `app_mention` for
messages posted by a bot: test with a human account. One app = one running instance (Socket Mode splits events).
The WALrus deployment in Nebius (`kubectl -n walrus scale deploy walrus --replicas=0`) must be down while these tokens are used.

## OpenAI

`OPENAI_API_KEY`; models `gpt-5.4-mini` (extractor, classifiers, claim judge) and `gpt-5.4` (decision record writer).
Verified 2026-09-12 via `/v1/models`. Optional `OPENAI_BASE_URL` for a proxy/OpenRouter.

## Exa (fact verification plugin)

`EXA_KEY` — verified 2026-09-12 (`POST https://api.exa.ai/search`). Without it the "Verify" button is not rendered.

## Atlassian (Recorder plugins)

Site `https://quorumai.atlassian.net`. As of 2026-09-12 12:00 the token in `.env` (`JIRA_EMAIL` begzad10.09@gmail.com) does
**not** authenticate (`/rest/api/3/myself` → 401) and Confluence is not provisioned on the site (`/wiki/...` returns the Jira
HTML page). Tokens are being prepared. When ready:

- `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (https://id.atlassian.com/manage-profile/security/api-tokens), `JIRA_PROJECT_KEY`.
- `CONFLUENCE_BASE_URL=https://quorumai.atlassian.net/wiki`, same email/token, `CONFLUENCE_SPACE_KEY`, optional `CONFLUENCE_PARENT_PAGE_ID`.
Until then the Recorder falls back to markdown ADR files in `records/` (mounted from the container).

## Previous project (WALrus) — for deploy reuse

Nebius k8s / registry / pull-secret notes live in `/Users/albel/hackhaton/docs/ACCESS.md` and `DEPLOY.md`. Quorum is
docker-compose first; a k8s deployment would be a copy of that recipe with a single Deployment and a PVC for `data/`.

## GitHub

Repo `git@github.com:Albel-Analyst/ai_quorum.git` (branch `main`).

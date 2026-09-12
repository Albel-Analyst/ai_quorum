# Access and connections

No secrets in this file. Real values live in the git-ignored `.env` (see `.env.example` for every variable);
`make env` checks all of them live.

## Slack

Workspace **Hackathon** (`hackathon-rro2244.slack.com`), team `T0C1AHML1L2`. App **quorum** (bot user `U0C1D21H27L`,
bot id `B0C1F1HNFG9`) created 2026-09-12 from `docs/slack_manifest.yaml` plus the demo scopes (`channels:manage`,
`channels:join`, `chat:write.customize`). Demo channel `#quorum-demo` = `C0C1B6KANQN` (bot is a member; Aleksei, xzoqpozx and
Begzad invited). Other channels: `#general` C0C0V882SKH, `#random` C0C1AHN3LBC, `#hackhaton` C0C1E989KDJ, `#walrus-ops` C0C1EJS7FN0.
Humans: Aleksei Bel `U0C18LRS4SJ`, xzoqpozx `U0C0VGBV815`, Begzad `U0C16MUR1GT`, Akmal `U0C1ESM9F9P`, Soliha `U0C1GJQN3RS`.
The older app `hackhatonwal` (`U0C0VDXBGUX`) belongs to the WALrus project — do not reuse its tokens.

Health checks:

```bash
set -a; . ./.env; set +a
curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" https://slack.com/api/auth.test | jq .
curl -s -I -H "Authorization: Bearer $SLACK_BOT_TOKEN" https://slack.com/api/auth.test | grep -i x-oauth-scopes
curl -s -X POST -H "Authorization: Bearer $SLACK_APP_TOKEN" https://slack.com/api/apps.connections.open | jq .ok
```

Gotchas: after adding a scope → Reinstall to Workspace (token may change). Slack does not deliver `app_mention` /
`reaction_added` for the bot's own messages and reactions: test entry points with a human account. One app = one running
instance (Socket Mode splits events) — `make up` (compose) and `make run` (laptop) must not run together.

## OpenAI

`OPENAI_API_KEY`; models `gpt-5.4-mini` (extractor, classifiers, claim judge) and `gpt-5.4` (decision record writer).
Verified 2026-09-12 via `/v1/models`. Optional `OPENAI_BASE_URL` for a proxy/OpenRouter.

## Exa (fact verification plugin)

`EXA_KEY` — verified 2026-09-12 (`POST https://api.exa.ai/search`). Without it the "Verify" button is not rendered.

## Atlassian (Recorder plugins)

Site `https://quorumai.atlassian.net` (cloud id `0ee52f86-fe3f-4660-adbb-6a18de79d4a1`), Jira + Confluence provisioned.
Status 2026-09-12 12:10: no token has authenticated yet — three tokens tried with `begzad10.09@gmail.com` and
`xzoqpozx@gmail.com` all give `401` on `/rest/api/3/myself` (direct host and `api.atlassian.com/ex/jira/<cloudId>`), and
`mypermissions?permissions=CREATE_ISSUES` says `havePermission=false`; the Jira site also has **no projects** yet.

To make the Recorder work:
1. Log in to `https://quorumai.atlassian.net` with the account that will own the token; make sure it has Jira and Confluence
   product access (Settings → User management).
2. https://id.atlassian.com/manage-profile/security/api-tokens → **Create API token** (the classic one, *not* "with scopes";
   a scoped token only works through `api.atlassian.com/ex/...` and needs `read:jira-user read:jira-work write:jira-work
   read:space:confluence read:page:confluence write:page:confluence`).
3. Create a Jira project (e.g. key `QUO`, team-managed, Kanban) and a Confluence space (e.g. key `QUO`).
4. `.env`: `ATLASSIAN_EMAIL`, `ATLASSIAN_TOKEN`, `JIRA_BASE_URL=https://quorum-ai.atlassian.net`, `JIRA_PROJECT_KEY=KAN`,
   `CONFLUENCE_SPACE_KEY=QUO`. Check: `curl -s -u "$ATLASSIAN_EMAIL:$ATLASSIAN_TOKEN" https://quorumai.atlassian.net/rest/api/3/myself`
   must return your profile JSON; then `make env`.
Until then the Recorder writes markdown ADRs into `records/` (mounted from the container).

## Previous project (WALrus) — for deploy reuse

Nebius k8s / registry / pull-secret notes live in `/Users/albel/hackhaton/docs/ACCESS.md` and `DEPLOY.md`. Quorum is
docker-compose first; a k8s deployment would be a copy of that recipe with a single Deployment and a PVC for `data/`.

## GitHub

Repo `git@github.com:Albel-Analyst/ai_quorum.git` (branch `main`).

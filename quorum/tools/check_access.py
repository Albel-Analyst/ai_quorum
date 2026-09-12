"""`make env`: verify every credential in .env without side effects."""
from __future__ import annotations

import asyncio
import base64

import aiohttp
from dotenv import load_dotenv

load_dotenv()
from quorum.config import settings


async def main() -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async def line(name: str, ok: bool, info: str = "") -> None:
            print(f"{'✅' if ok else '❌'} {name:<12} {info}")

        if settings.slack_bot_token:
            async with s.post("https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {settings.slack_bot_token}"}) as r:
                j = await r.json()
                await line("slack bot", j.get("ok", False), f"{j.get('team')} as {j.get('user')} scopes={r.headers.get('x-oauth-scopes','')}" if j.get("ok") else str(j))
        else:
            await line("slack bot", False, "SLACK_BOT_TOKEN empty")
        if settings.slack_app_token:
            async with s.post("https://slack.com/api/apps.connections.open", headers={"Authorization": f"Bearer {settings.slack_app_token}"}) as r:
                j = await r.json()
                await line("slack app", j.get("ok", False), "socket mode" if j.get("ok") else str(j))
        else:
            await line("slack app", False, "SLACK_APP_TOKEN empty")
        if settings.openai_api_key:
            async with s.get(f"{settings.openai_base_url or 'https://api.openai.com/v1'}/models", headers={"Authorization": f"Bearer {settings.openai_api_key}"}) as r:
                ids = {m["id"] for m in (await r.json()).get("data", [])} if r.status == 200 else set()
                await line("openai", r.status == 200, f"fast={settings.llm_model_fast}:{settings.llm_model_fast in ids} smart={settings.llm_model_smart}:{settings.llm_model_smart in ids}")
        else:
            await line("openai", False, "OPENAI_API_KEY empty -> FakeProvider")
        if settings.exa_key:
            async with s.post("https://api.exa.ai/search", headers={"x-api-key": settings.exa_key}, json={"query": "ping", "numResults": 1}) as r:
                await line("exa", r.status == 200, f"http {r.status}")
        else:
            await line("exa", False, "EXA_KEY empty -> Verify button hidden")
        if settings.confluence_base_url and settings.confluence_api_token:
            auth = base64.b64encode(f"{settings.confluence_email}:{settings.confluence_api_token}".encode()).decode()
            async with s.get(f"{settings.confluence_base_url}/rest/api/space/{settings.confluence_space_key}", headers={"Authorization": f"Basic {auth}", "Accept": "application/json"}) as r:
                await line("confluence", r.status == 200 and "json" in r.headers.get("content-type", ""), f"http {r.status} space={settings.confluence_space_key}")
        else:
            await line("confluence", False, "not configured -> markdown fallback")
        if settings.jira_base_url and settings.jira_api_token:
            auth = base64.b64encode(f"{settings.jira_email}:{settings.jira_api_token}".encode()).decode()
            async with s.get(f"{settings.jira_base_url}/rest/api/3/myself", headers={"Authorization": f"Basic {auth}", "Accept": "application/json"}) as r:
                who = (await r.json()).get("displayName") if r.status == 200 else await r.text()
                await line("jira", r.status == 200, f"http {r.status} as {str(who)[:60]} project={settings.jira_project_key or '(JIRA_PROJECT_KEY empty!)'}")
        else:
            await line("jira", False, "not configured -> no issues for follow-ups")


if __name__ == "__main__":
    asyncio.run(main())

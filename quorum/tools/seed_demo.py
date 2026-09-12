"""Seed a realistic decision thread into a Slack channel for a demo or a live test.

    uv run python -m quorum.tools.seed_demo --channel C0123 [--scenario db] [--mention U0C18LRS4SJ]

Messages are posted with `username`/`icon_emoji` (scope `chat:write.customize`) so that each persona looks like a
person. Set DEMO_PERSONAS=Ann,Bob,Cid in .env: the Slack adapter then treats those bot posts as humans, so the
extractor sees a real argument. The thread root is returned; mention @Quorum in it (or react ⚖️) to start tracking.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

load_dotenv()
from quorum.config import settings  # noqa: E402

PERSONAS = {
    "Ann": ":woman-technologist:",
    "Bob": ":man-technologist:",
    "Cid": ":male-scientist:",
}

# (persona, text). {mention} is replaced by <@USER> of the presenter so that a directed question targets a real human.
SCENARIOS: dict[str, list[tuple[str, str]]] = {
    "db": [
        ("Ann", "Folks, we need to pick the database for the new billing service. Postgres or Mongo? Let's decide by Wednesday."),
        ("Bob", "Postgres. We already run it, RDS db.t3.medium is about $60/month and the team knows it."),
        ("Cid", "Mongo Atlas gives us flexible schema for the invoice documents, and Atlas has a free tier for dev."),
        ("Ann", "{mention}, do you remember how much Atlas M10 costs in eu-central? That decides it for me."),
        ("Bob", "Also Postgres 17 shipped in September 2024 with faster JSON, so the schema argument is weaker now."),
        ("Cid", "Fair. If M10 is under $70 I still prefer Mongo, otherwise fine, Postgres."),
    ],
    "ru": [
        ("Ann", "Ребята, нужно выбрать базу для нового биллинг-сервиса. Postgres или Mongo? Давайте решим до среды."),
        ("Bob", "Postgres. Уже есть в проде, RDS db.t3.medium стоит около $60 в месяц, команда его знает."),
        ("Cid", "Mongo Atlas даёт гибкую схему под документы счетов, плюс у Atlas есть бесплатный tier для дева."),
        ("Ann", "{mention}, помнишь, сколько стоит Atlas M10 в eu-central? Для меня это решающее."),
        ("Bob", "И ещё: Postgres 17 вышел в сентябре 2024 с быстрым JSON, аргумент про схему уже слабее."),
        ("Cid", "Ок. Если M10 дешевле $70 — всё же Mongo, иначе ладно, Postgres."),
    ],
    "contradict": [
        ("Cid", "Starting the billing service scaffold today — going with Mongo Atlas, it is just faster for me."),
    ],
}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="channel id (the bot must be a member)")
    ap.add_argument("--scenario", default="db", choices=sorted(SCENARIOS))
    ap.add_argument("--mention", default="", help="user id to address the directed question to")
    ap.add_argument("--thread", default="", help="post into an existing thread (root ts) instead of a new one")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between messages")
    args = ap.parse_args()
    client = AsyncWebClient(token=settings.slack_bot_token)
    mention = f"<@{args.mention}>" if args.mention else "@team"
    root_ts = args.thread or None
    for persona, text in SCENARIOS[args.scenario]:
        kwargs = {"channel": args.channel, "text": text.replace("{mention}", mention)}
        if root_ts:
            kwargs["thread_ts"] = root_ts
        try:
            resp = await client.chat_postMessage(username=persona, icon_emoji=PERSONAS.get(persona, ":bust_in_silhouette:"), **kwargs)
        except SlackApiError as err:
            if err.response.get("error") == "missing_scope":
                print("scope chat:write.customize is missing: posting without personas", file=sys.stderr)
                resp = await client.chat_postMessage(**kwargs)
            else:
                raise
        root_ts = root_ts or resp["ts"]
        print(f"{persona:>4}: {resp['ts']}  {kwargs['text'][:70]}")
        await asyncio.sleep(args.delay)
    print(f"\nthread root ts = {root_ts}\nnow mention @Quorum in that thread (or react ⚖️ on the root) to start tracking")


if __name__ == "__main__":
    asyncio.run(main())

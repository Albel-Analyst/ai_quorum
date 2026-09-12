"""Start tracking a thread from the command line (same stack as the app, no Socket Mode):

    uv run python -m quorum.tools.track --channel C0123 --ts 1789202368.848439 [--by U0C18LRS4SJ]

Useful for a smoke test against real Slack (the card is posted with the real Block Kit) and to seed a demo.
Stop the running bot first if it shares data/quorum.db, or accept that it only sees the thread on the next event.
"""
from __future__ import annotations

import argparse
import asyncio

import structlog
from dotenv import load_dotenv

load_dotenv()
from quorum.app import _logging, build_llm
from quorum.config import settings
from quorum.core.engine import Engine
from quorum.domain.events import TrackRequested
from quorum.domain.models import ThreadRef
from quorum.platform.slack.adapter import SlackPlatform
from quorum.recorders.registry import build_recorders
from quorum.store.sqlite import Store
from quorum.verifier.exa import ExaVerifier


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--ts", required=True, help="root message ts")
    ap.add_argument("--by", default="", help="user id who asked (defaults to the bot)")
    args = ap.parse_args()
    _logging()
    log = structlog.get_logger("track")
    store = Store(settings.db_path)
    await store.open()
    llm = build_llm()
    platform = SlackPlatform(settings.slack_bot_token, settings.slack_app_token, settings=settings, lang=settings.lang)
    auth = await platform.client.auth_test()
    platform.bot_user_id = auth["user_id"]
    engine = Engine(platform=platform, store=store, llm=llm, recorders=build_recorders(settings), verifier=ExaVerifier(settings.exa_key, llm) if settings.exa_key else None, settings=settings)
    ref = ThreadRef(platform="slack", channel_id=args.channel, thread_id=args.ts)
    thread = await engine.track(TrackRequested(thread=ref, requested_by=args.by or platform.bot_user_id, via="mention"))
    await engine.flush()
    thread = await store.get_thread(ref.key)
    if thread:
        st = thread.state
        log.info("tracked", status=st.status.value, card=thread.card_message_id, options=[o.label for o in st.options], positions=len(st.positions), open_questions=len(st.open_questions), claims=len(st.claims), deadline=str(st.deadline), llm_error=st.last_llm_error)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Drive card actions from the CLI (same stack as the app, no Socket Mode) — smoke tests and demo prep:

    uv run python -m quorum.tools.act --channel C… --ts <root ts> --by U… open_voting
    uv run python -m quorum.tools.act --channel C… --ts <root ts> --by U… vote A
    uv run python -m quorum.tools.act --channel C… --ts <root ts> --by U… confirm A [--owner U…]
    uv run python -m quorum.tools.act --channel C… --ts <root ts> --by U… record
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
from quorum.domain.events import ButtonPressed, FormSubmitted
from quorum.domain.models import ThreadRef
from quorum.platform.slack.adapter import SlackPlatform
from quorum.recorders.registry import build_recorders
from quorum.store.sqlite import Store
from quorum.verifier.exa import ExaVerifier


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--ts", required=True)
    ap.add_argument("--by", required=True, help="acting user id")
    ap.add_argument("--owner", default="")
    ap.add_argument("action", choices=["open_voting", "vote", "confirm", "record", "unpark", "back_to_discussion"])
    ap.add_argument("option", nargs="?", default="")
    args = ap.parse_args()
    _logging()
    log = structlog.get_logger("act")
    store = Store(settings.db_path)
    await store.open()
    llm = build_llm()
    platform = SlackPlatform(settings.slack_bot_token, settings.slack_app_token, settings=settings, lang=settings.lang)
    platform.bot_user_id = (await platform.client.auth_test())["user_id"]
    engine = Engine(platform=platform, store=store, llm=llm, recorders=build_recorders(settings), verifier=ExaVerifier(settings.exa_key, llm) if settings.exa_key else None, settings=settings)
    ref = ThreadRef(platform="slack", channel_id=args.channel, thread_id=args.ts)
    if args.action == "confirm":
        await engine.handle(FormSubmitted(thread=ref, user_id=args.by, form_id="confirm", values={"option": args.option, "owner": args.owner, "note": ""}, payload={"t": ref.key}))
    elif args.action == "vote":
        await engine.handle(ButtonPressed(thread=ref, user_id=args.by, action="vote", payload={"option_id": args.option}))
    else:
        await engine.handle(ButtonPressed(thread=ref, user_id=args.by, action=args.action))
    await engine.flush()
    thread = await store.get_thread(ref.key)
    st = thread.state if thread else None
    log.info("done", status=st.status.value if st else None, decision=(st.decision.summary[:80] if st and st.decision else None), records=[(r.kind, r.url) for r in st.records] if st else None)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())

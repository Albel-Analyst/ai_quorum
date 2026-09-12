"""Entry point: one asyncio process — Slack Socket Mode adapter + engine + scheduler."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog
from dotenv import load_dotenv

load_dotenv()

from quorum.config import settings
from quorum.core.engine import Engine, Scheduler
from quorum.store.sqlite import Store


def _logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format="%(message)s")
    structlog.configure(
        processors=[structlog.processors.TimeStamper(fmt="%H:%M:%S"), structlog.processors.add_log_level, structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


def build_llm():
    if settings.openai_api_key:
        from quorum.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout_s,
            model_fast=settings.llm_model_fast,
            model_smart=settings.llm_model_smart,
            reasoning_effort=settings.llm_reasoning_effort,
        )
    from quorum.llm.fake import FakeProvider

    structlog.get_logger("main").warning("llm.fake", reason="OPENAI_API_KEY is empty; using the deterministic FakeProvider")
    return FakeProvider()


async def main() -> None:
    _logging()
    log = structlog.get_logger("main")
    if not (settings.slack_bot_token and settings.slack_app_token):
        log.error("slack.disabled", reason="SLACK_BOT_TOKEN / SLACK_APP_TOKEN are required")
        sys.exit(2)

    from quorum.platform.slack.adapter import SlackPlatform
    from quorum.recorders.ambiguous import AmbiguousTasks
    from quorum.recorders.registry import build_recorders
    from quorum.render.blockkit import render_home
    from quorum.verifier.exa import ExaVerifier

    store = Store(settings.db_path)
    await store.open()
    llm = build_llm()
    recorders = build_recorders(settings)
    verifier = ExaVerifier(settings.exa_key, llm) if settings.exa_key else None
    platform = SlackPlatform(settings.slack_bot_token, settings.slack_app_token, settings=settings, lang=settings.lang)
    engine = Engine(
        platform=platform,
        store=store,
        llm=llm,
        recorders=recorders,
        verifier=verifier,
        settings=settings,
        tasks=AmbiguousTasks(settings.ambiguous_api_key, settings.ambiguous_mcp_url, settings.ambiguous_assignees)
              if settings.ambiguous_api_key else None,
        home_view=lambda threads, decisions, lang: render_home(threads, decisions, lang=lang),
    )
    bridge_runner = None
    if settings.channels_enabled:
        from aiohttp import web

        from quorum.platform.channels_bridge import bridge_app

        auth = await platform.client.auth_test()
        platform.bot_user_id = auth["user_id"]
        bridge_runner = web.AppRunner(bridge_app(engine, settings.quorum_bridge_token))
        await bridge_runner.setup()
        await web.TCPSite(bridge_runner, "127.0.0.1", settings.quorum_bridge_port).start()
    else:
        platform.bind(engine, store.seen)
        await platform.start()
    scheduler = Scheduler(engine, settings.scheduler_tick_seconds)
    task = asyncio.create_task(scheduler.run(), name="scheduler")
    log.info(
        "quorum.started",
        recorders=[r.kind for r in recorders],
        verifier=verifier is not None and verifier.available(),
        lang=settings.lang,
        model=settings.llm_model_fast,
        db=settings.db_path,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("shutdown")
    scheduler.stop()
    task.cancel()
    if bridge_runner:
        await bridge_runner.cleanup()
    else:
        await platform.stop()
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())

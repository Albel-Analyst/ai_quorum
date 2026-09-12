"""Real Slack transport proof, explicitly not a human-approved sponsor lifecycle.

Posts three labeled diagnostic threads/cards in the channel supplied by the user.
Never touches existing threads or fabricates external completion.
"""
import argparse
import asyncio
import json
from pathlib import Path

from quorum.config import settings
from quorum.domain import state_machine as sm
from quorum.domain.models import Status, ThreadRef, TrackedThread
from quorum.platform.slack.adapter import SlackPlatform


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--output", default=".local/slack-smoke.json")
    args = parser.parse_args()
    platform = SlackPlatform(settings.slack_bot_token, settings.slack_app_token, settings=settings, lang="en")
    auth = await platform.client.auth_test()
    platform.bot_user_id = auth["user_id"]
    results = []
    for run in range(1, 4):
        title = f"Quorum transport check {run}/3 — test data, no human decision or external task"
        root = await platform.client.chat_postMessage(channel=args.channel, text=title)
        ref = ThreadRef(platform="slack", channel_id=args.channel, thread_id=root["ts"])
        messages = await platform.fetch_thread(ref)
        assert messages[0].id == root["ts"] and messages[0].text == title
        thread = TrackedThread(ref=ref, author_id=platform.bot_user_id, requested_by=platform.bot_user_id)
        thread.state.thread_id, thread.state.status, thread.state.question = ref.key, Status.OBSERVING, title
        thread.card_message_id = await platform.post_card(thread, thread.state)
        original_id = thread.card_message_id
        for _ in range(3):
            await platform.update_card(thread, thread.state)
        sm.transition(thread.state, Status.CANCELLED, reason="diagnostic finished; no execution claimed")
        await platform.update_card(thread, thread.state)
        final = await platform.fetch_thread(ref)
        assert len(final) == 2 and final[1].id == original_id and "CANCELLED" in final[1].text
        link = await platform.permalink(args.channel, original_id)
        results.append({"run": run, "thread_id": ref.thread_id, "card_id": original_id,
                        "card_count": 1, "status": "CANCELLED", "url": link})
        print(f"PASS {run}/3: one card, four updates, read-back confirmed: {link}")
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())

"""Renderer tests: Block Kit shape and Slack limits, the button vocabulary of docs/CONTRACTS.md, both languages.

No Slack, no LLM: `quorum.render.blockkit` is pure, so everything here is a plain function call.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quorum.domain.models import (
    CardState,
    Claim,
    Decision,
    DecisionHint,
    DecisionMemory,
    Dissent,
    FollowUp,
    OpenQuestion,
    Option,
    ParkInfo,
    Position,
    Record,
    Source,
    Status,
    ThreadRef,
    TrackedThread,
    Verification,
    Vote,
)
from quorum.domain.ui import Button, Form, FormField, Notice
from quorum.i18n import t
from quorum.render import blockkit as bk

NOW = datetime.now(UTC)
REF = ThreadRef(platform="slack", channel_id="C0DECIDE", thread_id="1700000000.000100")
KEY = REF.key
OTHER_KEY = "slack:C0OLDONE:1600000000.000100"

ANN, BOB, CID, DAN = "U0ANN", "U0BOB", "U0CID", "U0DAN"
NAMES = {ANN: "Ann", BOB: "Bob", CID: "Cid", DAN: "Dan", "plain-id": "Plain Name"}
LANGS = ["en", "ru"]


# --------------------------------------------------------------------------------------------------
# fixtures: a realistic card state
# --------------------------------------------------------------------------------------------------
def make_thread() -> TrackedThread:
    return TrackedThread(
        ref=REF,
        author_id=ANN,
        requested_by=BOB,
        card_message_id="1700000000.000200",
        root_text="Postgres or Mongo for the new service?",
        permalink="https://slack.test/archives/C0DECIDE/p1700000000000100",
        message_count=12,
    )


def make_state(status: Status, *, hint: bool = True, llm_error: str | None = None) -> CardState:
    """One state that carries every section the card can show; only `status` changes what is rendered."""
    return CardState(
        question="Postgres or Mongo for the new service?",
        context="New service, the team knows Postgres; Mongo was proposed for the flexible schema.",
        options=[
            Option(id="A", label="Postgres", summary="managed PG, one more database in the same cluster"),
            Option(id="B", label="Mongo Atlas", summary="document store, new operational surface"),
            Option(id="C", label="Postgres + JSONB", summary="documents inside the database we already run"),
        ],
        positions=[
            Position(user_id=ANN, option_id="A", argument="we already operate it", source="llm", at=NOW),
            Position(user_id=BOB, option_id="B", argument="the schema will keep moving", source="user", at=NOW),
            Position(user_id=CID, option_id=None, argument="depends on the read pattern", source="llm", at=NOW),
        ],
        open_questions=[
            OpenQuestion(id="q_atlas", text="How much is Atlas M10 per month?", directed_to=CID, asked_by=ANN),
            OpenQuestion(
                id="q_backup",
                text="Who owns backups after the switch?",
                directed_to=DAN,
                asked_by=ANN,
                nudged_at=NOW - timedelta(minutes=30),
            ),
            OpenQuestion(id="q_done", text="Do we need multi-region?", asked_by=BOB, answered=True),
        ],
        claims=[
            Claim(
                id="c_price",
                text="Atlas M10 costs $57/month",
                by=BOB,
                verification=Verification(
                    verdict="confirmed",
                    summary="MongoDB pricing page lists $0.08/hour for M10.",
                    sources=[
                        Source(title="MongoDB Atlas pricing", url="https://www.mongodb.com/pricing"),
                        Source(title="Atlas cluster tiers", url="https://www.mongodb.com/docs/atlas/cluster-tier/"),
                    ],
                    at=NOW,
                ),
            ),
            Claim(id="c_jsonb", text="JSONB has no partial index support", by=ANN, checking=True),
            Claim(id="c_scale", text="We will pass 2 TB within a year", by=CID),
        ],
        deadline=NOW + timedelta(days=2),
        deadline_source="button",
        status=status,
        decision=Decision(
            option_id="B",
            summary="We go with Mongo Atlas for the new service.",
            rationale="The schema is still moving and the team accepted the operational cost.",
            dissent=[Dissent(user_id=ANN, argument="one more database to operate")],
            owner=DAN,
            follow_ups=[
                FollowUp(text="Size the Atlas cluster", assignee=CID),
                FollowUp(text="Write the migration ADR", assignee=ANN, issue_url="https://jira.test/browse/Q-1"),
            ],
            decider=ANN,
            confirmed_by=ANN,
            confirmed_at=NOW,
            consequences="Two datastores in production.",
        ),
        decision_hint=DecisionHint(option_id="C", by=BOB, quote="ok, let's just use JSONB then") if hint else None,
        votes=[
            Vote(user_id=ANN, option_id="A", at=NOW),
            Vote(user_id=BOB, option_id="B", at=NOW),
            Vote(user_id=CID, option_id="B", at=NOW),
        ],
        parked=ParkInfo(reason="waiting for the Q3 budget", return_at=NOW + timedelta(days=14), by=BOB),
        records=[
            Record(kind="confluence", title="ADR-14 Datastore for the new service", url="https://conf.test/ADR-14", at=NOW),
            Record(kind="jira", title="Q-1", url="https://jira.test/browse/Q-1", at=NOW),
        ],
        participants=[ANN, BOB, CID, DAN],
        stakeholders=[ANN, BOB, CID, DAN],
        language="en",
        stalled_reason="Nobody answered the Atlas price question for two days.",
        expired_summary="Atlas price, backup ownership.",
        superseded_by=OTHER_KEY,
        last_llm_error=llm_error,
        updated_at=NOW - timedelta(minutes=3),
    )


def render(
    status: Status,
    *,
    lang: str = "en",
    verifier: bool = True,
    recorders: bool = True,
    hint: bool = True,
    llm_error: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    return bk.render_card(
        make_thread(),
        make_state(status, hint=hint, llm_error=llm_error),
        lang=lang,
        verifier_available=verifier,
        recorders_available=recorders,
        names=NAMES,
    )


# --------------------------------------------------------------------------------------------------
# the button vocabulary of docs/CONTRACTS.md
# --------------------------------------------------------------------------------------------------
#: buttons the *status* puts on the card (verify / hint-confirm are added on top, see `expected_actions`)
CHROME: dict[Status, list[str]] = {
    Status.FRAMING: ["q:my_position", "q:open_voting", "q:deadline", "q:park"],
    Status.DELIBERATING: ["q:my_position", "q:open_voting", "q:deadline", "q:park"],
    Status.VOTING: ["q:vote", "q:vote", "q:vote", "q:confirm", "q:back_to_discussion"],
    Status.DECIDED: ["q:record"],
    Status.RECORDED: [],
    Status.STALLED: ["q:open_voting", "q:deadline", "q:park", "q:my_position"],
    Status.PARKED: ["q:unpark"],
    Status.EXPIRED: ["q:unpark"],
    Status.SUPERSEDED: [],
}

#: the hint's "Confirm decision" button lives on these statuses only
HINT_STATUSES = (Status.FRAMING, Status.DELIBERATING, Status.STALLED)
#: claims (and therefore verify buttons) are hidden only on a superseded card
NO_CLAIMS = (Status.SUPERSEDED,)
#: claims of `make_state` that are neither verified nor being checked right now
PENDING_CLAIMS = 1


def expected_actions(status: Status, *, verifier: bool, recorders: bool, hint: bool) -> list[str]:
    expected = list(CHROME[status])
    if status is Status.DECIDED and not recorders:
        expected.remove("q:record")
    if verifier and status not in NO_CLAIMS:
        expected += ["q:verify"] * PENDING_CLAIMS
    if hint and status in HINT_STATUSES:
        expected.append("q:confirm")
    return sorted(expected)


def aid(b: dict[str, Any]) -> str:
    """`q:vote:A` -> `q:vote`: the suffix only keeps ids unique inside a block."""
    return ":".join(b["action_id"].split(":", 2)[:2])


def action_ids(blocks: list[dict[str, Any]]) -> list[str]:
    return sorted(aid(b) for b in bk.iter_buttons(blocks))


# --------------------------------------------------------------------------------------------------
# structural invariants (Slack rejects a message that breaks any of these)
# --------------------------------------------------------------------------------------------------
def assert_well_formed(blocks: list[dict[str, Any]], *, thread_key: str | None = KEY) -> None:
    assert len(blocks) <= bk.MAX_BLOCKS
    for block in blocks:
        kind = block.get("type")
        assert kind in {"section", "context", "actions", "divider", "header", "input"}, kind
        if kind == "section":
            assert len(block["text"]["text"]) <= bk.MAX_SECTION
            assert block["text"]["text"]
        if kind == "context":
            assert block["elements"], "an empty context block is rejected by Slack"
            for element in block["elements"]:
                assert len(element["text"]) <= bk.MAX_SECTION
        if kind == "actions":
            assert 1 <= len(block["elements"]) <= bk.MAX_ACTION_ELEMENTS
        if kind == "header":
            assert len(block["text"]["text"]) <= 150
    for element in bk.iter_buttons(blocks):
        assert element["action_id"].startswith("q:")
        label = element["text"]["text"]
        assert label and len(label) <= bk.MAX_BUTTON_LABEL
        assert len(element["value"]) <= 2000
        if element.get("url"):
            continue
        payload = json.loads(element["value"])
        assert isinstance(payload, dict)
        if thread_key is not None:
            assert payload["t"] == thread_key
    json.dumps(blocks, ensure_ascii=False)


@pytest.mark.parametrize("status", list(CHROME))
@pytest.mark.parametrize("lang", LANGS)
def test_card_is_well_formed_for_every_status(status: Status, lang: str) -> None:
    blocks, text = render(status, lang=lang)
    assert_well_formed(blocks)
    assert text and len(text) <= 300
    assert t(lang, f"status.{status.value}") in text


@pytest.mark.parametrize("status", list(CHROME))
def test_card_buttons_match_the_contract(status: Status) -> None:
    blocks, _ = render(status)
    assert action_ids(blocks) == expected_actions(status, verifier=True, recorders=True, hint=True)


@pytest.mark.parametrize("status", list(CHROME))
def test_card_buttons_without_plugins_and_without_hint(status: Status) -> None:
    blocks, _ = render(status, verifier=False, recorders=False, hint=False)
    assert action_ids(blocks) == expected_actions(status, verifier=False, recorders=False, hint=False)


def test_recorded_card_has_no_buttons_of_its_own() -> None:
    blocks, _ = render(Status.RECORDED, verifier=False, recorders=True)
    assert action_ids(blocks) == []
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert "https://conf.test/ADR-14" in dumped and "https://jira.test/browse/Q-1" in dumped


def test_decided_card_offers_record_only_with_recorders() -> None:
    with_recorders, _ = render(Status.DECIDED, verifier=False, recorders=True)
    without, _ = render(Status.DECIDED, verifier=False, recorders=False)
    assert action_ids(with_recorders) == ["q:record"]
    assert action_ids(without) == []


def test_verify_buttons_only_for_unverified_claims() -> None:
    blocks, _ = render(Status.DELIBERATING, verifier=True, hint=False)
    verify = [b for b in bk.iter_buttons(blocks) if aid(b) == "q:verify"]
    assert [json.loads(b["value"])["claim_id"] for b in verify] == ["c_scale"]

    off, _ = render(Status.DELIBERATING, verifier=False, hint=False)
    assert [b for b in bk.iter_buttons(off) if aid(b) == "q:verify"] == []


def test_claim_badges_and_sources() -> None:
    blocks, _ = render(Status.DELIBERATING)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert f"{bk.VERDICT_EMOJI['confirmed']} {t('en', 'verify.confirmed')}" in dumped
    assert f"{bk.CHECKING_EMOJI} {t('en', 'verify.checking')}" in dumped
    assert "https://www.mongodb.com/pricing" in dumped
    assert "https://www.mongodb.com/docs/atlas/cluster-tier/" in dumped


def test_vote_buttons_carry_every_option() -> None:
    blocks, _ = render(Status.VOTING)
    votes = [json.loads(b["value"]) for b in bk.iter_buttons(blocks) if aid(b) == "q:vote"]
    assert [v["option_id"] for v in votes] == ["A", "B", "C"]
    assert all(v["t"] == KEY for v in votes)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert t("en", "voted", n=3, total=4) in dumped
    assert t("en", "waiting_for") in dumped and f"<@{DAN}>" in dumped


def test_hint_confirm_carries_the_option() -> None:
    blocks, _ = render(Status.DELIBERATING)
    confirm = next(b for b in bk.iter_buttons(blocks) if aid(b) == "q:confirm")
    assert json.loads(confirm["value"]) == {"t": KEY, "option_id": "C"}
    assert "C · Postgres + JSONB" in json.dumps(blocks, ensure_ascii=False)


def test_hint_without_option_falls_back_to_the_other_string() -> None:
    state = make_state(Status.DELIBERATING)
    state.decision_hint = DecisionHint(option_id=None, by=BOB, quote="ok, ship it")
    blocks, _ = bk.render_card(
        make_thread(), state, lang="en", verifier_available=False, recorders_available=False, names=NAMES
    )
    confirm = next(b for b in bk.iter_buttons(blocks) if aid(b) == "q:confirm")
    assert json.loads(confirm["value"]) == {"t": KEY}
    assert "converged (" in json.dumps(blocks, ensure_ascii=False)


def test_decided_card_hides_the_discussion_and_shows_the_decision() -> None:
    blocks, _ = render(Status.DECIDED)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert t("en", "section.options") not in dumped
    assert t("en", "section.positions") not in dumped
    assert t("en", "section.open_questions") not in dumped
    for key in ("section.decision", "section.dissent", "section.follow_ups", "owner", "decider", "confirmed_by"):
        assert t("en", key) in dumped
    assert "https://jira.test/browse/Q-1" in dumped


def test_superseded_card_links_the_newer_thread() -> None:
    blocks, _ = render(Status.SUPERSEDED)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert bk.archive_url(OTHER_KEY) in dumped
    assert t("en", "section.claims") not in dumped
    assert action_ids(blocks) == []


def test_parked_and_expired_cards() -> None:
    parked, _ = render(Status.PARKED)
    dumped = json.dumps(parked, ensure_ascii=False)
    assert "waiting for the Q3 budget" in dumped
    assert "<!date^" in dumped

    expired, _ = render(Status.EXPIRED)
    assert "Atlas price, backup ownership." in json.dumps(expired, ensure_ascii=False)


def test_stalled_card_shows_what_blocks() -> None:
    blocks, _ = render(Status.STALLED)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert t("en", "section.stalled") in dumped
    assert "Nobody answered the Atlas price question" in dumped


def test_positions_mark_user_edits_and_silent_participants() -> None:
    blocks, _ = render(Status.DELIBERATING)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert bk.USER_EDIT_MARK in dumped                      # Bob corrected his position through the form
    assert t("en", "no_position") in dumped                 # Cid spoke without an option, Dan stayed silent
    assert f"<@{DAN}>" in dumped


def test_open_questions_show_the_addressee_and_the_nudge() -> None:
    blocks, _ = render(Status.DELIBERATING)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert "How much is Atlas M10 per month?" in dumped
    assert bk.NUDGED_EMOJI in dumped
    assert "Do we need multi-region?" not in dumped          # answered questions leave the card


def test_declined_question_leaves_the_card() -> None:
    state = make_state(Status.DELIBERATING)
    state.open_questions[0].declined = True
    blocks, _ = bk.render_card(
        make_thread(), state, lang="en", verifier_available=False, recorders_available=False, names=NAMES
    )
    assert "How much is Atlas M10 per month?" not in json.dumps(blocks, ensure_ascii=False)


@pytest.mark.parametrize("lang", LANGS)
def test_llm_failure_footer(lang: str) -> None:
    blocks, _ = render(Status.DELIBERATING, lang=lang, llm_error="timeout")
    footer = json.dumps(blocks[-1], ensure_ascii=False)
    assert "⚠️" in footer
    assert t(lang, "llm_failed") in footer
    assert t(lang, "updated") in footer

    ok, _ = render(Status.DELIBERATING, lang=lang)
    assert "⚠️" not in json.dumps(ok[-1], ensure_ascii=False)


# --------------------------------------------------------------------------------------------------
# languages
# --------------------------------------------------------------------------------------------------
CHROME_KEYS = [
    "section.options",
    "section.positions",
    "section.open_questions",
    "section.claims",
    "btn.my_position",
    "btn.open_voting",
    "btn.deadline",
    "btn.park",
    "deadline",
    "updated",
]


def test_russian_card_has_no_english_chrome() -> None:
    blocks, text = render(Status.DELIBERATING, lang="ru")
    dumped = json.dumps(blocks, ensure_ascii=False)
    for key in CHROME_KEYS:
        assert t("ru", key) in dumped, key
        assert t("en", key) not in dumped, key
    assert t("ru", "status.deliberating") in dumped and t("ru", "status.deliberating") in text
    assert t("en", "status.deliberating") not in dumped
    # the user content is untouched by the ui language
    assert "Postgres or Mongo for the new service?" in dumped


def test_russian_voting_and_decided_cards() -> None:
    voting, _ = json.dumps(render(Status.VOTING, lang="ru")[0], ensure_ascii=False), None
    assert t("ru", "btn.vote", label="A") in voting
    assert t("ru", "btn.back_to_discussion") in voting
    assert t("en", "btn.back_to_discussion") not in voting

    decided = json.dumps(render(Status.DECIDED, lang="ru")[0], ensure_ascii=False)
    assert t("ru", "section.decision") in decided
    assert t("ru", "btn.record") in decided
    assert t("en", "section.follow_ups") not in decided


# --------------------------------------------------------------------------------------------------
# limits under pressure
# --------------------------------------------------------------------------------------------------
def test_huge_state_stays_inside_the_slack_limits() -> None:
    state = make_state(Status.DELIBERATING)
    state.question = "Q? " * 2000
    state.context = "ctx " * 2000
    state.options = [Option(id=f"O{i}", label=f"Option {i}", summary="s" * 400) for i in range(20)]
    state.positions = [
        Position(user_id=f"U{i:04d}", option_id="O1", argument="a" * 300, at=NOW) for i in range(40)
    ]
    state.open_questions = [OpenQuestion(id=f"q{i}", text="why? " * 100) for i in range(30)]
    state.claims = [Claim(id=f"c{i}", text="claim " * 100, by=ANN) for i in range(40)]
    state.participants = [f"U{i:04d}" for i in range(40)]
    blocks, text = bk.render_card(
        make_thread(), state, lang="en", verifier_available=True, recorders_available=True, names=NAMES
    )
    assert_well_formed(blocks)
    assert len(blocks) == bk.MAX_BLOCKS
    assert len(text) <= 300


def test_many_vote_options_split_into_several_actions_blocks() -> None:
    state = make_state(Status.VOTING, hint=False)
    state.options = [Option(id=f"O{i}", label=f"Option {i}") for i in range(12)]
    blocks, _ = bk.render_card(
        make_thread(), state, lang="en", verifier_available=False, recorders_available=False, names=NAMES
    )
    assert_well_formed(blocks)
    assert len([b for b in bk.iter_buttons(blocks) if aid(b) == "q:vote"]) == 12


def test_empty_state_renders() -> None:
    blocks, text = bk.render_card(
        make_thread(),
        CardState(),
        lang="en",
        verifier_available=True,
        recorders_available=True,
        names={},
    )
    assert_well_formed(blocks)
    assert action_ids(blocks) == ["q:cancel", "q:check_now", "q:defer"]
    assert "Open loop" in text


# --------------------------------------------------------------------------------------------------
# forms (docs/CONTRACTS.md: my_position / deadline / park / confirm)
# --------------------------------------------------------------------------------------------------
def form_my_position(initial: str | None = "") -> Form:
    return Form(
        id="my_position",
        title=t("en", "form.my_position.title"),
        submit_label=t("en", "form.submit"),
        intro="Postgres or Mongo for the new service?",
        payload={"t": KEY},
        fields=[
            FormField(
                id="option",
                label=t("en", "form.my_position.option"),
                kind="select",
                options=[("", t("en", "form.my_position.none")), ("A", "A · Postgres"), ("B", "B · Mongo Atlas")],
                initial=initial,
            ),
            FormField(id="argument", label=t("en", "form.my_position.argument"), kind="text", optional=True),
        ],
    )


def form_deadline() -> Form:
    return Form(
        id="deadline",
        title=t("en", "form.deadline.title"),
        submit_label=t("en", "form.submit"),
        payload={"t": KEY},
        fields=[
            FormField(id="date", label=t("en", "form.deadline.date"), kind="date", initial="2026-09-30"),
            FormField(id="time", label="HH:MM", kind="text", optional=True, placeholder="18:00"),
        ],
    )


def form_park() -> Form:
    return Form(
        id="park",
        title=t("en", "form.park.title"),
        submit_label=t("en", "form.submit"),
        payload={"t": KEY},
        fields=[
            FormField(id="reason", label=t("en", "form.park.reason"), kind="text"),
            FormField(id="return", label=t("en", "form.park.return"), kind="date", optional=True),
        ],
    )


def form_confirm(preset: str | None = "B") -> Form:
    return Form(
        id="confirm",
        title=t("en", "form.confirm.title"),
        submit_label=t("en", "form.submit"),
        payload={"t": KEY},
        fields=[
            FormField(
                id="option",
                label=t("en", "form.confirm.option"),
                kind="select",
                options=[("A", "A · Postgres"), ("B", "B · Mongo Atlas")],
                initial=preset,
            ),
            FormField(id="owner", label=t("en", "form.confirm.owner"), kind="user", optional=True),
            FormField(id="note", label=t("en", "form.confirm.note"), kind="textarea", optional=True),
        ],
    )


def assert_modal(view: dict[str, Any], form: Form) -> None:
    assert view["type"] == "modal"
    assert view["callback_id"] == form.id
    assert json.loads(view["private_metadata"]) == form.payload
    assert len(view["title"]["text"]) <= 24
    assert len(view["submit"]["text"]) <= 24
    inputs = [b for b in view["blocks"] if b["type"] == "input"]
    assert [b["block_id"] for b in inputs] == [f.id for f in form.fields]
    assert [b["element"]["action_id"] for b in inputs] == [f.id for f in form.fields]
    assert [b["optional"] for b in inputs] == [f.optional for f in form.fields]
    json.dumps(view, ensure_ascii=False)


@pytest.mark.parametrize("lang", LANGS)
def test_render_form_my_position(lang: str) -> None:
    form = form_my_position(initial="")
    view = bk.render_form(form, lang=lang)
    assert_modal(view, form)
    option = view["blocks"][1]["element"] if form.intro else view["blocks"][0]["element"]
    assert option["type"] == "static_select"
    # Slack rejects an empty option value: "no option yet" travels as __none__ and comes back as ""
    assert option["options"][0]["value"] == bk.NONE_VALUE
    assert option["initial_option"]["value"] == bk.NONE_VALUE
    assert [o["value"] for o in option["options"]] == [bk.NONE_VALUE, "A", "B"]
    assert view["blocks"][0]["type"] == "section"          # the intro (the question)
    argument = view["blocks"][2]["element"]
    assert argument["type"] == "plain_text_input" and "multiline" not in argument


def test_render_form_my_position_preselects_the_current_option() -> None:
    view = bk.render_form(form_my_position(initial="B"), lang="en")
    option = view["blocks"][1]["element"]
    assert option["initial_option"]["value"] == "B"


def test_render_form_deadline() -> None:
    form = form_deadline()
    view = bk.render_form(form, lang="en")
    assert_modal(view, form)
    date, time = (b["element"] for b in view["blocks"] if b["type"] == "input")
    assert date["type"] == "datepicker" and date["initial_date"] == "2026-09-30"
    assert time["type"] == "plain_text_input"
    assert time["placeholder"]["text"] == "18:00"


def test_render_form_park() -> None:
    form = form_park()
    view = bk.render_form(form, lang="en")
    assert_modal(view, form)
    reason, ret = (b["element"] for b in view["blocks"] if b["type"] == "input")
    assert reason["type"] == "plain_text_input"
    assert ret["type"] == "datepicker"
    assert [b["optional"] for b in view["blocks"] if b["type"] == "input"] == [False, True]


def test_render_form_confirm() -> None:
    form = form_confirm()
    view = bk.render_form(form, lang="en")
    assert_modal(view, form)
    option, owner, note = (b["element"] for b in view["blocks"] if b["type"] == "input")
    assert option["initial_option"]["value"] == "B"
    assert owner["type"] == "users_select"
    assert note["type"] == "plain_text_input" and note["multiline"] is True


def test_render_form_confirm_without_preset() -> None:
    view = bk.render_form(form_confirm(preset=None), lang="en")
    option = next(b["element"] for b in view["blocks"] if b["type"] == "input")
    assert "initial_option" not in option


def test_render_form_select_without_options_is_still_openable() -> None:
    """A Confirm on a thread that has no options: Slack rejects a static_select with zero options."""
    form = Form(
        id="confirm",
        title=t("en", "form.confirm.title"),
        submit_label=t("en", "form.submit"),
        payload={"t": KEY},
        fields=[FormField(id="option", label=t("en", "form.confirm.option"), kind="select", options=[], initial=None)],
    )
    view = bk.render_form(form, lang="ru")
    assert_modal(view, form)
    option = view["blocks"][0]["element"]
    assert [o["value"] for o in option["options"]] == [bk.NONE_VALUE]
    assert option["options"][0]["text"]["text"] == t("ru", "form.my_position.none")


def test_render_form_caps_the_number_of_select_options() -> None:
    form = Form(
        id="my_position",
        title="t",
        submit_label="s",
        payload={"t": KEY},
        fields=[
            FormField(
                id="option",
                label="Option",
                kind="select",
                options=[(f"O{i}", f"Option {i}") for i in range(150)],
            )
        ],
    )
    view = bk.render_form(form, lang="en")
    assert len(view["blocks"][0]["element"]["options"]) == bk.MAX_SELECT_OPTIONS


def test_render_form_labels_follow_the_ui_language() -> None:
    view = bk.render_form(
        Form(
            id="park",
            title=t("ru", "form.park.title"),
            submit_label="",
            payload={"t": KEY},
            fields=[FormField(id="reason", label=t("ru", "form.park.reason"), kind="text")],
        ),
        lang="ru",
    )
    assert view["title"]["text"] == t("ru", "form.park.title")
    assert view["submit"]["text"] == t("ru", "form.submit")


# --------------------------------------------------------------------------------------------------
# notices and the home tab
# --------------------------------------------------------------------------------------------------
def test_render_notice_merges_the_thread_key_into_every_payload() -> None:
    notice = Notice(
        text=t("en", "notice.suggest", question="Postgres or Mongo?"),
        lines=["one more line"],
        buttons=[
            Button(label=t("en", "btn.track_yes"), action="track_yes", payload={"t": KEY}, style="primary"),
            Button(label=t("en", "btn.not_me"), action="not_me", payload={"question_id": "q_atlas"}),
            Button(label=t("en", "btn.open_thread"), action="open_thread", url="https://slack.test/x"),
        ],
    )
    blocks = bk.render_notice(notice, thread_key=KEY)
    assert_well_formed(blocks)
    values = {aid(b): json.loads(b["value"]) for b in bk.iter_buttons(blocks)}
    assert values["q:track_yes"] == {"t": KEY}
    assert values["q:not_me"] == {"t": KEY, "question_id": "q_atlas"}
    assert values["q:open_thread"] == {"t": KEY}
    link = next(b for b in bk.iter_buttons(blocks) if aid(b) == "q:open_thread")
    assert link["url"] == "https://slack.test/x"
    styled = next(b for b in bk.iter_buttons(blocks) if aid(b) == "q:track_yes")
    assert styled["style"] == "primary"


def test_render_notice_without_a_thread_key() -> None:
    notice = Notice(text="hello", buttons=[Button(label="ok", action="track_no", payload={"x": 1})])
    blocks = bk.render_notice(notice)
    assert_well_formed(blocks, thread_key=None)
    assert json.loads(next(bk.iter_buttons(blocks))["value"]) == {"x": 1}


def test_render_notice_does_not_overwrite_an_explicit_thread_key() -> None:
    notice = Notice(text="dispute", buttons=[Button(label="d", action="dispute", payload={"t": OTHER_KEY})])
    blocks = bk.render_notice(notice, thread_key=KEY)
    assert json.loads(next(bk.iter_buttons(blocks))["value"])["t"] == OTHER_KEY


def test_render_notice_plain_text_only() -> None:
    blocks = bk.render_notice(Notice(text=t("ru", "notice.position_saved")))
    assert [b["type"] for b in blocks] == ["section"]
    assert_well_formed(blocks, thread_key=None)


def test_render_notice_skips_an_empty_context_block() -> None:
    """Slack rejects a context block with no elements (the recorder fallback can hand over an empty markdown)."""
    blocks = bk.render_notice(Notice(text="x", lines=[""]))
    assert [b["type"] for b in blocks] == ["section"]
    assert_well_formed(blocks, thread_key=None)
    assert_well_formed(bk.render_notice(Notice(text="")), thread_key=None)


@pytest.mark.parametrize("lang", LANGS)
def test_render_home_empty(lang: str) -> None:
    view = bk.render_home([], [], lang=lang)
    assert view["type"] == "home"
    assert_well_formed(view["blocks"])
    dumped = json.dumps(view, ensure_ascii=False)
    assert t(lang, "home.title") in dumped and t(lang, "home.empty") in dumped


@pytest.mark.parametrize("lang", LANGS)
def test_render_home_populated(lang: str) -> None:
    threads = []
    for i, status in enumerate([Status.FRAMING, Status.VOTING, Status.STALLED]):
        thread = make_thread()
        thread.ref = ThreadRef(platform="slack", channel_id="C0DECIDE", thread_id=f"170000000{i}.000100")
        thread.state = make_state(status)
        thread.permalink = ""
        threads.append(thread)
    decisions = [
        DecisionMemory(
            thread_key=KEY,
            title="Postgres or Mongo?",
            summary="Mongo Atlas",
            option_label="B · Mongo Atlas",
            decided_at=NOW,
            channel_id="C0DECIDE",
            permalink="https://slack.test/p1",
            record_url="https://conf.test/ADR-14",
            owner=DAN,
        ),
        DecisionMemory(
            thread_key=OTHER_KEY,
            title="Old decision",
            summary="was replaced",
            decided_at=NOW - timedelta(days=30),
            channel_id="C0OLDONE",
            status="superseded",
            superseded_by=KEY,
        ),
    ]
    view = bk.render_home(threads, decisions, lang=lang)
    assert view["type"] == "home"
    assert_well_formed(view["blocks"])
    dumped = json.dumps(view, ensure_ascii=False)
    assert t(lang, "home.active") in dumped and t(lang, "home.decided") in dumped
    assert t(lang, "home.superseded") in dumped
    assert "https://conf.test/ADR-14" in dumped
    assert bk.archive_url(OTHER_KEY) in dumped               # no permalink, no record -> archive link
    for status in (Status.FRAMING, Status.VOTING, Status.STALLED):
        assert t(lang, f"status.{status.value}") in dumped


def test_render_home_stays_inside_the_block_limit() -> None:
    threads = []
    for i in range(60):
        thread = make_thread()
        thread.ref = ThreadRef(platform="slack", channel_id="C0DECIDE", thread_id=f"17000000{i:02d}.000100")
        thread.state = make_state(Status.DELIBERATING)
        threads.append(thread)
    decisions = [
        DecisionMemory(
            thread_key=f"slack:C0DECIDE:16000000{i:02d}.000100",
            title=f"Decision {i}",
            summary="s",
            decided_at=NOW,
            channel_id="C0DECIDE",
        )
        for i in range(60)
    ]
    view = bk.render_home(threads, decisions, lang="en")
    assert_well_formed(view["blocks"])


# --------------------------------------------------------------------------------------------------
# everything the adapter hands to Slack must survive json.dumps
# --------------------------------------------------------------------------------------------------
def test_every_rendered_view_is_json_serializable() -> None:
    payloads: list[Any] = []
    for status in Status:
        for lang in LANGS:
            blocks, text = render(status, lang=lang)
            payloads.append({"blocks": blocks, "text": text})
            banner = bk.phase_banner(status, lang)
            if banner:
                payloads.append([bk.context(banner), *blocks])
    for form in (form_my_position(), form_deadline(), form_park(), form_confirm()):
        payloads.append(bk.render_form(form, lang="en"))
    payloads.append(bk.render_home([], [], lang="en"))
    payloads.append(bk.render_notice(Notice(text="x", buttons=[Button(label="y", action="unpark")]), thread_key=KEY))
    for payload in payloads:
        assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


@pytest.mark.parametrize("status", list(CHROME))
def test_phase_banner_only_for_phase_changes(status: Status) -> None:
    banner = bk.phase_banner(status, "ru")
    if status in (Status.VOTING, Status.DECIDED, Status.RECORDED, Status.EXPIRED):
        assert banner == t("ru", f"phase.{status.value}")
    else:
        assert banner == ""


def test_who_falls_back_to_the_display_name() -> None:
    assert bk.who(ANN, NAMES) == f"<@{ANN}>"
    assert bk.who("plain-id", NAMES) == "Plain Name"
    assert bk.who("unknown-id", NAMES) == "unknown-id"
    assert bk.who(None, NAMES) == "—"


def test_archive_url() -> None:
    assert bk.archive_url(KEY) == "https://slack.com/archives/C0DECIDE/p1700000000000100"
    assert bk.archive_url("not-a-key") == ""


# --------------------------------------------------------------------------------------------------
# the same payloads through Slack's own block models (schema drift guard)
# --------------------------------------------------------------------------------------------------
def test_slack_sdk_accepts_every_payload_we_build() -> None:
    from slack_sdk.models.blocks import Block
    from slack_sdk.models.views import View

    for status in Status:
        for lang in LANGS:
            for block in render(status, lang=lang)[0]:
                Block.parse(block).validate_json()
    for form in (form_my_position(), form_deadline(), form_park(), form_confirm()):
        View(**bk.render_form(form, lang="ru")).validate_json()
    View(**bk.render_home([], [], lang="en")).validate_json()
    notice = Notice(text="x", lines=["y"], buttons=[Button(label="ok", action="unpark", payload={"t": KEY})])
    for block in bk.render_notice(notice, thread_key=KEY):
        Block.parse(block).validate_json()

from datetime import UTC, datetime, timedelta

from quorum.core.merge import merge
from quorum.domain.models import CardState, Message, Position, Status, Verification, Vote
from quorum.llm.base import ExtractedClaim, ExtractedOpenQuestion, ExtractedOption, ExtractedPosition, Extraction

T0 = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def _msgs():
    return [
        Message(id="1.0", user_id="U1", text="Postgres or Mongo?", at=T0),
        Message(id="2.0", user_id="U2", text="Postgres, we know it", at=T0 + timedelta(minutes=1)),
        Message(id="3.0", user_id="U1", text="<@U3> how much is Mongo Atlas?", at=T0 + timedelta(minutes=2), mentions=["U3"]),
    ]


def _ext(**kw):
    base = dict(
        question="Postgres or Mongo?",
        language="en",
        options=[ExtractedOption(id="A", label="Postgres"), ExtractedOption(id="B", label="Mongo")],
        positions=[ExtractedPosition(user_id="U2", option_id="A", argument="we know it")],
        open_questions=[ExtractedOpenQuestion(text="how much is Mongo Atlas?", directed_to="U3", asked_at_message_id="3.0")],
        claims=[ExtractedClaim(text="Atlas M10 costs $57/month", by="U2")],
    )
    base.update(kw)
    return Extraction(**base)


def test_merge_basic_and_participants():
    st = CardState()
    merge(st, _ext(), _msgs(), now=T0)
    assert [o.id for o in st.options] == ["A", "B"]
    assert st.positions[0].user_id == "U2" and st.positions[0].source == "llm"
    q = st.open_questions[0]
    assert q.directed_to == "U3" and q.asked_at == T0 + timedelta(minutes=2)
    assert st.participants == ["U1", "U2"] and "U3" in st.stakeholders
    assert st.claims[0].text.startswith("Atlas")


def test_user_position_survives_and_questions_keep_identity():
    st = CardState()
    merge(st, _ext(), _msgs(), now=T0)
    qid = st.open_questions[0].id
    st.positions = [Position(user_id="U2", option_id="B", argument="changed my mind", source="user")]
    st.open_questions[0].nudged_at = T0
    st.claims[0].verification = Verification(verdict="confirmed")
    merge(st, _ext(positions=[ExtractedPosition(user_id="U2", option_id="A"), ExtractedPosition(user_id="U1", option_id="B")], claims=[]), _msgs(), now=T0)
    p2 = st.position_of("U2")
    assert p2.option_id == "B" and p2.source == "user"
    assert st.position_of("U1").option_id == "B"
    assert st.open_questions[0].id == qid and st.open_questions[0].nudged_at == T0
    assert st.claims and st.claims[0].verification.verdict == "confirmed"  # verified claim stays


def test_questions_get_answered_when_dropped_or_listed():
    st = CardState()
    merge(st, _ext(), _msgs(), now=T0)
    merge(st, _ext(open_questions=[], answered_question_texts=["how much is Mongo Atlas"]), _msgs(), now=T0)
    assert all(q.answered for q in st.open_questions)


def test_voted_option_not_dropped_and_button_deadline_wins():
    st = CardState(status=Status.VOTING)
    merge(st, _ext(), _msgs(), now=T0)
    st.votes = [Vote(user_id="U1", option_id="B")]
    st.deadline, st.deadline_source = T0, "button"
    merge(st, _ext(options=[ExtractedOption(id="A", label="Postgres")], deadline=T0 + timedelta(days=1)), _msgs(), now=T0)
    assert {o.id for o in st.options} == {"A", "B"}
    assert st.deadline == T0


def test_decision_hint_only_while_open():
    st = CardState()
    merge(st, _ext(decision_reached=True, decision_option_id="a", decision_by="U1", decision_quote="ok, Postgres"), _msgs(), now=T0)
    assert st.decision_hint and st.decision_hint.option_id == "A"
    st.status = Status.VOTING
    merge(st, _ext(decision_reached=True, decision_option_id="A"), _msgs(), now=T0)
    assert st.decision_hint is None


def test_text_deadline_sets_source():
    st = CardState()
    merge(st, _ext(deadline=T0 + timedelta(days=2)), _msgs(), now=T0)
    assert st.deadline_source == "text"

import pytest

from quorum.domain import state_machine as sm
from quorum.domain.models import CardState, Option, Position, Status


def _state(status=Status.DELIBERATING, options=2):
    st = CardState(question="Which DB?", status=status)
    st.options = [Option(id=chr(65 + i), label=f"opt {i}") for i in range(options)]
    st.participants = ["U1", "U2"]
    return st


def test_framing_settles_to_deliberating_when_substantive():
    st = CardState(status=Status.FRAMING)
    assert sm.settle_after_extraction(st) is False
    st.options = [Option(id="A", label="a"), Option(id="B", label="b")]
    assert sm.settle_after_extraction(st) is True
    assert st.status == Status.DELIBERATING


def test_stalled_reopens_on_talk():
    st = _state(Status.STALLED)
    assert sm.settle_after_extraction(st)
    assert st.status == Status.DELIBERATING


def test_vote_flow_and_confirm():
    st = _state()
    assert sm.open_voting(st)
    assert st.status == Status.VOTING
    with pytest.raises(sm.IllegalTransition):
        sm.cast_vote(st, "U1", "Z")
    sm.cast_vote(st, "U1", "A")
    sm.cast_vote(st, "U1", "B")  # re-vote replaces
    assert st.tally() == {"A": 0, "B": 1}
    assert not sm.everyone_voted(st)
    sm.cast_vote(st, "U2", "B")
    assert sm.everyone_voted(st)
    assert sm.leading_option(st) == "B"
    assert sm.confirm_decision(st, by="U1", option_id="B")
    assert st.status == Status.DECIDED and st.decision.confirmed_by == "U1" and st.decision.decider == "U1"
    assert sm.mark_recorded(st)
    with pytest.raises(sm.IllegalTransition):
        sm.open_voting(st)
    assert sm.supersede(st, "slack:C:2")
    assert st.status == Status.SUPERSEDED


def test_vote_requires_voting_status_and_options():
    st = _state(options=0)
    with pytest.raises(sm.IllegalTransition):
        sm.open_voting(st)
    st = _state()
    with pytest.raises(sm.IllegalTransition):
        sm.cast_vote(st, "U1", "A")


def test_tie_has_no_leader():
    st = _state(Status.VOTING)
    sm.cast_vote(st, "U1", "A")
    sm.cast_vote(st, "U2", "B")
    assert sm.leading_option(st) is None


def test_stall_expire_and_park():
    st = _state()
    assert sm.stall(st, "no cost estimate")
    assert st.status == Status.STALLED and st.stalled_reason == "no cost estimate"
    assert sm.stall(st, "again") is False
    assert sm.park(st, by="U1", reason="wait for Q4 budget", return_at=None)
    assert st.status == Status.PARKED and st.parked.reason == "wait for Q4 budget"
    assert sm.unpark(st) and st.status == Status.DELIBERATING and st.parked is None
    sm.stall(st, "x")
    assert sm.expire(st, "open: cost")
    assert st.status == Status.EXPIRED
    assert sm.settle_after_extraction(st) and st.status == Status.DELIBERATING


def test_phase_change_set():
    assert Status.VOTING in sm.PHASE_CHANGE and Status.DELIBERATING not in sm.PHASE_CHANGE


def test_state_helpers():
    st = _state()
    st.positions = [Position(user_id="U1", option_id="A")]
    assert st.position_of("U1").option_id == "A" and st.position_of("U9") is None
    assert st.voters_missing() == ["U1", "U2"]

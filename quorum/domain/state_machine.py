"""Guarded OpenLoop lifecycle; legacy decision-card transitions remain readable.

OBSERVING -> NEEDS_EVIDENCE / NEEDS_INPUT -> DECIDED -> EXECUTING
-> VERIFYING -> CLOSED. Closure requires current authoritative evidence.
Cancellation, deferral and supersession stop scheduled monitoring.
"""
from __future__ import annotations

from datetime import datetime

from quorum.domain.models import ACTIVE, TERMINAL, AuditEvent, CardState, Decision, NextTrigger, ParkInfo, Status, now


class IllegalTransition(Exception):
    pass


_ALLOWED: dict[Status, set[Status]] = {
    Status.OBSERVING: {Status.NEEDS_INPUT, Status.NEEDS_EVIDENCE, Status.DECIDED},
    Status.NEEDS_INPUT: {Status.OBSERVING, Status.NEEDS_EVIDENCE, Status.DECIDED},
    Status.NEEDS_EVIDENCE: {Status.OBSERVING, Status.NEEDS_INPUT, Status.DECIDED},
    Status.EXECUTING: {Status.BLOCKED, Status.VERIFYING},
    Status.BLOCKED: {Status.EXECUTING, Status.VERIFYING},
    Status.VERIFYING: {Status.EXECUTING, Status.BLOCKED, Status.CLOSED},
    Status.CLOSED: {Status.SUPERSEDED},
    Status.DEFERRED: set(),
    Status.CANCELLED: set(),
    Status.FRAMING: {Status.DELIBERATING, Status.VOTING, Status.STALLED, Status.PARKED, Status.DECIDED},
    Status.DELIBERATING: {Status.VOTING, Status.STALLED, Status.PARKED, Status.DECIDED},
    Status.VOTING: {Status.DECIDED, Status.DELIBERATING, Status.PARKED, Status.STALLED},
    Status.STALLED: {Status.DELIBERATING, Status.VOTING, Status.PARKED, Status.EXPIRED, Status.DECIDED},
    Status.PARKED: {Status.DELIBERATING, Status.VOTING, Status.EXPIRED},
    Status.DECIDED: {Status.RECORDED, Status.SUPERSEDED, Status.DELIBERATING},
    Status.RECORDED: {Status.SUPERSEDED},
    Status.EXPIRED: {Status.DELIBERATING},
    Status.SUPERSEDED: set(),
}
_ALLOWED[Status.DECIDED].update({Status.EXECUTING, Status.VERIFYING})
for _status, _targets in _ALLOWED.items():
    if _status not in TERMINAL:
        _targets.update({Status.CANCELLED, Status.DEFERRED, Status.SUPERSEDED})

# Legacy renderer/API compatibility only. The engine never re-posts on phase changes.
PHASE_CHANGE = {Status.VOTING, Status.DECIDED, Status.RECORDED, Status.EXPIRED}


def can(state: CardState, to: Status) -> bool:
    return to in _ALLOWED[state.status]


def transition(state: CardState, to: Status, *, reason: str = "") -> bool:
    """Move to `to`. Returns True when the status actually changed. Raises on an illegal move."""
    if state.status == to:
        return False
    if not can(state, to):
        raise IllegalTransition(f"{state.status} -> {to}")
    if to == Status.DECIDED and not (state.decision and state.decision.confirmed_by):
        raise IllegalTransition("decision requires human confirmation")
    if to == Status.CLOSED and not closure_satisfied(state):
        raise IllegalTransition("closure requires current evidence for every approved commitment")
    before = state.status
    state.status = to
    state.updated_at = now()
    state.audit.append(AuditEvent(event_type="transition", state_before=before.value,
                                 state_after=to.value, source_ref=reason or "reducer"))
    if to in TERMINAL:
        state.next_trigger = None
    if to == Status.CLOSED:
        state.closed_at = now()
    if to in {Status.CANCELLED, Status.SUPERSEDED}:
        if to == Status.SUPERSEDED and state.decision:
            state.decision_history.append(state.decision.model_copy(deep=True))
        for c in state.commitments:
            if c.status not in {"completed", "cancelled", "superseded"}:
                c.status = "cancelled" if to == Status.CANCELLED else "superseded"
    if to == Status.STALLED:
        state.stalled_reason = reason
    if to != Status.PARKED:
        state.parked = None
    return True


# ---- automatic transitions driven by content ---------------------------------------------------

def settle_after_extraction(state: CardState) -> bool:
    """Framing -> Deliberating once the thread has substance; Stalled/Expired -> Deliberating when people talk again."""
    changed = False
    if state.status in {Status.OBSERVING, Status.NEEDS_INPUT, Status.NEEDS_EVIDENCE}:
        target = Status.OBSERVING
        if any(c.materiality == "material" and c.disputed and c.status == "unresolved" for c in state.claims):
            target = Status.NEEDS_EVIDENCE
        elif state.unanswered():
            target = Status.NEEDS_INPUT
        return transition(state, target)
    substantive = len(state.options) >= 2 or bool(state.positions)
    if state.status == Status.FRAMING and substantive or state.status in (Status.STALLED, Status.EXPIRED):
        changed |= transition(state, Status.DELIBERATING)
    return changed


# ---- human actions ---------------------------------------------------------------------------------

def open_voting(state: CardState) -> bool:
    if not state.options:
        raise IllegalTransition("cannot vote without options")
    return transition(state, Status.VOTING)


def reopen_discussion(state: CardState) -> bool:
    return transition(state, Status.DELIBERATING)


def cast_vote(state: CardState, user_id: str, option_id: str) -> None:
    from quorum.domain.models import Vote

    if state.status != Status.VOTING:
        raise IllegalTransition("vote is not open")
    if state.option(option_id) is None:
        raise IllegalTransition(f"unknown option {option_id}")
    state.votes = [v for v in state.votes if v.user_id != user_id] + [Vote(user_id=user_id, option_id=option_id)]
    if user_id not in state.participants:
        state.participants.append(user_id)
    state.updated_at = now()


def everyone_voted(state: CardState) -> bool:
    return bool(state.participants) and not state.voters_missing()


def leading_option(state: CardState) -> str | None:
    tally = state.tally()
    if not tally or not state.votes:
        return None
    best = max(tally.values())
    winners = [k for k, v in tally.items() if v == best]
    return winners[0] if len(winners) == 1 else None


def confirm_decision(state: CardState, *, by: str, option_id: str | None, decision: Decision | None = None) -> bool:
    """A human confirms. The vote is a snapshot of positions; the decision is the human's tap."""
    if not can(state, Status.DECIDED):
        raise IllegalTransition("decision is already confirmed or loop is terminal")
    if option_id and not state.option(option_id):
        raise IllegalTransition("unknown decision option")
    d = (decision or state.decision or Decision()).model_copy(deep=True)
    d.option_id = option_id
    d.confirmed_by = by
    d.confirmed_at = now()
    if not d.decider:
        d.decider = by
    state.decision = d
    state.decision_hint = None
    return transition(state, Status.DECIDED)


def closure_satisfied(state: CardState) -> bool:
    if not state.decision or not state.decision.confirmed_by or not state.last_reconciled_at:
        return False
    active = [c for c in state.commitments if c.status not in {"cancelled", "superseded"}]
    if not active or any(b.status == "open" for b in state.blockers):
        return False
    for c in active:
        condition = next((x for x in state.closure_conditions if x.id == c.closure_condition_id), None)
        if c.confirmation != "approved" or c.status != "completed" or not condition or not condition.evidence:
            return False
        e = condition.evidence[-1]
        if e.value != condition.expected_state or e.source != condition.source:
            return False
        if condition.mode == "observable":
            if not c.external_record_id or e.record_id != c.external_record_id:
                return False
            if e.observed_at < state.last_reconciled_at:
                return False
        elif condition.mode == "attestable" and e.actor != c.owner or condition.mode == "adjudicated" and e.actor != state.decision.decider:
            return False
    return True


def schedule_check(state: CardState, at: datetime) -> None:
    if state.status not in TERMINAL:
        state.next_trigger = NextTrigger(kind="scheduled", at=at, description="Re-read Slack and external evidence")


def mark_recorded(state: CardState) -> bool:
    return transition(state, Status.RECORDED)


def park(state: CardState, *, by: str, reason: str, return_at: datetime | None) -> bool:
    state.parked = ParkInfo(reason=reason, return_at=return_at, by=by)
    changed = state.status != Status.PARKED
    if changed:
        if not can(state, Status.PARKED):
            raise IllegalTransition(f"{state.status} -> parked")
        state.status = Status.PARKED
        state.updated_at = now()
    return changed


def unpark(state: CardState) -> bool:
    return transition(state, Status.DELIBERATING)


# ---- timers ----------------------------------------------------------------------------------------

def stall(state: CardState, reason: str) -> bool:
    if state.status not in (Status.DELIBERATING, Status.FRAMING):
        return False
    return transition(state, Status.STALLED, reason=reason)


def expire(state: CardState, summary: str) -> bool:
    if state.status not in (Status.STALLED, Status.PARKED):
        return False
    state.expired_summary = summary
    return transition(state, Status.EXPIRED)


def supersede(state: CardState, by_thread_key: str) -> bool:
    state.superseded_by = by_thread_key
    return transition(state, Status.SUPERSEDED)


def is_active(state: CardState) -> bool:
    return state.status in ACTIVE

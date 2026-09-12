"""Card state machine. Transitions are explicit, guarded, and testable without any platform.

    Framing -> Deliberating -> Voting -> Decided -> Recorded
                   |  ^          ^
                   v  |          |
               Stalled ----------+ (vote)        Parked (from any active), Expired (from Stalled),
                   |                             Superseded (from Decided/Recorded, by a newer decision)
                   v
                Expired
"""
from __future__ import annotations

from datetime import datetime

from quorum.domain.models import ACTIVE, CardState, Decision, ParkInfo, Status, now


class IllegalTransition(Exception):
    pass


_ALLOWED: dict[Status, set[Status]] = {
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

# A change of these statuses is a "phase change": the card is re-posted at the end of the thread (old one deleted)
# so the change itself is the notification. Everything else edits in place, silently.
PHASE_CHANGE = {Status.VOTING, Status.DECIDED, Status.RECORDED, Status.EXPIRED}


def can(state: CardState, to: Status) -> bool:
    return to in _ALLOWED[state.status]


def transition(state: CardState, to: Status, *, reason: str = "") -> bool:
    """Move to `to`. Returns True when the status actually changed. Raises on an illegal move."""
    if state.status == to:
        return False
    if not can(state, to):
        raise IllegalTransition(f"{state.status} -> {to}")
    state.status = to
    state.updated_at = now()
    if to == Status.STALLED:
        state.stalled_reason = reason
    if to != Status.PARKED:
        state.parked = None
    return True


# ---- automatic transitions driven by content ---------------------------------------------------

def settle_after_extraction(state: CardState) -> bool:
    """Framing -> Deliberating once the thread has substance; Stalled/Expired -> Deliberating when people talk again."""
    changed = False
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
    d = decision or state.decision or Decision()
    d.option_id = option_id
    d.confirmed_by = by
    d.confirmed_at = now()
    if not d.decider:
        d.decider = by
    state.decision = d
    state.decision_hint = None
    return transition(state, Status.DECIDED)


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

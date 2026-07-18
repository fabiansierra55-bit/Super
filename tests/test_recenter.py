from __future__ import annotations

from slp_model.constraints import validate_bundle
from slp_model.models import Ticket
from slp_model.recenter import (
    PositionalProfile,
    propose_recentered_ticket,
    recenter_bundle,
)


def _distance_objective(profile: PositionalProfile):
    def objective(tickets):
        return -sum(
            sum(
                (number - target) ** 2
                for number, target in zip(ticket.mains, profile.medians, strict=True)
            )
            for ticket in tickets
        )

    return objective


def test_recenter_preserves_uniqueness_and_ranges() -> None:
    profile = PositionalProfile(
        medians=(5, 12, 24, 36, 44),
        dispersions=(6, 6, 6, 6, 6),
        draw_count=120,
    )
    ticket = Ticket(mains=(1, 2, 3, 4, 5), mega=17)
    proposal = propose_recentered_ticket(ticket, profile, strength=0.25, max_shift=2)
    assert len(set(proposal.mains)) == 5
    assert all(1 <= value <= 47 for value in proposal.mains)
    assert proposal.mega == ticket.mega
    assert all(
        abs(after - before) <= 2 for before, after in zip(ticket.mains, proposal.mains, strict=True)
    )


def test_recenter_rejects_collapse_into_existing_main_set() -> None:
    profile = PositionalProfile(
        medians=(5, 15, 25, 35, 45),
        dispersions=(10, 10, 10, 10, 10),
        draw_count=180,
    )
    tickets = [
        Ticket(mains=(1, 10, 20, 30, 40), mega=1),
        Ticket(mains=(2, 11, 21, 31, 41), mega=2),
    ]
    result = recenter_bundle(
        tickets,
        profile,
        objective=_distance_objective(profile),
        strength=0.25,
        max_shift=2,
    )

    assert not result.decisions[0].accepted
    assert "constraint rejection" in result.decisions[0].reason
    assert len({ticket.mains for ticket in result.tickets}) == len(result.tickets)
    validate_bundle(list(result.tickets))
    assert result.final_objective >= result.original_objective


def test_recenter_rejects_any_global_objective_decrease() -> None:
    profile = PositionalProfile(
        medians=(8, 18, 28, 38, 46),
        dispersions=(10, 10, 10, 10, 10),
        draw_count=90,
    )
    ticket = Ticket(mains=(5, 15, 25, 35, 43), mega=9)

    def prefer_original(tickets):
        return -sum(
            sum((new - old) ** 2 for new, old in zip(value.mains, ticket.mains, strict=True))
            for value in tickets
        )

    result = recenter_bundle(
        [ticket],
        profile,
        objective=prefer_original,
        strength=0.25,
        max_shift=2,
    )
    assert result.tickets == (ticket,)
    assert not result.decisions[0].accepted
    assert result.decisions[0].reason == "global objective decreased"

"""Mild, constraint-safe positional recentering.

Recentering is a guarded local-search proposal, never a mandatory transform.
Every proposal must preserve the complete bundle constraints and must not lower
the caller's global objective.  This design prevents a shared positional target
from collapsing otherwise diverse tickets.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from .constraints import validate_bundle
from .modeling import FittedModel
from .models import Draw, Ticket

BundleObjective = Callable[[Sequence[Ticket]], float]


@dataclass(frozen=True)
class PositionalProfile:
    medians: tuple[float, float, float, float, float]
    dispersions: tuple[float, float, float, float, float]
    draw_count: int

    def __post_init__(self) -> None:
        if len(self.medians) != 5 or len(self.dispersions) != 5:
            raise ValueError("positional profile requires five positions")
        if self.draw_count <= 0:
            raise ValueError("positional profile draw_count must be positive")
        if any(not np.isfinite(value) for value in self.medians):
            raise ValueError("positional medians must be finite")
        if any(not np.isfinite(value) or value <= 0 for value in self.dispersions):
            raise ValueError("positional dispersions must be finite and positive")

    @classmethod
    def from_model(cls, model: FittedModel) -> PositionalProfile:
        return cls(
            medians=model.positional_medians,  # type: ignore[arg-type]
            dispersions=model.positional_dispersions,  # type: ignore[arg-type]
            draw_count=model.history_draw_count,
        )


@dataclass(frozen=True)
class RecenterDecision:
    ticket_index: int
    original: Ticket
    proposed: Ticket
    accepted: bool
    reason: str
    objective_before: float
    objective_after: float


@dataclass(frozen=True)
class RecenterResult:
    tickets: tuple[Ticket, ...]
    decisions: tuple[RecenterDecision, ...]
    original_objective: float
    final_objective: float

    @property
    def accepted_count(self) -> int:
        return sum(decision.accepted for decision in self.decisions)


def positional_profile(draws: Iterable[Draw]) -> PositionalProfile:
    history = sorted(draws, key=lambda draw: draw.draw_date)
    if not history:
        raise ValueError("at least one draw is required")
    values = np.asarray([draw.mains for draw in history], dtype=np.float64)
    medians = np.median(values, axis=0)
    mad = np.median(np.abs(values - medians), axis=0) * 1.4826
    standard_deviation = np.std(values, axis=0)
    dispersions = np.maximum(np.maximum(mad, standard_deviation), 1.0)
    return PositionalProfile(
        medians=tuple(float(value) for value in medians),  # type: ignore[arg-type]
        dispersions=tuple(float(value) for value in dispersions),  # type: ignore[arg-type]
        draw_count=len(history),
    )


def propose_recentered_ticket(
    ticket: Ticket,
    profile: PositionalProfile,
    *,
    strength: float = 0.15,
    max_shift: int = 2,
) -> Ticket:
    """Create a bounded positional proposal while preserving uniqueness."""

    if not 0.0 <= strength <= 0.25:
        raise ValueError("recentering strength must remain mild (0 through 0.25)")
    if max_shift < 0 or max_shift > 3:
        raise ValueError("max_shift must be between zero and three")
    original = np.asarray(ticket.mains, dtype=np.int16)
    medians = np.asarray(profile.medians, dtype=np.float64)
    dispersions = np.asarray(profile.dispersions, dtype=np.float64)
    displacement = np.clip(medians - original, -dispersions, dispersions)
    shifts = np.rint(strength * displacement).astype(np.int16)
    shifts = np.clip(shifts, -max_shift, max_shift)
    proposed = original + shifts

    # Each sorted position has a tighter legal range than the game boundary.
    lower = np.arange(1, 6, dtype=np.int16)
    upper = np.arange(43, 48, dtype=np.int16)
    proposed = np.minimum(np.maximum(proposed, lower), upper)
    for index in range(1, 5):
        proposed[index] = max(proposed[index], proposed[index - 1] + 1)
    for index in range(3, -1, -1):
        proposed[index] = min(proposed[index], proposed[index + 1] - 1)
    proposed = np.minimum(np.maximum(proposed, lower), upper)

    # Collision projection is not allowed to smuggle in a stronger recentering
    # than requested.  When it would, the safest proposal is no change.
    if np.any(np.abs(proposed - original) > max_shift) or len(np.unique(proposed)) != 5:
        proposed = original
    return Ticket(
        mains=tuple(int(value) for value in proposed),  # type: ignore[arg-type]
        mega=ticket.mega,
    )


def recenter_bundle(
    tickets: Sequence[Ticket],
    profile: PositionalProfile,
    *,
    objective: BundleObjective,
    strength: float = 0.15,
    max_shift: int = 2,
    max_overlap: int = 3,
    pair_cap: int = 2,
    triple_cap: int = 1,
    objective_tolerance: float = 1e-12,
) -> RecenterResult:
    """Apply only globally non-degrading, constraint-safe proposals."""

    if not tickets:
        raise ValueError("at least one ticket is required")
    current = list(tickets)
    validate_bundle(current, max_overlap=max_overlap, pair_cap=pair_cap, triple_cap=triple_cap)
    original_objective = float(objective(tuple(current)))
    if not np.isfinite(original_objective):
        raise ValueError("global objective must return a finite value")
    current_objective = original_objective
    decisions: list[RecenterDecision] = []

    for index, original in enumerate(tuple(current)):
        proposal = propose_recentered_ticket(
            original, profile, strength=strength, max_shift=max_shift
        )
        if proposal == original:
            decisions.append(
                RecenterDecision(
                    index,
                    original,
                    proposal,
                    False,
                    "unchanged",
                    current_objective,
                    current_objective,
                )
            )
            continue
        trial = current.copy()
        trial[index] = proposal
        try:
            validate_bundle(
                trial,
                max_overlap=max_overlap,
                pair_cap=pair_cap,
                triple_cap=triple_cap,
            )
        except ValueError as error:
            decisions.append(
                RecenterDecision(
                    index,
                    original,
                    proposal,
                    False,
                    f"constraint rejection: {error}",
                    current_objective,
                    current_objective,
                )
            )
            continue
        trial_objective = float(objective(tuple(trial)))
        if not np.isfinite(trial_objective):
            raise ValueError("global objective must return a finite value")
        if trial_objective + objective_tolerance < current_objective:
            decisions.append(
                RecenterDecision(
                    index,
                    original,
                    proposal,
                    False,
                    "global objective decreased",
                    current_objective,
                    trial_objective,
                )
            )
            continue
        before = current_objective
        current = trial
        current_objective = trial_objective
        decisions.append(
            RecenterDecision(
                index,
                original,
                proposal,
                True,
                "accepted",
                before,
                trial_objective,
            )
        )

    # A final whole-bundle assertion specifically guards against cumulative
    # recentering collapse.
    validate_bundle(current, max_overlap=max_overlap, pair_cap=pair_cap, triple_cap=triple_cap)
    if current_objective + objective_tolerance < original_objective:
        raise AssertionError("recentering reduced the global bundle objective")
    return RecenterResult(
        tickets=tuple(current),
        decisions=tuple(decisions),
        original_objective=original_objective,
        final_objective=current_objective,
    )


__all__ = [
    "BundleObjective",
    "PositionalProfile",
    "RecenterDecision",
    "RecenterResult",
    "positional_profile",
    "propose_recentered_ticket",
    "recenter_bundle",
]

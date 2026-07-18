"""Exact fair-draw coverage for a complete SuperLotto Plus bundle.

The adaptive model estimates outcomes under a fitted, potentially non-uniform
distribution.  This module supplies the complementary null model: every one
of the ``C(47, 5)`` main draws and every Mega value is equally likely.  The
result is exact rather than Monte Carlo, so it is both a promotion guard and a
plain-language statement of the bundle's combinatorial coverage.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import chain, combinations
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from .modeling import FittedModel
from .models import ExactUniformMetrics, Ticket

MAIN_DRAW_OUTCOME_COUNT: Literal[1_533_939] = 1_533_939
FULL_DRAW_OUTCOME_COUNT: Literal[41_416_353] = 41_416_353
OPTIMAL_30_LINE_GE3_COUNT = 258_582
OPTIMAL_30_LINE_GE4_COUNT = 6_330
OPTIMAL_30_LINE_3_PLUS_MEGA_COUNT = 264_630
assert math.comb(47, 5) == MAIN_DRAW_OUTCOME_COUNT
assert MAIN_DRAW_OUTCOME_COUNT * 27 == FULL_DRAW_OUTCOME_COUNT


@lru_cache(maxsize=1)
def _main_draw_incidence() -> NDArray[np.bool_]:
    """Return an immutable ``47 x C(47, 5)`` exact draw-incidence matrix."""

    flattened = np.fromiter(
        chain.from_iterable(combinations(range(47), 5)),
        dtype=np.int16,
        count=MAIN_DRAW_OUTCOME_COUNT * 5,
    )
    draws = flattened.reshape(MAIN_DRAW_OUTCOME_COUNT, 5)
    incidence = np.zeros((47, MAIN_DRAW_OUTCOME_COUNT), dtype=np.bool_)
    columns = np.arange(MAIN_DRAW_OUTCOME_COUNT)
    for position in range(5):
        incidence[draws[:, position], columns] = True
    incidence.setflags(write=False)
    return incidence


def exact_uniform_metrics(tickets: Sequence[Ticket]) -> ExactUniformMetrics:
    """Enumerate exact bundle coverage under the fair-lottery null model."""

    if not tickets:
        raise ValueError("exact fair coverage requires at least one ticket")
    incidence = _main_draw_incidence()
    best = np.zeros(MAIN_DRAW_OUTCOME_COUNT, dtype=np.uint8)
    mega_best = np.zeros((27, MAIN_DRAW_OUTCOME_COUNT), dtype=np.uint8)
    main_sets: set[tuple[int, ...]] = set()
    full_tickets: set[tuple[tuple[int, ...], int]] = set()

    for ticket in tickets:
        rows = np.asarray(ticket.mains, dtype=np.int16) - 1
        overlaps = incidence[rows].sum(axis=0, dtype=np.uint8)
        np.maximum(best, overlaps, out=best)
        np.maximum(mega_best[ticket.mega - 1], overlaps, out=mega_best[ticket.mega - 1])
        main_sets.add(ticket.mains)
        full_tickets.add((ticket.mains, ticket.mega))

    histogram_values = np.bincount(best, minlength=6)
    histogram = tuple(int(value) for value in histogram_values[:6])
    ge3_count = int(np.count_nonzero(best >= 3))
    ge4_count = int(np.count_nonzero(best >= 4))
    three_mega_count = int(np.count_nonzero(mega_best >= 3))
    four_mega_count = int(np.count_nonzero(mega_best >= 4))
    return ExactUniformMetrics(
        main_draw_outcome_count=MAIN_DRAW_OUTCOME_COUNT,
        full_draw_outcome_count=FULL_DRAW_OUTCOME_COUNT,
        covered_ge_3_mains_count=ge3_count,
        covered_ge_4_mains_count=ge4_count,
        covered_3_plus_mega_count=three_mega_count,
        covered_4_plus_mega_count=four_mega_count,
        covered_5_mains_count=len(main_sets),
        covered_jackpot_count=len(full_tickets),
        p_any_ge_3_mains=ge3_count / MAIN_DRAW_OUTCOME_COUNT,
        p_any_ge_4_mains=ge4_count / MAIN_DRAW_OUTCOME_COUNT,
        p_any_3_plus_mega=three_mega_count / FULL_DRAW_OUTCOME_COUNT,
        p_any_4_plus_mega=four_mega_count / FULL_DRAW_OUTCOME_COUNT,
        p_any_5_mains=len(main_sets) / MAIN_DRAW_OUTCOME_COUNT,
        p_jackpot=len(full_tickets) / FULL_DRAW_OUTCOME_COUNT,
        mean_best_main_matches=float(np.dot(np.arange(6), histogram_values[:6]))
        / MAIN_DRAW_OUTCOME_COUNT,
        best_match_histogram=histogram,  # type: ignore[arg-type]
    )


def fair_uniform_model(model: FittedModel) -> FittedModel:
    """Retain provenance/position data while replacing every draw weight by uniform."""

    mains = (1.0 / 47.0,) * 47
    mega = (1.0 / 27.0,) * 27
    return replace(
        model,
        mains_probabilities=mains,
        mega_probabilities=mega,
        recent_mains_probabilities=mains,
        recent_mega_probabilities=mega,
        stable_mains_probabilities=mains,
        stable_mega_probabilities=mega,
    )


@dataclass(frozen=True)
class PromotionDecision:
    selected: bool
    reason: str
    relative_primary_improvement: float


def exact_coverage_regressions(
    candidate: ExactUniformMetrics,
    reference: ExactUniformMetrics,
) -> tuple[str, ...]:
    """Name any exact fair events whose union coverage declines."""

    return tuple(
        label
        for label, candidate_value, reference_value in (
            ("3+ mains", candidate.p_any_ge_3_mains, reference.p_any_ge_3_mains),
            ("4+ mains", candidate.p_any_ge_4_mains, reference.p_any_ge_4_mains),
            ("3+Mega", candidate.p_any_3_plus_mega, reference.p_any_3_plus_mega),
            ("4+Mega", candidate.p_any_4_plus_mega, reference.p_any_4_plus_mega),
            ("jackpot", candidate.p_jackpot, reference.p_jackpot),
        )
        if candidate_value + 1e-15 < reference_value
    )


def fair_challenger_decision(
    challenger: ExactUniformMetrics,
    references: Sequence[ExactUniformMetrics],
    *,
    minimum_relative_improvement: float,
    require_30_line_optimum: bool = False,
    non_regression_references: Sequence[ExactUniformMetrics] = (),
) -> PromotionDecision:
    """Require model-reference improvement and no incumbent fair-odds regression."""

    if not references:
        raise ValueError("fair challenger promotion requires at least one reference")
    if minimum_relative_improvement < 0:
        raise ValueError("minimum relative improvement cannot be negative")
    if require_30_line_optimum and (
        challenger.covered_ge_3_mains_count != OPTIMAL_30_LINE_GE3_COUNT
        or challenger.covered_ge_4_mains_count != OPTIMAL_30_LINE_GE4_COUNT
        or challenger.covered_3_plus_mega_count != OPTIMAL_30_LINE_3_PLUS_MEGA_COUNT
        or challenger.covered_jackpot_count != 30
    ):
        return PromotionDecision(False, "global fair-coverage certificate not met", 0.0)
    reference_primary = max(item.p_any_ge_3_mains for item in references)
    required_primary = reference_primary * (1.0 + minimum_relative_improvement)
    relative = (
        (challenger.p_any_ge_3_mains / reference_primary) - 1.0 if reference_primary else math.inf
    )
    if challenger.p_any_ge_3_mains + 1e-15 < required_primary:
        return PromotionDecision(False, "primary fair-coverage threshold not met", relative)
    if non_regression_references and challenger.p_any_ge_3_mains + 1e-15 < max(
        item.p_any_ge_3_mains for item in non_regression_references
    ):
        return PromotionDecision(False, "incumbent fair 3+ coverage regressed", relative)
    guarded = (*references, *non_regression_references)
    if challenger.p_any_ge_4_mains + 1e-15 < max(item.p_any_ge_4_mains for item in guarded):
        return PromotionDecision(False, "fair 4+ coverage regressed", relative)
    if challenger.p_jackpot + 1e-15 < max(item.p_jackpot for item in guarded):
        return PromotionDecision(False, "fair jackpot coverage regressed", relative)
    return PromotionDecision(True, "exact fair-coverage promotion gate passed", relative)


__all__ = [
    "FULL_DRAW_OUTCOME_COUNT",
    "MAIN_DRAW_OUTCOME_COUNT",
    "OPTIMAL_30_LINE_3_PLUS_MEGA_COUNT",
    "OPTIMAL_30_LINE_GE3_COUNT",
    "OPTIMAL_30_LINE_GE4_COUNT",
    "PromotionDecision",
    "exact_coverage_regressions",
    "exact_uniform_metrics",
    "fair_challenger_decision",
    "fair_uniform_model",
]

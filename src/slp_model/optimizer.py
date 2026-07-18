"""Simulation-backed global bundle optimization.

The greedy pass is submodular for the primary coverage term: each round scores
the as-yet-uncovered simulated draws contributed by every eligible candidate.
Secondary coverage and an explicit anti-cannibalization penalty are layered on
without weakening hard diversity constraints.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from .constraints import validate_bundle
from .fair_odds import exact_uniform_metrics
from .modeling import FittedModel, TierName
from .models import Draw, Ticket
from .objectives import effective_event_weights
from .simulation import (
    BundleSimulationMetrics,
    Candidate,
    CandidatePool,
    SimulatedDraws,
    estimate_bundle_metrics,
    simulate_future_draws,
)

_POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)], dtype=np.uint8)
_TIER_ORDER: tuple[TierName, ...] = ("balanced", "aggressive", "conservative")


class OptimizationError(RuntimeError):
    """Raised when hard constraints make the requested bundle infeasible."""


@dataclass(frozen=True)
class OptimizerConstraints:
    bundle_size: int = 30
    tickets_per_tier: int = 10
    max_main_overlap: int = 3
    min_hamming_distance: int = 2
    pair_cap: int = 2
    triple_cap: int = 1
    mega_soft_cap: int = 4
    mega_hard_cap: int = 5

    def __post_init__(self) -> None:
        if self.bundle_size != 3 * self.tickets_per_tier:
            raise ValueError("bundle size must equal three tier quotas")
        if self.bundle_size <= 0 or self.tickets_per_tier <= 0:
            raise ValueError("bundle and tier sizes must be positive")
        if not 0 <= self.max_main_overlap <= 4:
            raise ValueError("max_main_overlap must be between zero and four")
        if self.min_hamming_distance < 0:
            raise ValueError("min_hamming_distance cannot be negative")
        if self.pair_cap <= 0 or self.triple_cap <= 0:
            raise ValueError("pair and triple caps must be positive")
        if self.mega_soft_cap <= 0 or self.mega_hard_cap < self.mega_soft_cap:
            raise ValueError("invalid Mega soft/hard caps")


@dataclass(frozen=True)
class ObjectiveWeights:
    mode: Literal["grind", "spike"] = "grind"
    p_ge_3: float = 1.0
    p_ge_4: float = 0.15
    three_plus_mega: float = 0.08
    four_plus_mega: float = 0.04
    anti_cannibalization: float = 0.025
    mega_repeat_penalty: float = 0.01
    aggressive_secondary_multiplier: float = 1.25

    def __post_init__(self) -> None:
        if self.mode not in ("grind", "spike"):
            raise ValueError("objective mode must be grind or spike")
        if any(
            value < 0
            for value in (
                self.p_ge_3,
                self.p_ge_4,
                self.three_plus_mega,
                self.four_plus_mega,
                self.anti_cannibalization,
                self.mega_repeat_penalty,
            )
        ):
            raise ValueError("objective weights cannot be negative")
        if self.aggressive_secondary_multiplier < 1:
            raise ValueError("aggressive secondary multiplier must be at least one")


DEFAULT_OPTIMIZER_CONSTRAINTS = OptimizerConstraints()
DEFAULT_OBJECTIVE_WEIGHTS = ObjectiveWeights()


@dataclass(frozen=True)
class MarginalContribution:
    selection_index: int
    generation_index: int
    tier: TierName
    primary_new_coverage: float
    four_plus_new_coverage: float
    three_plus_mega_new_coverage: float
    four_plus_mega_new_coverage: float
    anti_cannibalization_penalty: float
    weighted_gain: float


@dataclass(frozen=True)
class OptimizedBundle:
    candidates: tuple[Candidate, ...]
    marginal_contributions: tuple[MarginalContribution, ...]
    optimization_simulations: int
    scenario_p_ge_3: float
    scenario_p_ge_4: float
    scenario_p_3_plus_mega: float
    scenario_p_4_plus_mega: float
    scenario_objective: float
    adaptive_metrics: BundleSimulationMetrics | None

    @property
    def tickets(self) -> tuple[Ticket, ...]:
        return tuple(candidate.ticket for candidate in self.candidates)

    @property
    def tier_counts(self) -> dict[str, int]:
        return {
            tier: sum(candidate.tier == tier for candidate in self.candidates)
            for tier in ("aggressive", "balanced", "conservative")
        }


@dataclass(frozen=True)
class _Coverage:
    ge3: NDArray[np.uint8]
    ge4: NDArray[np.uint8]
    three_mega: NDArray[np.uint8]
    four_mega: NDArray[np.uint8]


def _candidate_arrays(
    candidates: Sequence[Candidate],
) -> tuple[
    NDArray[np.int16],
    NDArray[np.int16],
    NDArray[np.int32],
    NDArray[np.int32],
    NDArray[np.str_],
]:
    mains = np.asarray([candidate.ticket.mains for candidate in candidates], dtype=np.int16)
    mega = np.asarray([candidate.ticket.mega for candidate in candidates], dtype=np.int16)
    pair_ids = np.empty((len(candidates), 10), dtype=np.int32)
    triple_ids = np.empty((len(candidates), 10), dtype=np.int32)
    for index, row in enumerate(mains):
        numbers = tuple(int(value) for value in row)
        pair_ids[index] = [a * 48 + b for a, b in combinations(numbers, 2)]
        triple_ids[index] = [a * 48 * 48 + b * 48 + c for a, b, c in combinations(numbers, 3)]
    tiers = np.asarray([candidate.tier for candidate in candidates])
    return mains, mega, pair_ids, triple_ids, tiers


def _coverage_matrices(
    candidates: Sequence[Candidate],
    mains: NDArray[np.int16],
    mega: NDArray[np.int16],
    scenarios: SimulatedDraws,
    *,
    chunk_size: int = 1_024,
) -> _Coverage:
    scenario_count = len(scenarios)
    draw_incidence = np.zeros((47, scenario_count), dtype=np.uint8)
    columns = np.repeat(np.arange(scenario_count), 5)
    draw_incidence[scenarios.mains.reshape(-1) - 1, columns] = 1
    packed_width = (scenario_count + 7) // 8
    ge3 = np.empty((len(candidates), packed_width), dtype=np.uint8)
    ge4 = np.empty_like(ge3)
    three_mega = np.empty_like(ge3)
    four_mega = np.empty_like(ge3)

    for start in range(0, len(candidates), chunk_size):
        stop = min(start + chunk_size, len(candidates))
        incidence = np.zeros((stop - start, 47), dtype=np.uint8)
        rows = np.repeat(np.arange(stop - start), 5)
        incidence[rows, mains[start:stop].reshape(-1) - 1] = 1
        overlaps = incidence @ draw_incidence
        mega_hits = mega[start:stop, None] == scenarios.mega[None, :]
        ge3[start:stop] = np.packbits(overlaps >= 3, axis=1)
        ge4[start:stop] = np.packbits(overlaps >= 4, axis=1)
        three_mega[start:stop] = np.packbits((overlaps >= 3) & mega_hits, axis=1)
        four_mega[start:stop] = np.packbits((overlaps >= 4) & mega_hits, axis=1)
    return _Coverage(ge3, ge4, three_mega, four_mega)


def _new_coverage_counts(
    packed: NDArray[np.uint8],
    covered: NDArray[np.uint8],
    indices: NDArray[np.int64],
) -> NDArray[np.float64]:
    new_bits = np.bitwise_and(packed[indices], np.bitwise_not(covered)[None, :])
    return np.asarray(
        _POPCOUNT[new_bits].sum(axis=1, dtype=np.int64),
        dtype=np.float64,
    )


def _tier_multipliers(
    tier: TierName, *, aggressive_secondary_multiplier: float
) -> tuple[float, float, float, float, float]:
    # (primary, >=4, 3+Mega, 4+Mega, anti-cannibalization)
    if tier == "aggressive":
        return (
            0.95,
            1.08 * aggressive_secondary_multiplier,
            0.96 * aggressive_secondary_multiplier,
            1.12 * aggressive_secondary_multiplier,
            0.85,
        )
    if tier == "balanced":
        return 1.12, 1.00, 1.00, 1.00, 1.00
    return 1.00, 0.75, 0.80, 0.70, 1.15


def _objective_coefficients(
    weights: ObjectiveWeights,
) -> tuple[float, float, float, float]:
    return effective_event_weights(
        weights.mode,
        (
            weights.p_ge_3,
            weights.p_ge_4,
            weights.three_plus_mega,
            weights.four_plus_mega,
        ),
    )


def _previous_main_set(previous_draw: Draw | Sequence[int] | None) -> frozenset[int]:
    if previous_draw is None:
        return frozenset()
    values = previous_draw.mains if isinstance(previous_draw, Draw) else tuple(previous_draw)
    if len(values) != 5 or len(set(values)) != 5:
        raise ValueError("previous draw must contain five unique mains")
    return frozenset(int(value) for value in values)


def measure_bundle_marginals(
    tickets: Sequence[Ticket],
    tiers: Sequence[TierName],
    model: FittedModel,
    *,
    seed: int,
    simulations: int,
    weights: ObjectiveWeights = DEFAULT_OBJECTIVE_WEIGHTS,
    generation_indices: Sequence[int] | None = None,
    mega_soft_cap: int = 4,
    mega_hard_cap: int = 5,
) -> tuple[MarginalContribution, ...]:
    """Measure every final line's sequential marginal gain on shared scenarios.

    This is used after an accepted positional-recentering pass so the immutable
    audit metadata describes the lines that were actually locked, rather than
    only their pre-recentering source candidates.
    """

    if not tickets or len(tickets) != len(tiers):
        raise ValueError("tickets and tiers must be non-empty and have equal lengths")
    if simulations <= 0:
        raise ValueError("marginal simulations must be positive")
    if generation_indices is None:
        generation_indices = tuple(range(len(tickets)))
    if len(generation_indices) != len(tickets):
        raise ValueError("generation indices must match the ticket count")
    if mega_soft_cap <= 0 or mega_hard_cap < mega_soft_cap:
        raise ValueError("invalid Mega soft/hard caps")

    candidates = tuple(
        Candidate(
            ticket=ticket,
            tier=tier,
            generation_index=int(generation_index),
            sampling_log_weight=0.0,
        )
        for ticket, tier, generation_index in zip(tickets, tiers, generation_indices, strict=True)
    )
    mains, mega, pair_ids, triple_ids, _ = _candidate_arrays(candidates)
    scenarios = simulate_future_draws(model, count=simulations, seed=seed, tier="balanced")
    coverage = _coverage_matrices(candidates, mains, mega, scenarios)
    covered_ge3 = np.zeros(coverage.ge3.shape[1], dtype=np.uint8)
    covered_ge4 = np.zeros(coverage.ge4.shape[1], dtype=np.uint8)
    covered_three_mega = np.zeros(coverage.three_mega.shape[1], dtype=np.uint8)
    covered_four_mega = np.zeros(coverage.four_mega.shape[1], dtype=np.uint8)
    main_counts = np.zeros(48, dtype=np.int16)
    pair_counts = np.zeros(48 * 48, dtype=np.int16)
    triple_counts = np.zeros(48 * 48 * 48, dtype=np.int16)
    mega_counts = np.zeros(28, dtype=np.int16)
    contributions: list[MarginalContribution] = []
    selected_indices: list[int] = []
    primary_coefficient, four_coefficient, three_mega_coefficient, four_mega_coefficient = (
        _objective_coefficients(weights)
    )

    def new_fraction(bits: NDArray[np.uint8], covered: NDArray[np.uint8], index: int) -> float:
        new_bits = np.bitwise_and(bits[index], np.bitwise_not(covered))
        return float(_POPCOUNT[new_bits].sum(dtype=np.int64) / simulations)

    for index, candidate in enumerate(candidates):
        primary_new = new_fraction(coverage.ge3, covered_ge3, index)
        four_new = new_fraction(coverage.ge4, covered_ge4, index)
        three_mega_new = new_fraction(coverage.three_mega, covered_three_mega, index)
        four_mega_new = new_fraction(coverage.four_mega, covered_four_mega, index)
        denominator = max(len(selected_indices), 1)
        main_reuse = float(main_counts[mains[index]].sum()) / (5 * denominator)
        pair_reuse = float(pair_counts[pair_ids[index]].sum()) / (10 * denominator)
        triple_reuse = float(triple_counts[triple_ids[index]].sum()) / (10 * denominator)
        correlation = 0.0
        for selected_index in selected_indices:
            overlap = int(np.count_nonzero(mains[index, :, None] == mains[selected_index][None, :]))
            correlation += max(overlap - 1, 0) ** 2 / 16.0
        if selected_indices:
            correlation /= len(selected_indices)
        mega_excess = max(int(mega_counts[mega[index]]) + 1 - mega_soft_cap, 0) / mega_hard_cap
        structural_penalty = (
            0.32 * main_reuse + 0.30 * pair_reuse + 0.18 * triple_reuse + 0.15 * correlation
        )
        primary_mult, four_mult, three_mult, four_mega_mult, penalty_mult = _tier_multipliers(
            candidate.tier,
            aggressive_secondary_multiplier=weights.aggressive_secondary_multiplier,
        )
        penalty = (
            weights.anti_cannibalization * penalty_mult * structural_penalty
            + weights.mega_repeat_penalty * mega_excess
        )
        gain = (
            primary_coefficient * primary_mult * primary_new
            + four_coefficient * four_mult * four_new
            + three_mega_coefficient * three_mult * three_mega_new
            + four_mega_coefficient * four_mega_mult * four_mega_new
            - penalty
        )
        contributions.append(
            MarginalContribution(
                selection_index=index + 1,
                generation_index=candidate.generation_index,
                tier=candidate.tier,
                primary_new_coverage=primary_new,
                four_plus_new_coverage=four_new,
                three_plus_mega_new_coverage=three_mega_new,
                four_plus_mega_new_coverage=four_mega_new,
                anti_cannibalization_penalty=penalty,
                weighted_gain=gain,
            )
        )
        covered_ge3 |= coverage.ge3[index]
        covered_ge4 |= coverage.ge4[index]
        covered_three_mega |= coverage.three_mega[index]
        covered_four_mega |= coverage.four_mega[index]
        main_counts[mains[index]] += 1
        pair_counts[pair_ids[index]] += 1
        triple_counts[triple_ids[index]] += 1
        mega_counts[mega[index]] += 1
        selected_indices.append(index)
    return tuple(contributions)


def optimize_bundle(
    candidate_pool: CandidatePool | Sequence[Candidate],
    model: FittedModel,
    *,
    seed: int,
    previous_draw: Draw | Sequence[int] | None = None,
    constraints: OptimizerConstraints = DEFAULT_OPTIMIZER_CONSTRAINTS,
    weights: ObjectiveWeights = DEFAULT_OBJECTIVE_WEIGHTS,
    optimization_simulations: int = 2_048,
    estimate_final_metrics: bool = True,
    metric_min_simulations: int = 10_000,
    metric_max_simulations: int = 100_000,
    metric_batch_size: int = 5_000,
    confidence_tolerance: float = 0.01,
    metric_confidence_level: float = 0.95,
    metric_stable_batches_required: int = 2,
) -> OptimizedBundle:
    """Select a constrained 30-line bundle by global marginal contribution."""

    candidates = tuple(candidate_pool)
    if len(candidates) < constraints.bundle_size:
        raise OptimizationError("candidate pool is smaller than requested bundle")
    if optimization_simulations <= 0:
        raise ValueError("optimization_simulations must be positive")
    for tier in _TIER_ORDER:
        if sum(candidate.tier == tier for candidate in candidates) < constraints.tickets_per_tier:
            raise OptimizationError(f"candidate pool lacks enough {tier} tickets")

    prior = _previous_main_set(previous_draw)
    mains, mega, pair_ids, triple_ids, tiers = _candidate_arrays(candidates)
    scenarios = simulate_future_draws(
        model,
        count=optimization_simulations,
        seed=seed,
        tier="balanced",
    )
    coverage = _coverage_matrices(candidates, mains, mega, scenarios)
    byte_width = coverage.ge3.shape[1]
    covered_ge3 = np.zeros(byte_width, dtype=np.uint8)
    covered_ge4 = np.zeros(byte_width, dtype=np.uint8)
    covered_three_mega = np.zeros(byte_width, dtype=np.uint8)
    covered_four_mega = np.zeros(byte_width, dtype=np.uint8)

    selected_mask = np.zeros(len(candidates), dtype=bool)
    permanently_compatible = np.ones(len(candidates), dtype=bool)
    if prior:
        aggressive = tiers == "aggressive"
        overlaps_prior = np.asarray(
            [len(set(int(value) for value in row) & prior) for row in mains]
        )
        permanently_compatible &= ~aggressive | (overlaps_prior <= 1)

    main_counts = np.zeros(48, dtype=np.int16)
    pair_counts = np.zeros(48 * 48, dtype=np.int16)
    triple_counts = np.zeros(48 * 48 * 48, dtype=np.int16)
    mega_counts = np.zeros(28, dtype=np.int16)
    selected_indices: list[int] = []
    contributions: list[MarginalContribution] = []
    (
        coefficient_primary,
        coefficient_four,
        coefficient_three_mega,
        coefficient_four_mega,
    ) = _objective_coefficients(weights)

    schedule = tuple(tier for _ in range(constraints.tickets_per_tier) for tier in _TIER_ORDER)
    for selection_index, requested_tier in enumerate(schedule):
        eligible = (
            (tiers == requested_tier)
            & ~selected_mask
            & permanently_compatible
            & (mega_counts[mega] < constraints.mega_hard_cap)
        )
        eligible &= np.all(pair_counts[pair_ids] < constraints.pair_cap, axis=1)
        eligible &= np.all(triple_counts[triple_ids] < constraints.triple_cap, axis=1)
        eligible_indices = np.flatnonzero(eligible)
        if eligible_indices.size == 0:
            raise OptimizationError(
                f"hard constraints left no eligible {requested_tier} candidate at "
                f"line {selection_index + 1}"
            )

        primary_new = (
            _new_coverage_counts(coverage.ge3, covered_ge3, eligible_indices)
            / optimization_simulations
        )
        four_new = (
            _new_coverage_counts(coverage.ge4, covered_ge4, eligible_indices)
            / optimization_simulations
        )
        three_mega_new = (
            _new_coverage_counts(coverage.three_mega, covered_three_mega, eligible_indices)
            / optimization_simulations
        )
        four_mega_new = (
            _new_coverage_counts(coverage.four_mega, covered_four_mega, eligible_indices)
            / optimization_simulations
        )

        candidate_mains = mains[eligible_indices]
        main_reuse = main_counts[candidate_mains].sum(axis=1) / max(
            5 * max(len(selected_indices), 1), 1
        )
        pair_reuse = pair_counts[pair_ids[eligible_indices]].sum(axis=1) / max(
            10 * max(len(selected_indices), 1), 1
        )
        triple_reuse = triple_counts[triple_ids[eligible_indices]].sum(axis=1) / max(
            10 * max(len(selected_indices), 1), 1
        )
        mega_excess = (
            np.maximum(mega_counts[mega[eligible_indices]] + 1 - constraints.mega_soft_cap, 0)
            / constraints.mega_hard_cap
        )
        correlation = np.zeros(eligible_indices.size, dtype=np.float64)
        for selected_index in selected_indices:
            overlap_values = np.count_nonzero(
                candidate_mains[:, :, None] == mains[selected_index][None, None, :],
                axis=(1, 2),
            )
            correlation += np.square(np.maximum(overlap_values - 1, 0)) / 16.0
        if selected_indices:
            correlation /= len(selected_indices)
        structural_penalty = (
            0.32 * main_reuse + 0.30 * pair_reuse + 0.18 * triple_reuse + 0.15 * correlation
        )

        primary_mult, four_mult, three_mega_mult, four_mega_mult, penalty_mult = _tier_multipliers(
            requested_tier,
            aggressive_secondary_multiplier=weights.aggressive_secondary_multiplier,
        )
        penalties = (
            weights.anti_cannibalization * penalty_mult * structural_penalty
            + weights.mega_repeat_penalty * mega_excess
        )
        gains = (
            coefficient_primary * primary_mult * primary_new
            + coefficient_four * four_mult * four_new
            + coefficient_three_mega * three_mega_mult * three_mega_new
            + coefficient_four_mega * four_mega_mult * four_mega_new
            - penalties
        )
        # np.argmax is deterministic and candidate generation order is stable.
        local_choice = int(np.argmax(gains))
        chosen_index = int(eligible_indices[local_choice])

        selected_indices.append(chosen_index)
        selected_mask[chosen_index] = True
        covered_ge3 |= coverage.ge3[chosen_index]
        covered_ge4 |= coverage.ge4[chosen_index]
        covered_three_mega |= coverage.three_mega[chosen_index]
        covered_four_mega |= coverage.four_mega[chosen_index]
        main_counts[mains[chosen_index]] += 1
        pair_counts[pair_ids[chosen_index]] += 1
        triple_counts[triple_ids[chosen_index]] += 1
        mega_counts[mega[chosen_index]] += 1

        chosen_mains = mains[chosen_index]
        chosen_mega = mega[chosen_index]
        overlaps = np.count_nonzero(mains[:, :, None] == chosen_mains[None, None, :], axis=(1, 2))
        positional_hamming = np.count_nonzero(mains != chosen_mains[None, :], axis=1) + (
            mega != chosen_mega
        )
        permanently_compatible &= overlaps <= constraints.max_main_overlap
        permanently_compatible &= positional_hamming >= constraints.min_hamming_distance

        contributions.append(
            MarginalContribution(
                selection_index=selection_index + 1,
                generation_index=candidates[chosen_index].generation_index,
                tier=requested_tier,
                primary_new_coverage=float(primary_new[local_choice]),
                four_plus_new_coverage=float(four_new[local_choice]),
                three_plus_mega_new_coverage=float(three_mega_new[local_choice]),
                four_plus_mega_new_coverage=float(four_mega_new[local_choice]),
                anti_cannibalization_penalty=float(penalties[local_choice]),
                weighted_gain=float(gains[local_choice]),
            )
        )

    selected = tuple(candidates[index] for index in selected_indices)
    selected_tickets = [candidate.ticket for candidate in selected]
    validate_bundle(
        selected_tickets,
        max_overlap=constraints.max_main_overlap,
        pair_cap=constraints.pair_cap,
        triple_cap=constraints.triple_cap,
    )
    for index, ticket in enumerate(selected_tickets):
        for other in selected_tickets[index + 1 :]:
            hamming = sum(
                left != right for left, right in zip(ticket.mains, other.mains, strict=True)
            ) + (ticket.mega != other.mega)
            if hamming < constraints.min_hamming_distance:
                raise AssertionError("optimizer produced a Hamming-distance violation")
    if (
        max(Counter(ticket.mega for ticket in selected_tickets).values())
        > constraints.mega_hard_cap
    ):
        raise AssertionError("optimizer produced a Mega hard-cap violation")
    if prior and any(
        candidate.tier == "aggressive" and len(set(candidate.ticket.mains) & prior) > 1
        for candidate in selected
    ):
        raise AssertionError("optimizer produced an aggressive prior-overlap violation")

    def covered_fraction(bits: NDArray[np.uint8]) -> float:
        return float(_POPCOUNT[bits].sum(dtype=np.int64) / optimization_simulations)

    p_ge3 = covered_fraction(covered_ge3)
    p_ge4 = covered_fraction(covered_ge4)
    p_three_mega = covered_fraction(covered_three_mega)
    p_four_mega = covered_fraction(covered_four_mega)
    scenario_objective = (
        coefficient_primary * p_ge3
        + coefficient_four * p_ge4
        + coefficient_three_mega * p_three_mega
        + coefficient_four_mega * p_four_mega
        - sum(item.anti_cannibalization_penalty for item in contributions)
    )
    adaptive_metrics = None
    if estimate_final_metrics:
        adaptive_metrics = estimate_bundle_metrics(
            selected_tickets,
            model,
            seed=seed ^ 0xD1B54A32D192ED03,
            min_simulations=metric_min_simulations,
            max_simulations=metric_max_simulations,
            batch_size=metric_batch_size,
            confidence_tolerance=confidence_tolerance,
            confidence_level=metric_confidence_level,
            stable_batches_required=metric_stable_batches_required,
        )

    return OptimizedBundle(
        candidates=selected,
        marginal_contributions=tuple(contributions),
        optimization_simulations=optimization_simulations,
        scenario_p_ge_3=p_ge3,
        scenario_p_ge_4=p_ge4,
        scenario_p_3_plus_mega=p_three_mega,
        scenario_p_4_plus_mega=p_four_mega,
        scenario_objective=scenario_objective,
        adaptive_metrics=adaptive_metrics,
    )


def optimize_fair_coverage(
    candidate_pool: CandidatePool | Sequence[Candidate],
    model: FittedModel,
    *,
    seed: int,
    previous_draw: Draw | Sequence[int] | None = None,
    constraints: OptimizerConstraints = DEFAULT_OPTIMIZER_CONSTRAINTS,
    weights: ObjectiveWeights = DEFAULT_OBJECTIVE_WEIGHTS,
    marginal_simulations: int = 4_096,
    restarts: int = 4,
) -> OptimizedBundle:
    """Build a balanced linear packing and score its exact fair-draw coverage.

    Requiring every main-number pair to be globally unique makes any two lines
    share at most one main.  The greedy cost is the exact incremental convex
    reuse cost, so it balances 150 number incidences across 47 labels.  Mega
    repeats are permitted only between main-disjoint lines, eliminating lost
    fair 3+Mega coverage.  Multiple deterministic restarts are ranked by exact
    enumeration, never by a fitted historical distribution.
    """

    candidates = tuple(candidate_pool)
    if len(candidates) < constraints.bundle_size:
        raise OptimizationError("candidate pool is smaller than requested bundle")
    if restarts <= 0:
        raise ValueError("fair optimizer restarts must be positive")
    if marginal_simulations <= 0:
        raise ValueError("fair marginal simulations must be positive")
    for tier in _TIER_ORDER:
        if sum(candidate.tier == tier for candidate in candidates) < constraints.tickets_per_tier:
            raise OptimizationError(f"candidate pool lacks enough {tier} tickets")

    prior = _previous_main_set(previous_draw)
    mains, mega, pair_ids, _, tiers = _candidate_arrays(candidates)
    permanently_compatible = np.ones(len(candidates), dtype=bool)
    if prior:
        aggressive = tiers == "aggressive"
        overlaps_prior = np.asarray(
            [len(set(int(value) for value in row) & prior) for row in mains]
        )
        permanently_compatible &= ~aggressive | (overlaps_prior <= 1)

    schedule = tuple(tier for _ in range(constraints.tickets_per_tier) for tier in _TIER_ORDER)
    variants: list[tuple[tuple[int, ...], tuple[Candidate, ...]]] = []
    base_tie_seed = 0x647373683
    for restart in range(restarts):
        tie_seed = base_tie_seed if restart == 0 else (seed ^ (restart * 0x9E3779B9))
        rng = np.random.default_rng(tie_seed)
        selected_mask = np.zeros(len(candidates), dtype=bool)
        main_counts = np.zeros(48, dtype=np.int16)
        pair_counts = np.zeros(48 * 48, dtype=np.int16)
        mega_counts = np.zeros(28, dtype=np.int16)
        selected_indices: list[int] = []
        feasible = True

        for requested_tier in schedule:
            eligible = (
                (tiers == requested_tier)
                & ~selected_mask
                & permanently_compatible
                & (mega_counts[mega] < constraints.mega_hard_cap)
            )
            eligible &= np.all(pair_counts[pair_ids] == 0, axis=1)
            # A cap of five is a feasibility guard; the convex score normally
            # reaches the sharper balanced 38x3 + 9x4 degree distribution.
            eligible &= np.all(main_counts[mains] < 5, axis=1)
            for selected_index in selected_indices:
                same_mega = mega == mega[selected_index]
                if not np.any(same_mega):
                    continue
                overlap = np.count_nonzero(
                    mains[:, :, None] == mains[selected_index][None, None, :],
                    axis=(1, 2),
                )
                eligible &= ~same_mega | (overlap == 0)
            eligible_indices = np.flatnonzero(eligible)
            if eligible_indices.size == 0:
                feasible = False
                break

            candidate_mains = mains[eligible_indices]
            incremental_reuse = main_counts[candidate_mains].sum(axis=1)
            post_square_cost = np.square(main_counts[candidate_mains] + 1).sum(axis=1)
            mega_reuse = mega_counts[mega[eligible_indices]]
            jitter = rng.random(eligible_indices.size) * 1e-3
            scores = 1_000.0 * incremental_reuse + 10.0 * post_square_cost + mega_reuse + jitter
            chosen_index = int(eligible_indices[int(np.argmin(scores))])
            selected_indices.append(chosen_index)
            selected_mask[chosen_index] = True
            main_counts[mains[chosen_index]] += 1
            pair_counts[pair_ids[chosen_index]] += 1
            mega_counts[mega[chosen_index]] += 1

        if not feasible:
            continue
        selected = tuple(candidates[index] for index in selected_indices)
        selected_tickets = tuple(candidate.ticket for candidate in selected)
        try:
            validate_bundle(
                selected_tickets,
                max_overlap=min(constraints.max_main_overlap, 1),
                min_hamming=constraints.min_hamming_distance,
                pair_cap=1,
                triple_cap=1,
                mega_hard_cap=constraints.mega_hard_cap,
            )
        except ValueError:
            continue
        exact = exact_uniform_metrics(selected_tickets)
        variants.append(
            (
                (
                    exact.covered_ge_3_mains_count,
                    exact.covered_3_plus_mega_count,
                    exact.covered_ge_4_mains_count,
                    int(round(exact.mean_best_main_matches * 1_000_000_000)),
                ),
                selected,
            )
        )

    if not variants:
        raise OptimizationError("fair structural optimizer found no feasible bundle")
    _, selected = max(variants, key=lambda item: item[0])
    selected_tickets = tuple(candidate.ticket for candidate in selected)
    selected_tiers = tuple(candidate.tier for candidate in selected)
    exact = exact_uniform_metrics(selected_tickets)
    contributions = measure_bundle_marginals(
        selected_tickets,
        selected_tiers,
        model,
        seed=seed ^ 0xD1B54A32D192ED03,
        simulations=marginal_simulations,
        weights=weights,
        generation_indices=tuple(candidate.generation_index for candidate in selected),
        mega_soft_cap=constraints.mega_soft_cap,
        mega_hard_cap=constraints.mega_hard_cap,
    )
    primary, four, three_mega, four_mega = _objective_coefficients(weights)
    objective = (
        primary * exact.p_any_ge_3_mains
        + four * exact.p_any_ge_4_mains
        + three_mega * exact.p_any_3_plus_mega
        + four_mega * exact.p_any_4_plus_mega
    )
    return OptimizedBundle(
        candidates=selected,
        marginal_contributions=contributions,
        optimization_simulations=exact.main_draw_outcome_count,
        scenario_p_ge_3=exact.p_any_ge_3_mains,
        scenario_p_ge_4=exact.p_any_ge_4_mains,
        scenario_p_3_plus_mega=exact.p_any_3_plus_mega,
        scenario_p_4_plus_mega=exact.p_any_4_plus_mega,
        scenario_objective=objective,
        adaptive_metrics=None,
    )


__all__ = [
    "MarginalContribution",
    "ObjectiveWeights",
    "OptimizationError",
    "OptimizedBundle",
    "OptimizerConstraints",
    "measure_bundle_marginals",
    "optimize_bundle",
    "optimize_fair_coverage",
]

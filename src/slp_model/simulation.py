"""Deterministic candidate generation and adaptive future-draw simulation."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import overload

import numpy as np
from numpy.typing import NDArray

from .modeling import FittedModel, TierName
from .models import Draw, Ticket

CANDIDATE_POOL_ALGORITHM_VERSION = "deterministic-tiered-weighted-sampling-v1"
CANDIDATE_POOL_DIGEST_VERSION = "candidate-pool-v1"

PRODUCTION_MINIMUM_CANDIDATES = 50_000
TIERS: tuple[TierName, ...] = ("aggressive", "balanced", "conservative")


@dataclass(frozen=True)
class Candidate:
    ticket: Ticket
    tier: TierName
    generation_index: int
    sampling_log_weight: float

    @property
    def signature(self) -> tuple[tuple[int, ...], int]:
        return self.ticket.mains, self.ticket.mega


@dataclass(frozen=True)
class CandidatePool(Sequence[Candidate]):
    candidates: tuple[Candidate, ...]
    seed: int
    requested_size: int

    def __len__(self) -> int:
        return len(self.candidates)

    @overload
    def __getitem__(self, index: int) -> Candidate: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[Candidate, ...]: ...

    def __getitem__(self, index: int | slice) -> Candidate | tuple[Candidate, ...]:
        return self.candidates[index]

    def __iter__(self) -> Iterator[Candidate]:
        return iter(self.candidates)

    @property
    def tier_counts(self) -> dict[str, int]:
        return dict(Counter(candidate.tier for candidate in self.candidates))

    def content_sha256(self) -> str:
        """Bind the complete ordered pool without persisting 50,000 CSV rows."""

        digest = hashlib.sha256()
        digest.update(
            f"{CANDIDATE_POOL_DIGEST_VERSION}|{self.seed}|{self.requested_size}\n".encode()
        )
        for candidate in self.candidates:
            record = (
                f"{candidate.generation_index}|{candidate.tier}|"
                f"{','.join(map(str, candidate.ticket.mains))}|{candidate.ticket.mega}|"
                f"{candidate.sampling_log_weight.hex()}\n"
            )
            digest.update(record.encode())
        return digest.hexdigest()


@dataclass(frozen=True)
class SimulatedDraws:
    mains: NDArray[np.int16]
    mega: NDArray[np.int16]
    seed: int
    tier: TierName

    def __post_init__(self) -> None:
        if self.mains.ndim != 2 or self.mains.shape[1] != 5:
            raise ValueError("simulated mains must have shape (n, 5)")
        if self.mega.shape != (self.mains.shape[0],):
            raise ValueError("simulated Mega shape does not match mains")
        if np.any(self.mains < 1) or np.any(self.mains > 47):
            raise ValueError("simulated main outside 1-47")
        if np.any(self.mega < 1) or np.any(self.mega > 27):
            raise ValueError("simulated Mega outside 1-27")
        if any(len(np.unique(row)) != 5 for row in self.mains):
            raise ValueError("simulated mains must be unique within each draw")

    def __len__(self) -> int:
        return int(self.mains.shape[0])


@dataclass(frozen=True)
class BundleSimulationMetrics:
    simulation_count: int
    stable: bool
    confidence_level: float
    stable_batches: int
    confidence_tolerance: float
    primary_confidence_half_width: float
    secondary_confidence_half_width: float
    mega_confidence_half_width: float
    p_ge_2: float
    p_ge_3: float
    p_ge_4: float
    p_3_plus_mega: float
    p_4_plus_mega: float
    mean_best_main_matches: float
    population_std_best_main_matches: float
    sample_std_best_main_matches: float
    best_match_histogram: tuple[int, int, int, int, int, int]


def _tier_target_counts(size: int) -> dict[TierName, int]:
    base, remainder = divmod(size, 3)
    result: dict[TierName, int] = {
        "aggressive": base,
        "balanced": base,
        "conservative": base,
    }
    # Put an odd remainder into the primary model first, then conservative.
    if remainder:
        result["balanced"] += 1
    if remainder == 2:
        result["conservative"] += 1
    return result


def _sample_main_batch(
    probabilities: NDArray[np.float64],
    rng: np.random.Generator,
    size: int,
) -> NDArray[np.int16]:
    uniforms = np.maximum(rng.random((size, 47)), np.finfo(np.float64).tiny)
    exponential_keys = -np.log(uniforms) / probabilities[None, :]
    values = np.argpartition(exponential_keys, 4, axis=1)[:, :5] + 1
    values.sort(axis=1)
    return values.astype(np.int16, copy=False)


def _previous_mains(previous_draw: Draw | Sequence[int] | None) -> frozenset[int]:
    if previous_draw is None:
        return frozenset()
    values = previous_draw.mains if isinstance(previous_draw, Draw) else tuple(previous_draw)
    if (
        len(values) != 5
        or len(set(values)) != 5
        or any(value < 1 or value > 47 for value in values)
    ):
        raise ValueError("previous draw must contain five unique mains in 1-47")
    return frozenset(int(value) for value in values)


def generate_candidate_pool(
    model: FittedModel,
    *,
    size: int = PRODUCTION_MINIMUM_CANDIDATES,
    seed: int,
    previous_draw: Draw | Sequence[int] | None = None,
    enforce_production_minimum: bool = True,
    batch_size: int = 8_192,
) -> CandidatePool:
    """Generate a unique, reproducible pool using weighted sampling.

    Production calls enforce at least 50,000 full-ticket signatures.  Tests and
    small diagnostic backtests may explicitly disable that guard.  Main sets
    are sampled without replacement; Mega is sampled independently.
    """

    if size <= 0:
        raise ValueError("candidate-pool size must be positive")
    if enforce_production_minimum and size < PRODUCTION_MINIMUM_CANDIDATES:
        raise ValueError(
            f"production candidate pools require at least {PRODUCTION_MINIMUM_CANDIDATES:,} tickets"
        )
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    prior = _previous_mains(previous_draw)
    rng = np.random.default_rng(seed)
    targets = _tier_target_counts(size)
    signatures: set[tuple[tuple[int, ...], int]] = set()
    candidates: list[Candidate] = []

    for tier in TIERS:
        mains_probabilities, mega_probabilities = model.tier_probabilities(tier)
        accepted = 0
        attempts = 0
        target = targets[tier]
        while accepted < target:
            remaining = target - accepted
            sample_size = min(batch_size, max(256, int(remaining * 1.3)))
            mains_batch = _sample_main_batch(mains_probabilities, rng, sample_size)
            mega_batch = rng.choice(
                np.arange(1, 28, dtype=np.int16),
                size=sample_size,
                replace=True,
                p=mega_probabilities,
            )
            attempts += sample_size
            for row, mega_value in zip(mains_batch, mega_batch, strict=True):
                mains = (
                    int(row[0]),
                    int(row[1]),
                    int(row[2]),
                    int(row[3]),
                    int(row[4]),
                )
                if tier == "aggressive" and prior and len(set(mains) & prior) > 1:
                    continue
                mega = int(mega_value)
                signature = (mains, mega)
                if signature in signatures:
                    continue
                signatures.add(signature)
                log_weight = float(
                    sum(math.log(mains_probabilities[value - 1]) for value in mains)
                    + math.log(mega_probabilities[mega - 1])
                )
                candidates.append(
                    Candidate(
                        ticket=Ticket(mains=mains, mega=mega),
                        tier=tier,
                        generation_index=len(candidates),
                        sampling_log_weight=log_weight,
                    )
                )
                accepted += 1
                if accepted == target:
                    break
            if attempts > max(100_000, target * 200):
                raise RuntimeError(f"could not generate {target} unique {tier} candidates")

    return CandidatePool(tuple(candidates), int(seed), size)


def _sample_draw_arrays(
    mains_probabilities: NDArray[np.float64],
    mega_probabilities: NDArray[np.float64],
    rng: np.random.Generator,
    count: int,
) -> tuple[NDArray[np.int16], NDArray[np.int16]]:
    mains = _sample_main_batch(mains_probabilities, rng, count)
    mega = rng.choice(
        np.arange(1, 28, dtype=np.int16),
        size=count,
        replace=True,
        p=mega_probabilities,
    ).astype(np.int16, copy=False)
    return mains, mega


def simulate_future_draws(
    model: FittedModel,
    *,
    count: int,
    seed: int,
    tier: TierName = "balanced",
    batch_size: int = 20_000,
) -> SimulatedDraws:
    if count <= 0:
        raise ValueError("simulation count must be positive")
    mains_probabilities, mega_probabilities = model.tier_probabilities(tier)
    rng = np.random.default_rng(seed)
    main_parts: list[NDArray[np.int16]] = []
    mega_parts: list[NDArray[np.int16]] = []
    remaining = count
    while remaining:
        current = min(batch_size, remaining)
        mains, mega = _sample_draw_arrays(mains_probabilities, mega_probabilities, rng, current)
        main_parts.append(mains)
        mega_parts.append(mega)
        remaining -= current
    return SimulatedDraws(
        mains=np.concatenate(main_parts),
        mega=np.concatenate(mega_parts),
        seed=int(seed),
        tier=tier,
    )


def _ticket_value(ticket_or_candidate: Ticket | Candidate) -> Ticket:
    return (
        ticket_or_candidate.ticket
        if isinstance(ticket_or_candidate, Candidate)
        else ticket_or_candidate
    )


def _wilson_half_width(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return float("inf")
    proportion = successes / total
    denominator = 1.0 + z * z / total
    return (
        z
        * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total))
        / denominator
    )


def estimate_bundle_metrics(
    tickets: Sequence[Ticket | Candidate],
    model: FittedModel,
    *,
    seed: int,
    min_simulations: int = 10_000,
    max_simulations: int = 100_000,
    batch_size: int = 5_000,
    confidence_tolerance: float = 0.01,
    confidence_level: float = 0.95,
    stable_batches_required: int = 1,
    tier: TierName = "balanced",
) -> BundleSimulationMetrics:
    """Adapt simulation count until key bundle estimates meet a CI tolerance."""

    if not tickets:
        raise ValueError("at least one ticket is required")
    if min_simulations <= 0 or max_simulations < min_simulations:
        raise ValueError("invalid simulation limits")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if confidence_tolerance <= 0:
        raise ValueError("confidence_tolerance must be positive")
    if not 0.5 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0.5 and 1")
    if stable_batches_required <= 0:
        raise ValueError("stable_batches_required must be positive")
    normalized = [_ticket_value(ticket) for ticket in tickets]
    ticket_incidence = np.zeros((47, len(normalized)), dtype=np.uint8)
    ticket_mega = np.empty(len(normalized), dtype=np.int16)
    for column, ticket in enumerate(normalized):
        ticket_incidence[np.asarray(ticket.mains) - 1, column] = 1
        ticket_mega[column] = ticket.mega

    mains_probabilities, mega_probabilities = model.tier_probabilities(tier)
    rng = np.random.default_rng(seed)
    histogram = np.zeros(6, dtype=np.int64)
    count_ge_2 = count_ge_3 = count_ge_4 = 0
    count_three_mega = count_four_mega = 0
    total_best = total_best_squared = 0.0
    completed = 0
    stable = False
    stable_batches = 0
    z_score = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    primary_half_width = secondary_half_width = mega_half_width = float("inf")

    while completed < max_simulations:
        current = min(batch_size, max_simulations - completed)
        mains, mega = _sample_draw_arrays(mains_probabilities, mega_probabilities, rng, current)
        draw_incidence = np.zeros((current, 47), dtype=np.uint8)
        rows = np.repeat(np.arange(current), 5)
        draw_incidence[rows, mains.reshape(-1) - 1] = 1
        overlaps = draw_incidence @ ticket_incidence
        best = overlaps.max(axis=1)
        mega_hits = mega[:, None] == ticket_mega[None, :]
        three_mega = np.any((overlaps >= 3) & mega_hits, axis=1)
        four_mega = np.any((overlaps >= 4) & mega_hits, axis=1)

        histogram += np.bincount(best, minlength=6)[:6]
        count_ge_2 += int(np.count_nonzero(best >= 2))
        count_ge_3 += int(np.count_nonzero(best >= 3))
        count_ge_4 += int(np.count_nonzero(best >= 4))
        count_three_mega += int(np.count_nonzero(three_mega))
        count_four_mega += int(np.count_nonzero(four_mega))
        total_best += float(best.sum())
        total_best_squared += float(np.square(best.astype(np.float64)).sum())
        completed += current

        if completed >= min_simulations:
            primary_half_width = _wilson_half_width(count_ge_3, completed, z_score)
            secondary_half_width = _wilson_half_width(count_ge_4, completed, z_score)
            mega_half_width = _wilson_half_width(count_three_mega, completed, z_score)
            within_tolerance = (
                max(primary_half_width, secondary_half_width, mega_half_width)
                <= confidence_tolerance
            )
            stable_batches = stable_batches + 1 if within_tolerance else 0
            stable = stable_batches >= stable_batches_required
            if stable:
                break

    mean = total_best / completed
    variance = max(total_best_squared / completed - mean * mean, 0.0)
    sample_variance = variance * completed / (completed - 1) if completed > 1 else 0.0
    return BundleSimulationMetrics(
        simulation_count=completed,
        stable=stable,
        confidence_level=confidence_level,
        stable_batches=stable_batches,
        confidence_tolerance=confidence_tolerance,
        primary_confidence_half_width=primary_half_width,
        secondary_confidence_half_width=secondary_half_width,
        mega_confidence_half_width=mega_half_width,
        p_ge_2=count_ge_2 / completed,
        p_ge_3=count_ge_3 / completed,
        p_ge_4=count_ge_4 / completed,
        p_3_plus_mega=count_three_mega / completed,
        p_4_plus_mega=count_four_mega / completed,
        mean_best_main_matches=mean,
        population_std_best_main_matches=math.sqrt(variance),
        sample_std_best_main_matches=math.sqrt(sample_variance),
        best_match_histogram=tuple(int(value) for value in histogram),  # type: ignore[arg-type]
    )


__all__ = [
    "PRODUCTION_MINIMUM_CANDIDATES",
    "TIERS",
    "BundleSimulationMetrics",
    "Candidate",
    "CandidatePool",
    "SimulatedDraws",
    "estimate_bundle_metrics",
    "generate_candidate_pool",
    "simulate_future_draws",
]

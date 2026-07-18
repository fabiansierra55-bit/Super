"""Deterministic candidate generation and adaptive future-draw simulation."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from statistics import NormalDist
from typing import overload

import numpy as np
from numpy.typing import NDArray

from .modeling import FittedModel, TierName
from .models import Draw, Ticket

LEGACY_CANDIDATE_POOL_ALGORITHM_VERSION = "deterministic-tiered-weighted-sampling-v1"
LEGACY_CANDIDATE_POOL_DIGEST_VERSION = "candidate-pool-v1"
CANDIDATE_POOL_ALGORITHM_VERSION = "portable-fixed-point-splitmix64-v2"
CANDIDATE_POOL_DIGEST_VERSION = "candidate-pool-v2"

_UINT64_RANGE = 1 << 64
_UINT64_MASK = _UINT64_RANGE - 1
_PORTABLE_WEIGHT_SCALE = 1_000_000_000

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
    algorithm_version: str = CANDIDATE_POOL_ALGORITHM_VERSION
    digest_version: str = CANDIDATE_POOL_DIGEST_VERSION
    sampling_weights_sha256: str | None = None
    previous_mains: tuple[int, ...] = ()

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
        if self.digest_version == LEGACY_CANDIDATE_POOL_DIGEST_VERSION:
            header = f"{self.digest_version}|{self.seed}|{self.requested_size}\n"
        else:
            if self.sampling_weights_sha256 is None:
                raise ValueError("portable pool digest requires a sampling-weight hash")
            header = (
                f"{self.digest_version}|{self.algorithm_version}|{self.seed}|"
                f"{self.requested_size}|{','.join(map(str, self.previous_mains))}|"
                f"{self.sampling_weights_sha256}\n"
            )
        digest.update(header.encode())
        for candidate in self.candidates:
            record = (
                f"{candidate.generation_index}|{candidate.tier}|"
                f"{','.join(map(str, candidate.ticket.mains))}|{candidate.ticket.mega}"
            )
            if self.digest_version == LEGACY_CANDIDATE_POOL_DIGEST_VERSION:
                record += f"|{candidate.sampling_log_weight.hex()}"
            record += "\n"
            digest.update(record.encode())
        return digest.hexdigest()


class _PortableRandom:
    """Small, specified integer PRNG for cross-runtime artifact reproduction.

    NumPy's high-level weighted sampling and platform math libraries are not a
    stable serialization format.  SplitMix64 has a compact, public integer
    transition, so the same seed produces the same stream on every supported
    Python and operating system.
    """

    def __init__(self, seed: int) -> None:
        if not 0 <= int(seed) < _UINT64_RANGE:
            raise ValueError("portable random seed must be in 0..2**64-1")
        self._state = int(seed)

    def next_uint64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _UINT64_MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _UINT64_MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _UINT64_MASK
        return (value ^ (value >> 31)) & _UINT64_MASK

    def randbelow(self, stop: int) -> int:
        if stop <= 0 or stop > _UINT64_RANGE:
            raise ValueError("portable random bound must be in 1..2**64")
        limit = _UINT64_RANGE - (_UINT64_RANGE % stop)
        while True:
            value = self.next_uint64()
            if value < limit:
                return value % stop


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


def _integerize_decimal_weights(values: Sequence[Decimal]) -> tuple[int, ...]:
    total = sum(values, start=Decimal(0))
    if total <= 0 or any(value <= 0 for value in values):
        raise ValueError("portable sampling weights must be positive")
    scale = Decimal(_PORTABLE_WEIGHT_SCALE)
    quotas = tuple(value * scale / total for value in values)
    floors = [int(quota.to_integral_value(rounding=ROUND_FLOOR)) for quota in quotas]
    missing = _PORTABLE_WEIGHT_SCALE - sum(floors)
    order = sorted(
        range(len(quotas)),
        key=lambda index: (quotas[index] - floors[index], -index),
        reverse=True,
    )
    for index in order[:missing]:
        floors[index] += 1
    if any(weight <= 0 for weight in floors) or sum(floors) != _PORTABLE_WEIGHT_SCALE:
        raise ValueError("portable probability quantization failed")
    return tuple(floors)


def _portable_component_weights(
    base_values: Sequence[float],
    short_or_stable_values: Sequence[float],
    *,
    tier: TierName,
    aggressive_other_weight: Decimal,
    aggressive_power: Decimal,
    conservative_power: Decimal,
) -> tuple[int, ...]:
    base = tuple(Decimal.from_float(float(value)) for value in base_values)
    other = tuple(Decimal.from_float(float(value)) for value in short_or_stable_values)
    if len(base) != len(other):
        raise ValueError("portable tier probability supports do not match")
    if tier == "balanced":
        values = base
    elif tier == "aggressive":
        base_exponent = (Decimal(1) - aggressive_other_weight) * aggressive_power
        other_exponent = aggressive_other_weight * aggressive_power
        values = tuple(
            (base_value.ln() * base_exponent + other_value.ln() * other_exponent).exp()
            for base_value, other_value in zip(base, other, strict=True)
        )
    elif tier == "conservative":
        values = tuple(
            (
                (Decimal("0.35") * base_value + Decimal("0.65") * other_value).ln()
                * conservative_power
            ).exp()
            for base_value, other_value in zip(base, other, strict=True)
        )
    else:
        raise ValueError(f"unknown tier: {tier}")
    return _integerize_decimal_weights(values)


def _portable_tier_weights(
    model: FittedModel, tier: TierName
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Derive fixed-point tier weights with Python's decimal arithmetic.

    The calculation mirrors ``FittedModel.tier_probabilities`` but starts from
    the exact IEEE-754 values locked in the calibration artifact and avoids
    NumPy/libm.  A fixed context and largest-remainder normalization make the
    resulting integer vectors part of the versioned sampling contract.
    """

    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        mains_other = (
            model.recent_mains_probabilities
            if tier == "aggressive"
            else model.stable_mains_probabilities
        )
        mega_other = (
            model.recent_mega_probabilities
            if tier == "aggressive"
            else model.stable_mega_probabilities
        )
        mains = _portable_component_weights(
            model.mains_probabilities,
            mains_other,
            tier=tier,
            aggressive_other_weight=Decimal("0.62"),
            aggressive_power=Decimal("1.18"),
            conservative_power=Decimal("0.92"),
        )
        mega = _portable_component_weights(
            model.mega_probabilities,
            mega_other,
            tier=tier,
            aggressive_other_weight=Decimal("0.58"),
            aggressive_power=Decimal("1.12"),
            conservative_power=Decimal("0.94"),
        )
    return mains, mega


def _sampling_weights_sha256(
    weights: dict[TierName, tuple[tuple[int, ...], tuple[int, ...]]],
) -> str:
    digest = hashlib.sha256(b"candidate-pool-fixed-point-weights-v1\n")
    for tier in TIERS:
        mains, mega = weights[tier]
        digest.update(f"{tier}|{','.join(map(str, mains))}|{','.join(map(str, mega))}\n".encode())
    return digest.hexdigest()


def _portable_weighted_index(
    weights: Sequence[int],
    rng: _PortableRandom,
    *,
    excluded: frozenset[int] = frozenset(),
) -> int:
    total = sum(weight for index, weight in enumerate(weights) if index not in excluded)
    target = rng.randbelow(total)
    cumulative = 0
    for index, weight in enumerate(weights):
        if index in excluded:
            continue
        cumulative += weight
        if target < cumulative:
            return index
    raise RuntimeError("portable weighted selection did not resolve an index")


def _portable_main_sample(
    weights: Sequence[int], rng: _PortableRandom
) -> tuple[int, int, int, int, int]:
    selected: set[int] = set()
    while len(selected) < 5:
        selected.add(_portable_weighted_index(weights, rng, excluded=frozenset(selected)))
    return tuple(index + 1 for index in sorted(selected))  # type: ignore[return-value]


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


def _generate_candidate_pool_legacy(
    model: FittedModel,
    *,
    size: int = PRODUCTION_MINIMUM_CANDIDATES,
    seed: int,
    previous_draw: Draw | Sequence[int] | None = None,
    enforce_production_minimum: bool = True,
    batch_size: int = 8_192,
) -> CandidatePool:
    """Reproduce the original NumPy-based sampler when its runtime matches.

    Version 1 is retained only for forensic replay.  Its high-level NumPy
    choices and float-bearing digest are intentionally not used for new
    production artifacts because they vary across NumPy/platform combinations.
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

    return CandidatePool(
        tuple(candidates),
        int(seed),
        size,
        algorithm_version=LEGACY_CANDIDATE_POOL_ALGORITHM_VERSION,
        digest_version=LEGACY_CANDIDATE_POOL_DIGEST_VERSION,
    )


def _generate_candidate_pool_portable(
    model: FittedModel,
    *,
    size: int,
    seed: int,
    previous_draw: Draw | Sequence[int] | None,
    enforce_production_minimum: bool,
    batch_size: int,
) -> CandidatePool:
    if size <= 0:
        raise ValueError("candidate-pool size must be positive")
    if enforce_production_minimum and size < PRODUCTION_MINIMUM_CANDIDATES:
        raise ValueError(
            f"production candidate pools require at least {PRODUCTION_MINIMUM_CANDIDATES:,} tickets"
        )
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    prior = _previous_mains(previous_draw)
    rng = _PortableRandom(seed)
    targets = _tier_target_counts(size)
    signatures: set[tuple[tuple[int, ...], int]] = set()
    candidates: list[Candidate] = []
    tier_weights = {tier: _portable_tier_weights(model, tier) for tier in TIERS}
    weights_sha256 = _sampling_weights_sha256(tier_weights)

    for tier in TIERS:
        mains_weights, mega_weights = tier_weights[tier]
        mains_total = sum(mains_weights)
        mega_total = sum(mega_weights)
        accepted = 0
        attempts = 0
        target = targets[tier]
        while accepted < target:
            remaining = target - accepted
            sample_size = min(batch_size, max(256, int(remaining * 1.3)))
            attempts += sample_size
            for _ in range(sample_size):
                mains = _portable_main_sample(mains_weights, rng)
                mega = _portable_weighted_index(mega_weights, rng) + 1
                if tier == "aggressive" and prior and len(set(mains) & prior) > 1:
                    continue
                signature = (mains, mega)
                if signature in signatures:
                    continue
                signatures.add(signature)
                log_weight = float(
                    sum(math.log(mains_weights[value - 1] / mains_total) for value in mains)
                    + math.log(mega_weights[mega - 1] / mega_total)
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

    return CandidatePool(
        tuple(candidates),
        int(seed),
        size,
        sampling_weights_sha256=weights_sha256,
        previous_mains=tuple(sorted(prior)),
    )


def generate_candidate_pool(
    model: FittedModel,
    *,
    size: int = PRODUCTION_MINIMUM_CANDIDATES,
    seed: int,
    previous_draw: Draw | Sequence[int] | None = None,
    enforce_production_minimum: bool = True,
    batch_size: int = 8_192,
    algorithm_version: str = CANDIDATE_POOL_ALGORITHM_VERSION,
) -> CandidatePool:
    """Generate a unique, versioned pool using weighted sampling.

    Version 2 uses only specified uint64 transitions and fixed-point selection
    for the artifact-bearing pool.  Supplying the legacy version is supported
    for forensic replay, but new callers use the portable version by default.
    """

    if algorithm_version == CANDIDATE_POOL_ALGORITHM_VERSION:
        return _generate_candidate_pool_portable(
            model,
            size=size,
            seed=seed,
            previous_draw=previous_draw,
            enforce_production_minimum=enforce_production_minimum,
            batch_size=batch_size,
        )
    if algorithm_version == LEGACY_CANDIDATE_POOL_ALGORITHM_VERSION:
        return _generate_candidate_pool_legacy(
            model,
            size=size,
            seed=seed,
            previous_draw=previous_draw,
            enforce_production_minimum=enforce_production_minimum,
            batch_size=batch_size,
        )
    raise ValueError(f"unsupported candidate-pool algorithm version: {algorithm_version}")


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
    "CANDIDATE_POOL_ALGORITHM_VERSION",
    "CANDIDATE_POOL_DIGEST_VERSION",
    "LEGACY_CANDIDATE_POOL_ALGORITHM_VERSION",
    "LEGACY_CANDIDATE_POOL_DIGEST_VERSION",
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

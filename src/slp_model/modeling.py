"""Adaptive, deterministic probability models for SuperLotto Plus.

The model deliberately makes a modest claim: it is an auditable way to weight
the finite ticket space, not evidence that lottery draws are predictable.  All
forward-selection helpers enforce an explicit history cutoff and train every
fold using only draws that precede its target draw.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from typing import Literal, cast

import numpy as np
from numpy.typing import NDArray

from .models import Draw

WINDOWS: tuple[int, ...] = (60, 90, 120, 180, 240)
ANCHOR_WINDOW = 240
MAIN_SIGMAS: tuple[float, ...] = (1.0, 1.125, 1.15, 1.3)
MEGA_SIGMAS: tuple[float, ...] = (0.9, 1.0, 1.15, 1.3)
HALF_LIVES: tuple[float, ...] = (16.0, 20.0, 24.0, 28.0, 36.0, 45.0, 60.0)

TierName = Literal["aggressive", "balanced", "conservative"]


@dataclass(frozen=True, order=True)
class ComponentParameters:
    """Parameters for one independently fitted number distribution."""

    window: int
    sigma: float
    half_life: float

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError("window must be positive")
        if self.sigma <= 0:
            raise ValueError("sigma must be positive")
        if self.half_life <= 0:
            raise ValueError("half_life must be positive")


@dataclass(frozen=True)
class ModelParameters:
    """Independently selected main-number and Mega parameters."""

    mains: ComponentParameters
    mega: ComponentParameters


@dataclass(frozen=True)
class FittedModel:
    """Serializable fitted distributions and their provenance."""

    parameters: ModelParameters
    mains_probabilities: tuple[float, ...]
    mega_probabilities: tuple[float, ...]
    recent_mains_probabilities: tuple[float, ...]
    recent_mega_probabilities: tuple[float, ...]
    stable_mains_probabilities: tuple[float, ...]
    stable_mega_probabilities: tuple[float, ...]
    positional_medians: tuple[float, ...]
    positional_dispersions: tuple[float, ...]
    history_start_date: date
    history_cutoff_date: date
    history_draw_count: int

    def __post_init__(self) -> None:
        _validate_probability_tuple(self.mains_probabilities, 47, "mains")
        _validate_probability_tuple(self.mega_probabilities, 27, "Mega")
        _validate_probability_tuple(self.recent_mains_probabilities, 47, "recent mains")
        _validate_probability_tuple(self.recent_mega_probabilities, 27, "recent Mega")
        _validate_probability_tuple(self.stable_mains_probabilities, 47, "stable mains")
        _validate_probability_tuple(self.stable_mega_probabilities, 27, "stable Mega")

    def tier_probabilities(self, tier: TierName) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Return genuinely different, normalized distributions for a tier.

        Aggressive blends toward the short model and sharpens conviction.
        Conservative blends toward the long-run model and gently flattens it.
        Balanced is exactly the selected forward-optimal model.
        """

        base_m = np.asarray(self.mains_probabilities, dtype=np.float64)
        base_g = np.asarray(self.mega_probabilities, dtype=np.float64)
        if tier == "balanced":
            return base_m.copy(), base_g.copy()
        if tier == "aggressive":
            mains = _geometric_blend(base_m, np.asarray(self.recent_mains_probabilities), 0.62)
            mega = _geometric_blend(base_g, np.asarray(self.recent_mega_probabilities), 0.58)
            return _power_normalize(mains, 1.18), _power_normalize(mega, 1.12)
        if tier == "conservative":
            mains = 0.35 * base_m + 0.65 * np.asarray(self.stable_mains_probabilities)
            mega = 0.35 * base_g + 0.65 * np.asarray(self.stable_mega_probabilities)
            return _power_normalize(mains, 0.92), _power_normalize(mega, 0.94)
        raise ValueError(f"unknown tier: {tier}")


@dataclass(frozen=True)
class ForwardCandidateScore:
    component: Literal["mains", "mega"]
    parameters: ComponentParameters
    forward_bundle_score: float
    primary_hit_rate: float
    secondary_hit_rate: float
    heldout_log_likelihood: float
    stable: bool
    folds: int


@dataclass(frozen=True)
class AdaptiveSelection:
    model: FittedModel
    mains_scores: tuple[ForwardCandidateScore, ...]
    mega_scores: tuple[ForwardCandidateScore, ...]
    joint_forward_bundle_score: float
    fold_training_cutoffs: tuple[date, ...]


def _validate_probability_tuple(probabilities: tuple[float, ...], expected: int, name: str) -> None:
    if len(probabilities) != expected:
        raise ValueError(f"{name} distribution must have {expected} entries")
    values = np.asarray(probabilities, dtype=np.float64)
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError(f"{name} probabilities must be finite and positive")
    if not math.isclose(float(values.sum()), 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"{name} probabilities must sum to one")


def _ordered_history(draws: Iterable[Draw], cutoff_date: date | None = None) -> list[Draw]:
    history = sorted(
        (draw for draw in draws if cutoff_date is None or draw.draw_date <= cutoff_date),
        key=lambda draw: draw.draw_date,
    )
    if not history:
        raise ValueError("at least one draw is required")
    dates = [draw.draw_date for draw in history]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate draw dates are not valid model input")
    return history


def _smoothed_distribution(
    draws: Sequence[Draw],
    *,
    support: int,
    sigma: float,
    half_life: float,
    component: Literal["mains", "mega"],
) -> NDArray[np.float64]:
    if not draws:
        raise ValueError("cannot fit an empty history")
    counts = np.zeros(support, dtype=np.float64)
    decay = math.log(2.0) / half_life
    for age, draw in enumerate(reversed(draws)):
        weight = math.exp(-decay * age)
        values = draw.mains if component == "mains" else (draw.mega,)
        for number in values:
            counts[number - 1] += weight

    positions = np.arange(1, support + 1, dtype=np.float64)
    distances = positions[:, None] - positions[None, :]
    kernel = np.exp(-0.5 * np.square(distances / sigma))
    # Boundary observations must not lose mass merely because their Gaussian
    # tail extends outside the game's finite support.
    kernel /= kernel.sum(axis=0, keepdims=True)
    smoothed = kernel @ counts
    smoothed = np.maximum(smoothed, np.finfo(np.float64).tiny)
    return np.asarray(smoothed / smoothed.sum(), dtype=np.float64)


def _position_summary(
    draws: Sequence[Draw],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    values = np.asarray([draw.mains for draw in draws], dtype=np.float64)
    medians = np.median(values, axis=0)
    median_deviation = np.median(np.abs(values - medians), axis=0) * 1.4826
    standard_deviation = np.std(values, axis=0)
    dispersions = np.maximum(np.maximum(median_deviation, standard_deviation), 1.0)
    return tuple(float(value) for value in medians), tuple(float(value) for value in dispersions)


def fit_model(
    draws: Iterable[Draw],
    parameters: ModelParameters,
    *,
    cutoff_date: date | None = None,
) -> FittedModel:
    """Fit independent Gaussian-smoothed recency models.

    ``cutoff_date`` is inclusive and is persisted on the returned model.  It is
    intentionally applied before either rolling window is sliced.
    """

    history = _ordered_history(draws, cutoff_date)
    main_history = history[-parameters.mains.window :]
    mega_history = history[-parameters.mega.window :]
    mains = _smoothed_distribution(
        main_history,
        support=47,
        sigma=parameters.mains.sigma,
        half_life=parameters.mains.half_life,
        component="mains",
    )
    mega = _smoothed_distribution(
        mega_history,
        support=27,
        sigma=parameters.mega.sigma,
        half_life=parameters.mega.half_life,
        component="mega",
    )

    recent_history = history[-min(60, len(history)) :]
    stable_history = history[-min(240, len(history)) :]
    recent_mains = _smoothed_distribution(
        recent_history,
        support=47,
        sigma=1.0,
        half_life=min(parameters.mains.half_life, 20.0),
        component="mains",
    )
    recent_mega = _smoothed_distribution(
        recent_history,
        support=27,
        sigma=0.9,
        half_life=min(parameters.mega.half_life, 20.0),
        component="mega",
    )
    stable_mains = _smoothed_distribution(
        stable_history,
        support=47,
        sigma=1.3,
        half_life=max(parameters.mains.half_life, 60.0),
        component="mains",
    )
    stable_mega = _smoothed_distribution(
        stable_history,
        support=27,
        sigma=1.3,
        half_life=max(parameters.mega.half_life, 60.0),
        component="mega",
    )
    medians, dispersions = _position_summary(stable_history)
    return FittedModel(
        parameters=parameters,
        mains_probabilities=tuple(float(value) for value in mains),
        mega_probabilities=tuple(float(value) for value in mega),
        recent_mains_probabilities=tuple(float(value) for value in recent_mains),
        recent_mega_probabilities=tuple(float(value) for value in recent_mega),
        stable_mains_probabilities=tuple(float(value) for value in stable_mains),
        stable_mega_probabilities=tuple(float(value) for value in stable_mega),
        positional_medians=medians,
        positional_dispersions=dispersions,
        history_start_date=history[0].draw_date,
        history_cutoff_date=history[-1].draw_date,
        history_draw_count=len(history),
    )


def _geometric_blend(
    base: NDArray[np.float64], other: NDArray[np.float64], other_weight: float
) -> NDArray[np.float64]:
    tiny = np.finfo(np.float64).tiny
    blended = np.exp(
        (1.0 - other_weight) * np.log(np.maximum(base, tiny))
        + other_weight * np.log(np.maximum(other, tiny))
    )
    return cast(NDArray[np.float64], blended / blended.sum())


def _power_normalize(probabilities: NDArray[np.float64], power: float) -> NDArray[np.float64]:
    values = np.power(np.maximum(probabilities, np.finfo(np.float64).tiny), power)
    return cast(NDArray[np.float64], values / values.sum())


def _stable_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


def _weighted_main_sample(
    probabilities: NDArray[np.float64], rng: np.random.Generator
) -> tuple[int, int, int, int, int]:
    # Exponential ranks implement deterministic weighted sampling without
    # replacement while avoiding 47 Python-level choices per ticket.
    keys = -np.log(np.maximum(rng.random(47), np.finfo(float).tiny)) / probabilities
    selected = np.argpartition(keys, 4)[:5] + 1
    return tuple(int(value) for value in np.sort(selected))  # type: ignore[return-value]


def _forward_main_bundle(
    probabilities: NDArray[np.float64],
    *,
    seed: int,
    size: int,
) -> tuple[tuple[int, int, int, int, int], ...]:
    rng = np.random.default_rng(seed)
    selected: list[tuple[int, int, int, int, int]] = []
    pair_counts: Counter[tuple[int, int]] = Counter()
    triple_counts: Counter[tuple[int, int, int]] = Counter()
    attempts = 0
    while len(selected) < size and attempts < size * 2_000:
        attempts += 1
        mains = _weighted_main_sample(probabilities, rng)
        if mains in selected:
            continue
        if any(len(set(mains) & set(other)) > 3 for other in selected):
            continue
        pairs = tuple(combinations(mains, 2))
        triples = tuple(combinations(mains, 3))
        if any(pair_counts[pair] >= 2 for pair in pairs):
            continue
        if any(triple_counts[triple] >= 1 for triple in triples):
            continue
        selected.append(mains)
        pair_counts.update(pairs)
        triple_counts.update(triples)
    if len(selected) != size:
        raise RuntimeError("could not construct a constrained forward bundle")
    return tuple(selected)


def _forward_mega_bundle(
    probabilities: NDArray[np.float64],
    *,
    seed: int,
    size: int,
    hard_cap: int = 5,
) -> tuple[int, ...]:
    rng = np.random.default_rng(seed)
    counts = np.zeros(27, dtype=np.int16)
    result: list[int] = []
    while len(result) < size:
        eligible = counts < hard_cap
        adjusted = probabilities * eligible
        if adjusted.sum() <= 0:
            raise RuntimeError("Mega cap makes requested bundle impossible")
        adjusted /= adjusted.sum()
        number = int(rng.choice(np.arange(1, 28), p=adjusted))
        counts[number - 1] += 1
        result.append(number)
    return tuple(result)


@dataclass(frozen=True)
class _CompleteBundleOutcome:
    """One cutoff-safe bundle's realized performance on its next draw."""

    objective: float
    any_three_plus: bool
    any_four_plus: bool
    any_three_plus_mega: bool
    any_four_plus_mega: bool


def _complete_bundle_outcome(
    mains_bundle: Sequence[tuple[int, int, int, int, int]],
    mega_bundle: Sequence[int],
    target: Draw,
) -> _CompleteBundleOutcome:
    """Score complete tickets against one strictly forward target draw.

    The event-level objective mirrors production priorities: 3+ mains is the
    dominant term, followed by 4+ mains and Mega-bearing 3+/4+ outcomes.  Two
    very small bounded terms make sparse walk-forward comparisons deterministic
    without displacing any of those event outcomes.
    """

    if not mains_bundle or len(mains_bundle) != len(mega_bundle):
        raise ValueError("forward mains and Mega bundles must have equal positive size")
    target_mains = set(target.mains)
    overlaps = tuple(len(target_mains.intersection(mains)) for mains in mains_bundle)
    any_three_plus = any(value >= 3 for value in overlaps)
    any_four_plus = any(value >= 4 for value in overlaps)
    mega_overlaps = tuple(
        overlap for overlap, mega in zip(overlaps, mega_bundle, strict=True) if mega == target.mega
    )
    any_three_plus_mega = any(value >= 3 for value in mega_overlaps)
    any_four_plus_mega = any(value >= 4 for value in mega_overlaps)

    # These bounded tie-breakers are still complete-ticket forward outcomes.
    # Their combined maximum (0.003) is far below the smallest event reward.
    best_overlap = max(overlaps)
    best_mega_overlap = max(mega_overlaps, default=0)
    objective = (
        float(any_three_plus)
        + 0.15 * float(any_four_plus)
        + 0.05 * float(any_three_plus_mega)
        + 0.025 * float(any_four_plus_mega)
        + 0.002 * best_overlap / 5.0
        + 0.001 * best_mega_overlap / 5.0
    )
    return _CompleteBundleOutcome(
        objective=objective,
        any_three_plus=any_three_plus,
        any_four_plus=any_four_plus,
        any_three_plus_mega=any_three_plus_mega,
        any_four_plus_mega=any_four_plus_mega,
    )


@dataclass(frozen=True)
class _ForwardFold:
    """Target draw and counterpart bundles derived solely from its prefix."""

    training: tuple[Draw, ...]
    target: Draw
    baseline_mains: tuple[tuple[int, int, int, int, int], ...]
    baseline_mega: tuple[int, ...]


def _forward_folds(
    history: Sequence[Draw],
    fold_indices: Sequence[int],
    *,
    bundle_size: int,
    random_seed: int,
) -> tuple[_ForwardFold, ...]:
    """Build common, cutoff-safe folds used by every component candidate.

    The independently tuned component is paired with a conservative 240-draw
    (or all-prefix) baseline for the other component.  Keeping these bundles
    fixed within a fold prevents counterpart noise from favoring one candidate.
    """

    result: list[_ForwardFold] = []
    for fold_index in fold_indices:
        training = tuple(history[:fold_index])
        target = history[fold_index]
        stable_training = training[-min(ANCHOR_WINDOW, len(training)) :]
        baseline_mains_probabilities = _smoothed_distribution(
            stable_training,
            support=47,
            sigma=1.3,
            half_life=60.0,
            component="mains",
        )
        baseline_mega_probabilities = _smoothed_distribution(
            stable_training,
            support=27,
            sigma=1.3,
            half_life=60.0,
            component="mega",
        )
        seed = _stable_seed(random_seed, "forward-baseline", target.draw_date.isoformat())
        result.append(
            _ForwardFold(
                training=training,
                target=target,
                baseline_mains=_forward_main_bundle(
                    baseline_mains_probabilities,
                    seed=seed,
                    size=bundle_size,
                ),
                baseline_mega=_forward_mega_bundle(
                    baseline_mega_probabilities,
                    seed=seed ^ 0x9E3779B97F4A7C15,
                    size=bundle_size,
                ),
            )
        )
    return tuple(result)


def _candidate_grid(
    windows: Sequence[int], sigmas: Sequence[float], half_lives: Sequence[float]
) -> tuple[ComponentParameters, ...]:
    return tuple(
        ComponentParameters(int(window), float(sigma), float(half_life))
        for window in windows
        for sigma in sigmas
        for half_life in half_lives
    )


def _select_with_anchor_rule(
    scores: Sequence[ForwardCandidateScore], *, anchor_min_improvement: float
) -> ForwardCandidateScore:
    usable = [score for score in scores if score.stable]
    if not usable:
        usable = list(scores)
    if not usable:
        raise ValueError("no hyperparameter candidate has enough forward folds")

    def rank(
        score: ForwardCandidateScore,
    ) -> tuple[float, float, float, int, float, float]:
        # Likelihood is deliberately absent: it is a stability gate, never the
        # optimization target or a ranking tie-breaker.
        return (
            score.forward_bundle_score,
            score.primary_hit_rate,
            score.secondary_hit_rate,
            -score.parameters.window,
            -score.parameters.sigma,
            -score.parameters.half_life,
        )

    non_anchor = [score for score in usable if score.parameters.window != ANCHOR_WINDOW]
    anchor = [score for score in usable if score.parameters.window == ANCHOR_WINDOW]
    if not non_anchor:
        return max(usable, key=rank)
    best_non_anchor = max(non_anchor, key=rank)
    if anchor:
        best_anchor = max(anchor, key=rank)
        if (
            best_anchor.forward_bundle_score
            >= best_non_anchor.forward_bundle_score + anchor_min_improvement
        ):
            return best_anchor
    return best_non_anchor


def select_hyperparameters(
    draws: Iterable[Draw],
    *,
    cutoff_date: date | None = None,
    windows: Sequence[int] = WINDOWS,
    main_sigmas: Sequence[float] = MAIN_SIGMAS,
    mega_sigmas: Sequence[float] = MEGA_SIGMAS,
    half_lives: Sequence[float] = HALF_LIVES,
    validation_draws: int = 20,
    forward_bundle_size: int = 30,
    random_seed: int = 0,
    anchor_min_improvement: float = 0.01,
    likelihood_stability_margin: float = 1.5,
) -> AdaptiveSelection:
    """Select independent parameters using complete-ticket forward performance.

    Every validation ticket bundle and its fixed counterpart bundle are
    generated from the preceding prefix.  A mains candidate is paired with a
    cutoff-safe stable Mega bundle; a Mega candidate is paired with a
    cutoff-safe stable mains bundle.  Thus both independently tuned components
    are selected by complete-ticket 3+/4+/Mega outcomes.  Held-out likelihood
    is solely a broad stability gate.  The optional 240-draw anchor wins only
    when its complete-bundle score improves on the best shorter window by
    ``anchor_min_improvement``.
    """

    history = _ordered_history(draws, cutoff_date)
    if validation_draws < 2:
        raise ValueError("validation_draws must be at least two")
    requested_windows = tuple(sorted({int(value) for value in windows if value > 0}))
    if not requested_windows:
        raise ValueError("at least one candidate window is required")
    available_windows = tuple(value for value in requested_windows if len(history) >= value + 2)
    if not available_windows:
        raise ValueError("insufficient history for walk-forward selection")

    # Every viable candidate is evaluated on exactly the same forward targets.
    # Otherwise short windows can receive more (and earlier) folds than long
    # windows, making their mean objectives incomparable.
    start = max(max(available_windows), len(history) - validation_draws)
    fold_indices = tuple(range(start, len(history)))
    if len(fold_indices) < 2:
        raise ValueError("insufficient history for at least two common forward folds")
    folds = _forward_folds(
        history,
        fold_indices,
        bundle_size=forward_bundle_size,
        random_seed=random_seed,
    )
    main_scores: list[ForwardCandidateScore] = []
    mega_scores: list[ForwardCandidateScore] = []

    for component, sigmas, destination in (
        ("mains", main_sigmas, main_scores),
        ("mega", mega_sigmas, mega_scores),
    ):
        uniform_log_likelihood = -math.log(47 if component == "mains" else 27)
        for component_parameters in _candidate_grid(available_windows, sigmas, half_lives):
            bundle_scores: list[float] = []
            primary_hits: list[float] = []
            secondary_hits: list[float] = []
            log_likelihoods: list[float] = []
            for fold in folds:
                training_window = fold.training[-component_parameters.window :]
                probabilities = _smoothed_distribution(
                    training_window,
                    support=47 if component == "mains" else 27,
                    sigma=component_parameters.sigma,
                    half_life=component_parameters.half_life,
                    component=component,  # type: ignore[arg-type]
                )
                # Common random numbers reduce Monte Carlo ranking noise: the
                # candidate parameters affect weights, never the random stream.
                seed = _stable_seed(
                    random_seed,
                    "forward-candidate",
                    component,
                    fold.target.draw_date.isoformat(),
                )
                if component == "mains":
                    main_bundle = _forward_main_bundle(
                        probabilities, seed=seed, size=forward_bundle_size
                    )
                    outcome = _complete_bundle_outcome(
                        main_bundle,
                        fold.baseline_mega,
                        fold.target,
                    )
                    log_likelihood = float(
                        np.mean([math.log(probabilities[value - 1]) for value in fold.target.mains])
                    )
                else:
                    mega_bundle_fold = _forward_mega_bundle(
                        probabilities, seed=seed, size=forward_bundle_size
                    )
                    outcome = _complete_bundle_outcome(
                        fold.baseline_mains,
                        mega_bundle_fold,
                        fold.target,
                    )
                    log_likelihood = math.log(probabilities[fold.target.mega - 1])
                bundle_scores.append(outcome.objective)
                primary_hits.append(float(outcome.any_three_plus))
                secondary_hits.append(float(outcome.any_four_plus))
                log_likelihoods.append(log_likelihood)

            mean_log_likelihood = float(np.mean(log_likelihoods))
            destination.append(
                ForwardCandidateScore(
                    component=component,  # type: ignore[arg-type]
                    parameters=component_parameters,
                    forward_bundle_score=float(np.mean(bundle_scores)),
                    primary_hit_rate=float(np.mean(primary_hits)),
                    secondary_hit_rate=float(np.mean(secondary_hits)),
                    heldout_log_likelihood=mean_log_likelihood,
                    stable=(
                        math.isfinite(mean_log_likelihood)
                        and mean_log_likelihood
                        >= uniform_log_likelihood - likelihood_stability_margin
                    ),
                    folds=len(bundle_scores),
                )
            )

    selected_mains = _select_with_anchor_rule(
        main_scores, anchor_min_improvement=anchor_min_improvement
    )
    selected_mega = _select_with_anchor_rule(
        mega_scores, anchor_min_improvement=anchor_min_improvement
    )
    selected_parameters = ModelParameters(selected_mains.parameters, selected_mega.parameters)
    model = fit_model(history, selected_parameters)

    # Report the selected pair on the same targets as an audit metric.  This is
    # not a combinatorial second tuning pass; independent selection above has
    # already used full tickets with fixed prefix-only counterpart bundles.
    joint_scores: list[float] = []
    joint_cutoffs: list[date] = []
    for fold in folds:
        fold_model = fit_model(fold.training, selected_parameters)
        main_probabilities = np.asarray(fold_model.mains_probabilities)
        mega_probabilities = np.asarray(fold_model.mega_probabilities)
        seed = _stable_seed(random_seed, "joint", fold.target.draw_date.isoformat())
        mains_bundle = _forward_main_bundle(main_probabilities, seed=seed, size=forward_bundle_size)
        mega_bundle = _forward_mega_bundle(
            mega_probabilities, seed=seed ^ 0x9E3779B97F4A7C15, size=forward_bundle_size
        )
        outcome = _complete_bundle_outcome(mains_bundle, mega_bundle, fold.target)
        joint_scores.append(outcome.objective)
        joint_cutoffs.append(fold.training[-1].draw_date)

    return AdaptiveSelection(
        model=model,
        mains_scores=tuple(main_scores),
        mega_scores=tuple(mega_scores),
        joint_forward_bundle_score=float(np.mean(joint_scores)),
        fold_training_cutoffs=tuple(joint_cutoffs),
    )


__all__ = [
    "ANCHOR_WINDOW",
    "HALF_LIVES",
    "MAIN_SIGMAS",
    "MEGA_SIGMAS",
    "WINDOWS",
    "AdaptiveSelection",
    "ComponentParameters",
    "FittedModel",
    "ForwardCandidateScore",
    "ModelParameters",
    "TierName",
    "fit_model",
    "select_hyperparameters",
]

"""End-to-end deterministic candidate generation, optimization, and bundle assembly."""

from __future__ import annotations

import hashlib
import platform
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from itertools import combinations
from typing import Literal

from .calibration import CalibrationArtifact
from .config import AppConfig
from .constraints import validate_bundle
from .dates import next_draw_date
from .exceptions import SimulationStabilityError
from .fair_odds import (
    OPTIMAL_30_LINE_3_PLUS_MEGA_COUNT,
    OPTIMAL_30_LINE_GE3_COUNT,
    OPTIMAL_30_LINE_GE4_COUNT,
    exact_coverage_regressions,
    exact_uniform_metrics,
    fair_challenger_decision,
    fair_uniform_model,
)
from .models import (
    BundleMetadata,
    ExactUniformMetrics,
    FairCoverageChallengerEvidence,
    LockedBundle,
    LockedLine,
    OptimizerSettings,
    SimulationSummary,
    Ticket,
    VerifiedDraw,
)
from .objectives import effective_event_weights
from .optimizer import (
    ObjectiveWeights,
    OptimizerConstraints,
    measure_bundle_marginals,
    optimize_bundle,
    optimize_fair_coverage,
)
from .recenter import PositionalProfile, recenter_bundle
from .simulation import (
    CANDIDATE_POOL_ALGORITHM_VERSION,
    BundleSimulationMetrics,
    estimate_bundle_metrics,
    generate_candidate_pool,
)
from .storage import canonical_json_bytes, sha256_bytes


def deterministic_seed(
    *, history_snapshot_sha256: str, intended_draw_date: date, model_version: str
) -> int:
    identity = (
        f"{history_snapshot_sha256}|{intended_draw_date.isoformat()}|{model_version}"
    ).encode()
    return int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=False)


def _runtime_environment() -> dict[str, str]:
    packages: dict[str, str] = {}
    for package in ("superlotto-model", "numpy", "pydantic"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "not-installed"
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": sys.platform,
        **packages,
    }


def _anti_cannibalization(tickets: Sequence[Ticket]) -> float:
    mains = Counter(number for ticket in tickets for number in ticket.mains)
    pairs = Counter(pair for ticket in tickets for pair in combinations(ticket.mains, 2))
    triples = Counter(triple for ticket in tickets for triple in combinations(ticket.mains, 3))
    mega = Counter(ticket.mega for ticket in tickets)
    size = max(len(tickets), 1)
    return (
        0.35 * sum(max(count - 1, 0) ** 2 for count in mains.values()) / (5 * size)
        + 0.35 * sum(max(count - 1, 0) ** 2 for count in pairs.values()) / (10 * size)
        + 0.20 * sum(max(count - 1, 0) ** 2 for count in triples.values()) / (10 * size)
        + 0.10 * sum(max(count - 4, 0) ** 2 for count in mega.values()) / size
    )


def _objective_from_metrics(
    metrics: BundleSimulationMetrics,
    tickets: Sequence[Ticket],
    config: AppConfig,
) -> float:
    weights = config.objective
    p_ge_3, p_ge_4, three_plus_mega, four_plus_mega = effective_event_weights(
        weights.mode,
        (
            weights.p_ge_3_weight,
            weights.p_ge_4_weight,
            weights.three_plus_mega_weight,
            weights.four_plus_weight,
        ),
    )
    return (
        p_ge_3 * metrics.p_ge_3
        + p_ge_4 * metrics.p_ge_4
        + three_plus_mega * metrics.p_3_plus_mega
        + four_plus_mega * metrics.p_4_plus_mega
        - weights.anti_cannibalization_weight * _anti_cannibalization(tickets)
    )


def _assert_correction_non_regression(
    candidate: ExactUniformMetrics,
    incumbent: ExactUniformMetrics | None,
) -> None:
    if incumbent is None:
        return
    regressions = exact_coverage_regressions(candidate, incumbent)
    if regressions:
        raise ValueError(
            "correction candidate regresses active incumbent exact coverage: "
            + ", ".join(regressions)
        )


def _simulation_summary(
    metrics: BundleSimulationMetrics,
    *,
    candidate_pool_size: int,
    fair_uniform_exact: ExactUniformMetrics | None = None,
) -> SimulationSummary:
    maximum_half_width = max(
        metrics.primary_confidence_half_width,
        metrics.secondary_confidence_half_width,
        metrics.mega_confidence_half_width,
    )
    return SimulationSummary(
        simulation_count=metrics.simulation_count,
        candidate_pool_size=candidate_pool_size,
        confidence_level=metrics.confidence_level,
        maximum_confidence_half_width=maximum_half_width,
        stable=metrics.stable,
        stable_batches=metrics.stable_batches,
        p_any_ge_3_mains=metrics.p_ge_3,
        p_any_ge_4_mains=metrics.p_ge_4,
        p_any_3_plus_mega=metrics.p_3_plus_mega,
        p_any_4_plus=metrics.p_4_plus_mega,
        p_any_4_plus_mega=metrics.p_4_plus_mega,
        mean_best_main_matches=metrics.mean_best_main_matches,
        fair_uniform_exact=fair_uniform_exact,
    )


def build_locked_bundle(
    history: Sequence[VerifiedDraw],
    *,
    history_snapshot_sha256: str,
    calibration: CalibrationArtifact,
    config: AppConfig,
    intended_draw_date: date | None = None,
    random_seed: int | None = None,
    generated_timestamp_utc: datetime | None = None,
    lock_version: int = 1,
    supersedes_bundle_id: str | None = None,
    correction_reason: str | None = None,
    apply_recentering: bool = True,
    incumbent_bundle: LockedBundle | None = None,
) -> LockedBundle:
    if not history:
        raise ValueError("verified history is required")
    ordered = sorted(history, key=lambda draw: draw.draw_date)
    if len({draw.draw_date for draw in ordered}) != len(ordered):
        raise ValueError("history contains duplicate draw dates")
    cutoff = ordered[-1].draw_date
    target = intended_draw_date or next_draw_date(cutoff)
    if target != next_draw_date(cutoff):
        raise ValueError("production bundle target must be the next scheduled draw")
    if lock_version == 1:
        if supersedes_bundle_id is not None or correction_reason is not None:
            raise ValueError("initial bundle generation cannot include correction metadata")
    elif (
        supersedes_bundle_id is None
        or correction_reason is None
        or len(correction_reason.strip()) < 8
    ):
        raise ValueError("corrected bundle generation requires a parent ID and reason")
    if calibration.history_snapshot_sha256 != history_snapshot_sha256:
        raise ValueError("calibration was not fitted to the selected history snapshot")
    if calibration.history_cutoff_date != cutoff:
        raise ValueError("calibration history cutoff does not match verified history")

    seed = random_seed
    if seed is None:
        seed = deterministic_seed(
            history_snapshot_sha256=history_snapshot_sha256,
            intended_draw_date=target,
            model_version=config.model_version,
        )
    generated = (generated_timestamp_utc or datetime.now(UTC)).astimezone(UTC)
    model = calibration.restore_model()
    pool = generate_candidate_pool(
        model,
        size=config.simulation.candidate_pool_size,
        seed=seed,
        previous_draw=ordered[-1],
        enforce_production_minimum=True,
    )
    candidate_pool_sha256 = pool.content_sha256()
    constraints = OptimizerConstraints(
        bundle_size=config.bundle.size,
        tickets_per_tier=config.bundle.aggressive_count,
        max_main_overlap=config.bundle.max_main_overlap,
        min_hamming_distance=config.bundle.min_hamming_distance,
        pair_cap=config.bundle.pair_repeat_cap,
        triple_cap=config.bundle.triple_repeat_cap,
        mega_soft_cap=config.bundle.mega_soft_cap,
        mega_hard_cap=config.bundle.mega_hard_cap,
    )
    if not (
        config.bundle.aggressive_count
        == config.bundle.balanced_count
        == config.bundle.conservative_count
    ):
        raise ValueError("the current optimizer requires equal production tier quotas")
    weights = ObjectiveWeights(
        mode=config.objective.mode,
        p_ge_3=config.objective.p_ge_3_weight,
        p_ge_4=config.objective.p_ge_4_weight,
        three_plus_mega=config.objective.three_plus_mega_weight,
        four_plus_mega=config.objective.four_plus_weight,
        anti_cannibalization=config.objective.anti_cannibalization_weight,
        mega_repeat_penalty=config.objective.mega_repeat_penalty,
        aggressive_secondary_multiplier=(config.objective.aggressive_secondary_multiplier),
    )
    model_optimizer_seed = seed ^ 0x9E3779B97F4A7C15
    optimized = optimize_bundle(
        pool,
        model,
        seed=model_optimizer_seed,
        previous_draw=ordered[-1],
        constraints=constraints,
        weights=weights,
        optimization_simulations=config.simulation.optimization_draws,
        estimate_final_metrics=True,
        metric_min_simulations=config.simulation.initial_draws,
        metric_max_simulations=config.simulation.maximum_draws,
        metric_batch_size=config.simulation.batch_draws,
        confidence_tolerance=config.simulation.confidence_half_width_tolerance,
        metric_confidence_level=config.simulation.confidence_level,
        metric_stable_batches_required=config.simulation.stable_batches_required,
    )
    if optimized.adaptive_metrics is None:
        raise AssertionError("optimizer omitted final adaptive metrics")

    adaptive_tickets = tuple(candidate.ticket for candidate in optimized.candidates)
    adaptive_fair = exact_uniform_metrics(adaptive_tickets)
    incumbent_fair = (
        exact_uniform_metrics(incumbent_bundle.lines) if incumbent_bundle is not None else None
    )
    fair_evidence: FairCoverageChallengerEvidence | None = None
    fair_selected = False
    chosen = optimized
    metrics = optimized.adaptive_metrics
    if config.fair_coverage.enabled:
        fair_constraints = OptimizerConstraints(
            bundle_size=config.bundle.size,
            tickets_per_tier=config.bundle.aggressive_count,
            max_main_overlap=config.fair_coverage.max_main_overlap,
            min_hamming_distance=config.bundle.min_hamming_distance,
            pair_cap=config.fair_coverage.pair_repeat_cap,
            triple_cap=config.bundle.triple_repeat_cap,
            mega_soft_cap=config.fair_coverage.mega_soft_cap,
            mega_hard_cap=config.fair_coverage.mega_hard_cap,
        )
        fair_weights = ObjectiveWeights(
            mode=config.objective.mode,
            p_ge_3=config.objective.p_ge_3_weight,
            p_ge_4=config.objective.p_ge_4_weight,
            three_plus_mega=config.objective.three_plus_mega_weight,
            four_plus_mega=config.objective.four_plus_weight,
            anti_cannibalization=config.fair_coverage.anti_cannibalization_weight,
            mega_repeat_penalty=config.objective.mega_repeat_penalty,
            aggressive_secondary_multiplier=(config.objective.aggressive_secondary_multiplier),
        )
        fair_optimized = optimize_fair_coverage(
            pool,
            fair_uniform_model(model),
            seed=seed ^ 0xA5A5A5A55A5A5A5A,
            previous_draw=ordered[-1],
            constraints=fair_constraints,
            weights=fair_weights,
            marginal_simulations=config.simulation.optimization_draws,
        )
        fair_tickets = tuple(candidate.ticket for candidate in fair_optimized.candidates)
        challenger_fair = exact_uniform_metrics(fair_tickets)
        promotion_decision = fair_challenger_decision(
            challenger_fair,
            [adaptive_fair],
            minimum_relative_improvement=(config.fair_coverage.minimum_relative_improvement),
            require_30_line_optimum=config.fair_coverage.require_global_optimum,
            non_regression_references=([incumbent_fair] if incumbent_fair is not None else []),
        )
        challenger_model_metrics = estimate_bundle_metrics(
            fair_tickets,
            model,
            seed=model_optimizer_seed ^ 0xD1B54A32D192ED03,
            min_simulations=config.simulation.initial_draws,
            max_simulations=config.simulation.maximum_draws,
            batch_size=config.simulation.batch_draws,
            confidence_tolerance=config.simulation.confidence_half_width_tolerance,
            confidence_level=config.simulation.confidence_level,
            stable_batches_required=config.simulation.stable_batches_required,
        )
        fair_selected = promotion_decision.selected
        if fair_selected:
            chosen = fair_optimized
            metrics = challenger_model_metrics
        model_summary = _simulation_summary(
            optimized.adaptive_metrics,
            candidate_pool_size=config.simulation.candidate_pool_size,
            fair_uniform_exact=adaptive_fair,
        )
        challenger_summary = _simulation_summary(
            challenger_model_metrics,
            candidate_pool_size=config.simulation.candidate_pool_size,
            fair_uniform_exact=challenger_fair,
        )
        fair_evidence = FairCoverageChallengerEvidence(
            evidence_version=3,
            selection_policy=config.fair_coverage.selection_policy,
            model_skill_status=config.fair_coverage.model_skill_status,
            selected=fair_selected,
            global_optimum_certified=(
                challenger_fair.covered_ge_3_mains_count == OPTIMAL_30_LINE_GE3_COUNT
                and challenger_fair.covered_ge_4_mains_count == OPTIMAL_30_LINE_GE4_COUNT
                and challenger_fair.covered_3_plus_mega_count == OPTIMAL_30_LINE_3_PLUS_MEGA_COUNT
                and challenger_fair.covered_jackpot_count == 30
            ),
            selection_reason=(
                f"{promotion_decision.reason}; selected under explicit fair-null robustness "
                "because fitted-model predictive skill remains unvalidated"
                if fair_selected
                else f"{promotion_decision.reason}; model candidate retained after explicit "
                "fair-null robustness evaluation"
            ),
            minimum_relative_improvement=(config.fair_coverage.minimum_relative_improvement),
            relative_primary_improvement=promotion_decision.relative_primary_improvement,
            model_optimized_candidate=adaptive_fair,
            challenger=challenger_fair,
            incumbent=incumbent_fair,
            model_optimized_simulation=model_summary,
            challenger_model_simulation=challenger_summary,
            incumbent_model_simulation=(
                incumbent_bundle.metadata.simulation if incumbent_bundle is not None else None
            ),
            relative_challenger_model_p_ge_3_change=(
                challenger_model_metrics.p_ge_3 / optimized.adaptive_metrics.p_ge_3 - 1.0
            ),
            relative_primary_change_vs_incumbent=(
                challenger_fair.p_any_ge_3_mains / incumbent_fair.p_any_ge_3_mains - 1.0
                if incumbent_fair is not None
                else None
            ),
        )

    tickets = tuple(candidate.ticket for candidate in chosen.candidates)
    tiers = tuple(candidate.tier for candidate in chosen.candidates)
    marginal_contributions = chosen.marginal_contributions
    marginal_basis: Literal["optimizer_selected_candidates", "final_locked_lines"] = (
        "optimizer_selected_candidates"
    )
    recenter_accepted = 0
    recenter_evaluation_seed: int | None = None
    recenter_evaluation_count = 0
    recenter_original_objective: float | None = None
    recenter_proposed_objective: float | None = None
    recenter_decision_records: list[dict[str, float | int | bool | str]] = []
    if apply_recentering and not fair_selected:
        screening_seed = seed ^ 0xD1B54A32D192ED03

        def objective(candidate_tickets: Sequence[Ticket]) -> float:
            screening_metrics = estimate_bundle_metrics(
                candidate_tickets,
                model,
                seed=screening_seed,
                min_simulations=config.simulation.recenter_evaluation_draws,
                max_simulations=config.simulation.recenter_evaluation_draws,
                confidence_tolerance=1e-12,
                stable_batches_required=1,
            )
            return _objective_from_metrics(screening_metrics, candidate_tickets, config)

        profile = PositionalProfile.from_model(model)
        recentered = recenter_bundle(
            tickets,
            profile,
            objective=objective,
            strength=config.bundle.recenter_strength,
            max_overlap=config.bundle.max_main_overlap,
            pair_cap=config.bundle.pair_repeat_cap,
            triple_cap=config.bundle.triple_repeat_cap,
        )
        proposed = recentered.tickets
        aggressive_valid = all(
            tier != "aggressive"
            or len(set(ticket.mains) & set(ordered[-1].mains))
            <= config.bundle.aggressive_previous_draw_overlap_cap
            for ticket, tier in zip(proposed, tiers, strict=True)
        )
        production_gate_reason = "no screening proposal changed a ticket"
        recenter_evaluation_seed = screening_seed
        recenter_evaluation_count = config.simulation.recenter_evaluation_draws
        recenter_original_objective = recentered.original_objective
        recenter_proposed_objective = recentered.final_objective
        fair_original = exact_uniform_metrics(tickets)
        fair_proposed = exact_uniform_metrics(proposed)
        if proposed != tickets and not aggressive_valid:
            production_gate_reason = "aggressive previous-draw overlap constraint failed"
        elif (
            proposed != tickets
            and fair_proposed.p_any_ge_3_mains + 1e-15 < fair_original.p_any_ge_3_mains
        ):
            production_gate_reason = "exact fair 3+ coverage decreased"
        elif proposed != tickets:
            # The screening search is intentionally cheap.  A separate holdout
            # gate re-evaluates the complete original and proposed bundles on
            # identical scenarios at the already-stable production scale.
            recenter_evaluation_seed = seed ^ 0xA24BAED4963EE407
            comparison_count = min(
                config.simulation.maximum_draws,
                max(
                    config.simulation.initial_draws,
                    config.simulation.recenter_evaluation_draws,
                    optimized.adaptive_metrics.simulation_count,
                ),
            )
            original_comparison: BundleSimulationMetrics | None = None
            proposed_comparison: BundleSimulationMetrics | None = None
            while True:
                comparison_minimum = max(
                    1_000,
                    comparison_count
                    - config.simulation.batch_draws
                    * (config.simulation.stable_batches_required - 1),
                )

                def compare(
                    candidate_tickets: Sequence[Ticket],
                    *,
                    minimum: int,
                    maximum: int,
                ) -> BundleSimulationMetrics:
                    return estimate_bundle_metrics(
                        candidate_tickets,
                        model,
                        seed=recenter_evaluation_seed,
                        min_simulations=minimum,
                        max_simulations=maximum,
                        batch_size=config.simulation.batch_draws,
                        confidence_tolerance=(config.simulation.confidence_half_width_tolerance),
                        confidence_level=config.simulation.confidence_level,
                        stable_batches_required=(config.simulation.stable_batches_required),
                    )

                original_comparison = compare(
                    tickets, minimum=comparison_minimum, maximum=comparison_count
                )
                proposed_comparison = compare(
                    proposed, minimum=comparison_minimum, maximum=comparison_count
                )
                if original_comparison.stable and proposed_comparison.stable:
                    break
                if comparison_count >= config.simulation.maximum_draws:
                    break
                comparison_count = min(
                    config.simulation.maximum_draws,
                    comparison_count + config.simulation.batch_draws,
                )
            recenter_evaluation_count = comparison_count
            recenter_original_objective = _objective_from_metrics(
                original_comparison, tickets, config
            )
            recenter_proposed_objective = _objective_from_metrics(
                proposed_comparison, proposed, config
            )
            if not original_comparison.stable or not proposed_comparison.stable:
                production_gate_reason = "common-scenario estimates were not stable"
            elif recenter_proposed_objective + 1e-12 < recenter_original_objective:
                production_gate_reason = "common-scenario production objective decreased"
            else:
                tickets = proposed
                metrics = proposed_comparison
                recenter_accepted = recentered.accepted_count
                production_gate_reason = "accepted by common-scenario production gate"
                marginal_contributions = measure_bundle_marginals(
                    tickets,
                    tiers,
                    model,
                    seed=recenter_evaluation_seed,
                    simulations=recenter_evaluation_count,
                    weights=weights,
                    generation_indices=tuple(
                        candidate.generation_index for candidate in optimized.candidates
                    ),
                    mega_soft_cap=config.bundle.mega_soft_cap,
                    mega_hard_cap=config.bundle.mega_hard_cap,
                )
                marginal_basis = "final_locked_lines"
        for decision in recentered.decisions:
            recenter_decision_records.append(
                {
                    "ticket_index": decision.ticket_index,
                    "screening_accepted": decision.accepted,
                    "locked_accepted": bool(recenter_accepted and decision.accepted),
                    "screening_reason": decision.reason,
                    "production_gate_reason": production_gate_reason,
                    "original_mains": "-".join(map(str, decision.original.mains)),
                    "proposed_mains": "-".join(map(str, decision.proposed.mains)),
                    "screening_objective_before": decision.objective_before,
                    "screening_objective_after": decision.objective_after,
                }
            )

    selected_constraints = (
        {
            "max_main_overlap": config.fair_coverage.max_main_overlap,
            "pair_repeat_cap": config.fair_coverage.pair_repeat_cap,
            "mega_soft_cap": config.fair_coverage.mega_soft_cap,
            "mega_hard_cap": config.fair_coverage.mega_hard_cap,
        }
        if fair_selected
        else {
            "max_main_overlap": config.bundle.max_main_overlap,
            "pair_repeat_cap": config.bundle.pair_repeat_cap,
            "mega_soft_cap": config.bundle.mega_soft_cap,
            "mega_hard_cap": config.bundle.mega_hard_cap,
        }
    )
    lines: list[LockedLine] = []
    tier_line_ids: Counter[str] = Counter()
    for ticket, tier in zip(tickets, tiers, strict=True):
        tier_line_ids[tier] += 1
        lines.append(
            LockedLine(
                strategy=tier,
                line_id=tier_line_ids[tier],
                mains=ticket.mains,
                mega=ticket.mega,
            )
        )
    validate_bundle(
        lines,
        max_overlap=selected_constraints["max_main_overlap"],
        min_hamming=config.bundle.min_hamming_distance,
        pair_cap=selected_constraints["pair_repeat_cap"],
        triple_cap=config.bundle.triple_repeat_cap,
        mega_hard_cap=selected_constraints["mega_hard_cap"],
        expected_size=config.bundle.size,
        previous_draw_mains=ordered[-1].mains,
        aggressive_previous_overlap_cap=config.bundle.aggressive_previous_draw_overlap_cap,
    )

    if not metrics.stable:
        raise SimulationStabilityError(
            "bundle estimates did not reach the configured confidence tolerance "
            f"after {metrics.simulation_count:,} simulations"
        )

    final_fair_metrics = exact_uniform_metrics(tickets)
    _assert_correction_non_regression(final_fair_metrics, incumbent_fair)
    optimizer_settings = OptimizerSettings(
        algorithm=(
            "exact-fair-linear-packing-v4"
            if fair_selected
            else "simulation-greedy-submodular-v4-fair-robustness-evaluated"
        ),
        optimization_basis=(
            "exact_fair_uniform_coverage" if fair_selected else "adaptive_model_simulation"
        ),
        objective_mode=config.objective.mode,
        objective_weights={
            "p_ge_3": config.objective.p_ge_3_weight,
            "p_ge_4": config.objective.p_ge_4_weight,
            "three_plus_mega": config.objective.three_plus_mega_weight,
            "four_plus_mega": config.objective.four_plus_weight,
            "mega_repeat_penalty": config.objective.mega_repeat_penalty,
            "aggressive_secondary_multiplier": (config.objective.aggressive_secondary_multiplier),
        },
        constraints={
            "max_main_overlap": selected_constraints["max_main_overlap"],
            "min_hamming_distance": config.bundle.min_hamming_distance,
            "pair_repeat_cap": selected_constraints["pair_repeat_cap"],
            "triple_repeat_cap": config.bundle.triple_repeat_cap,
            "mega_soft_cap": selected_constraints["mega_soft_cap"],
            "mega_hard_cap": selected_constraints["mega_hard_cap"],
            "aggressive_previous_draw_overlap_cap": (
                config.bundle.aggressive_previous_draw_overlap_cap
            ),
            "adjacency_allowed": config.bundle.adjacency_allowed,
            "parity_rule": config.bundle.parity_rule,
            "band_rule": config.bundle.band_rule,
            "recentering_accepted": recenter_accepted,
        },
        anti_cannibalization_weight=(
            config.fair_coverage.anti_cannibalization_weight
            if fair_selected
            else config.objective.anti_cannibalization_weight
        ),
        optimization_simulation_count=chosen.optimization_simulations,
        local_search_iterations=recenter_accepted,
        recenter_evaluation_seed=recenter_evaluation_seed,
        recenter_evaluation_simulations=recenter_evaluation_count,
        recenter_original_objective=recenter_original_objective,
        recenter_proposed_objective=recenter_proposed_objective,
        recenter_accepted_count=recenter_accepted,
        recenter_decisions=tuple(recenter_decision_records),
        marginal_contribution_basis=marginal_basis,
        marginal_contributions=tuple(
            {
                "selection_index": item.selection_index,
                "generation_index": item.generation_index,
                "tier": item.tier,
                "primary_new_coverage": item.primary_new_coverage,
                "four_plus_new_coverage": item.four_plus_new_coverage,
                "three_plus_mega_new_coverage": item.three_plus_mega_new_coverage,
                "four_plus_mega_new_coverage": item.four_plus_mega_new_coverage,
                "anti_cannibalization_penalty": item.anti_cannibalization_penalty,
                "weighted_gain": item.weighted_gain,
            }
            for item in marginal_contributions
        ),
        fair_coverage_challenger=fair_evidence,
    )
    identity = {
        "history_snapshot_sha256": history_snapshot_sha256,
        "calibration_id": calibration.calibration_id,
        "intended_draw_date": target.isoformat(),
        "model_version": config.model_version,
        "runtime_environment": _runtime_environment(),
        "configuration_sha256": config.snapshot_sha256(),
        "seed": seed,
        "candidate_pool_sha256": candidate_pool_sha256,
        "fair_coverage_challenger": (
            fair_evidence.model_dump(mode="json") if fair_evidence is not None else None
        ),
        "lock_version": lock_version,
        "supersedes_bundle_id": supersedes_bundle_id,
        "correction_reason": correction_reason,
        "lines": [line.model_dump(mode="json") for line in lines],
    }
    identity_hash = sha256_bytes(canonical_json_bytes(identity))
    bundle_id = f"slp-{target.isoformat()}-v{lock_version}-{identity_hash[:16]}"
    metadata = BundleMetadata(
        bundle_id=bundle_id,
        generated_timestamp_utc=generated,
        intended_draw_date=target,
        draw_id=None,
        game_rules_version=config.game.rules_version,
        model_version=config.model_version,
        runtime_environment=_runtime_environment(),
        configuration_snapshot=config.snapshot(),
        configuration_sha256=config.snapshot_sha256(),
        random_seed=seed,
        candidate_pool_sha256=candidate_pool_sha256,
        candidate_pool_algorithm_version=CANDIDATE_POOL_ALGORITHM_VERSION,
        source_verification_metadata=ordered[-1].verification,
        history_cutoff_date=cutoff,
        history_snapshot_sha256=history_snapshot_sha256,
        calibration_id=calibration.calibration_id,
        calibration_random_seed=calibration.selection_random_seed,
        selected_hyperparameters=calibration.bundle_hyperparameters(),
        simulation=_simulation_summary(
            metrics,
            candidate_pool_size=config.simulation.candidate_pool_size,
            fair_uniform_exact=final_fair_metrics,
        ),
        optimizer=optimizer_settings,
        bundle_size=config.bundle.size,
        lock_version=lock_version,
        supersedes_bundle_id=supersedes_bundle_id,
        correction_reason=correction_reason,
    )
    return LockedBundle(metadata=metadata, lines=tuple(lines))

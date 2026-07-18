"""Cutoff-safe walk-forward backtests with explicit no-future-data proofs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from .config import AppConfig
from .modeling import select_hyperparameters
from .models import VerifiedDraw
from .optimizer import ObjectiveWeights, OptimizerConstraints, optimize_bundle
from .scoring import score_ticket
from .simulation import estimate_bundle_metrics, generate_candidate_pool
from .storage import canonical_json_bytes, sha256_bytes, write_new_file


def _prefix_sha256(prefix: Sequence[VerifiedDraw]) -> str:
    return sha256_bytes(canonical_json_bytes([draw.model_dump(mode="json") for draw in prefix]))


def _seed(prefix_sha256: str, target: date, offset: int) -> int:
    digest = sha256_bytes(
        canonical_json_bytes(
            {"training_prefix": prefix_sha256, "target": target.isoformat(), "offset": offset}
        )
    )
    return int(digest[:16], 16)


def assert_no_future_leakage(
    *, target_date: date, training_cutoff_date: date, fold_cutoffs: Sequence[date]
) -> None:
    if training_cutoff_date >= target_date:
        raise AssertionError("backtest training cutoff reaches or exceeds its target")
    if any(cutoff >= target_date for cutoff in fold_cutoffs):
        raise AssertionError("hyperparameter fold used target or future history")


def run_backtest(
    history: Sequence[VerifiedDraw],
    *,
    history_snapshot_sha256: str,
    config: AppConfig,
    evaluations: int = 3,
    diagnostic_candidate_pool_size: int = 6_000,
    optimization_simulations: int = 2_048,
    final_simulations: int = 10_000,
) -> dict[str, Any]:
    ordered = sorted(history, key=lambda draw: draw.draw_date)
    if len(ordered) < min(config.training.windows) + evaluations:
        raise ValueError("insufficient verified history for requested walk-forward backtest")
    if not 1 <= evaluations <= 20:
        raise ValueError("evaluations must be between 1 and 20")
    if diagnostic_candidate_pool_size < 300:
        raise ValueError("diagnostic candidate pool is too small")

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
    records: list[dict[str, Any]] = []
    start = len(ordered) - evaluations
    for offset, target_index in enumerate(range(start, len(ordered))):
        prefix = ordered[:target_index]
        target = ordered[target_index]
        prefix_sha256 = _prefix_sha256(prefix)
        seed = _seed(prefix_sha256, target.draw_date, offset)
        selection = select_hyperparameters(
            prefix,
            cutoff_date=prefix[-1].draw_date,
            windows=config.training.windows,
            main_sigmas=config.training.main_sigmas,
            mega_sigmas=config.training.mega_sigmas,
            half_lives=config.training.half_lives_draws,
            validation_draws=config.training.forward_folds,
            forward_bundle_size=config.training.forward_bundle_size,
            random_seed=seed,
            anchor_min_improvement=config.training.anchor_min_relative_improvement,
            likelihood_stability_margin=config.training.likelihood_stability_margin,
        )
        assert_no_future_leakage(
            target_date=target.draw_date,
            training_cutoff_date=selection.model.history_cutoff_date,
            fold_cutoffs=selection.fold_training_cutoffs,
        )
        pool = generate_candidate_pool(
            selection.model,
            size=diagnostic_candidate_pool_size,
            seed=seed,
            previous_draw=prefix[-1],
            enforce_production_minimum=False,
        )
        optimized = optimize_bundle(
            pool,
            selection.model,
            seed=seed ^ 0x9E3779B97F4A7C15,
            previous_draw=prefix[-1],
            constraints=constraints,
            weights=weights,
            optimization_simulations=optimization_simulations,
            estimate_final_metrics=False,
        )
        predicted = estimate_bundle_metrics(
            optimized.tickets,
            selection.model,
            seed=seed ^ 0xD1B54A32D192ED03,
            min_simulations=final_simulations,
            max_simulations=final_simulations,
            confidence_tolerance=1e-12,
        )
        line_scores = [score_ticket(ticket, target) for ticket in optimized.tickets]
        best = max(score.main_matches for score in line_scores)
        any_ge_3 = any(score.main_matches >= 3 for score in line_scores)
        any_ge_4 = any(score.main_matches >= 4 for score in line_scores)
        any_3_mega = any(score.main_matches >= 3 and score.mega_hit for score in line_scores)
        parameters = selection.model.parameters
        records.append(
            {
                "target_draw_date": target.draw_date.isoformat(),
                "training_cutoff_date": prefix[-1].draw_date.isoformat(),
                "training_draw_count": len(prefix),
                "training_prefix_sha256": prefix_sha256,
                "fold_training_cutoffs": [
                    cutoff.isoformat() for cutoff in selection.fold_training_cutoffs
                ],
                "random_seed": seed,
                "parameters": {
                    "mains": {
                        "window": parameters.mains.window,
                        "sigma": parameters.mains.sigma,
                        "half_life": parameters.mains.half_life,
                    },
                    "mega": {
                        "window": parameters.mega.window,
                        "sigma": parameters.mega.sigma,
                        "half_life": parameters.mega.half_life,
                    },
                },
                "candidate_pool_size": diagnostic_candidate_pool_size,
                "optimization_simulations": optimization_simulations,
                "metric_simulations": final_simulations,
                "predicted": {
                    "p_any_ge_3_mains": predicted.p_ge_3,
                    "p_any_ge_4_mains": predicted.p_ge_4,
                    "p_any_3_plus_mega": predicted.p_3_plus_mega,
                },
                "realized": {
                    "any_ge_3_mains": any_ge_3,
                    "any_ge_4_mains": any_ge_4,
                    "any_3_plus_mega": any_3_mega,
                    "best_main_match_count": best,
                    "mega_hit_count": sum(score.mega_hit for score in line_scores),
                },
                "line_main_match_histogram": {
                    str(value): sum(score.main_matches == value for score in line_scores)
                    for value in range(6)
                },
            }
        )
    report = {
        "schema_version": 1,
        "report_type": "cutoff_safe_walk_forward_backtest",
        "history_snapshot_sha256": history_snapshot_sha256,
        "history_cutoff_date": ordered[-1].draw_date.isoformat(),
        "evaluation_count": len(records),
        "production_candidate_minimum": config.simulation.candidate_pool_size,
        "diagnostic_candidate_pool_size": diagnostic_candidate_pool_size,
        "no_future_information": True,
        "records": records,
        "aggregate": {
            "realized_rate_any_ge_3": mean(
                float(record["realized"]["any_ge_3_mains"]) for record in records
            ),
            "realized_rate_any_ge_4": mean(
                float(record["realized"]["any_ge_4_mains"]) for record in records
            ),
            "mean_predicted_p_ge_3": mean(
                float(record["predicted"]["p_any_ge_3_mains"]) for record in records
            ),
            "mean_predicted_p_ge_4": mean(
                float(record["predicted"]["p_any_ge_4_mains"]) for record in records
            ),
        },
        "disclaimer": (
            "Diagnostic backtests do not imply that random lottery outcomes are predictable."
        ),
    }
    return report


def _markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        "# Cutoff-safe SuperLotto Plus backtest",
        "",
        report["disclaimer"],
        "",
        f"Evaluations: {report['evaluation_count']}",
        f"History cutoff: {report['history_cutoff_date']}",
        f"No future information: {report['no_future_information']}",
        f"Diagnostic candidate pool: {report['diagnostic_candidate_pool_size']} "
        f"(production minimum: {report['production_candidate_minimum']})",
        "",
        f"Realized rate any >=3 mains: {aggregate['realized_rate_any_ge_3']:.4f}",
        f"Mean predicted P(any >=3 mains): {aggregate['mean_predicted_p_ge_3']:.4f}",
        f"Realized rate any >=4 mains: {aggregate['realized_rate_any_ge_4']:.4f}",
        f"Mean predicted P(any >=4 mains): {aggregate['mean_predicted_p_ge_4']:.4f}",
        "",
        "| Target | Training cutoff | Main params | Mega params | Pred P>=3 | Realized | Best |",
        "|---|---|---|---|---:|---|---:|",
    ]
    for record in report["records"]:
        lines.append(
            f"| {record['target_draw_date']} | {record['training_cutoff_date']} | "
            f"{record['parameters']['mains']} | {record['parameters']['mega']} | "
            f"{record['predicted']['p_any_ge_3_mains']:.4f} | "
            f"{record['realized']['any_ge_3_mains']} | "
            f"{record['realized']['best_main_match_count']} |"
        )
    return "\n".join(lines) + "\n"


def write_backtest_report(report: dict[str, Any], root: Path) -> tuple[Path, Path]:
    identity = sha256_bytes(canonical_json_bytes(report))[:16]
    cutoff = report["history_cutoff_date"]
    base = root / "backtests" / f"backtest-{cutoff}-{identity}"
    json_path = base.with_suffix(".json")
    markdown_path = base.with_suffix(".md")
    payloads = (
        (json_path, canonical_json_bytes(report)),
        (markdown_path, _markdown(report).encode("utf-8")),
    )
    for path, payload in payloads:
        if path.exists():
            if path.read_bytes() != payload:
                raise RuntimeError(f"backtest report identity collision: {path}")
        else:
            write_new_file(path, payload)
    return json_path, markdown_path

"""Immutable human- and machine-readable scoring performance reports."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from statistics import mean, pstdev, stdev
from typing import Any

from .models import BundleScore, ScoredLine
from .storage import canonical_json_bytes, sha256_bytes, write_new_file


def _aggregate_lines(lines: Sequence[ScoredLine]) -> dict[str, Any]:
    counts = [line.main_match_count for line in lines]
    size = len(lines)
    histogram = Counter(counts)
    categories = Counter(line.prize_category for line in lines)
    mega_hits = sum(line.mega_hit for line in lines)
    return {
        "line_count": size,
        "histogram": {str(value): histogram[value] for value in range(6)},
        "mega_hit_count": mega_hits,
        "mega_hit_rate": mega_hits / size if size else 0.0,
        "mean_main_matches": mean(counts) if counts else 0.0,
        "population_stddev": pstdev(counts) if counts else 0.0,
        "sample_stddev": stdev(counts) if size > 1 else 0.0,
        "empirical_p_ge_2": sum(value >= 2 for value in counts) / size if size else 0.0,
        "empirical_p_ge_3": sum(value >= 3 for value in counts) / size if size else 0.0,
        "empirical_p_ge_4": sum(value >= 4 for value in counts) / size if size else 0.0,
        "category_counts": dict(sorted(categories.items())),
    }


def _score_regime(score: BundleScore) -> tuple[int, str]:
    """Return explicit, legacy-safe score provenance for report grouping."""

    return (score.bundle_size or len(score.lines), score.model_version)


def _regime_report(scores: Sequence[BundleScore]) -> dict[str, Any]:
    ordered = sorted(scores, key=lambda score: score.intended_draw_date)
    bundle_size, model_version = _score_regime(ordered[0])
    all_lines = [line for score in ordered for line in score.lines]
    return {
        "regime_id": f"{bundle_size}-line::{model_version}",
        "bundle_size": bundle_size,
        "model_version": model_version,
        "provenance_complete": model_version != "unknown"
        and all(score.bundle_size is not None for score in ordered),
        "score_count": len(ordered),
        "score_ids": [score.score_id for score in ordered],
        "draw_date_start": ordered[0].intended_draw_date.isoformat(),
        "draw_date_end": ordered[-1].intended_draw_date.isoformat(),
        "overall": _aggregate_lines(all_lines),
        "tiers": {
            tier: _aggregate_lines([line for line in all_lines if line.strategy == tier])
            for tier in ("aggressive", "balanced", "conservative")
        },
        "latest_rolling_calibration": ordered[-1].calibration_error,
    }


def build_performance_report(scores: Sequence[BundleScore]) -> dict[str, Any]:
    ordered = sorted(scores, key=lambda score: score.intended_draw_date)
    grouped_scores: dict[tuple[int, str], list[BundleScore]] = {}
    for score in ordered:
        grouped_scores.setdefault(_score_regime(score), []).append(score)
    regimes = [
        _regime_report(grouped_scores[key])
        for key in sorted(grouped_scores, key=lambda item: (item[0], item[1]))
    ]
    all_lines = [line for score in ordered for line in score.lines]
    tier_statistics = {
        tier: _aggregate_lines([line for line in all_lines if line.strategy == tier])
        for tier in ("aggressive", "balanced", "conservative")
    }
    ranked_items: list[dict[str, Any]] = [
        {
            "draw_date": score.intended_draw_date.isoformat(),
            "bundle_id": score.bundle_id,
            "bundle_size": score.bundle_size or len(score.lines),
            "model_version": score.model_version,
            "regime_id": f"{score.bundle_size or len(score.lines)}-line::{score.model_version}",
            "strategy": line.strategy,
            "line_id": line.line_id,
            "mains": list(line.mains),
            "mega": line.mega,
            "matched_mains": list(line.matched_mains),
            "main_match_count": line.main_match_count,
            "mega_hit": line.mega_hit,
            "prize_category": line.prize_category,
        }
        for score in ordered
        for line in score.lines
    ]
    ranked = sorted(
        ranked_items,
        key=lambda item: (int(item["main_match_count"]), bool(item["mega_hit"]), item["draw_date"]),
        reverse=True,
    )
    comparisons = [
        {
            "draw_date": score.intended_draw_date.isoformat(),
            "bundle_id": score.bundle_id,
            "bundle_size": score.bundle_size or len(score.lines),
            "model_version": score.model_version,
            "regime_id": (f"{score.bundle_size or len(score.lines)}-line::{score.model_version}"),
            "predicted": {
                "p_any_ge_3_mains": score.predicted_metrics.p_any_ge_3_mains,
                "p_any_ge_4_mains": score.predicted_metrics.p_any_ge_4_mains,
                "p_any_3_plus_mega": score.predicted_metrics.p_any_3_plus_mega,
                "p_any_4_plus": score.predicted_metrics.p_any_4_plus,
                "p_any_4_plus_mega": score.predicted_metrics.p_any_4_plus_mega,
            },
            "realized": score.realized_metrics,
            "calibration_error": score.calibration_error,
            "overall": score.overall.model_dump(mode="json"),
            "tiers": {
                tier: statistics.model_dump(mode="json") for tier, statistics in score.tiers.items()
            },
            "best_line_keys": list(score.best_line_keys),
        }
        for score in ordered
    ]
    return {
        "schema_version": 3,
        "report_type": "scored_bundle_performance",
        "score_count": len(ordered),
        "draw_date_start": ordered[0].intended_draw_date.isoformat() if ordered else None,
        "draw_date_end": ordered[-1].intended_draw_date.isoformat() if ordered else None,
        "score_ids": [score.score_id for score in ordered],
        "regime_count": len(regimes),
        "mixed_regimes": len(regimes) > 1,
        "regimes": regimes,
        "cross_regime_aggregation_scope": (
            "descriptive line outcomes only; rolling calibration is regime-specific"
        ),
        "overall": _aggregate_lines(all_lines),
        "tiers": tier_statistics,
        "best_performing_tickets": ranked[: min(10, len(ranked))],
        "predicted_vs_realized": comparisons,
        "latest_rolling_calibration": (ordered[-1].calibration_error if ordered else {}),
        "latest_calibration_regime_id": (
            f"{_score_regime(ordered[-1])[0]}-line::{_score_regime(ordered[-1])[1]}"
            if ordered
            else None
        ),
        "disclaimer": (
            "Lottery outcomes are random; these statistics do not establish predictability."
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "# SuperLotto Plus Performance Report",
        "",
        report["disclaimer"],
        "",
        f"Scored bundles: {report['score_count']}",
        (
            f"Draw range: {report['draw_date_start'] or 'n/a'} through "
            f"{report['draw_date_end'] or 'n/a'}"
        ),
        f"Calibration regimes: {report['regime_count']}",
        "",
        "## Calibration regimes",
        "",
    ]
    if not report["regimes"]:
        lines.append("No scored calibration regimes exist yet.")
    else:
        lines.extend(
            [
                "| Regime | Draws | Range | Lines scored |",
                "|---|---:|---|---:|",
            ]
        )
        for regime in report["regimes"]:
            lines.append(
                f"| `{regime['regime_id']}` | {regime['score_count']} | "
                f"{regime['draw_date_start']} through {regime['draw_date_end']} | "
                f"{regime['overall']['line_count']} |"
            )
    lines.extend(
        [
            "",
            (
                "Overall statistics below pool line outcomes for descriptive reporting only; "
                "rolling calibration is calculated within matching bundle-size/model regimes."
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Overall line statistics",
            "",
            f"- Lines: {overall['line_count']}",
            f"- Main-match histogram (0-5): {overall['histogram']}",
            f"- Mega hits: {overall['mega_hit_count']} ({overall['mega_hit_rate']:.4f})",
            f"- Mean main matches: {overall['mean_main_matches']:.4f}",
            (
                "- Population/sample standard deviation: "
                f"{overall['population_stddev']:.4f} / {overall['sample_stddev']:.4f}"
            ),
            f"- Empirical P(>=2): {overall['empirical_p_ge_2']:.4f}",
            f"- Empirical P(>=3): {overall['empirical_p_ge_3']:.4f}",
            f"- Empirical P(>=4): {overall['empirical_p_ge_4']:.4f}",
            "",
            "## Tier summary",
            "",
            "| Tier | Lines | Mean mains | Mega rate | P(>=3) | Histogram |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for tier, statistics in report["tiers"].items():
        lines.append(
            f"| {tier} | {statistics['line_count']} | "
            f"{statistics['mean_main_matches']:.4f} | {statistics['mega_hit_rate']:.4f} | "
            f"{statistics['empirical_p_ge_3']:.4f} | {statistics['histogram']} |"
        )
    lines.extend(["", "## Best-performing tickets", ""])
    if not report["best_performing_tickets"]:
        lines.append("No immutable scoring artifacts exist yet.")
    else:
        lines.extend(
            [
                "| Draw | Tier/line | Ticket | Matches | Mega | Category |",
                "|---|---|---|---|---|---|",
            ]
        )
        for item in report["best_performing_tickets"]:
            ticket = " ".join(str(value) for value in item["mains"])
            lines.append(
                f"| {item['draw_date']} | {item['strategy']}:{item['line_id']} | "
                f"{ticket} + {item['mega']} | {item['matched_mains']} | "
                f"{'yes' if item['mega_hit'] else 'no'} | {item['prize_category']} |"
            )
    lines.extend(["", "## Predicted versus realized", ""])
    for comparison in report["predicted_vs_realized"]:
        lines.append(
            f"- {comparison['draw_date']} `{comparison['bundle_id']}` "
            f"(`{comparison['regime_id']}`): "
            f"predicted P(>=3)={comparison['predicted']['p_any_ge_3_mains']:.4f}; "
            f"realized={comparison['realized']['any_ge_3_mains']}"
        )
    return "\n".join(lines) + "\n"


def write_performance_report(scores: Sequence[BundleScore], root: Path) -> tuple[Path, Path]:
    report = build_performance_report(scores)
    identity = sha256_bytes(canonical_json_bytes(report))[:16]
    end = report["draw_date_end"] or "no-scores"
    base = root / "performance" / f"performance-{end}-{identity}"
    json_path = base.with_suffix(".json")
    markdown_path = base.with_suffix(".md")
    json_payload = canonical_json_bytes(report)
    markdown_payload = _markdown(report).encode("utf-8")
    for path, payload in ((json_path, json_payload), (markdown_path, markdown_payload)):
        if path.exists():
            if path.read_bytes() != payload:
                raise RuntimeError(f"report identity collision: {path}")
        else:
            write_new_file(path, payload)
    return json_path, markdown_path

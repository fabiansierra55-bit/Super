from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from slp_model.fair_odds import (
    FULL_DRAW_OUTCOME_COUNT,
    MAIN_DRAW_OUTCOME_COUNT,
    MAX_LINEAR_PACKING_LINES,
    OPTIMAL_30_LINE_3_PLUS_MEGA_COUNT,
    OPTIMAL_30_LINE_GE3_COUNT,
    OPTIMAL_30_LINE_GE4_COUNT,
    exact_coverage_regressions,
    exact_uniform_metrics,
    fair_challenger_decision,
    fair_coverage_certificate,
    matches_fair_coverage_certificate,
)
from slp_model.models import ExactUniformMetrics, LockedBundle, Ticket


def test_single_ticket_exact_uniform_arithmetic() -> None:
    metrics = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=7)])

    assert metrics.main_draw_outcome_count == 1_533_939
    assert metrics.full_draw_outcome_count == 41_416_353
    assert metrics.best_match_histogram == (850_668, 559_650, 114_800, 8_610, 210, 1)
    assert metrics.covered_ge_3_mains_count == 8_821
    assert metrics.covered_ge_4_mains_count == 211
    assert metrics.covered_3_plus_mega_count == 8_821
    assert metrics.covered_4_plus_mega_count == 211
    assert metrics.covered_jackpot_count == 1
    assert metrics.p_jackpot == 1 / FULL_DRAW_OUTCOME_COUNT


def test_size_aware_fair_coverage_certificates_preserve_30_and_define_60() -> None:
    thirty = fair_coverage_certificate(30)
    sixty = fair_coverage_certificate(60)

    assert (
        thirty.covered_ge_3_mains_count,
        thirty.covered_ge_4_mains_count,
        thirty.covered_3_plus_mega_count,
        thirty.covered_jackpot_count,
    ) == (
        OPTIMAL_30_LINE_GE3_COUNT,
        OPTIMAL_30_LINE_GE4_COUNT,
        OPTIMAL_30_LINE_3_PLUS_MEGA_COUNT,
        30,
    )
    assert (
        sixty.main_degree_floor,
        sixty.main_degree_ceiling,
        sixty.high_degree_number_count,
        sixty.intersecting_line_pair_count,
    ) == (6, 7, 18, 813)
    assert (
        sixty.covered_ge_3_mains_count,
        sixty.covered_ge_4_mains_count,
        sixty.covered_3_plus_mega_count,
        sixty.covered_4_plus_mega_count,
        sixty.covered_jackpot_count,
    ) == (499_992, 12_660, 529_260, 12_660, 60)


def test_fair_coverage_certificate_rejects_impossible_linear_sizes() -> None:
    assert MAX_LINEAR_PACKING_LINES == 103
    with pytest.raises(ValueError, match="positive"):
        fair_coverage_certificate(0)
    with pytest.raises(ValueError, match="pair capacity"):
        fair_coverage_certificate(MAX_LINEAR_PACKING_LINES + 1)


def test_certificate_match_requires_unique_main_sets() -> None:
    single = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=1)])
    repeated_mains = exact_uniform_metrics(
        [
            Ticket(mains=(1, 2, 3, 4, 5), mega=1),
            Ticket(mains=(1, 2, 3, 4, 5), mega=2),
        ]
    )

    assert matches_fair_coverage_certificate(single, 1)
    assert not matches_fair_coverage_certificate(repeated_mains, 2)


def test_exact_metrics_reject_histogram_numerator_disagreement() -> None:
    metrics = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=7)])
    payload = metrics.model_dump(mode="json")
    payload["covered_ge_3_mains_count"] += 1

    with pytest.raises(ValidationError, match="histogram"):
        ExactUniformMetrics.model_validate(payload)


def test_mega_spread_increases_joint_coverage_without_changing_mains() -> None:
    one = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=1)])
    two = exact_uniform_metrics(
        [
            Ticket(mains=(1, 2, 3, 4, 5), mega=1),
            Ticket(mains=(1, 2, 3, 4, 5), mega=2),
        ]
    )

    assert two.covered_ge_3_mains_count == one.covered_ge_3_mains_count
    assert two.covered_3_plus_mega_count == 2 * one.covered_3_plus_mega_count
    assert two.covered_jackpot_count == 2
    assert two.covered_5_mains_count == 1


def test_locked_v2_exact_uniform_golden_metrics() -> None:
    path = Path("data/predictions/locked/2026-07-18/slp-2026-07-18-v2-647373683ab9e4b8/bundle.json")
    bundle = LockedBundle.model_validate(json.loads(path.read_text())["bundle"])
    metrics = exact_uniform_metrics(bundle.lines)

    assert metrics.main_draw_outcome_count == MAIN_DRAW_OUTCOME_COUNT
    assert metrics.best_match_histogram == (6, 165_971, 1_120_785, 240_847, 6_300, 30)
    assert metrics.covered_ge_3_mains_count == 247_177
    assert metrics.covered_ge_4_mains_count == 6_330
    assert metrics.covered_3_plus_mega_count == 264_234
    assert metrics.covered_4_plus_mega_count == 6_330


def test_fair_promotion_requires_primary_lift_and_no_secondary_regression() -> None:
    incumbent = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=1)])
    challenger = exact_uniform_metrics(
        [
            Ticket(mains=(1, 2, 3, 4, 5), mega=1),
            Ticket(mains=(6, 7, 8, 9, 10), mega=2),
        ]
    )

    promoted = fair_challenger_decision(challenger, [incumbent], minimum_relative_improvement=0.001)
    retained = fair_challenger_decision(incumbent, [challenger], minimum_relative_improvement=0.001)

    assert promoted.selected
    assert promoted.relative_primary_improvement > 0
    assert not retained.selected


def test_fair_promotion_allows_equal_incumbent_coverage() -> None:
    model_candidate = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=1)])
    incumbent = exact_uniform_metrics(
        [
            Ticket(mains=(1, 2, 3, 4, 5), mega=1),
            Ticket(mains=(6, 7, 8, 9, 10), mega=2),
        ]
    )

    decision = fair_challenger_decision(
        incumbent,
        [model_candidate],
        minimum_relative_improvement=0.001,
        non_regression_references=[incumbent],
    )

    assert decision.selected


def test_size_aware_promotion_certificate_keeps_legacy_alias_compatible() -> None:
    metrics = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=1)])

    decision = fair_challenger_decision(
        metrics,
        [metrics],
        minimum_relative_improvement=0,
        require_line_optimum=1,
    )

    assert decision.selected
    with pytest.raises(ValueError, match="conflicting"):
        fair_challenger_decision(
            metrics,
            [metrics],
            minimum_relative_improvement=0,
            require_30_line_optimum=True,
            require_line_optimum=1,
        )


def test_exact_coverage_regression_gate_detects_a_worse_correction() -> None:
    incumbent = exact_uniform_metrics(
        [
            Ticket(mains=(1, 2, 3, 4, 5), mega=1),
            Ticket(mains=(6, 7, 8, 9, 10), mega=2),
        ]
    )
    candidate = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=1)])

    assert "3+ mains" in exact_coverage_regressions(candidate, incumbent)
    assert exact_coverage_regressions(incumbent, incumbent) == ()

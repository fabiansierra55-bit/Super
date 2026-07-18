import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from slp_model.models import (
    Draw,
    ExactUniformMetrics,
    FairCoverageChallengerEvidence,
    LockedBundle,
    SimulationSummary,
    SourceEvidence,
    VerificationMetadata,
    VerifiedDraw,
)


def exact_sixty_line_certificate() -> ExactUniformMetrics:
    main_outcomes = 1_533_939
    full_outcomes = main_outcomes * 27
    ge_3 = 499_992
    ge_4 = 12_660
    jackpots = 60
    histogram = (0, 0, main_outcomes - ge_3, ge_3 - ge_4, ge_4 - jackpots, jackpots)
    return ExactUniformMetrics(
        main_draw_outcome_count=main_outcomes,
        full_draw_outcome_count=full_outcomes,
        covered_ge_3_mains_count=ge_3,
        covered_ge_4_mains_count=ge_4,
        covered_3_plus_mega_count=529_260,
        covered_4_plus_mega_count=12_660,
        covered_5_mains_count=jackpots,
        covered_jackpot_count=jackpots,
        p_any_ge_3_mains=ge_3 / main_outcomes,
        p_any_ge_4_mains=ge_4 / main_outcomes,
        p_any_3_plus_mega=529_260 / full_outcomes,
        p_any_4_plus_mega=12_660 / full_outcomes,
        p_any_5_mains=jackpots / main_outcomes,
        p_jackpot=jackpots / full_outcomes,
        mean_best_main_matches=sum(matches * count for matches, count in enumerate(histogram))
        / main_outcomes,
        best_match_histogram=histogram,
    )


def simulation_for(metrics: ExactUniformMetrics) -> SimulationSummary:
    return SimulationSummary(
        simulation_count=50_000,
        candidate_pool_size=50_000,
        confidence_level=0.95,
        maximum_confidence_half_width=0.001,
        stable=True,
        stable_batches=2,
        p_any_ge_3_mains=metrics.p_any_ge_3_mains,
        p_any_ge_4_mains=metrics.p_any_ge_4_mains,
        p_any_3_plus_mega=metrics.p_any_3_plus_mega,
        p_any_4_plus=metrics.p_any_4_plus_mega,
        p_any_4_plus_mega=metrics.p_any_4_plus_mega,
        mean_best_main_matches=metrics.mean_best_main_matches,
        fair_uniform_exact=metrics,
    )


def source(name: str, role: str) -> SourceEvidence:
    return SourceEvidence(
        source_name=name,
        role=role,
        source_url=f"https://example.test/{name}",
        fetched_timestamp_utc=datetime(2026, 1, 2, tzinfo=UTC),
        raw_sha256="a" * 64,
        parser_version="test-v1",
        http_status=200,
    )


def test_game_rules_reject_duplicate_and_out_of_range_numbers():
    with pytest.raises(ValidationError, match="five unique"):
        Draw(draw_date=date(2026, 1, 1), mains=(1, 1, 2, 3, 4), mega=1)
    with pytest.raises(ValidationError, match="outside 1-47"):
        Draw(draw_date=date(2026, 1, 1), mains=(1, 2, 3, 4, 48), mega=1)
    with pytest.raises(ValidationError, match="outside 1-27"):
        Draw(draw_date=date(2026, 1, 1), mains=(1, 2, 3, 4, 5), mega=28)


def test_verified_draw_requires_official_and_backup_evidence():
    with pytest.raises(ValidationError, match="at least two sources"):
        VerificationMetadata(
            status="verified",
            verified_timestamp_utc=datetime(2026, 1, 2, tzinfo=UTC),
            official_post_timestamp_utc=datetime(2026, 1, 1, 4, tzinfo=UTC),
            sources=(source("official", "official"),),
            comparison_sha256="b" * 64,
        )

    verified = VerifiedDraw(
        draw_date=date(2026, 1, 1),
        mains=(5, 4, 3, 2, 1),
        mega=7,
        verification=VerificationMetadata(
            status="verified",
            verified_timestamp_utc=datetime(2026, 1, 2, tzinfo=UTC),
            official_post_timestamp_utc=datetime(2026, 1, 1, 4, tzinfo=UTC),
            sources=(source("official", "official"), source("backup", "backup")),
            comparison_sha256="b" * 64,
        ),
    )
    assert verified.mains == (1, 2, 3, 4, 5)


def test_versioned_correction_evidence_requires_incumbent_binding() -> None:
    path = Path("data/predictions/locked/2026-07-18/slp-2026-07-18-v5-ca0077ce15c2753f/bundle.json")
    payload = json.loads(path.read_text(encoding="utf-8"))["bundle"]
    evidence = payload["metadata"]["optimizer"]["fair_coverage_challenger"]
    evidence["incumbent"] = None
    evidence["incumbent_model_simulation"] = None

    with pytest.raises(ValidationError, match="bind its incumbent"):
        LockedBundle.model_validate(payload)


def test_existing_thirty_line_bundle_remains_loadable() -> None:
    path = Path("data/predictions/locked/2026-07-18/slp-2026-07-18-v5-ca0077ce15c2753f/bundle.json")

    payload = json.loads(path.read_text(encoding="utf-8"))["bundle"]
    bundle = LockedBundle.model_validate(payload)

    assert bundle.metadata.bundle_size == 30
    assert bundle.metadata.optimizer.fair_coverage_challenger is not None
    assert bundle.metadata.optimizer.fair_coverage_challenger.global_optimum_certified


def test_version_four_evidence_binds_sixty_line_certificate() -> None:
    certificate = exact_sixty_line_certificate()
    simulation = simulation_for(certificate)

    evidence = FairCoverageChallengerEvidence(
        evidence_version=4,
        selection_policy="fair_null_robustness_over_unvalidated_model_v1",
        certificate_bundle_size=60,
        selected=True,
        global_optimum_certified=True,
        selection_reason="exact sixty-line certificate",
        minimum_relative_improvement=0.0,
        relative_primary_improvement=0.0,
        model_optimized_candidate=certificate,
        challenger=certificate,
        model_optimized_simulation=simulation,
        challenger_model_simulation=simulation,
        relative_challenger_model_p_ge_3_change=0.0,
    )

    assert evidence.certificate_bundle_size == 60


def test_version_four_evidence_rejects_wrong_certificate_size() -> None:
    certificate = exact_sixty_line_certificate()

    with pytest.raises(ValidationError, match="does not match challenger"):
        FairCoverageChallengerEvidence(
            evidence_version=4,
            certificate_bundle_size=30,
            selected=True,
            global_optimum_certified=True,
            selection_reason="mismatched certificate",
            minimum_relative_improvement=0.0,
            relative_primary_improvement=0.0,
            model_optimized_candidate=certificate,
            challenger=certificate,
        )


def test_legacy_evidence_rejects_contradictory_explicit_certificate_size() -> None:
    certificate = exact_sixty_line_certificate()

    with pytest.raises(ValidationError, match="before 4"):
        FairCoverageChallengerEvidence(
            evidence_version=3,
            certificate_bundle_size=60,
            selected=False,
            global_optimum_certified=False,
            selection_reason="invalid legacy certificate declaration",
            minimum_relative_improvement=0.0,
            relative_primary_improvement=0.0,
            model_optimized_candidate=certificate,
            challenger=certificate,
        )

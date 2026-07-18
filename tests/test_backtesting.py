from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np

from slp_model.backtesting import (
    BACKTEST_PREFIX_ALGORITHM_VERSION,
    BACKTEST_SEED_ALGORITHM_VERSION,
    _prefix_sha256,
    _seed,
    run_backtest,
)
from slp_model.config import AppConfig
from slp_model.models import SourceEvidence, VerificationMetadata, VerifiedDraw


def _verified_history(count: int, *, seed: int = 41) -> list[VerifiedDraw]:
    rng = np.random.default_rng(seed)
    start = date(2025, 1, 1)
    results: list[VerifiedDraw] = []
    current = start
    for index in range(count):
        while current.weekday() not in (2, 5):
            current += timedelta(days=1)
        mains = tuple(int(value) for value in sorted(rng.choice(47, 5, replace=False) + 1))
        mega = int(rng.integers(1, 28))
        fetched = datetime.combine(current + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        sources = (
            SourceEvidence(
                source_name="california_lottery",
                role="official",
                source_url="https://www.calottery.com/example",
                fetched_timestamp_utc=fetched,
                raw_sha256=f"{index:064x}"[-64:],
                parser_version="fixture-v1",
            ),
            SourceEvidence(
                source_name="lotteryusa",
                role="backup",
                source_url="https://www.lotteryusa.com/example",
                fetched_timestamp_utc=fetched,
                raw_sha256=f"{index + 1000:064x}"[-64:],
                parser_version="fixture-v1",
            ),
        )
        results.append(
            VerifiedDraw(
                draw_date=current,
                draw_id=str(3000 + index),
                mains=mains,
                mega=mega,
                verification=VerificationMetadata(
                    status="verified",
                    verified_timestamp_utc=fetched,
                    official_post_timestamp_utc=fetched - timedelta(hours=1),
                    sources=sources,
                    comparison_sha256=f"{index + 2000:064x}"[-64:],
                ),
            )
        )
        current += timedelta(days=1)
    return results


def test_backtest_prediction_is_invariant_to_target_numbers() -> None:
    history = _verified_history(63)
    altered = list(history)
    altered[-1] = altered[-1].model_copy(update={"mains": (1, 2, 3, 4, 47), "mega": 27})
    config = AppConfig()
    kwargs = {
        "history_snapshot_sha256": "a" * 64,
        "config": config,
        "evaluations": 1,
        "diagnostic_candidate_pool_size": 300,
        "optimization_simulations": 256,
        "final_simulations": 1_000,
    }

    original = run_backtest(history, **kwargs)["records"][0]
    changed_target = run_backtest(altered, **kwargs)["records"][0]

    for key in (
        "training_prefix_sha256",
        "random_seed",
        "parameters",
        "selection_basis",
        "fair_coverage_challenger",
        "predicted",
    ):
        assert original[key] == changed_target[key]
    for candidate in ("model_candidate", "fair_challenger"):
        original_candidate = original["champion_challenger_comparison"][candidate]
        changed_candidate = changed_target["champion_challenger_comparison"][candidate]
        assert (original_candidate is None) == (changed_candidate is None)
        if original_candidate is not None and changed_candidate is not None:
            assert (
                original_candidate["predicted_p_any_ge_3_mains"]
                == changed_candidate["predicted_p_any_ge_3_mains"]
            )
            assert (
                original_candidate["predicted_p_any_ge_4_mains"]
                == changed_candidate["predicted_p_any_ge_4_mains"]
            )


def test_backtest_seed_for_common_target_is_invariant_to_evaluation_count() -> None:
    history = _verified_history(64)
    config = AppConfig()

    def fold_seeds(evaluations: int) -> dict[date, int]:
        start = len(history) - evaluations
        return {
            history[target_index].draw_date: _seed(
                _prefix_sha256(history[:target_index]),
                history[target_index].draw_date,
                config.model_version,
            )
            for target_index in range(start, len(history))
        }

    one_fold = fold_seeds(1)
    two_folds = fold_seeds(2)
    common_target = history[-1].draw_date
    assert one_fold[common_target] == two_folds[common_target]


def test_backtest_prefix_seed_excludes_post_target_verification_provenance() -> None:
    history = _verified_history(64)
    prefix = history[:-1]
    altered_prefix: list[VerifiedDraw] = []
    for offset, draw in enumerate(prefix):
        changed_sources = tuple(
            source.model_copy(
                update={
                    "fetched_timestamp_utc": source.fetched_timestamp_utc
                    + timedelta(days=365 + offset),
                    "raw_sha256": f"{10_000 + offset:064x}"[-64:],
                }
            )
            for source in draw.verification.sources
        )
        changed_verification = draw.verification.model_copy(
            update={
                "verified_timestamp_utc": draw.verification.verified_timestamp_utc
                + timedelta(days=365 + offset),
                "sources": changed_sources,
                "comparison_sha256": f"{20_000 + offset:064x}"[-64:],
            }
        )
        altered_prefix.append(draw.model_copy(update={"verification": changed_verification}))

    original_hash = _prefix_sha256(prefix)
    altered_hash = _prefix_sha256(altered_prefix)
    target = history[-1].draw_date
    assert original_hash == altered_hash
    assert _seed(original_hash, target, AppConfig().model_version) == _seed(
        altered_hash, target, AppConfig().model_version
    )

    changed_fact = list(prefix)
    replacement_mega = 1 if changed_fact[-1].mega != 1 else 2
    changed_fact[-1] = changed_fact[-1].model_copy(update={"mega": replacement_mega})
    assert _prefix_sha256(changed_fact) != original_hash


def test_backtest_fold_seed_is_invariant_to_future_suffix_content() -> None:
    history = _verified_history(66)
    target_index = 63
    target_date = history[target_index].draw_date
    original_prefix_hash = _prefix_sha256(history[:target_index])
    original_seed = _seed(original_prefix_hash, target_date, AppConfig().model_version)

    altered = list(history)
    for index in range(target_index, len(altered)):
        original = altered[index]
        replacement_mains = (
            (1, 2, 3, 4, 47)
            if original.mains != (1, 2, 3, 4, 47)
            else (
                2,
                3,
                4,
                5,
                46,
            )
        )
        altered[index] = original.model_copy(
            update={"mains": replacement_mains, "mega": 27 if original.mega != 27 else 26}
        )

    altered_prefix_hash = _prefix_sha256(altered[:target_index])
    assert altered_prefix_hash == original_prefix_hash
    assert _seed(altered_prefix_hash, target_date, AppConfig().model_version) == original_seed


def test_backtest_report_records_cutoff_safe_seed_contract() -> None:
    history = _verified_history(63)
    report = run_backtest(
        history,
        history_snapshot_sha256="f" * 64,
        config=AppConfig(),
        evaluations=1,
        diagnostic_candidate_pool_size=300,
        optimization_simulations=256,
        final_simulations=1_000,
    )

    assert report["schema_version"] == 4
    assert report["backtest_prefix_algorithm_version"] == BACKTEST_PREFIX_ALGORITHM_VERSION
    assert report["backtest_seed_algorithm_version"] == BACKTEST_SEED_ALGORITHM_VERSION
    assert report["history_snapshot_used_for_seed"] is False
    assert (
        report["records"][0]["training_prefix_algorithm_version"]
        == BACKTEST_PREFIX_ALGORITHM_VERSION
    )

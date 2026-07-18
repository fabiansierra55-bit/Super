from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np

from slp_model.backtesting import run_backtest
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
        "predicted",
    ):
        assert original[key] == changed_target[key]

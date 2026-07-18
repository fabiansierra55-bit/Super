from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from slp_model.source_runtime import SourceManager
from slp_model.sources import CaliforniaLotteryAdapter, LotteryUSAAdapter
from slp_model.storage import AppendOnlyLog
from slp_model.verification import verify_draw

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
FETCHED_AT = datetime(2026, 7, 16, 4, 0, tzinfo=UTC)


def test_source_success_audit_is_idempotent_for_identical_evidence(tmp_path: Path) -> None:
    official = CaliforniaLotteryAdapter.parse(
        (FIXTURES / "calottery_page.json").read_bytes(),
        source_url="https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/1/20",
        fetched_at_utc=FETCHED_AT,
    )
    backup = LotteryUSAAdapter.parse(
        (FIXTURES / "lotteryusa_year.html").read_bytes(),
        source_url="https://www.lotteryusa.com/california/super-lotto-plus/year",
        fetched_at_utc=FETCHED_AT,
    )
    verified = verify_draw(
        official,
        backup,
        date(2026, 7, 15),
        as_of_utc=FETCHED_AT,
    )
    replay = replace(verified, verified_at_utc=FETCHED_AT + timedelta(hours=1))

    manager = object.__new__(SourceManager)
    manager.audit_log = AppendOnlyLog(tmp_path / "events.jsonl")
    manager._record_success(verified)
    manager._record_success(replay)

    assert manager.audit_log.verify() == 1

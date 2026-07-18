from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from slp_model.models import Draw
from slp_model.sources import (
    CaliforniaLotteryAdapter,
    LotteryNetAdapter,
    LotteryUSAAdapter,
    SourceRecord,
)
from slp_model.verification import (
    InsufficientEvidenceError,
    InvalidEvidenceError,
    PrematureDrawError,
    SourceMismatchError,
    expected_latest_posted_draw_date,
    latest_eligible_official_date,
    verify_draw,
    verify_history,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
FETCHED_AT = datetime(2026, 7, 16, 4, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
TARGET_DATE = date(2026, 7, 15)


def load_evidence() -> tuple[
    tuple[SourceRecord, ...],
    tuple[SourceRecord, ...],
    tuple[SourceRecord, ...],
]:
    official = CaliforniaLotteryAdapter.parse(
        (FIXTURES / "calottery_page.json").read_bytes(),
        source_url=("https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/1/20"),
        fetched_at_utc=FETCHED_AT,
    )
    lotteryusa = LotteryUSAAdapter.parse(
        (FIXTURES / "lotteryusa_year.html").read_bytes(),
        source_url="https://www.lotteryusa.com/california/super-lotto-plus/year",
        fetched_at_utc=FETCHED_AT,
    )
    lotterynet = LotteryNetAdapter.parse(
        (FIXTURES / "lotterynet_2026.html").read_bytes(),
        source_url=("https://www.lottery.net/california/superlotto-plus/numbers/2026"),
        fetched_at_utc=FETCHED_AT,
    )
    return official, lotteryusa, lotterynet


def record_for(records: tuple[SourceRecord, ...], wanted: date) -> SourceRecord:
    return next(item for item in records if item.draw.draw_date == wanted)


def test_exact_official_plus_two_backup_consensus() -> None:
    official, lotteryusa, lotterynet = load_evidence()

    verified = verify_draw(
        official,
        (*lotteryusa, *lotterynet),
        TARGET_DATE,
        as_of_utc=AS_OF,
    )

    assert verified.draw.mains == (2, 5, 34, 36, 37)
    assert verified.draw.mega == 3
    assert verified.draw_id == "4099"
    assert {item.source_name for item in verified.evidence} == {
        "california_lottery",
        "lotteryusa",
        "lottery_net",
    }
    metadata = verified.source_verification_metadata
    assert metadata["verification_status"] == "verified_two_source"
    assert len(metadata["verification_id"]) == 64
    assert len(metadata["sources"]) == 3
    json.dumps(verified.as_audit_dict())


def test_one_disagreeing_backup_halts_even_when_another_agrees() -> None:
    official, lotteryusa, lotterynet = load_evidence()
    target = record_for(lotteryusa, TARGET_DATE)
    bad = replace(
        target,
        draw=Draw(
            draw_date=TARGET_DATE,
            mains=target.draw.mains,
            mega=4,
        ),
    )
    backups = tuple(item for item in lotteryusa if item is not target) + (
        bad,
        *lotterynet,
    )

    with pytest.raises(SourceMismatchError) as raised:
        verify_draw(official, backups, TARGET_DATE, as_of_utc=AS_OF)

    audit = raised.value.audit_record
    assert audit["status"] == "mismatch"
    assert audit["details"]["differences"]["mega"] == {
        "official": 3,
        "backup": 4,
    }
    assert "verification_id" not in audit


def test_draw_id_disagreement_halts() -> None:
    official, _, lotterynet = load_evidence()
    target = record_for(lotterynet, TARGET_DATE)
    wrong_id = replace(target, draw_id="9999")
    backups = tuple(item for item in lotterynet if item is not target) + (wrong_id,)

    with pytest.raises(SourceMismatchError) as raised:
        verify_draw(official, backups, TARGET_DATE, as_of_utc=AS_OF)

    assert raised.value.audit_record["details"]["differences"]["draw_id"] == {
        "official": "4099",
        "backup": "9999",
    }


def test_draw_id_associated_with_wrong_date_halts() -> None:
    official, _, lotterynet = load_evidence()
    target = record_for(lotterynet, TARGET_DATE)
    wrong_date = replace(
        target,
        draw=Draw(
            draw_date=date(2026, 7, 12),
            mains=target.draw.mains,
            mega=target.draw.mega,
        ),
    )
    backups = tuple(item for item in lotterynet if item is not target) + (wrong_date,)

    with pytest.raises(SourceMismatchError) as raised:
        verify_draw(official, backups, TARGET_DATE, as_of_utc=AS_OF)

    assert raised.value.audit_record["details"]["differences"]["draw_date"] == {
        "official": "2026-07-15",
        "backup": "2026-07-12",
    }


def test_missing_backup_result_never_verifies() -> None:
    official, lotteryusa, _ = load_evidence()
    only_other_date = tuple(item for item in lotteryusa if item.draw.draw_date != TARGET_DATE)

    with pytest.raises(InsufficientEvidenceError) as raised:
        verify_draw(official, only_other_date, TARGET_DATE, as_of_utc=AS_OF)

    assert raised.value.audit_record["details"]["reason"] == "backup_result_missing"
    assert raised.value.audit_record["details"]["backup_fetches"][0]["requested_url"].startswith(
        "https://www.lotteryusa.com/"
    )


def test_unapproved_backup_is_rejected() -> None:
    official, lotteryusa, _ = load_evidence()
    unapproved = tuple(replace(item, source_name="random_blog") for item in lotteryusa)

    with pytest.raises(InvalidEvidenceError, match="unapproved"):
        verify_draw(official, unapproved, TARGET_DATE, as_of_utc=AS_OF)


def test_guard_halts_before_eight_pm_pacific() -> None:
    official, lotteryusa, _ = load_evidence()
    before_gate = datetime(2026, 7, 16, 2, 59, tzinfo=UTC)

    with pytest.raises(PrematureDrawError) as raised:
        verify_draw(official, lotteryusa, TARGET_DATE, as_of_utc=before_gate)

    assert raised.value.audit_record["status"] == "premature"
    assert raised.value.audit_record["details"]["official_post_gate_pt"].endswith("-07:00")


def test_evidence_fetched_before_post_gate_is_rejected() -> None:
    official, lotteryusa, _ = load_evidence()
    before_gate = datetime(2026, 7, 16, 2, 50, tzinfo=UTC)
    early_official = tuple(replace(item, fetched_at_utc=before_gate) for item in official)

    with pytest.raises(PrematureDrawError) as raised:
        verify_draw(early_official, lotteryusa, TARGET_DATE, as_of_utc=AS_OF)

    assert raised.value.audit_record["status"] == "premature_source_evidence"


def test_only_wednesday_and_saturday_are_valid_draw_dates() -> None:
    official, lotteryusa, _ = load_evidence()

    with pytest.raises(InvalidEvidenceError, match="Wednesday or Saturday"):
        verify_draw(
            official,
            lotteryusa,
            date(2026, 7, 16),
            as_of_utc=AS_OF,
        )


def test_history_requires_consensus_for_every_requested_date() -> None:
    official, lotteryusa, _ = load_evidence()

    verified = verify_history(
        official,
        lotteryusa,
        draw_dates=(date(2026, 7, 11), TARGET_DATE),
        as_of_utc=AS_OF,
    )

    assert [item.draw.draw_date for item in verified] == [
        date(2026, 7, 11),
        TARGET_DATE,
    ]
    assert latest_eligible_official_date(official, as_of_utc=AS_OF) == TARGET_DATE


def test_history_rejects_duplicate_requested_dates() -> None:
    official, lotteryusa, _ = load_evidence()

    with pytest.raises(InvalidEvidenceError, match="duplicate draw dates"):
        verify_history(
            official,
            lotteryusa,
            draw_dates=(TARGET_DATE, TARGET_DATE),
            as_of_utc=AS_OF,
        )


def test_expected_latest_date_is_independent_of_stale_source_records() -> None:
    official, _, _ = load_evidence()
    after_saturday_gate = datetime(2026, 7, 19, 4, 0, tzinfo=UTC)

    assert latest_eligible_official_date(official, as_of_utc=after_saturday_gate) == TARGET_DATE
    assert expected_latest_posted_draw_date(as_of_utc=after_saturday_gate) == date(2026, 7, 18)


def test_evidence_cannot_postdate_verification() -> None:
    official, lotteryusa, _ = load_evidence()

    with pytest.raises(InvalidEvidenceError, match="later than verification"):
        verify_draw(
            official,
            lotteryusa,
            TARGET_DATE,
            as_of_utc=FETCHED_AT - timedelta(seconds=1),
        )

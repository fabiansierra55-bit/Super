"""Strict two-source consensus and Pacific-time publication guards.

Only the California Lottery adapter may supply official evidence.  A draw is
verified only when at least one distinct approved backup source asserts the
same date, five normalized mains, Mega number, and (when both sources publish
one) draw id.  Every disagreement raises an exception carrying an audit-ready
record; callers must persist that record and stop their workflow.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from .models import Draw
from .sources import SourceRecord, deduplicate_source_records

PACIFIC = ZoneInfo("America/Los_Angeles")
# Draw entry closes at 7:45 p.m. PT.  Results are normally published shortly
# after the drawing; 8:00 p.m. is a deliberately conservative default gate.
DEFAULT_OFFICIAL_POST_TIME_PT = time(20, 0)
OFFICIAL_SOURCE_NAME = "california_lottery"
APPROVED_BACKUP_SOURCE_NAMES = frozenset({"lotteryusa", "lottery_net", "lotterycorner"})


class VerificationError(RuntimeError):
    """Base fail-closed verification exception with audit metadata."""

    def __init__(self, message: str, audit_record: Mapping[str, Any]):
        super().__init__(message)
        self.audit_record = dict(audit_record)


class InsufficientEvidenceError(VerificationError):
    """The required official-plus-backup evidence was not present."""


class SourceMismatchError(VerificationError):
    """Two named sources disagreed about a draw identity or result."""


class PrematureDrawError(VerificationError):
    """Verification was attempted before the conservative Pacific post gate."""


class InvalidEvidenceError(VerificationError):
    """Evidence metadata, schedule, or source identity was invalid."""


@dataclass(frozen=True, slots=True)
class VerifiedDraw:
    """A normalized result backed by official and independent evidence."""

    draw: Draw
    draw_id: str | None
    verified_at_utc: datetime
    evidence: tuple[SourceRecord, ...]
    verification_id: str
    post_time_pt: time = DEFAULT_OFFICIAL_POST_TIME_PT
    verification_status: str = "verified_two_source"

    def __post_init__(self) -> None:
        verified_at = _aware_utc(self.verified_at_utc)
        object.__setattr__(self, "verified_at_utc", verified_at)
        official = [
            item
            for item in self.evidence
            if item.source_name == OFFICIAL_SOURCE_NAME and item.official
        ]
        backups = [
            item
            for item in self.evidence
            if item.source_name in APPROVED_BACKUP_SOURCE_NAMES and not item.official
        ]
        if len(official) != 1 or not backups:
            raise ValueError("verified draw requires official and approved backup evidence")
        if len(official) + len(backups) != len(self.evidence):
            raise ValueError("verified draw contains unapproved evidence")
        if len({item.source_name for item in self.evidence}) != len(self.evidence):
            raise ValueError("verified draw contains duplicate source evidence")
        if any(item.draw != self.draw for item in self.evidence):
            raise ValueError("verified evidence does not exactly match the normalized draw")
        if official[0].draw_id != self.draw_id:
            raise ValueError("verified draw id does not match official evidence")
        if any(
            item.draw_id is not None and self.draw_id is not None and item.draw_id != self.draw_id
            for item in backups
        ):
            raise ValueError("backup draw id does not match official evidence")
        if self.verification_status != "verified_two_source":
            raise ValueError("unsupported verification status")
        expected_id = _verification_digest(
            self.draw,
            self.draw_id,
            self.evidence,
            self.post_time_pt,
        )
        if not _is_sha256(self.verification_id) or self.verification_id != expected_id:
            raise ValueError("verification_id does not match canonical evidence")

    @property
    def source_verification_metadata(self) -> dict[str, Any]:
        """Serializable metadata suitable for a locked bundle snapshot."""

        return {
            "verification_status": self.verification_status,
            "verification_id": self.verification_id,
            "verified_at_utc": self.verified_at_utc.isoformat(),
            "draw_date": self.draw.draw_date.isoformat(),
            "draw_id": self.draw_id,
            "mains": list(self.draw.mains),
            "mega": self.draw.mega,
            "official_post_time_pt": self.post_time_pt.isoformat(),
            "sources": [item.as_audit_dict() for item in self.evidence],
        }

    def as_audit_dict(self) -> dict[str, Any]:
        return {
            "event_type": "source_verification",
            "status": self.verification_status,
            **self.source_verification_metadata,
        }


def official_post_timestamp(
    draw_date: date,
    *,
    post_time_pt: time = DEFAULT_OFFICIAL_POST_TIME_PT,
) -> datetime:
    """Return the configured post gate as an aware Pacific datetime."""

    if post_time_pt.tzinfo is not None:
        raise ValueError("post_time_pt must be a naive wall-clock time")
    return datetime.combine(draw_date, post_time_pt, tzinfo=PACIFIC)


def verify_draw(
    official_records: Sequence[SourceRecord],
    backup_records: Sequence[SourceRecord],
    draw_date: date,
    *,
    as_of_utc: datetime | None = None,
    post_time_pt: time = DEFAULT_OFFICIAL_POST_TIME_PT,
) -> VerifiedDraw:
    """Verify exactly one draw, raising audit-rich errors on every unsafe state."""

    as_of = _aware_utc(as_of_utc or datetime.now(UTC))
    _validate_scheduled_draw_date(draw_date, as_of=as_of)
    gate = official_post_timestamp(draw_date, post_time_pt=post_time_pt)
    if as_of < gate.astimezone(UTC):
        raise PrematureDrawError(
            "draw cannot be verified before the official Pacific post gate",
            _failure_audit(
                status="premature",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={
                    "official_post_gate_pt": gate.isoformat(),
                    "attempted_at_utc": as_of.isoformat(),
                },
            ),
        )

    official = deduplicate_source_records(official_records)
    backups = deduplicate_source_records(backup_records)
    _validate_source_roles(official, backups, draw_date=draw_date, as_of=as_of)

    official_for_date = [item for item in official if item.draw.draw_date == draw_date]
    if len(official_for_date) != 1:
        raise InsufficientEvidenceError(
            "exactly one official assertion is required for the requested draw date",
            _failure_audit(
                status="insufficient_evidence",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={
                    "reason": "official_result_missing",
                    "official_records_for_date": [
                        item.as_audit_dict() for item in official_for_date
                    ],
                    "official_fetches": _source_fetch_metadata(official),
                },
            ),
        )
    official_record = official_for_date[0]
    _validate_evidence_timestamp(
        official_record,
        gate=gate,
        as_of=as_of,
        draw_date=draw_date,
    )

    backups_by_source: dict[str, list[SourceRecord]] = {}
    for record in backups:
        backups_by_source.setdefault(record.source_name, []).append(record)

    agreements: list[SourceRecord] = []
    missing_sources: list[str] = []
    for source_name, source_records in sorted(backups_by_source.items()):
        matching_date = [item for item in source_records if item.draw.draw_date == draw_date]
        if not matching_date:
            matching_id = [
                item
                for item in source_records
                if official_record.draw_id is not None and item.draw_id == official_record.draw_id
            ]
            if matching_id:
                backup = matching_id[0]
                raise SourceMismatchError(
                    f"{source_name} associates the official draw id with another date",
                    _failure_audit(
                        status="mismatch",
                        draw_date=draw_date,
                        detected_at_utc=as_of,
                        details={
                            "official": official_record.as_audit_dict(),
                            "backup": backup.as_audit_dict(),
                            "differences": _compare_assertions(official_record, backup),
                        },
                    ),
                )
            missing_sources.append(source_name)
            continue
        if len(matching_date) != 1:
            # Normalization should make this unreachable, but fail explicitly.
            raise InvalidEvidenceError(
                "backup source has ambiguous records for the requested date",
                _failure_audit(
                    status="invalid_evidence",
                    draw_date=draw_date,
                    detected_at_utc=as_of,
                    details={
                        "source_name": source_name,
                        "records": [item.as_audit_dict() for item in matching_date],
                    },
                ),
            )
        backup = matching_date[0]
        _validate_evidence_timestamp(backup, gate=gate, as_of=as_of, draw_date=draw_date)
        differences = _compare_assertions(official_record, backup)
        if differences:
            raise SourceMismatchError(
                f"{source_name} disagrees with the official result for {draw_date}",
                _failure_audit(
                    status="mismatch",
                    draw_date=draw_date,
                    detected_at_utc=as_of,
                    details={
                        "official": official_record.as_audit_dict(),
                        "backup": backup.as_audit_dict(),
                        "differences": differences,
                    },
                ),
            )
        agreements.append(backup)

    if not agreements:
        raise InsufficientEvidenceError(
            "no approved backup source agrees on the requested draw",
            _failure_audit(
                status="insufficient_evidence",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={
                    "reason": "backup_result_missing",
                    "backup_sources_checked": sorted(backups_by_source),
                    "backup_sources_missing_draw": missing_sources,
                    "backup_fetches": _source_fetch_metadata(backups),
                    "official": official_record.as_audit_dict(),
                },
            ),
        )

    evidence = (official_record, *agreements)
    verification_id = _verification_digest(
        official_record.draw,
        official_record.draw_id,
        evidence,
        post_time_pt,
    )
    return VerifiedDraw(
        draw=official_record.draw,
        draw_id=official_record.draw_id,
        verified_at_utc=as_of,
        evidence=evidence,
        verification_id=verification_id,
        post_time_pt=post_time_pt,
    )


def verify_history(
    official_records: Sequence[SourceRecord],
    backup_records: Sequence[SourceRecord],
    *,
    draw_dates: Iterable[date] | None = None,
    as_of_utc: datetime | None = None,
    post_time_pt: time = DEFAULT_OFFICIAL_POST_TIME_PT,
) -> tuple[VerifiedDraw, ...]:
    """Verify a bounded history without accepting partial or mismatched rows.

    When ``draw_dates`` is omitted, every official record whose post gate has
    passed is in scope.  A caller rebuilding only a bounded window should pass
    that exact date set; every requested date must be present and verified.
    """

    as_of = _aware_utc(as_of_utc or datetime.now(UTC))
    official = deduplicate_source_records(official_records)
    backups = deduplicate_source_records(backup_records)
    if draw_dates is None:
        selected_dates = sorted(
            {
                record.draw.draw_date
                for record in official
                if as_of
                >= official_post_timestamp(
                    record.draw.draw_date, post_time_pt=post_time_pt
                ).astimezone(UTC)
            }
        )
    else:
        supplied_dates = list(draw_dates)
        if len(supplied_dates) != len(set(supplied_dates)):
            raise InvalidEvidenceError(
                "requested history contains duplicate draw dates",
                _failure_audit(
                    status="invalid_request",
                    draw_date=None,
                    detected_at_utc=as_of,
                    details={
                        "reason": "duplicate_requested_dates",
                        "draw_dates": [item.isoformat() for item in supplied_dates],
                    },
                ),
            )
        selected_dates = sorted(supplied_dates)
    if not selected_dates:
        raise InsufficientEvidenceError(
            "history verification has no eligible draw dates",
            _failure_audit(
                status="insufficient_evidence",
                draw_date=None,
                detected_at_utc=as_of,
                details={"reason": "no_eligible_draw_dates"},
            ),
        )

    verified = [
        verify_draw(
            official,
            backups,
            selected_date,
            as_of_utc=as_of,
            post_time_pt=post_time_pt,
        )
        for selected_date in selected_dates
    ]
    return tuple(verified)


def latest_eligible_official_date(
    records: Sequence[SourceRecord],
    *,
    as_of_utc: datetime | None = None,
    post_time_pt: time = DEFAULT_OFFICIAL_POST_TIME_PT,
) -> date:
    """Return the latest official record whose Pacific post gate has passed."""

    as_of = _aware_utc(as_of_utc or datetime.now(UTC))
    official = deduplicate_source_records(records)
    eligible = [
        item.draw.draw_date
        for item in official
        if item.source_name == OFFICIAL_SOURCE_NAME
        and item.official
        and as_of
        >= official_post_timestamp(item.draw.draw_date, post_time_pt=post_time_pt).astimezone(UTC)
    ]
    if not eligible:
        raise InsufficientEvidenceError(
            "no eligible official draw has passed the post gate",
            _failure_audit(
                status="insufficient_evidence",
                draw_date=None,
                detected_at_utc=as_of,
                details={"reason": "no_eligible_official_draw"},
            ),
        )
    return max(eligible)


def expected_latest_posted_draw_date(
    *,
    as_of_utc: datetime | None = None,
    post_time_pt: time = DEFAULT_OFFICIAL_POST_TIME_PT,
) -> date:
    """Return the latest scheduled draw whose conservative post gate passed."""

    as_of = _aware_utc(as_of_utc or datetime.now(UTC))
    candidate = as_of.astimezone(PACIFIC).date()
    for offset in range(8):
        proposed = candidate - timedelta(days=offset)
        if proposed.weekday() not in (2, 5):
            continue
        if as_of >= official_post_timestamp(proposed, post_time_pt=post_time_pt).astimezone(UTC):
            return proposed
    raise AssertionError("a Wednesday or Saturday must occur within eight days")


def _validate_source_roles(
    official: Sequence[SourceRecord],
    backups: Sequence[SourceRecord],
    *,
    draw_date: date,
    as_of: datetime,
) -> None:
    invalid_official = [
        item for item in official if item.source_name != OFFICIAL_SOURCE_NAME or not item.official
    ]
    invalid_backups = [
        item
        for item in backups
        if item.source_name not in APPROVED_BACKUP_SOURCE_NAMES or item.official
    ]
    if invalid_official or invalid_backups:
        raise InvalidEvidenceError(
            "evidence includes an unapproved or incorrectly classified source",
            _failure_audit(
                status="invalid_evidence",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={
                    "invalid_official_records": [item.as_audit_dict() for item in invalid_official],
                    "invalid_backup_records": [item.as_audit_dict() for item in invalid_backups],
                    "approved_backup_sources": sorted(APPROVED_BACKUP_SOURCE_NAMES),
                },
            ),
        )
    if not official or not backups:
        raise InsufficientEvidenceError(
            "official and approved backup evidence are both required",
            _failure_audit(
                status="insufficient_evidence",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={
                    "official_record_count": len(official),
                    "backup_record_count": len(backups),
                },
            ),
        )


def _validate_evidence_timestamp(
    record: SourceRecord,
    *,
    gate: datetime,
    as_of: datetime,
    draw_date: date,
) -> None:
    gate_utc = gate.astimezone(UTC)
    if record.fetched_at_utc < gate_utc:
        raise PrematureDrawError(
            "source evidence was fetched before the Pacific post gate",
            _failure_audit(
                status="premature_source_evidence",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={
                    "official_post_gate_pt": gate.isoformat(),
                    "evidence": record.as_audit_dict(),
                },
            ),
        )
    if record.fetched_at_utc > as_of:
        raise InvalidEvidenceError(
            "source fetch timestamp is later than verification time",
            _failure_audit(
                status="invalid_evidence",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={
                    "reason": "future_fetch_timestamp",
                    "evidence": record.as_audit_dict(),
                },
            ),
        )


def _validate_scheduled_draw_date(draw_date: date, *, as_of: datetime) -> None:
    # datetime.weekday(): Wednesday=2, Saturday=5.
    if draw_date.weekday() not in (2, 5):
        raise InvalidEvidenceError(
            "SuperLotto Plus draw date must be Wednesday or Saturday",
            _failure_audit(
                status="invalid_evidence",
                draw_date=draw_date,
                detected_at_utc=as_of,
                details={"reason": "invalid_draw_weekday"},
            ),
        )


def _compare_assertions(official: SourceRecord, backup: SourceRecord) -> dict[str, Any]:
    differences: dict[str, Any] = {}
    if official.draw.draw_date != backup.draw.draw_date:
        differences["draw_date"] = {
            "official": official.draw.draw_date.isoformat(),
            "backup": backup.draw.draw_date.isoformat(),
        }
    if official.draw.mains != backup.draw.mains:
        differences["mains"] = {
            "official": list(official.draw.mains),
            "backup": list(backup.draw.mains),
        }
    if official.draw.mega != backup.draw.mega:
        differences["mega"] = {
            "official": official.draw.mega,
            "backup": backup.draw.mega,
        }
    if (
        official.draw_id is not None
        and backup.draw_id is not None
        and official.draw_id != backup.draw_id
    ):
        differences["draw_id"] = {
            "official": official.draw_id,
            "backup": backup.draw_id,
        }
    return differences


def _verification_digest(
    draw: Draw,
    draw_id: str | None,
    evidence: Sequence[SourceRecord],
    post_time_pt: time,
) -> str:
    canonical = {
        "schema_version": 1,
        "draw_date": draw.draw_date.isoformat(),
        "draw_id": draw_id,
        "mains": list(draw.mains),
        "mega": draw.mega,
        "post_time_pt": post_time_pt.isoformat(),
        "evidence": [
            {
                "source_name": item.source_name,
                "source_url": item.source_url,
                "requested_url": item.requested_url,
                "fetched_at_utc": item.fetched_at_utc.isoformat(),
                "content_sha256": item.content_sha256,
                "draw_id": item.draw_id,
            }
            for item in sorted(evidence, key=lambda record: record.source_name)
        ],
    }
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _failure_audit(
    *,
    status: str,
    draw_date: date | None,
    detected_at_utc: datetime,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "event_type": "source_verification",
        "status": status,
        "draw_date": draw_date.isoformat() if draw_date is not None else None,
        "detected_at_utc": _aware_utc(detected_at_utc).isoformat(),
        "details": dict(details),
    }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _source_fetch_metadata(records: Sequence[SourceRecord]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, datetime, str], SourceRecord] = {}
    for record in records:
        key = (
            record.source_name,
            record.requested_url,
            record.fetched_at_utc,
            record.content_sha256,
        )
        unique[key] = record
    return [
        {
            "source_name": record.source_name,
            "requested_url": record.requested_url,
            "source_url": record.source_url,
            "fetched_at_utc": record.fetched_at_utc.isoformat(),
            "content_sha256": record.content_sha256,
            "from_cache": record.from_cache,
        }
        for record in sorted(
            unique.values(),
            key=lambda item: (
                item.source_name,
                item.requested_url,
                item.fetched_at_utc,
            ),
        )
    ]


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = [
    "APPROVED_BACKUP_SOURCE_NAMES",
    "DEFAULT_OFFICIAL_POST_TIME_PT",
    "InsufficientEvidenceError",
    "InvalidEvidenceError",
    "OFFICIAL_SOURCE_NAME",
    "PACIFIC",
    "PrematureDrawError",
    "SourceMismatchError",
    "VerificationError",
    "VerifiedDraw",
    "latest_eligible_official_date",
    "official_post_timestamp",
    "verify_draw",
    "verify_history",
]

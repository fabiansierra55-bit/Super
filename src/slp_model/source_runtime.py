"""Runtime orchestration for fetching, verifying, and auditing source consensus."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from .config import AppConfig
from .models import SourceEvidence, VerificationMetadata, VerifiedDraw
from .sources import (
    CaliforniaLotteryAdapter,
    HttpFetcher,
    LotteryNetAdapter,
    LotteryUSAAdapter,
    SessionLike,
    SourceError,
    SourceRecord,
    build_retry_session,
    deduplicate_source_records,
)
from .storage import AppendOnlyLog, canonical_json_bytes, sha256_bytes
from .verification import (
    VerificationError as SourceVerificationError,
)
from .verification import (
    VerifiedDraw as SourceVerifiedDraw,
)
from .verification import (
    expected_latest_posted_draw_date,
    official_post_timestamp,
    verify_draw,
    verify_history,
)


class SourceManager:
    """Use one official adapter and every enabled implemented backup adapter."""

    def __init__(
        self,
        *,
        config: AppConfig,
        cache_dir: Path,
        audit_log: AppendOnlyLog,
    ) -> None:
        self.config = config
        self.audit_log = audit_log
        session = build_retry_session(
            user_agent=config.sources.user_agent,
            retry_total=config.sources.retries,
            backoff_factor=config.sources.backoff_seconds,
        )
        fetcher = HttpFetcher(
            session=cast(SessionLike, session),
            timeout=(
                min(5.0, config.sources.request_timeout_seconds),
                config.sources.request_timeout_seconds,
            ),
            cache_dir=cache_dir,
            cache_ttl=timedelta(seconds=config.sources.cache_ttl_seconds),
        )
        self.official = CaliforniaLotteryAdapter(fetcher=fetcher)
        enabled = {
            endpoint.name
            for endpoint in config.sources.endpoints
            if endpoint.enabled and endpoint.role == "backup"
        }
        self.lotteryusa = LotteryUSAAdapter(fetcher=fetcher) if "lotteryusa" in enabled else None
        self.lottery_net = LotteryNetAdapter(fetcher=fetcher) if "lottery_net" in enabled else None
        unsupported = enabled - {"lotteryusa", "lottery_net"}
        if unsupported:
            raise ValueError(
                "enabled source adapters are not implemented: " + ", ".join(sorted(unsupported))
            )

    def _record_failure(self, error: BaseException, *, operation: str) -> None:
        if isinstance(error, SourceVerificationError):
            details = error.audit_record
        else:
            details = {
                "event_type": "source_acquisition",
                "status": "failed",
                "operation": operation,
                "error_type": type(error).__name__,
                "error": str(error),
            }
        identity = sha256_bytes(canonical_json_bytes(details))
        self.audit_log.append(
            event_id=f"source-failure:{identity}",
            event_type="source_verification_failed",
            payload=details,
        )

    def _record_success(self, verified: SourceVerifiedDraw) -> None:
        event_id = f"source-verification:{verified.verification_id}"
        # The verification digest already binds the normalized result, source
        # URLs, raw payload hashes, and fetch timestamps. Re-observing the same
        # cached evidence is an idempotent replay even though the caller's
        # wall-clock verification timestamp is later.
        if any(event["event_id"] == event_id for event in self.audit_log.read()):
            return
        payload = verified.as_audit_dict()
        self.audit_log.append(
            event_id=event_id,
            event_type="source_verification_succeeded",
            payload=payload,
            timestamp_utc=verified.verified_at_utc,
        )

    def _latest_backup_records(self, year: int) -> tuple[SourceRecord, ...]:
        records: list[SourceRecord] = []
        if self.lotteryusa is not None:
            records.extend(self.lotteryusa.fetch_history())
        if self.lottery_net is not None:
            records.extend(self.lottery_net.fetch_history(year=year))
        if not records:
            raise RuntimeError("no enabled backup source returned history")
        return deduplicate_source_records(records)

    def verify_latest(self, *, as_of_utc: datetime | None = None) -> VerifiedDraw:
        requested_as_of = (as_of_utc or datetime.now(UTC)).astimezone(UTC)
        try:
            official_records = self.official.fetch_history(pages=1, size=20)
            draw_date = expected_latest_posted_draw_date(
                as_of_utc=requested_as_of,
                post_time_pt=self.config.game.official_post_time_pacific,
            )
            backups = self._latest_backup_records(draw_date.year)
            verified_at = max(
                requested_as_of,
                datetime.now(UTC),
                *(record.fetched_at_utc for record in (*official_records, *backups)),
            )
            verified = verify_draw(
                official_records,
                backups,
                draw_date,
                as_of_utc=verified_at,
                post_time_pt=self.config.game.official_post_time_pacific,
            )
        except (SourceError, SourceVerificationError, RuntimeError, ValueError) as exc:
            self._record_failure(exc, operation="verify_latest")
            raise
        self._record_success(verified)
        return to_domain_verified_draw(verified)

    def verify_date(self, draw_date: date, *, as_of_utc: datetime | None = None) -> VerifiedDraw:
        requested_as_of = (as_of_utc or datetime.now(UTC)).astimezone(UTC)
        approximate_draws = max((requested_as_of.date() - draw_date).days * 2 / 7, 0) + 20
        pages = max(1, math.ceil(approximate_draws / 20))
        if pages > 200:
            raise ValueError("requested draw is outside the bounded online archive scan")
        try:
            official_records = self.official.fetch_history(pages=pages, size=20)
            backups: list[SourceRecord] = []
            if self.lottery_net is not None:
                backups.extend(self.lottery_net.fetch_history(year=draw_date.year))
            if self.lotteryusa is not None:
                backups.extend(self.lotteryusa.fetch_history())
            if not backups:
                raise RuntimeError("no enabled backup source returned verification evidence")
            normalized_backups = deduplicate_source_records(backups)
            verified_at = max(
                requested_as_of,
                datetime.now(UTC),
                *(record.fetched_at_utc for record in (*official_records, *normalized_backups)),
            )
            verified = verify_draw(
                official_records,
                normalized_backups,
                draw_date,
                as_of_utc=verified_at,
                post_time_pt=self.config.game.official_post_time_pacific,
            )
        except (SourceError, SourceVerificationError, RuntimeError, ValueError) as exc:
            self._record_failure(exc, operation="verify_date")
            raise
        self._record_success(verified)
        return to_domain_verified_draw(verified)

    def rebuild_history(
        self,
        *,
        minimum_draws: int = 240,
        as_of_utc: datetime | None = None,
    ) -> tuple[VerifiedDraw, ...]:
        if minimum_draws < 60:
            raise ValueError("history rebuild requires at least 60 draws")
        requested_as_of = (as_of_utc or datetime.now(UTC)).astimezone(UTC)
        pages = math.ceil(minimum_draws / 20)
        try:
            official_records = self.official.fetch_history(pages=pages, size=20)
            eligible = [
                record
                for record in official_records
                if requested_as_of
                >= official_post_timestamp(
                    record.draw.draw_date,
                    post_time_pt=self.config.game.official_post_time_pacific,
                ).astimezone(UTC)
            ]
            if len(eligible) < minimum_draws:
                raise RuntimeError(
                    f"official source returned {len(eligible)} eligible draws; "
                    f"{minimum_draws} required"
                )
            expected_latest = expected_latest_posted_draw_date(
                as_of_utc=requested_as_of,
                post_time_pt=self.config.game.official_post_time_pacific,
            )
            if eligible[-1].draw.draw_date != expected_latest:
                raise RuntimeError(
                    "official source has not published the latest scheduled draw: "
                    f"expected {expected_latest}, found {eligible[-1].draw.draw_date}"
                )
            selected = eligible[-minimum_draws:]
            years = sorted({record.draw.draw_date.year for record in selected})
            backups: list[SourceRecord] = []
            if self.lottery_net is not None:
                for year in years:
                    backups.extend(self.lottery_net.fetch_history(year=year))
            if self.lotteryusa is not None:
                backups.extend(self.lotteryusa.fetch_history())
            if not backups:
                raise RuntimeError("no enabled backup source returned rebuild evidence")
            normalized_backups = deduplicate_source_records(backups)
            verified_at = max(
                requested_as_of,
                datetime.now(UTC),
                *(record.fetched_at_utc for record in (*official_records, *normalized_backups)),
            )
            verified = verify_history(
                official_records,
                normalized_backups,
                draw_dates=[record.draw.draw_date for record in selected],
                as_of_utc=verified_at,
                post_time_pt=self.config.game.official_post_time_pacific,
            )
        except (SourceError, SourceVerificationError, RuntimeError, ValueError) as exc:
            self._record_failure(exc, operation="rebuild_history")
            raise
        for item in verified:
            self._record_success(item)
        return tuple(to_domain_verified_draw(item) for item in verified)


def to_domain_verified_draw(value: SourceVerifiedDraw) -> VerifiedDraw:
    evidence = tuple(
        SourceEvidence(
            source_name=record.source_name,
            role="official" if record.official else "backup",
            source_url=record.source_url,
            fetched_timestamp_utc=record.fetched_at_utc,
            raw_sha256=record.content_sha256,
            draw_id=record.draw_id,
            parser_version=record.parser_version,
            http_status=None,
            cache_hit=record.from_cache,
        )
        for record in value.evidence
    )
    metadata = VerificationMetadata(
        status="verified",
        verified_timestamp_utc=value.verified_at_utc,
        official_post_timestamp_utc=official_post_timestamp(
            value.draw.draw_date, post_time_pt=value.post_time_pt
        ).astimezone(UTC),
        sources=evidence,
        comparison_sha256=value.verification_id,
    )
    return VerifiedDraw(
        draw_date=value.draw.draw_date,
        draw_id=value.draw_id,
        mains=value.draw.mains,
        mega=value.draw.mega,
        verification=metadata,
    )

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from slp_model.models import (
    Draw,
    SourceEvidence,
    VerificationMetadata,
    VerifiedDraw,
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

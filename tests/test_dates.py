from datetime import UTC, date, datetime

import pytest

from slp_model.dates import (
    ensure_posted,
    latest_posted_draw_date,
    next_draw_date,
    official_post_timestamp,
)
from slp_model.exceptions import DrawNotPostedError


def test_next_draw_schedule():
    assert next_draw_date(date(2026, 1, 1)) == date(2026, 1, 3)
    assert next_draw_date(date(2026, 1, 3)) == date(2026, 1, 7)


def test_post_gate_uses_pacific_time_and_dst():
    winter = official_post_timestamp(date(2026, 1, 3))
    summer = official_post_timestamp(date(2026, 7, 15))
    assert winter == datetime(2026, 1, 4, 4, tzinfo=UTC)
    assert summer == datetime(2026, 7, 16, 3, tzinfo=UTC)
    with pytest.raises(DrawNotPostedError):
        ensure_posted(date(2026, 7, 15), now_utc=datetime(2026, 7, 16, 2, 59, tzinfo=UTC))


def test_latest_posted_draw_before_and_after_gate():
    assert latest_posted_draw_date(now_utc=datetime(2026, 7, 16, 2, 30, tzinfo=UTC)) == date(
        2026, 7, 11
    )
    assert latest_posted_draw_date(now_utc=datetime(2026, 7, 16, 3, 30, tzinfo=UTC)) == date(
        2026, 7, 15
    )

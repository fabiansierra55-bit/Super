"""Pacific-time draw schedule helpers with an explicit official-post gate."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .exceptions import DrawNotPostedError

PACIFIC = ZoneInfo("America/Los_Angeles")
DRAW_WEEKDAYS = (2, 5)  # Wednesday, Saturday


def is_draw_date(value: date) -> bool:
    return value.weekday() in DRAW_WEEKDAYS


def require_draw_date(value: date) -> None:
    if not is_draw_date(value):
        raise ValueError(f"{value.isoformat()} is not a Wednesday or Saturday draw date")


def official_post_timestamp(draw_date: date, *, post_time_pacific: time = time(20, 0)) -> datetime:
    require_draw_date(draw_date)
    return datetime.combine(draw_date, post_time_pacific, tzinfo=PACIFIC).astimezone(UTC)


def ensure_posted(
    draw_date: date,
    *,
    now_utc: datetime | None = None,
    post_time_pacific: time = time(20, 0),
) -> datetime:
    now = now_utc or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    posted = official_post_timestamp(draw_date, post_time_pacific=post_time_pacific)
    if now.astimezone(UTC) < posted:
        raise DrawNotPostedError(
            f"draw {draw_date.isoformat()} cannot be verified before "
            f"{posted.isoformat()} (configured Pacific post time)"
        )
    return posted


def next_draw_date(after: date) -> date:
    candidate = after + timedelta(days=1)
    while not is_draw_date(candidate):
        candidate += timedelta(days=1)
    return candidate


def previous_draw_date(before: date) -> date:
    candidate = before - timedelta(days=1)
    while not is_draw_date(candidate):
        candidate -= timedelta(days=1)
    return candidate


def latest_posted_draw_date(
    *,
    now_utc: datetime | None = None,
    post_time_pacific: time = time(20, 0),
) -> date:
    now = (now_utc or datetime.now(UTC)).astimezone(PACIFIC)
    candidate = now.date()
    while not is_draw_date(candidate):
        candidate -= timedelta(days=1)
    posted_local = datetime.combine(candidate, post_time_pacific, tzinfo=PACIFIC)
    if now < posted_local:
        candidate = previous_draw_date(candidate)
    return candidate

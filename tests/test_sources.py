from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from requests.adapters import HTTPAdapter

from slp_model.sources import (
    CaliforniaLotteryAdapter,
    HttpFetcher,
    LotteryNetAdapter,
    LotteryUSAAdapter,
    ResponseLike,
    SourceConflictError,
    SourceParseError,
    build_retry_session,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
FETCHED_AT = datetime(2026, 7, 16, 4, 0, tzinfo=UTC)
OFFICIAL_URL = "https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/1/20"
LOTTERYUSA_URL = "https://www.lotteryusa.com/california/super-lotto-plus/year"
LOTTERYNET_URL = "https://www.lottery.net/california/superlotto-plus/numbers/2026"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_california_lottery_parses_explicit_special_marker() -> None:
    records = CaliforniaLotteryAdapter.parse(
        fixture_bytes("calottery_page.json"),
        source_url=OFFICIAL_URL,
        fetched_at_utc=FETCHED_AT,
    )

    latest = records[-1]
    assert latest.source_name == "california_lottery"
    assert latest.official is True
    assert latest.draw_id == "4099"
    assert latest.draw.mains == (2, 5, 34, 36, 37)
    assert latest.draw.mega == 3
    assert latest.content_sha256
    assert latest.fetched_at_utc == FETCHED_AT


def test_california_lottery_never_infers_unmarked_mega() -> None:
    payload = json.loads(fixture_bytes("calottery_page.json"))
    payload["PreviousDraws"][0]["WinningNumbers"]["5"]["IsSpecial"] = False

    with pytest.raises(SourceParseError, match="five mains and one marked Mega"):
        CaliforniaLotteryAdapter.parse(
            json.dumps(payload),
            source_url=OFFICIAL_URL,
            fetched_at_utc=FETCHED_AT,
        )


def test_california_lottery_rejects_conflicting_duplicate_date() -> None:
    payload = json.loads(fixture_bytes("calottery_page.json"))
    duplicate = deepcopy(payload["PreviousDraws"][0])
    duplicate["WinningNumbers"]["5"]["Number"] = "4"
    payload["PreviousDraws"].append(duplicate)

    with pytest.raises(SourceConflictError) as raised:
        CaliforniaLotteryAdapter.parse(
            json.dumps(payload),
            source_url=OFFICIAL_URL,
            fetched_at_utc=FETCHED_AT,
        )

    assert raised.value.audit_record["status"] == "conflict"
    assert raised.value.audit_record["first"]["draw_date"] == "2026-07-15"


def test_california_lottery_collapses_only_exact_duplicates() -> None:
    payload = json.loads(fixture_bytes("calottery_page.json"))
    payload["PreviousDraws"].append(deepcopy(payload["PreviousDraws"][0]))

    records = CaliforniaLotteryAdapter.parse(
        json.dumps(payload),
        source_url=OFFICIAL_URL,
        fetched_at_utc=FETCHED_AT,
    )

    assert len(records) == 2


def test_california_lottery_rejects_draw_id_reused_for_another_date() -> None:
    payload = json.loads(fixture_bytes("calottery_page.json"))
    payload["PreviousDraws"][1]["DrawNumber"] = 4099

    with pytest.raises(SourceConflictError, match="draw id"):
        CaliforniaLotteryAdapter.parse(
            json.dumps(payload),
            source_url=OFFICIAL_URL,
            fetched_at_utc=FETCHED_AT,
        )


def test_california_lottery_allows_empty_terminal_page_only_when_explicit() -> None:
    payload = json.loads(fixture_bytes("calottery_page.json"))
    payload["PreviousDraws"] = []

    with pytest.raises(SourceParseError, match="no PreviousDraws"):
        CaliforniaLotteryAdapter.parse(
            json.dumps(payload),
            source_url=OFFICIAL_URL,
            fetched_at_utc=FETCHED_AT,
        )

    assert (
        CaliforniaLotteryAdapter.parse(
            json.dumps(payload),
            source_url=OFFICIAL_URL,
            fetched_at_utc=FETCHED_AT,
            allow_empty=True,
        )
        == ()
    )


def test_source_parser_enforces_game_number_ranges() -> None:
    payload = json.loads(fixture_bytes("calottery_page.json"))
    payload["PreviousDraws"][0]["WinningNumbers"]["4"]["Number"] = "48"

    with pytest.raises(SourceParseError, match="outside"):
        CaliforniaLotteryAdapter.parse(
            json.dumps(payload),
            source_url=OFFICIAL_URL,
            fetched_at_utc=FETCHED_AT,
        )


def test_lotteryusa_parses_draw_rows_and_ignores_ad_rows() -> None:
    records = LotteryUSAAdapter.parse(
        fixture_bytes("lotteryusa_year.html"),
        source_url=LOTTERYUSA_URL,
        fetched_at_utc=FETCHED_AT,
    )

    assert len(records) == 2
    assert records[-1].draw.mains == (2, 5, 34, 36, 37)
    assert records[-1].draw.mega == 3
    assert records[-1].draw_id is None
    assert records[-1].source_url == LOTTERYUSA_URL


def test_lotteryusa_requires_explicit_mega_label() -> None:
    html = (
        fixture_bytes("lotteryusa_year.html").decode().replace('title="Mega"', 'title="Bonus"', 1)
    )

    with pytest.raises(SourceParseError, match="explicitly labeled Mega"):
        LotteryUSAAdapter.parse(
            html,
            source_url=LOTTERYUSA_URL,
            fetched_at_utc=FETCHED_AT,
        )


def test_lotterynet_parses_draw_id_and_per_draw_url() -> None:
    records = LotteryNetAdapter.parse(
        fixture_bytes("lotterynet_2026.html"),
        source_url=LOTTERYNET_URL,
        fetched_at_utc=FETCHED_AT,
    )

    latest = records[-1]
    assert latest.draw_id == "4099"
    assert latest.draw.mains == (2, 5, 34, 36, 37)
    assert latest.draw.mega == 3
    assert latest.source_url.endswith("/numbers/07-15-2026")


def test_html_schema_change_fails_instead_of_returning_empty_history() -> None:
    with pytest.raises(SourceParseError, match="history table was not found"):
        LotteryUSAAdapter.parse(
            "<html><body>maintenance</body></html>",
            source_url=LOTTERYUSA_URL,
            fetched_at_utc=FETCHED_AT,
        )


class FakeResponse:
    status_code = 200
    content = b'{"ok": true}'
    url = "https://example.test/final"
    headers: Mapping[str, str] = {"Content-Type": "application/json"}

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, tuple[float, float]]] = []

    def get(self, url: str, *, timeout: tuple[float, float]) -> ResponseLike:
        self.calls.append((url, timeout))
        return FakeResponse()


def test_http_fetcher_uses_only_fresh_integrity_checked_cache(tmp_path: Path) -> None:
    session = FakeSession()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    fetcher = HttpFetcher(
        session=session,
        cache_dir=tmp_path,
        cache_ttl=timedelta(minutes=5),
        clock=lambda: now,
    )

    first = fetcher.get("https://example.test/results")
    second = fetcher.get("https://example.test/results")

    assert len(session.calls) == 1
    assert session.calls[0][1] == (5.0, 20.0)
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.content_sha256 == first.content_sha256
    assert second.fetched_at_utc == first.fetched_at_utc


def test_corrupt_cache_is_never_used_as_source_evidence(tmp_path: Path) -> None:
    session = FakeSession()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    fetcher = HttpFetcher(session=session, cache_dir=tmp_path, clock=lambda: now)
    fetcher.get("https://example.test/results")
    body_path = next(tmp_path.glob("*.body"))
    body_path.write_bytes(b"tampered")

    document = fetcher.get("https://example.test/results")

    assert len(session.calls) == 2
    assert document.from_cache is False
    assert document.body == FakeResponse.content


def test_retry_session_is_get_only_and_identifies_project() -> None:
    session = build_retry_session(retry_total=3, backoff_factor=0.25)
    adapter = session.get_adapter("https://")
    assert isinstance(adapter, HTTPAdapter)
    retry = adapter.max_retries

    assert retry.total == 3
    assert retry.allowed_methods == frozenset({"GET"})
    assert retry.status_forcelist == (429, 500, 502, 503, 504)
    assert "github.com/fabiansierra55-bit/Super" in session.headers["User-Agent"]

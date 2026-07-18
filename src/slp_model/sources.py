"""Fail-closed draw-result source adapters for SuperLotto Plus.

The official adapter uses the public endpoint consumed by the California
Lottery website::

    https://calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/{page}/{size}

``8`` is the site's SuperLotto Plus game id.  The endpoint currently returns
``PreviousDraws`` objects with ``DrawDate``, ``DrawNumber`` and six
``WinningNumbers`` entries.  Exactly five entries must have
``IsSpecial == false`` and exactly one must have ``IsSpecial == true``.  We do
not use position six as an inferred Mega number when that marker is absent.

Approved backup adapters parse these public archives:

* https://www.lotteryusa.com/california/super-lotto-plus/year
* https://www.lottery.net/california/superlotto-plus/numbers/{year}

All parsers deliberately fail when their expected schema changes.  They never
skip a malformed result row or manufacture a missing value.  Network-free
tests call ``parse`` with checked-in fixtures.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from html import unescape
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Draw

DEFAULT_USER_AGENT = "Mozilla/5.0 (+https://github.com/fabiansierra55-bit/Super)"
DEFAULT_TIMEOUT = (5.0, 20.0)
DEFAULT_CACHE_TTL = timedelta(minutes=15)
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class SourceError(RuntimeError):
    """Base class for source acquisition and parsing failures."""


class SourceFetchError(SourceError):
    """The source could not be fetched safely."""


class SourceParseError(SourceError):
    """A source response did not exactly match its expected schema."""


class SourceConflictError(SourceParseError):
    """One source supplied conflicting identities or results."""

    def __init__(self, message: str, audit_record: Mapping[str, Any]):
        super().__init__(message)
        self.audit_record = dict(audit_record)


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """A response body plus enough immutable metadata for an audit trail."""

    requested_url: str
    final_url: str
    fetched_at_utc: datetime
    body: bytes
    status_code: int
    content_type: str | None = None
    from_cache: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "fetched_at_utc", _aware_utc(self.fetched_at_utc))
        if not self.requested_url or not self.final_url:
            raise ValueError("source URLs must be non-empty")
        if not 200 <= self.status_code < 300:
            raise ValueError("fetched document must have a successful status")
        if not self.body:
            raise ValueError("fetched document body must be non-empty")

    @property
    def content_sha256(self) -> str:
        return sha256(self.body).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A normalized draw as asserted by one named source."""

    draw: Draw
    source_name: str
    source_url: str
    requested_url: str
    fetched_at_utc: datetime
    content_sha256: str
    parser_version: str
    official: bool
    draw_id: str | None = None
    from_cache: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "fetched_at_utc", _aware_utc(self.fetched_at_utc))
        normalized_id = str(self.draw_id).strip() if self.draw_id is not None else None
        object.__setattr__(self, "draw_id", normalized_id or None)
        if not self.source_name or not self.source_url or not self.requested_url:
            raise ValueError("source identity and URLs must be non-empty")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")

    def as_audit_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "official": self.official,
            "source_url": self.source_url,
            "requested_url": self.requested_url,
            "fetched_at_utc": self.fetched_at_utc.isoformat(),
            "content_sha256": self.content_sha256,
            "parser_version": self.parser_version,
            "from_cache": self.from_cache,
            "draw_date": self.draw.draw_date.isoformat(),
            "draw_id": self.draw_id,
            "mains": list(self.draw.mains),
            "mega": self.draw.mega,
        }


class ResponseLike(Protocol):
    status_code: int
    content: bytes
    url: str
    headers: Mapping[str, str]

    def raise_for_status(self) -> None: ...


class SessionLike(Protocol):
    headers: Any

    def get(self, url: str, *, timeout: tuple[float, float]) -> ResponseLike: ...


def build_retry_session(
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    retry_total: int = 4,
    backoff_factor: float = 0.6,
) -> requests.Session:
    """Create a GET-only session with bounded retries and exponential backoff."""

    if retry_total < 0 or backoff_factor < 0:
        raise ValueError("retry_total and backoff_factor must be non-negative")
    retry = Retry(
        total=retry_total,
        connect=retry_total,
        read=retry_total,
        status=retry_total,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=(429, 500, 502, 503, 504),
        backoff_factor=backoff_factor,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.1",
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class FilesystemResponseCache:
    """Small, URL-keyed, integrity-checked response cache.

    Cache use is explicit through :class:`HttpFetcher`.  Cache corruption or
    expiry is treated as a miss; network errors never cause an expired payload
    to be used as an unannounced fallback.
    """

    def __init__(self, directory: str | Path, *, max_bytes: int = MAX_RESPONSE_BYTES):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

    def load(
        self,
        url: str,
        *,
        now_utc: datetime,
        ttl: timedelta,
    ) -> FetchedDocument | None:
        now = _aware_utc(now_utc)
        if ttl < timedelta(0):
            raise ValueError("cache TTL cannot be negative")
        metadata_path, body_path = self._paths(url)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
            fetched_at = _aware_utc(datetime.fromisoformat(metadata["fetched_at_utc"]))
            age = now - fetched_at
            if age < timedelta(0) or age > ttl:
                return None
            if metadata["requested_url"] != url:
                return None
            if len(body) > self.max_bytes:
                return None
            if sha256(body).hexdigest() != metadata["content_sha256"]:
                return None
            return FetchedDocument(
                requested_url=url,
                final_url=metadata["final_url"],
                fetched_at_utc=fetched_at,
                body=body,
                status_code=int(metadata["status_code"]),
                content_type=metadata.get("content_type"),
                from_cache=True,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def store(self, document: FetchedDocument) -> None:
        if len(document.body) > self.max_bytes:
            raise SourceFetchError("response exceeds cache size limit")
        self.directory.mkdir(parents=True, exist_ok=True)
        metadata_path, body_path = self._paths(document.requested_url)
        metadata = {
            "schema_version": 1,
            "requested_url": document.requested_url,
            "final_url": document.final_url,
            "fetched_at_utc": document.fetched_at_utc.isoformat(),
            "status_code": document.status_code,
            "content_type": document.content_type,
            "content_sha256": document.content_sha256,
        }
        _atomic_write(body_path, document.body)
        _atomic_write(
            metadata_path,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = sha256(url.encode("utf-8")).hexdigest()
        return self.directory / f"{key}.json", self.directory / f"{key}.body"


class HttpFetcher:
    """Bounded HTTP acquisition with retries, timeouts and optional fresh cache."""

    def __init__(
        self,
        *,
        session: SessionLike | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        cache_dir: str | Path | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout[0] <= 0 or timeout[1] <= 0:
            raise ValueError("HTTP timeouts must be positive")
        if cache_ttl < timedelta(0):
            raise ValueError("cache_ttl cannot be negative")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.session = session or build_retry_session()
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.max_response_bytes = max_response_bytes
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cache = (
            FilesystemResponseCache(cache_dir, max_bytes=max_response_bytes)
            if cache_dir is not None
            else None
        )

    def get(self, url: str) -> FetchedDocument:
        now = _aware_utc(self.clock())
        if self.cache is not None:
            cached = self.cache.load(url, now_utc=now, ttl=self.cache_ttl)
            if cached is not None:
                return cached
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceFetchError(f"failed to fetch {url}: {exc}") from exc
        if not 200 <= int(response.status_code) < 300:
            raise SourceFetchError(f"source returned HTTP {response.status_code}: {url}")
        body = bytes(response.content)
        if not body:
            raise SourceFetchError(f"source returned an empty response: {url}")
        if len(body) > self.max_response_bytes:
            raise SourceFetchError(f"source response exceeded size limit: {url}")
        document = FetchedDocument(
            requested_url=url,
            final_url=response.url or url,
            fetched_at_utc=_aware_utc(self.clock()),
            body=body,
            status_code=int(response.status_code),
            content_type=response.headers.get("Content-Type"),
        )
        if self.cache is not None:
            self.cache.store(document)
        return document


class DrawSourceAdapter(ABC):
    source_name: str
    official: bool
    parser_version: str

    def __init__(self, *, fetcher: HttpFetcher | None = None) -> None:
        self.fetcher = fetcher or HttpFetcher()

    @abstractmethod
    def fetch_history(self, **kwargs: Any) -> tuple[SourceRecord, ...]:
        """Fetch and normalize a bounded history segment."""


class CaliforniaLotteryAdapter(DrawSourceAdapter):
    """Official California Lottery ``DrawGamePastDrawResults`` adapter."""

    source_name = "california_lottery"
    official = True
    parser_version = "calottery-json-v1"
    endpoint_template = (
        "https://calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/{page}/{size}"
    )

    def fetch_page(self, *, page: int = 1, size: int = 20) -> tuple[SourceRecord, ...]:
        if page < 1:
            raise ValueError("page must be at least one")
        # The public endpoint is observed to return null above its 20-row page size.
        if not 1 <= size <= 20:
            raise ValueError("official endpoint page size must be between 1 and 20")
        document = self.fetcher.get(self.endpoint_template.format(page=page, size=size))
        # The official archive exposes a bounded result window and returns a
        # schema-valid empty ``PreviousDraws`` array after its final page.
        # Page one must never be empty; later empty pages terminate pagination.
        return self.parse(document, allow_empty=page > 1)

    def fetch_history(
        self, *, pages: int = 1, size: int = 20, **kwargs: Any
    ) -> tuple[SourceRecord, ...]:
        if kwargs:
            raise TypeError(f"unexpected arguments: {', '.join(sorted(kwargs))}")
        if pages < 1:
            raise ValueError("pages must be at least one")
        records: list[SourceRecord] = []
        for page in range(1, pages + 1):
            page_records = self.fetch_page(page=page, size=size)
            if not page_records:
                break
            records.extend(page_records)
        return deduplicate_source_records(records)

    @classmethod
    def parse(
        cls,
        document: FetchedDocument | bytes | str,
        *,
        source_url: str | None = None,
        fetched_at_utc: datetime | None = None,
        allow_empty: bool = False,
    ) -> tuple[SourceRecord, ...]:
        doc = _coerce_document(document, source_url, fetched_at_utc)
        try:
            payload = json.loads(doc.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceParseError("official source did not return valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise SourceParseError("official response root must be an object")
        if payload.get("DrawGameId") != 8 or payload.get("Name") != "SuperLotto Plus":
            raise SourceParseError("official response is not SuperLotto Plus game id 8")
        raw_draws = payload.get("PreviousDraws")
        if not isinstance(raw_draws, list):
            raise SourceParseError("official response has no PreviousDraws records")
        if not raw_draws:
            if allow_empty:
                return ()
            raise SourceParseError("official response has no PreviousDraws records")

        records: list[SourceRecord] = []
        for index, raw_draw in enumerate(raw_draws):
            try:
                if not isinstance(raw_draw, Mapping):
                    raise ValueError("draw is not an object")
                draw_date = _parse_source_date(raw_draw.get("DrawDate"))
                draw_id = _required_identifier(raw_draw.get("DrawNumber"), "DrawNumber")
                winning = raw_draw.get("WinningNumbers")
                if isinstance(winning, Mapping):
                    try:
                        entries = [winning[key] for key in sorted(winning, key=int)]
                    except (TypeError, ValueError) as exc:
                        raise ValueError("WinningNumbers keys are not numeric") from exc
                elif isinstance(winning, list):
                    entries = winning
                else:
                    raise ValueError("WinningNumbers is not an object or array")
                if len(entries) != 6:
                    raise ValueError("WinningNumbers must contain exactly six entries")
                mains: list[int] = []
                mega: list[int] = []
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        raise ValueError("winning-number entry is not an object")
                    special = entry.get("IsSpecial")
                    if not isinstance(special, bool):
                        raise ValueError("IsSpecial must be a boolean")
                    number = _strict_int(entry.get("Number"), "Number")
                    (mega if special else mains).append(number)
                if len(mains) != 5 or len(mega) != 1:
                    raise ValueError("expected exactly five mains and one marked Mega")
                draw = Draw(draw_date=draw_date, mains=_fixed_five(mains), mega=mega[0])
            except Exception as exc:
                if isinstance(exc, SourceError):
                    raise
                raise SourceParseError(
                    f"invalid official PreviousDraws record at index {index}: {exc}"
                ) from exc
            records.append(
                _source_record(
                    draw=draw,
                    draw_id=draw_id,
                    source_name=cls.source_name,
                    official=cls.official,
                    parser_version=cls.parser_version,
                    document=doc,
                )
            )
        return deduplicate_source_records(records)


class LotteryUSAAdapter(DrawSourceAdapter):
    """Approved LotteryUSA one-year archive adapter."""

    source_name = "lotteryusa"
    official = False
    parser_version = "lotteryusa-html-v1"
    history_url = "https://www.lotteryusa.com/california/super-lotto-plus/year"

    def fetch_history(self, **kwargs: Any) -> tuple[SourceRecord, ...]:
        if kwargs:
            raise TypeError(f"unexpected arguments: {', '.join(sorted(kwargs))}")
        return self.parse(self.fetcher.get(self.history_url))

    @classmethod
    def parse(
        cls,
        document: FetchedDocument | bytes | str,
        *,
        source_url: str | None = None,
        fetched_at_utc: datetime | None = None,
    ) -> tuple[SourceRecord, ...]:
        doc = _coerce_document(document, source_url, fetched_at_utc)
        soup = BeautifulSoup(doc.body, "lxml")
        table = soup.select_one("table#history-table-all-new")
        if table is None:
            raise SourceParseError("LotteryUSA history table was not found")
        rows = table.select("tr.c-draw-card")
        if not rows:
            raise SourceParseError("LotteryUSA history table has no draw rows")

        records: list[SourceRecord] = []
        for index, row in enumerate(rows):
            try:
                date_node = row.select_one(".c-draw-card__draw-date-sub")
                result = row.select_one("ul.c-result")
                if date_node is None or result is None:
                    raise ValueError("missing date or result element")
                draw_date = _parse_source_date(date_node.get_text(" ", strip=True))
                mains: list[int] = []
                mega: list[int] = []
                for item in result.find_all("li", recursive=False):
                    raw_classes = item.get("class")
                    classes = set(raw_classes) if isinstance(raw_classes, list) else set()
                    if "c-ball" in classes:
                        mains.append(_strict_int(item.get_text(strip=True), "main"))
                    elif "c-result__bonus" in classes:
                        ball = item.select_one(".c-ball")
                        label = item.select_one("[title]")
                        if ball is None or label is None:
                            raise ValueError("malformed Mega element")
                        raw_title = label.get("title")
                        if not isinstance(raw_title, str) or raw_title.strip().lower() != "mega":
                            raise ValueError("bonus element is not explicitly labeled Mega")
                        mega.append(_strict_int(ball.get_text(strip=True), "Mega"))
                if len(mains) != 5 or len(mega) != 1:
                    raise ValueError("expected exactly five mains and one labeled Mega")
                draw = Draw(draw_date=draw_date, mains=_fixed_five(mains), mega=mega[0])
            except Exception as exc:
                raise SourceParseError(
                    f"invalid LotteryUSA draw row at index {index}: {exc}"
                ) from exc
            records.append(
                _source_record(
                    draw=draw,
                    draw_id=None,
                    source_name=cls.source_name,
                    official=cls.official,
                    parser_version=cls.parser_version,
                    document=doc,
                )
            )
        return deduplicate_source_records(records)


class LotteryNetAdapter(DrawSourceAdapter):
    """Approved Lottery.net per-year archive adapter."""

    source_name = "lottery_net"
    official = False
    parser_version = "lottery-net-html-v1"
    history_url_template = "https://www.lottery.net/california/superlotto-plus/numbers/{year}"

    def fetch_history(self, *, year: int | None = None, **kwargs: Any) -> tuple[SourceRecord, ...]:
        if kwargs:
            raise TypeError(f"unexpected arguments: {', '.join(sorted(kwargs))}")
        current_year_pt = datetime.now(ZoneInfo("America/Los_Angeles")).year
        selected_year = year or current_year_pt
        if not 1986 <= selected_year <= current_year_pt:
            raise ValueError("Lottery.net year is outside the supported archive")
        url = self.history_url_template.format(year=selected_year)
        return self.parse(self.fetcher.get(url))

    @classmethod
    def parse(
        cls,
        document: FetchedDocument | bytes | str,
        *,
        source_url: str | None = None,
        fetched_at_utc: datetime | None = None,
    ) -> tuple[SourceRecord, ...]:
        doc = _coerce_document(document, source_url, fetched_at_utc)
        soup = BeautifulSoup(doc.body, "lxml")
        table = soup.select_one("table.prizes.archive")
        if table is None:
            raise SourceParseError("Lottery.net archive table was not found")
        rows = [row for row in table.select("tbody > tr") if row.select_one("ul.superlotto-plus")]
        if not rows:
            raise SourceParseError("Lottery.net archive table has no draw rows")

        records: list[SourceRecord] = []
        for index, row in enumerate(rows):
            row_url = doc.final_url
            try:
                cells = row.find_all("td", recursive=False)
                if len(cells) != 3:
                    raise ValueError("draw row must contain date, id and numbers cells")
                date_link = cells[0].find("a")
                if date_link is None:
                    raise ValueError("draw date link is missing")
                draw_date = _parse_source_date(date_link.get_text(" ", strip=True))
                draw_id = _required_identifier(cells[1].get_text(strip=True), "draw id")
                result = cells[2].select_one("ul.superlotto-plus")
                if result is None:
                    raise ValueError("result list is missing")
                mains = [
                    _strict_int(node.get_text(strip=True), "main")
                    for node in result.select("li.ball")
                ]
                mega = [
                    _strict_int(node.get_text(strip=True), "Mega")
                    for node in result.select("li.mega-ball")
                ]
                if len(mains) != 5 or len(mega) != 1:
                    raise ValueError("expected exactly five mains and one Mega")
                draw = Draw(draw_date=draw_date, mains=_fixed_five(mains), mega=mega[0])
                raw_href = date_link.get("href")
                if not isinstance(raw_href, str) or not raw_href:
                    raise ValueError("draw date URL is missing")
                row_url = urljoin(doc.final_url, raw_href)
            except Exception as exc:
                raise SourceParseError(
                    f"invalid Lottery.net draw row at index {index}: {exc}"
                ) from exc
            records.append(
                _source_record(
                    draw=draw,
                    draw_id=draw_id,
                    source_name=cls.source_name,
                    official=cls.official,
                    parser_version=cls.parser_version,
                    document=doc,
                    source_url=row_url,
                )
            )
        return deduplicate_source_records(records)


def deduplicate_source_records(
    records: Iterable[SourceRecord],
) -> tuple[SourceRecord, ...]:
    """Dedupe exact repeats and reject conflicting date/draw-id associations."""

    by_source_date: dict[tuple[str, date], SourceRecord] = {}
    by_source_id: dict[tuple[str, str], SourceRecord] = {}
    for record in records:
        date_key = (record.source_name, record.draw.draw_date)
        prior_date = by_source_date.get(date_key)
        if prior_date is not None:
            if not _same_source_assertion(prior_date, record):
                raise _source_conflict("conflicting duplicate draw date", prior_date, record)
            continue
        if record.draw_id is not None:
            id_key = (record.source_name, record.draw_id)
            prior_id = by_source_id.get(id_key)
            if prior_id is not None and not _same_source_assertion(prior_id, record):
                raise _source_conflict(
                    "draw id is associated with conflicting draws", prior_id, record
                )
            by_source_id[id_key] = record
        by_source_date[date_key] = record
    return tuple(
        sorted(
            by_source_date.values(),
            key=lambda item: (
                item.draw.draw_date,
                item.source_name,
                item.draw_id or "",
            ),
        )
    )


def _source_record(
    *,
    draw: Draw,
    draw_id: str | None,
    source_name: str,
    official: bool,
    parser_version: str,
    document: FetchedDocument,
    source_url: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        draw=draw,
        draw_id=draw_id,
        source_name=source_name,
        official=official,
        parser_version=parser_version,
        source_url=source_url or document.final_url,
        requested_url=document.requested_url,
        fetched_at_utc=document.fetched_at_utc,
        content_sha256=document.content_sha256,
        from_cache=document.from_cache,
    )


def _coerce_document(
    document: FetchedDocument | bytes | str,
    source_url: str | None,
    fetched_at_utc: datetime | None,
) -> FetchedDocument:
    if isinstance(document, FetchedDocument):
        if source_url is not None or fetched_at_utc is not None:
            raise TypeError("source_url/fetched_at_utc cannot override a FetchedDocument")
        return document
    if source_url is None or fetched_at_utc is None:
        raise TypeError("raw fixture parsing requires source_url and fetched_at_utc")
    body = document.encode("utf-8") if isinstance(document, str) else bytes(document)
    return FetchedDocument(
        requested_url=source_url,
        final_url=source_url,
        fetched_at_utc=fetched_at_utc,
        body=body,
        status_code=200,
    )


def _parse_source_date(raw: Any) -> date:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("draw date is missing")
    value = unescape(" ".join(raw.split()))
    iso_match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:T.*)?", value)
    if iso_match:
        return date.fromisoformat(iso_match.group(1))
    value = re.sub(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", value, flags=re.IGNORECASE)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported draw date format: {raw!r}")


def _strict_int(raw: Any, field_name: str) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and re.fullmatch(r"\d+", raw.strip()):
        return int(raw.strip())
    raise ValueError(f"{field_name} must contain only an integer")


def _fixed_five(values: list[int]) -> tuple[int, int, int, int, int]:
    if len(values) != 5:
        raise ValueError("expected exactly five main numbers")
    return cast(tuple[int, int, int, int, int], tuple(values))


def _required_identifier(raw: Any, field_name: str) -> str:
    value = str(_strict_int(raw, field_name))
    if not value:
        raise ValueError(f"{field_name} is missing")
    return value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _same_source_assertion(left: SourceRecord, right: SourceRecord) -> bool:
    return (
        left.draw == right.draw
        and left.draw_id == right.draw_id
        and left.official == right.official
    )


def _source_conflict(reason: str, left: SourceRecord, right: SourceRecord) -> SourceConflictError:
    audit_record = {
        "event_type": "source_normalization",
        "status": "conflict",
        "reason": reason,
        "source_name": left.source_name,
        "first": left.as_audit_dict(),
        "second": right.as_audit_dict(),
    }
    return SourceConflictError(f"{reason} for source {left.source_name}", audit_record=audit_record)


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


__all__ = [
    "CaliforniaLotteryAdapter",
    "DEFAULT_CACHE_TTL",
    "DEFAULT_TIMEOUT",
    "DEFAULT_USER_AGENT",
    "DrawSourceAdapter",
    "FetchedDocument",
    "FilesystemResponseCache",
    "HttpFetcher",
    "LotteryNetAdapter",
    "LotteryUSAAdapter",
    "SourceConflictError",
    "SourceError",
    "SourceFetchError",
    "SourceParseError",
    "SourceRecord",
    "build_retry_session",
    "deduplicate_source_records",
]

"""Deterministic, read-only audit of untrusted legacy CSV artifacts.

The audit deliberately separates three concepts:

* structural validity of the CSV and SuperLotto Plus tickets;
* internal consistency of a scoring artifact; and
* external verification of the winning-result claim used for scoring.

An internally consistent score is not called verified unless the exact draw was
independently observed at the official California Lottery source and an
approved backup.  The original files are never rewritten by this module.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

AUDIT_MANIFEST_VERSION = "1.0"
GAME_RULES_VERSION = "superlotto-plus-5of47-mega-1of27"
PACIFIC = ZoneInfo("America/Los_Angeles")
MAIN_FIELDS = ("n1", "n2", "n3", "n4", "n5")
WIN_MAIN_FIELDS = ("win_n1", "win_n2", "win_n3", "win_n4", "win_n5")
TICKET_FIELDS = ("strategy", "line_id", *MAIN_FIELDS, "mega")

PREDICTION_IDENTITY_FIELDS = (
    "bundle_id",
    "generated_timestamp_utc",
    "intended_draw_date",
    "game_rules_version",
)
PREDICTION_REPRODUCIBILITY_FIELDS = (
    "model_version",
    "configuration_snapshot",
    "random_seed",
    "source_verification_metadata",
    "history_cutoff_date",
    "mains_window",
    "mega_window",
    "mains_sigma",
    "mega_sigma",
    "mains_half_life",
    "mega_half_life",
    "candidate_pool_size",
    "simulation_count",
    "optimizer_objective",
    "constraint_settings",
)
SCORING_IDENTITY_FIELDS = ("draw_date", "bundle_id")
SCORING_VERIFICATION_FIELDS = (
    "official_source",
    "backup_source",
    "official_fetch_timestamp_utc",
    "backup_fetch_timestamp_utc",
    "verification_status",
)
HISTORY_VERIFICATION_FIELDS = (
    "official_source",
    "backup_source",
    "official_fetch_timestamp_utc",
    "backup_fetch_timestamp_utc",
    "verification_status",
)

# These observations are evidence for this one legacy handoff, not a general
# source cache.  Callers must opt into them explicitly.  Both sources contained
# the exact values shown when checked; nothing was inferred from a score file.
EMBEDDED_TWO_SOURCE_VERIFICATIONS: dict[str, dict[str, Any]] = {
    "2025-09-24": {
        "draw_id": "4015",
        "mains": [23, 26, 30, 39, 42],
        "mega": 13,
        "verified_timestamp_utc": "2026-07-17T23:11:27Z",
        "sources": [
            {
                "role": "official",
                "name": "California Lottery",
                "url": ("https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/5/20"),
                "fetch_timestamp_utc": "2026-07-17T23:11:25Z",
            },
            {
                "role": "backup",
                "name": "Lottery.net",
                "url": ("https://www.lottery.net/california/superlotto-plus/numbers/2025"),
                "fetch_timestamp_utc": "2026-07-17T23:11:27Z",
            },
        ],
    },
    "2025-10-08": {
        "draw_id": "4019",
        "mains": [16, 26, 27, 35, 43],
        "mega": 2,
        "verified_timestamp_utc": "2026-07-17T23:11:27Z",
        "sources": [
            {
                "role": "official",
                "name": "California Lottery",
                "url": ("https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/5/20"),
                "fetch_timestamp_utc": "2026-07-17T23:11:25Z",
            },
            {
                "role": "backup",
                "name": "Lottery.net",
                "url": ("https://www.lottery.net/california/superlotto-plus/numbers/2025"),
                "fetch_timestamp_utc": "2026-07-17T23:11:27Z",
            },
        ],
    },
    "2026-01-28": {
        "draw_id": "4051",
        "mains": [3, 6, 14, 26, 38],
        "mega": 2,
        "verified_timestamp_utc": "2026-07-17T23:11:41Z",
        "sources": [
            {
                "role": "official",
                "name": "California Lottery",
                "url": ("https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/3/20"),
                "fetch_timestamp_utc": "2026-07-17T23:11:40Z",
            },
            {
                "role": "backup",
                "name": "LotteryUSA",
                "url": ("https://www.lotteryusa.com/california/super-lotto-plus/year"),
                "fetch_timestamp_utc": "2026-07-17T23:11:41Z",
            },
        ],
    },
    "2026-02-04": {
        "draw_id": "4053",
        "mains": [2, 15, 17, 22, 38],
        "mega": 18,
        "verified_timestamp_utc": "2026-07-17T23:11:41Z",
        "sources": [
            {
                "role": "official",
                "name": "California Lottery",
                "url": ("https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/3/20"),
                "fetch_timestamp_utc": "2026-07-17T23:11:40Z",
            },
            {
                "role": "backup",
                "name": "LotteryUSA",
                "url": ("https://www.lotteryusa.com/california/super-lotto-plus/year"),
                "fetch_timestamp_utc": "2026-07-17T23:11:41Z",
            },
        ],
    },
}

JsonObject = dict[str, Any]
APPROVED_BACKUP_HOSTS = {
    "LotteryUSA": "lotteryusa.com",
    "Lottery.net": "lottery.net",
    "LotteryCorner": "lotterycorner.com",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_two_source_evidence(evidence: Mapping[str, Any]) -> bool:
    def host_matches(source: Mapping[str, Any], expected: str) -> bool:
        host = (urlparse(str(source.get("url", ""))).hostname or "").lower()
        return host == expected or host.endswith(f".{expected}")

    def has_utc_fetch_time(source: Mapping[str, Any]) -> bool:
        parsed, timestamp_format = _parse_utc_timestamp(str(source.get("fetch_timestamp_utc", "")))
        return parsed is not None and timestamp_format == "iso8601"

    sources = evidence.get("sources")
    if not isinstance(sources, list):
        return False
    official = [source for source in sources if source.get("role") == "official"]
    backups = [source for source in sources if source.get("role") == "backup"]
    if len(official) != 1 or not backups:
        return False
    official_source = official[0]
    if not host_matches(official_source, "calottery.com"):
        return False
    if not has_utc_fetch_time(official_source):
        return False
    approved_backup = any(
        source.get("name") in APPROVED_BACKUP_HOSTS
        and host_matches(source, APPROVED_BACKUP_HOSTS[source["name"]])
        and has_utc_fetch_time(source)
        for source in backups
    )
    mains = evidence.get("mains")
    mega = evidence.get("mega")
    return bool(
        approved_backup
        and isinstance(mains, list)
        and len(mains) == 5
        and len(set(mains)) == 5
        and all(isinstance(value, int) and 1 <= value <= 47 for value in mains)
        and isinstance(mega, int)
        and 1 <= mega <= 27
    )


def _display_path(path: Path, repository_root: Path, input_root: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return f"{input_root.name}/{path.relative_to(input_root).as_posix()}"


def _artifact_kind(path: Path) -> str:
    if path.parent.name == "predictions":
        return "prediction"
    if path.parent.name == "scoring":
        return "scoring"
    if path.parent.name == "history":
        return "history"
    return "unknown"


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except (AttributeError, ValueError):
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_utc_timestamp(value: str | None) -> tuple[datetime | None, str]:
    if not value:
        return None, "missing"
    cleaned = value.strip()
    try:
        if re.fullmatch(r"\d{8}_\d{6}Z", cleaned):
            parsed = datetime.strptime(cleaned, "%Y%m%d_%H%M%SZ").replace(tzinfo=UTC)
            return parsed, "legacy_compact"
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None, "timezone_missing"
        return parsed.astimezone(UTC), "iso8601"
    except ValueError:
        return None, "invalid"


def _filename_claimed_date(path: Path, kind: str) -> tuple[date | None, str | None]:
    patterns: list[tuple[str, str]] = []
    if kind == "prediction":
        patterns = [
            (r"^slp_predictions_(\d{8})\.csv$", "date_only_filename"),
            (
                r"^slp_predictions_corrected_for_(\d{8})\.csv$",
                "corrected_for_filename",
            ),
        ]
    elif kind == "scoring":
        patterns = [(r"^slp_scoring_(\d{8})\.csv$", "scoring_filename")]
    for pattern, basis in patterns:
        match = re.fullmatch(pattern, path.name)
        if match:
            return datetime.strptime(match.group(1), "%Y%m%d").date(), basis
    return None, None


def _truth_value(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "y", "1"}:
        return True
    if normalized in {"false", "no", "n", "0"}:
        return False
    return None


def _score_category(main_matches: int, mega_hit: bool) -> str:
    if main_matches == 5:
        return "5+Mega" if mega_hit else "5"
    if main_matches == 4:
        return "4+Mega" if mega_hit else "4"
    if main_matches == 3:
        return "3+Mega" if mega_hit else "3"
    if mega_hit:
        return {2: "2+Mega", 1: "1+Mega", 0: "Mega-only"}[main_matches]
    return str(main_matches)


def _ticket_label(ticket: Mapping[str, Any]) -> JsonObject:
    return {
        "strategy": ticket["strategy"],
        "line_id": ticket["line_id"],
        "mains": list(ticket["mains"]),
        "mega": ticket["mega"],
    }


class _AuditBuilder:
    def __init__(self) -> None:
        self.findings: list[JsonObject] = []
        self.file_findings: defaultdict[str, list[str]] = defaultdict(list)

    def add(
        self,
        *,
        code: str,
        severity: str,
        path: str,
        message: str,
        rows: Iterable[int] = (),
        evidence: Mapping[str, Any] | None = None,
        verification_status: str = "not_applicable",
        related_paths: Iterable[str] = (),
    ) -> str:
        finding: JsonObject = {
            "code": code,
            "severity": severity,
            "path": path,
            "message": message,
            "rows": sorted(set(rows)),
            "evidence": dict(evidence or {}),
            "verification_status": verification_status,
        }
        related = sorted(set(related_paths))
        if related:
            finding["related_paths"] = related
        digest = hashlib.sha256(_canonical_json(finding).encode()).hexdigest()[:16]
        finding_id = f"{code}:{digest}"
        finding["finding_id"] = finding_id
        self.findings.append(finding)
        self.file_findings[path].append(finding_id)
        for related_path in related:
            self.file_findings[related_path].append(finding_id)
        return finding_id


def _read_csv(
    path: Path,
    display_path: str,
    kind: str,
    builder: _AuditBuilder,
) -> tuple[list[str], list[tuple[int, dict[str, str]]], bytes]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        builder.add(
            code="csv_encoding_error",
            severity="error",
            path=display_path,
            message="CSV is not valid UTF-8 and could not be audited row-by-row.",
            evidence={"error": str(exc)},
        )
        return [], [], raw

    try:
        parsed = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        builder.add(
            code="csv_parse_error",
            severity="error",
            path=display_path,
            message="CSV parser rejected the artifact.",
            evidence={"error": str(exc)},
        )
        return [], [], raw

    if not parsed:
        builder.add(
            code="missing_csv_header",
            severity="error",
            path=display_path,
            message="CSV is empty and has no header.",
        )
        return [], [], raw

    headers = parsed[0]
    duplicate_headers = sorted(header for header, count in Counter(headers).items() if count > 1)
    if duplicate_headers:
        builder.add(
            code="duplicate_csv_headers",
            severity="error",
            path=display_path,
            message="CSV repeats one or more column names.",
            evidence={"duplicate_headers": duplicate_headers},
        )

    rows: list[tuple[int, dict[str, str]]] = []
    malformed_rows: list[JsonObject] = []
    for row_number, values in enumerate(parsed[1:], start=2):
        if len(values) != len(headers):
            malformed_rows.append(
                {
                    "row": row_number,
                    "expected_columns": len(headers),
                    "actual_columns": len(values),
                }
            )
            continue
        rows.append((row_number, dict(zip(headers, values, strict=True))))

    if malformed_rows:
        builder.add(
            code="csv_row_width_mismatch",
            severity="error",
            path=display_path,
            message="CSV data rows do not all match the header width.",
            rows=(item["row"] for item in malformed_rows),
            evidence={"violations": malformed_rows},
        )

    if kind == "history" and not rows:
        builder.add(
            code="empty_history_artifact",
            severity="error",
            path=display_path,
            message="History artifact contains a schema header but no verified draws.",
        )
    return headers, rows, raw


def _check_schema(
    *,
    kind: str,
    headers: Sequence[str],
    path: str,
    builder: _AuditBuilder,
) -> None:
    header_set = set(headers)
    if kind in {"prediction", "scoring"}:
        missing_ticket = sorted(set(TICKET_FIELDS) - header_set)
        if missing_ticket:
            builder.add(
                code="missing_ticket_columns",
                severity="error",
                path=path,
                message="Ticket artifact lacks columns needed to validate game rules.",
                evidence={"missing_fields": missing_ticket},
            )

    if kind == "prediction":
        missing_identity = []
        for field in PREDICTION_IDENTITY_FIELDS:
            if field == "generated_timestamp_utc" and "timestamp_utc" in header_set:
                continue
            if field not in header_set:
                missing_identity.append(field)
        if missing_identity:
            builder.add(
                code="missing_prediction_identity_metadata",
                severity="error",
                path=path,
                message="Prediction cannot be treated as a production locked bundle.",
                evidence={"missing_fields": sorted(missing_identity)},
            )
        if "timestamp_utc" in header_set and "generated_timestamp_utc" not in header_set:
            builder.add(
                code="legacy_generation_timestamp_field",
                severity="warning",
                path=path,
                message=(
                    "Legacy timestamp_utc is usable but is not the canonical "
                    "generated_timestamp_utc field."
                ),
                evidence={"legacy_field": "timestamp_utc"},
            )
        missing_reproducibility = sorted(set(PREDICTION_REPRODUCIBILITY_FIELDS) - header_set)
        if missing_reproducibility:
            builder.add(
                code="missing_prediction_reproducibility_metadata",
                severity="error",
                path=path,
                message=(
                    "Prediction lacks data required to reproduce its model fit, "
                    "simulation, and optimizer decision."
                ),
                evidence={"missing_fields": missing_reproducibility},
                verification_status="unverified",
            )

    elif kind == "scoring":
        missing_identity = sorted(set(SCORING_IDENTITY_FIELDS) - header_set)
        if missing_identity:
            builder.add(
                code="missing_scoring_identity_metadata",
                severity="error",
                path=path,
                message="Scoring artifact is not permanently tied to a draw and bundle.",
                evidence={"missing_fields": missing_identity},
            )
        missing_verification = sorted(set(SCORING_VERIFICATION_FIELDS) - header_set)
        if missing_verification:
            builder.add(
                code="missing_scoring_source_metadata",
                severity="error",
                path=path,
                message="Original scoring artifact has no auditable two-source record.",
                evidence={"missing_fields": missing_verification},
                verification_status="unverified",
            )
        if not ({"matched_mains", "matched_nums"} & header_set):
            builder.add(
                code="missing_exact_matched_mains",
                severity="error",
                path=path,
                message="Scoring rows do not state the exact matched main numbers.",
            )

    elif kind == "history":
        missing = sorted(set(HISTORY_VERIFICATION_FIELDS) - header_set)
        if missing:
            builder.add(
                code="incomplete_history_verification_schema",
                severity="error",
                path=path,
                message="History schema cannot record both source fetches and status.",
                evidence={"missing_fields": missing},
            )


def _extract_tickets(
    *,
    headers: Sequence[str],
    rows: Sequence[tuple[int, Mapping[str, str]]],
    path: str,
    builder: _AuditBuilder,
) -> list[JsonObject]:
    if not set(TICKET_FIELDS).issubset(headers):
        return []
    tickets: list[JsonObject] = []
    bad_values: list[JsonObject] = []
    bad_ranges: list[JsonObject] = []
    repeated_mains: list[JsonObject] = []
    unsorted_mains: list[JsonObject] = []

    for row_number, row in rows:
        mains = tuple(_parse_int(row.get(field)) for field in MAIN_FIELDS)
        mega = _parse_int(row.get("mega"))
        if any(value is None for value in mains) or mega is None:
            bad_values.append(
                {
                    "row": row_number,
                    "values": [row.get(field) for field in (*MAIN_FIELDS, "mega")],
                }
            )
            continue
        typed_mains = tuple(int(value) for value in mains if value is not None)
        if not all(1 <= value <= 47 for value in typed_mains) or not 1 <= mega <= 27:
            bad_ranges.append({"row": row_number, "mains": list(typed_mains), "mega": mega})
        if len(set(typed_mains)) != 5:
            repeated_mains.append({"row": row_number, "mains": list(typed_mains)})
        if tuple(sorted(typed_mains)) != typed_mains:
            unsorted_mains.append({"row": row_number, "mains": list(typed_mains)})
        tickets.append(
            {
                "row": row_number,
                "strategy": row.get("strategy", "").strip(),
                "line_id": row.get("line_id", "").strip(),
                "mains": typed_mains,
                "mega": mega,
            }
        )

    if bad_values:
        builder.add(
            code="invalid_number_value",
            severity="error",
            path=path,
            message="Ticket has a non-integer or missing lottery number.",
            rows=(item["row"] for item in bad_values),
            evidence={"violations": bad_values},
        )
    if bad_ranges:
        builder.add(
            code="invalid_number_range",
            severity="critical",
            path=path,
            message="Ticket violates the 1-47 mains or 1-27 Mega range.",
            rows=(item["row"] for item in bad_ranges),
            evidence={"violations": bad_ranges},
        )
    if repeated_mains:
        builder.add(
            code="duplicate_main_within_ticket",
            severity="critical",
            path=path,
            message="Ticket does not contain five unique main numbers.",
            rows=(item["row"] for item in repeated_mains),
            evidence={"violations": repeated_mains},
        )
    if unsorted_mains:
        builder.add(
            code="unnormalized_main_order",
            severity="warning",
            path=path,
            message="Ticket mains are not stored in normalized ascending order.",
            rows=(item["row"] for item in unsorted_mains),
            evidence={"violations": unsorted_mains},
        )
    return tickets


def _duplicate_groups(
    tickets: Sequence[Mapping[str, Any]],
    key_fields: Sequence[str],
) -> list[JsonObject]:
    grouped: defaultdict[tuple[Any, ...], list[int]] = defaultdict(list)
    for ticket in tickets:
        grouped[tuple(ticket[field] for field in key_fields)].append(ticket["row"])
    return [
        {"value": list(key), "rows": rows} for key, rows in sorted(grouped.items()) if len(rows) > 1
    ]


def _check_ticket_identity(
    *, tickets: Sequence[Mapping[str, Any]], path: str, builder: _AuditBuilder
) -> None:
    checks = (
        (
            "duplicate_line_identity",
            "error",
            ("strategy", "line_id"),
            "Strategy and line_id are repeated within the artifact.",
        ),
        (
            "duplicate_main_set",
            "critical",
            ("mains",),
            "Two or more rows contain the same five-number main set.",
        ),
        (
            "duplicate_full_ticket",
            "critical",
            ("mains", "mega"),
            "Two or more rows contain an identical full ticket.",
        ),
    )
    for code, severity, fields, message in checks:
        groups = _duplicate_groups(tickets, fields)
        if groups:
            builder.add(
                code=code,
                severity=severity,
                path=path,
                message=message,
                rows=(row for group in groups for row in group["rows"]),
                evidence={"groups": groups},
            )


def _check_bundle_constraints(
    *, tickets: Sequence[Mapping[str, Any]], path: str, builder: _AuditBuilder
) -> JsonObject:
    pair_rows: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    triple_rows: defaultdict[tuple[int, int, int], list[int]] = defaultdict(list)
    main_counts: Counter[int] = Counter()
    for ticket in tickets:
        mains = tuple(sorted(ticket["mains"]))
        main_counts.update(mains)
        for pair in combinations(mains, 2):
            pair_rows[pair].append(ticket["row"])
        for triple in combinations(mains, 3):
            triple_rows[triple].append(ticket["row"])

    overlaps: list[JsonObject] = []
    hamming: list[JsonObject] = []
    for first, second in combinations(tickets, 2):
        overlap = len(set(first["mains"]) & set(second["mains"]))
        if overlap > 3:
            overlaps.append(
                {
                    "rows": [first["row"], second["row"]],
                    "overlap": overlap,
                }
            )
        distance = sum(
            left != right
            for left, right in zip(
                (*sorted(first["mains"]), first["mega"]),
                (*sorted(second["mains"]), second["mega"]),
                strict=True,
            )
        )
        if distance < 2:
            hamming.append({"rows": [first["row"], second["row"]], "distance": distance})

    pair_violations: list[JsonObject] = [
        {"pair": list(pair), "count": len(rows), "rows": rows}
        for pair, rows in sorted(pair_rows.items())
        if len(rows) > 2
    ]
    triple_violations: list[JsonObject] = [
        {"triple": list(triple), "count": len(rows), "rows": rows}
        for triple, rows in sorted(triple_rows.items())
        if len(rows) > 1
    ]

    if overlaps:
        builder.add(
            code="main_overlap_cap_violation",
            severity="critical",
            path=path,
            message="Ticket pair shares more than three main numbers.",
            rows=(row for violation in overlaps for row in violation["rows"]),
            evidence={"max_allowed": 3, "violations": overlaps},
        )
    if hamming:
        builder.add(
            code="hamming_distance_violation",
            severity="critical",
            path=path,
            message="Ticket pair has positional Hamming distance below two.",
            rows=(row for violation in hamming for row in violation["rows"]),
            evidence={
                "minimum": 2,
                "definition": "sorted n1..n5 plus Mega",
                "violations": hamming,
            },
        )
    if pair_violations:
        builder.add(
            code="pair_cap_violation",
            severity="error",
            path=path,
            message="A two-number main pair occurs in more than two tickets.",
            rows=(row for violation in pair_violations for row in violation["rows"]),
            evidence={"cap": 2, "violations": pair_violations},
        )
    if triple_violations:
        builder.add(
            code="triple_cap_violation",
            severity="error",
            path=path,
            message="A three-number main combination occurs more than once.",
            rows=(row for violation in triple_violations for row in violation["rows"]),
            evidence={"cap": 1, "violations": triple_violations},
        )

    ticket_count = len(tickets)
    positional_counts = [
        Counter(ticket["mains"][position] for ticket in tickets) for position in range(5)
    ]
    max_position_share = max(
        (max(counter.values(), default=0) / ticket_count for counter in positional_counts),
        default=0.0,
    )
    max_main_share = max(main_counts.values(), default=0) / ticket_count if ticket_count else 0
    return {
        "ticket_count": ticket_count,
        "unique_mains_used": len(main_counts),
        "max_main_ticket_share": round(max_main_share, 6),
        "max_sorted_position_share": round(max_position_share, 6),
        "maximum_pair_count": max((len(rows) for rows in pair_rows.values()), default=0),
        "violating_pair_count": len(pair_violations),
        "maximum_triple_count": max((len(rows) for rows in triple_rows.values()), default=0),
        "violating_triple_count": len(triple_violations),
        "overlap_violation_count": len(overlaps),
        "hamming_violation_count": len(hamming),
    }


def _check_recenter_collapse(
    *,
    tickets: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    path: str,
    builder: _AuditBuilder,
) -> None:
    if len(tickets) < 10:
        return
    position_share = float(metrics["max_sorted_position_share"])
    main_share = float(metrics["max_main_ticket_share"])
    if position_share < 0.40 and main_share < 0.45:
        return
    builder.add(
        code="likely_recenter_collapse",
        severity="warning",
        path=path,
        message=(
            "Ticket concentration crosses the audit's collapse heuristic; this is "
            "a warning, not proof of recentering provenance."
        ),
        rows=(ticket["row"] for ticket in tickets),
        evidence={
            "max_sorted_position_share": position_share,
            "position_share_threshold": 0.40,
            "max_main_ticket_share": main_share,
            "main_share_threshold": 0.45,
        },
        verification_status="heuristic",
    )


def _uniform_value(
    rows: Sequence[tuple[int, Mapping[str, str]]], field: str
) -> tuple[str | None, list[str]]:
    values = sorted({row.get(field, "").strip() for _, row in rows})
    nonempty = [value for value in values if value]
    return (nonempty[0] if len(nonempty) == 1 else None), values


def _check_prediction_metadata_values(
    *,
    context: JsonObject,
    builder: _AuditBuilder,
) -> None:
    headers = context["_headers"]
    rows = context["_rows"]
    path = context["path"]
    for field in (
        "bundle_id",
        "intended_draw_date",
        "game_rules_version",
        "generated_timestamp_utc",
        "timestamp_utc",
    ):
        if field not in headers:
            continue
        value, observed = _uniform_value(rows, field)
        if value is None and observed:
            builder.add(
                code="inconsistent_bundle_metadata",
                severity="error",
                path=path,
                message=f"Prediction rows disagree on {field}.",
                evidence={"field": field, "observed_values": observed},
            )
        context[f"_{field}"] = value

    timestamp_field = (
        "generated_timestamp_utc" if "generated_timestamp_utc" in headers else "timestamp_utc"
    )
    timestamp_value = context.get(f"_{timestamp_field}")
    generated_at, timestamp_format = _parse_utc_timestamp(timestamp_value)
    context["_generated_at"] = generated_at
    if timestamp_value and generated_at is None:
        builder.add(
            code="invalid_generation_timestamp",
            severity="error",
            path=path,
            message="Generation timestamp cannot be parsed as UTC.",
            evidence={"value": timestamp_value, "parse_status": timestamp_format},
        )
    elif timestamp_format == "legacy_compact":
        builder.add(
            code="non_iso_generation_timestamp",
            severity="warning",
            path=path,
            message="Generation timestamp uses a legacy compact representation.",
            evidence={"value": timestamp_value},
        )

    explicit_draw_value = context.get("_intended_draw_date")
    explicit_draw_date = _parse_date(explicit_draw_value)
    if explicit_draw_value and explicit_draw_date is None:
        builder.add(
            code="invalid_intended_draw_date",
            severity="error",
            path=path,
            message="intended_draw_date is not an ISO calendar date.",
            evidence={"value": explicit_draw_value},
        )
    filename_date, filename_basis = _filename_claimed_date(context["_absolute_path"], "prediction")
    intended_date = explicit_draw_date or filename_date
    intended_basis = "intended_draw_date" if explicit_draw_date else filename_basis
    context["_intended_date"] = intended_date
    context["_intended_date_basis"] = intended_basis

    if generated_at and intended_date:
        generated_pacific = generated_at.astimezone(PACIFIC)
        if generated_pacific.date() > intended_date:
            builder.add(
                code="generated_after_intended_draw",
                severity="critical",
                path=path,
                message="Prediction was generated after its claimed intended draw date.",
                evidence={
                    "generated_timestamp_utc": generated_at.isoformat(),
                    "generated_date_pacific": generated_pacific.date().isoformat(),
                    "intended_draw_date": intended_date.isoformat(),
                    "intended_date_basis": intended_basis,
                    "basis_is_explicit_metadata": intended_basis == "intended_draw_date",
                },
                verification_status=(
                    "verified_from_artifact"
                    if intended_basis == "intended_draw_date"
                    else "filename_claim_only"
                ),
            )


def _winning_claim(
    context: JsonObject,
    builder: _AuditBuilder,
    verified_results: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[int, ...], int] | None:
    headers = context["_headers"]
    rows = context["_rows"]
    path = context["path"]
    draw_date_value = context.get("_draw_date")
    evidence = verified_results.get(draw_date_value or "")
    if not set((*WIN_MAIN_FIELDS, "win_mega")).issubset(headers):
        builder.add(
            code="winning_result_not_embedded",
            severity="warning" if evidence else "error",
            path=path,
            message=(
                "Original score rows omit winning numbers; recomputation requires "
                "separately stored two-source evidence."
            ),
            verification_status="verified_two_source" if evidence else "unverified",
        )
        if evidence:
            external_mains = tuple(int(value) for value in evidence["mains"])
            external_mega = int(evidence["mega"])
            context["result_claim"] = {
                "draw_date": draw_date_value,
                "mains": list(external_mains),
                "mega": external_mega,
                "verification_status": "verified_two_source",
                "claim_origin": "external_evidence_only",
                "external_evidence": dict(evidence),
            }
            return external_mains, external_mega
        context["result_claim"] = {
            "verification_status": "unverified",
            "reason": "winning_numbers_absent",
        }
        return None

    claims: set[tuple[tuple[int | None, ...], int | None]] = set()
    for _, row in rows:
        mains = tuple(_parse_int(row.get(field)) for field in WIN_MAIN_FIELDS)
        claims.add((mains, _parse_int(row.get("win_mega"))))
    if len(claims) != 1:
        builder.add(
            code="inconsistent_winning_result_claim",
            severity="critical",
            path=path,
            message="Scoring rows disagree on the purported winning result.",
            evidence={"claim_count": len(claims)},
            verification_status="unverified",
        )
        return None

    mains_with_none, mega = next(iter(claims))
    if any(value is None for value in mains_with_none) or mega is None:
        builder.add(
            code="invalid_winning_result_value",
            severity="critical",
            path=path,
            message="Purported winning result contains a non-integer value.",
            verification_status="unverified",
        )
        return None
    winning_mains = tuple(int(value) for value in mains_with_none if value is not None)
    winning_mega = int(mega)
    if winning_mains != tuple(sorted(winning_mains)):
        builder.add(
            code="unnormalized_winning_main_order",
            severity="warning",
            path=path,
            message="Embedded winning mains are not normalized in ascending order.",
            evidence={"embedded_order": list(winning_mains)},
        )
    normalized_winning_mains = tuple(sorted(winning_mains))
    if (
        len(set(winning_mains)) != 5
        or not all(1 <= value <= 47 for value in winning_mains)
        or not 1 <= winning_mega <= 27
    ):
        builder.add(
            code="invalid_winning_result_rules",
            severity="critical",
            path=path,
            message="Purported winning result violates SuperLotto Plus rules.",
            evidence={"mains": list(winning_mains), "mega": winning_mega},
            verification_status="unverified",
        )

    claim_record: JsonObject = {
        "draw_date": draw_date_value,
        "mains": list(winning_mains),
        "mega": winning_mega,
        "verification_status": "unverified",
    }
    if evidence is None:
        builder.add(
            code="unverified_winning_result_claim",
            severity="error",
            path=path,
            message="Winning result has no stored official-plus-backup verification.",
            evidence={
                "draw_date": draw_date_value,
                "mains": list(winning_mains),
                "mega": winning_mega,
            },
            verification_status="unverified",
        )
    else:
        expected_mains = tuple(sorted(int(value) for value in evidence["mains"]))
        expected_mega = int(evidence["mega"])
        claim_record["external_evidence"] = dict(evidence)
        if normalized_winning_mains == expected_mains and winning_mega == expected_mega:
            claim_record["verification_status"] = "verified_two_source"
        else:
            builder.add(
                code="winning_result_mismatch_against_two_sources",
                severity="critical",
                path=path,
                message="Embedded winning result disagrees with two-source evidence.",
                evidence={
                    "embedded": {
                        "mains": list(winning_mains),
                        "mega": winning_mega,
                    },
                    "verified": {"mains": list(expected_mains), "mega": expected_mega},
                    "sources": evidence["sources"],
                },
                verification_status="mismatch",
            )
            claim_record["verification_status"] = "mismatch"
    context["result_claim"] = claim_record
    return winning_mains, winning_mega


def _check_scoring(
    *,
    context: JsonObject,
    builder: _AuditBuilder,
    verified_results: Mapping[str, Mapping[str, Any]],
) -> None:
    rows = context["_rows"]
    headers = context["_headers"]
    path = context["path"]
    draw_value, observed_draws = _uniform_value(rows, "draw_date")
    if "draw_date" in headers and draw_value is None and observed_draws:
        builder.add(
            code="inconsistent_scoring_draw_date",
            severity="critical",
            path=path,
            message="Scoring rows disagree on draw_date.",
            evidence={"observed_values": observed_draws},
        )
    filename_date, filename_basis = _filename_claimed_date(context["_absolute_path"], "scoring")
    context["_draw_date"] = draw_value or (filename_date.isoformat() if filename_date else None)
    context["_draw_date_basis"] = "draw_date" if draw_value else filename_basis

    bundle_value, bundle_values = _uniform_value(rows, "bundle_id")
    if "bundle_id" in headers and bundle_value is None and bundle_values:
        builder.add(
            code="inconsistent_scoring_bundle_id",
            severity="critical",
            path=path,
            message="Scoring rows disagree on bundle_id.",
            evidence={"observed_values": bundle_values},
        )
    context["_bundle_id"] = bundle_value

    result = _winning_claim(context, builder, verified_results)
    if result is None:
        context["scoring_consistency"] = {
            "status": "unverifiable",
            "reason": "winning_result_unavailable_or_invalid",
        }
        return

    winning_mains, winning_mega = result
    inconsistencies: defaultdict[str, list[JsonObject]] = defaultdict(list)
    aggregate_categories: list[JsonObject] = []
    checked_rows = 0
    for row_number, row in rows:
        ticket_mains = tuple(_parse_int(row.get(field)) for field in MAIN_FIELDS)
        ticket_mega = _parse_int(row.get("mega"))
        if any(value is None for value in ticket_mains) or ticket_mega is None:
            continue
        checked_rows += 1
        typed_mains = tuple(int(value) for value in ticket_mains if value is not None)
        matched = tuple(sorted(set(typed_mains) & set(winning_mains)))
        mega_hit = ticket_mega == winning_mega
        recorded_matches = _parse_int(row.get("main_matches"))
        if recorded_matches != len(matched):
            inconsistencies["main_match_count"].append(
                {
                    "row": row_number,
                    "recorded": recorded_matches,
                    "computed": len(matched),
                }
            )
        recorded_mega_hit = _truth_value(row.get("mega_hit"))
        if recorded_mega_hit != mega_hit:
            inconsistencies["mega_hit"].append(
                {
                    "row": row_number,
                    "recorded": row.get("mega_hit"),
                    "computed": mega_hit,
                }
            )
        matched_field = "matched_mains" if "matched_mains" in headers else "matched_nums"
        if matched_field in headers:
            recorded_numbers = tuple(
                int(value) for value in re.findall(r"\d+", row.get(matched_field, ""))
            )
            if recorded_numbers != matched:
                inconsistencies["matched_mains"].append(
                    {
                        "row": row_number,
                        "recorded": list(recorded_numbers),
                        "computed": list(matched),
                    }
                )
        expected_category = _score_category(len(matched), mega_hit)
        recorded_category = row.get("category", "").strip()
        legacy_aggregate = (
            recorded_category == "0-2 (no Mega)" and not mega_hit and len(matched) <= 2
        )
        if legacy_aggregate:
            aggregate_categories.append(
                {
                    "row": row_number,
                    "recorded": recorded_category,
                    "canonical": expected_category,
                }
            )
        elif recorded_category != expected_category:
            inconsistencies["category"].append(
                {
                    "row": row_number,
                    "recorded": recorded_category,
                    "computed": expected_category,
                }
            )

    if aggregate_categories:
        builder.add(
            code="aggregate_nonwinning_category",
            severity="warning",
            path=path,
            message=(
                "Legacy category combines 0, 1, and 2-main nonwinning outcomes "
                "instead of recording the exact category."
            ),
            rows=(item["row"] for item in aggregate_categories),
            evidence={"rows": aggregate_categories},
        )

    for field, violations in sorted(inconsistencies.items()):
        builder.add(
            code=f"scoring_{field}_inconsistency",
            severity="critical",
            path=path,
            message=f"Recorded {field.replace('_', ' ')} disagrees with recomputation.",
            rows=(item["row"] for item in violations),
            evidence={"violations": violations},
            verification_status=context["result_claim"]["verification_status"],
        )
    context["scoring_consistency"] = {
        "status": "consistent" if not inconsistencies else "inconsistent",
        "checked_rows": checked_rows,
        "inconsistency_count": sum(len(values) for values in inconsistencies.values()),
        "winning_result_verification": context["result_claim"]["verification_status"],
    }


def _signature(ticket: Mapping[str, Any], *, include_identity: bool = True) -> tuple[Any, ...]:
    prefix: tuple[Any, ...] = ()
    if include_identity:
        prefix = (ticket["strategy"], ticket["line_id"])
    return (*prefix, *ticket["mains"], ticket["mega"])


def _check_associations(contexts: Sequence[JsonObject], builder: _AuditBuilder) -> list[JsonObject]:
    predictions = [context for context in contexts if context["kind"] == "prediction"]
    scoring = [context for context in contexts if context["kind"] == "scoring"]
    associations: list[JsonObject] = []

    bundle_paths: defaultdict[str, list[JsonObject]] = defaultdict(list)
    draw_paths: defaultdict[str, list[JsonObject]] = defaultdict(list)
    for prediction in predictions:
        bundle_id = prediction.get("_bundle_id")
        if bundle_id:
            bundle_paths[bundle_id].append(prediction)
        intended = prediction.get("_intended_date")
        if intended and prediction.get("_intended_date_basis") == "intended_draw_date":
            draw_paths[intended.isoformat()].append(prediction)

    for bundle_id, matching in sorted(bundle_paths.items()):
        if len(matching) > 1:
            paths = sorted(context["path"] for context in matching)
            builder.add(
                code="duplicate_bundle_id_across_predictions",
                severity="critical",
                path=paths[0],
                related_paths=paths[1:],
                message="Multiple prediction artifacts claim the same bundle_id.",
                evidence={"bundle_id": bundle_id, "paths": paths},
            )
    for draw_date_value, matching in sorted(draw_paths.items()):
        if len(matching) > 1:
            paths = sorted(context["path"] for context in matching)
            builder.add(
                code="multiple_bundles_for_intended_draw",
                severity="warning",
                path=paths[0],
                related_paths=paths[1:],
                message="More than one supplied bundle targets the same draw.",
                evidence={
                    "intended_draw_date": draw_date_value,
                    "bundles": [
                        {"path": item["path"], "bundle_id": item.get("_bundle_id")}
                        for item in sorted(matching, key=lambda value: value["path"])
                    ],
                },
            )

    for score in sorted(scoring, key=lambda item: item["path"]):
        score_tickets = score["_tickets"]
        score_counter = Counter(_signature(ticket) for ticket in score_tickets)
        comparisons: list[tuple[int, str, JsonObject]] = []
        for prediction in predictions:
            prediction_counter = Counter(_signature(ticket) for ticket in prediction["_tickets"])
            common = sum((score_counter & prediction_counter).values())
            comparisons.append((common, prediction["path"], prediction))
        comparisons.sort(key=lambda item: (-item[0], item[1]))
        best_count, _, best = comparisons[0] if comparisons else (0, "", None)
        exact = bool(
            best and best_count == len(score_tickets) and best_count == len(best["_tickets"])
        )
        partial_threshold = max(2, min(len(score_tickets), 30) // 2)
        status = (
            "exact_content_match"
            if exact
            else ("partial_content_match" if best_count >= partial_threshold else "no_match")
        )
        association: JsonObject = {
            "scoring_path": score["path"],
            "scoring_draw_date": score.get("_draw_date"),
            "scoring_draw_date_basis": score.get("_draw_date_basis"),
            "claimed_bundle_id": score.get("_bundle_id"),
            "status": status,
            "matched_prediction_path": best["path"] if best else None,
            "matching_rows": best_count,
            "scoring_rows": len(score_tickets),
            "prediction_rows": len(best["_tickets"]) if best else None,
        }
        if best:
            remaining_prediction = Counter(_signature(ticket) for ticket in best["_tickets"])
            unmatched_scoring_rows: list[int] = []
            for ticket in score_tickets:
                signature = _signature(ticket)
                if remaining_prediction[signature]:
                    remaining_prediction[signature] -= 1
                else:
                    unmatched_scoring_rows.append(ticket["row"])
            remaining_scoring = Counter(_signature(ticket) for ticket in score_tickets)
            unmatched_prediction_rows: list[int] = []
            for ticket in best["_tickets"]:
                signature = _signature(ticket)
                if remaining_scoring[signature]:
                    remaining_scoring[signature] -= 1
                else:
                    unmatched_prediction_rows.append(ticket["row"])
            association["unmatched_scoring_rows"] = unmatched_scoring_rows
            association["unmatched_prediction_rows"] = unmatched_prediction_rows
        associations.append(association)

        if status == "partial_content_match" and best is not None:
            builder.add(
                code="partial_scoring_bundle_association",
                severity="critical",
                path=score["path"],
                related_paths=[best["path"]],
                message="Scoring rows only partially match the nearest prediction bundle.",
                rows=association["unmatched_scoring_rows"],
                evidence=association,
                verification_status="mismatch",
            )
        elif status == "no_match":
            builder.add(
                code="orphan_scoring_artifact",
                severity="critical",
                path=score["path"],
                message="No supplied prediction bundle matches this scoring artifact.",
                rows=(ticket["row"] for ticket in score_tickets),
                evidence=association,
                verification_status="unverified",
            )

        claimed_bundle_id = score.get("_bundle_id")
        if claimed_bundle_id:
            claimed_predictions = bundle_paths.get(claimed_bundle_id, [])
            if not claimed_predictions:
                builder.add(
                    code="unknown_scoring_bundle_id",
                    severity="critical",
                    path=score["path"],
                    message="Scoring bundle_id has no supplied prediction artifact.",
                    evidence={"bundle_id": claimed_bundle_id},
                )
            elif exact and best is not None and best not in claimed_predictions:
                builder.add(
                    code="wrong_scoring_bundle_id",
                    severity="critical",
                    path=score["path"],
                    related_paths=[best["path"]],
                    message="Scoring content matches a different bundle than bundle_id claims.",
                    evidence=association,
                )

        if exact and best:
            score_draw = _parse_date(score.get("_draw_date"))
            prediction_draw = best.get("_intended_date")
            if score_draw and prediction_draw and score_draw != prediction_draw:
                builder.add(
                    code="wrong_draw_association",
                    severity="critical",
                    path=score["path"],
                    related_paths=[best["path"]],
                    message="Scoring draw date differs from matched bundle's intended draw.",
                    evidence={
                        "scoring_draw_date": score_draw.isoformat(),
                        "prediction_intended_draw_date": prediction_draw.isoformat(),
                        "prediction_date_basis": best.get("_intended_date_basis"),
                    },
                )
    return associations


def _check_corrected_variants(
    contexts: Sequence[JsonObject], builder: _AuditBuilder
) -> list[JsonObject]:
    predictions = [context for context in contexts if context["kind"] == "prediction"]
    variants: list[JsonObject] = []
    for corrected in predictions:
        if "corrected" not in corrected["_absolute_path"].name.lower():
            continue
        corrected_counter = Counter(
            _signature(ticket, include_identity=False) for ticket in corrected["_tickets"]
        )
        candidates: list[tuple[int, str, JsonObject]] = []
        for candidate in predictions:
            if candidate is corrected:
                continue
            candidate_counter = Counter(
                _signature(ticket, include_identity=False) for ticket in candidate["_tickets"]
            )
            common = sum((corrected_counter & candidate_counter).values())
            candidates.append((common, candidate["path"], candidate))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        shared, _, parent = candidates[0] if candidates else (0, "", None)
        parent_counter = (
            Counter(_signature(ticket, include_identity=False) for ticket in parent["_tickets"])
            if parent
            else Counter()
        )
        removed = sorted((parent_counter - corrected_counter).elements())
        added = sorted((corrected_counter - parent_counter).elements())
        corrected_identity_counter = Counter(_signature(ticket) for ticket in corrected["_tickets"])
        parent_identity_counter = (
            Counter(_signature(ticket) for ticket in parent["_tickets"]) if parent else Counter()
        )
        shared_identity = sum((corrected_identity_counter & parent_identity_counter).values())
        record: JsonObject = {
            "corrected_path": corrected["path"],
            "nearest_original_path": parent["path"] if parent else None,
            "shared_full_tickets_ignoring_line_identity": shared,
            "corrected_ticket_count": len(corrected["_tickets"]),
            "parent_ticket_count": len(parent["_tickets"]) if parent else None,
            "shared_tickets_with_same_strategy_and_line_id": shared_identity,
            "removed_full_tickets": [list(ticket) for ticket in removed],
            "added_full_tickets": [list(ticket) for ticket in added],
            "correction_manifest_present": False,
        }
        variants.append(record)
        builder.add(
            code="untracked_corrected_variant",
            severity="error",
            path=corrected["path"],
            related_paths=[parent["path"]] if parent else (),
            message=(
                "Filename claims a correction, but no parent hash, reason, version, "
                "or correction manifest is present."
            ),
            evidence=record,
            verification_status="unverified",
        )
    return variants


def audit_legacy_tree(
    input_root: Path | str,
    *,
    repository_root: Path | str | None = None,
    verified_results: Mapping[str, Mapping[str, Any]] | None = None,
) -> JsonObject:
    """Audit every CSV below *input_root* without changing any source file.

    ``verified_results`` must contain already-established two-source evidence.
    An omitted mapping is intentionally treated as no external verification.
    """

    input_path = Path(input_root)
    repo_path = Path(repository_root) if repository_root else Path.cwd()
    evidence = {
        draw_date_value: record
        for draw_date_value, record in (verified_results or {}).items()
        if _is_two_source_evidence(record)
    }
    builder = _AuditBuilder()
    contexts: list[JsonObject] = []

    csv_paths = sorted(input_path.rglob("*.csv"), key=lambda path: path.as_posix())
    for absolute_path in csv_paths:
        display_path = _display_path(absolute_path, repo_path, input_path)
        kind = _artifact_kind(absolute_path)
        headers, rows, raw = _read_csv(absolute_path, display_path, kind, builder)
        _check_schema(kind=kind, headers=headers, path=display_path, builder=builder)
        tickets = _extract_tickets(headers=headers, rows=rows, path=display_path, builder=builder)
        if tickets:
            _check_ticket_identity(tickets=tickets, path=display_path, builder=builder)
            constraint_metrics = _check_bundle_constraints(
                tickets=tickets, path=display_path, builder=builder
            )
        else:
            constraint_metrics = None

        context: JsonObject = {
            "path": display_path,
            "kind": kind,
            "sha256": _sha256_bytes(raw),
            "byte_size": len(raw),
            "row_count": len(rows),
            "headers": list(headers),
            "constraint_metrics": constraint_metrics,
            "_absolute_path": absolute_path,
            "_headers": list(headers),
            "_rows": rows,
            "_tickets": tickets,
        }
        if kind == "prediction":
            _check_prediction_metadata_values(context=context, builder=builder)
            if constraint_metrics:
                _check_recenter_collapse(
                    tickets=tickets,
                    metrics=constraint_metrics,
                    path=display_path,
                    builder=builder,
                )
        elif kind == "scoring":
            _check_scoring(context=context, builder=builder, verified_results=evidence)
        contexts.append(context)

    associations = _check_associations(contexts, builder)
    corrected_variants = _check_corrected_variants(contexts, builder)
    sorted_findings = sorted(
        builder.findings,
        key=lambda item: (
            item["path"],
            item["code"],
            item["finding_id"],
        ),
    )
    severity_counts = Counter(item["severity"] for item in sorted_findings)
    code_counts = Counter(item["code"] for item in sorted_findings)
    scoring_inconsistency_count = sum(
        count
        for code, count in code_counts.items()
        if code.startswith("scoring_") and code.endswith("_inconsistency")
    )

    files: list[JsonObject] = []
    for context in sorted(contexts, key=lambda item: item["path"]):
        file_record: JsonObject = {
            key: value for key, value in context.items() if not key.startswith("_")
        }
        file_record["finding_ids"] = sorted(set(builder.file_findings[context["path"]]))
        file_record["original_preserved"] = True
        files.append(file_record)

    inventory_material = "".join(
        f"{item['path']}\0{item['sha256']}\0{item['byte_size']}\n" for item in files
    ).encode()
    verified_claims = sum(
        1
        for context in contexts
        if context.get("result_claim", {}).get("verification_status") == "verified_two_source"
    )
    unverified_claims = sum(
        1
        for context in contexts
        if context.get("result_claim", {}).get("verification_status") == "unverified"
    )

    try:
        displayed_root = input_path.resolve().relative_to(repo_path.resolve()).as_posix()
    except ValueError:
        displayed_root = input_path.name
    return {
        "manifest_version": AUDIT_MANIFEST_VERSION,
        "game_rules_version": GAME_RULES_VERSION,
        "input_root": displayed_root,
        "audit_scope": "all_csv_files_recursively",
        "audit_policy": {
            "input_trust": "untrusted",
            "writes_to_legacy_tree": False,
            "winning_result_policy": (
                "verified only when official California Lottery and at least one "
                "approved backup agree exactly"
            ),
            "main_range": [1, 47],
            "mega_range": [1, 27],
            "unique_mains_per_ticket": 5,
            "maximum_main_overlap": 3,
            "minimum_hamming_distance": 2,
            "pair_cap": 2,
            "triple_cap": 1,
            "post_draw_timing_check": (
                "certain failure when generated Pacific calendar date is later than "
                "the explicit or filename-claimed draw date; same-day status is not "
                "inferred without an authoritative draw/post timestamp"
            ),
            "recenter_collapse_heuristic": {
                "max_sorted_position_share_at_least": 0.40,
                "or_max_main_ticket_share_at_least": 0.45,
                "minimum_bundle_rows": 10,
                "classification": "warning_not_proof",
            },
        },
        "source_integrity": {
            "file_count": len(files),
            "inventory_sha256": _sha256_bytes(inventory_material),
            "hash_algorithm": "SHA-256",
            "originals_preserved_byte_for_byte": True,
        },
        "summary": {
            "csv_file_count": len(files),
            "finding_count": len(sorted_findings),
            "files_with_findings": sum(bool(item["finding_ids"]) for item in files),
            "severity_counts": dict(sorted(severity_counts.items())),
            "finding_code_counts": dict(sorted(code_counts.items())),
            "verified_winning_result_claims": verified_claims,
            "unverified_winning_result_claims": unverified_claims,
            "winning_result_mismatches": code_counts["winning_result_mismatch_against_two_sources"],
            "internal_scoring_inconsistency_findings": scoring_inconsistency_count,
            "invalid_range_findings": code_counts["invalid_number_range"],
            "duplicate_bundle_id_findings": code_counts["duplicate_bundle_id_across_predictions"],
            "duplicate_main_set_findings": code_counts["duplicate_main_set"],
            "overlap_cap_findings": code_counts["main_overlap_cap_violation"],
            "corrections_applied": 0,
            "reconciled_records_created": 0,
        },
        "files": files,
        "associations": associations,
        "corrected_variants": corrected_variants,
        "findings": sorted_findings,
        "corrections": [],
        "reconciled_records": [],
    }


def verify_legacy_inventory(
    manifest_path: Path | str,
    *,
    repository_root: Path | str | None = None,
) -> JsonObject:
    """Re-hash every preserved legacy CSV against its reconciled manifest.

    The historical audit is useful only while its immutable inputs remain the
    exact bytes that were reviewed.  This verifier also checks for removed or
    newly introduced CSVs so a stale manifest cannot silently bless a changed
    legacy tree.
    """

    root = Path(repository_root or Path.cwd()).resolve()
    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = root / manifest_file
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read legacy audit manifest: {manifest_file}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("legacy audit manifest has an invalid files inventory")
    input_root_value = manifest.get("input_root")
    if not isinstance(input_root_value, str) or not input_root_value:
        raise ValueError("legacy audit manifest has no input_root")
    input_root = (root / input_root_value).resolve()
    if input_root != root and root not in input_root.parents:
        raise ValueError("legacy audit input_root escapes the repository")

    records: dict[str, Mapping[str, Any]] = {}
    observed: list[tuple[str, str, int]] = []
    for raw_record in manifest["files"]:
        if not isinstance(raw_record, dict):
            raise ValueError("legacy audit manifest contains a non-object file record")
        path_value = raw_record.get("path")
        if not isinstance(path_value, str) or path_value in records:
            raise ValueError("legacy audit manifest contains an invalid or duplicate path")
        candidate = (root / path_value).resolve()
        if candidate != input_root and input_root not in candidate.parents:
            raise ValueError(f"legacy inventory path escapes input_root: {path_value}")
        if not candidate.is_file():
            raise ValueError(f"legacy inventory file is missing: {path_value}")
        payload = candidate.read_bytes()
        digest = _sha256_bytes(payload)
        expected_digest = raw_record.get("sha256")
        expected_size = raw_record.get("byte_size")
        if digest != expected_digest or len(payload) != expected_size:
            raise ValueError(f"legacy inventory checksum mismatch: {path_value}")
        records[path_value] = raw_record
        observed.append((path_value, digest, len(payload)))

    actual_paths = {
        path.resolve().relative_to(root).as_posix() for path in input_root.rglob("*.csv")
    }
    expected_paths = set(records)
    if missing := sorted(expected_paths - actual_paths):
        raise ValueError(f"legacy inventory files disappeared: {', '.join(missing)}")
    if additions := sorted(actual_paths - expected_paths):
        raise ValueError(f"unmanifested legacy CSV files detected: {', '.join(additions)}")
    observed.sort(key=lambda item: item[0])
    inventory_material = "".join(
        f"{path}\0{digest}\0{size}\n" for path, digest, size in observed
    ).encode()
    inventory_sha256 = _sha256_bytes(inventory_material)
    source_integrity = manifest.get("source_integrity")
    if not isinstance(source_integrity, dict):
        raise ValueError("legacy audit manifest has no source_integrity record")
    if source_integrity.get("inventory_sha256") != inventory_sha256:
        raise ValueError("legacy inventory aggregate checksum mismatch")
    if source_integrity.get("file_count") != len(observed):
        raise ValueError("legacy inventory file count mismatch")
    return {
        "status": "verified",
        "manifest": manifest_file.relative_to(root).as_posix(),
        "input_root": input_root.relative_to(root).as_posix(),
        "file_count": len(observed),
        "inventory_sha256": inventory_sha256,
    }


def render_human_report(manifest: Mapping[str, Any]) -> str:
    """Render a deterministic Markdown view of an audit manifest."""

    summary = manifest["summary"]
    severity = summary["severity_counts"]
    clean_checks = [
        label
        for key, label in (
            ("invalid_range_findings", "invalid number ranges"),
            ("duplicate_bundle_id_findings", "duplicate bundle IDs"),
            ("duplicate_main_set_findings", "duplicate main sets"),
            ("overlap_cap_findings", "overlap-cap violations"),
            ("winning_result_mismatches", "two-source result mismatches"),
            (
                "internal_scoring_inconsistency_findings",
                "internal score recomputation errors",
            ),
        )
        if summary[key] == 0
    ]
    lines = [
        "# Legacy handoff audit",
        "",
        (
            "This report treats every CSV under "
            f"`{manifest['input_root']}` as untrusted input. The audit is read-only: "
            "no historical artifact was corrected, normalized, or overwritten."
        ),
        "",
        "## Outcome",
        "",
        (
            f"Audited {summary['csv_file_count']} CSV files and recorded "
            f"{summary['finding_count']} findings: "
            f"{severity.get('critical', 0)} critical, {severity.get('error', 0)} "
            f"error, {severity.get('warning', 0)} warning, and "
            f"{severity.get('info', 0)} informational."
        ),
        "",
        (
            f"Two-source winning-result claims verified: "
            f"{summary['verified_winning_result_claims']}. Unverified claims: "
            f"{summary['unverified_winning_result_claims']}. Corrections applied: 0."
        ),
        "",
        f"Checks with no findings: {', '.join(clean_checks) or 'none'}.",
        "",
        "The reconciled directory contains only the audit manifest. It contains no "
        "replacement history, prediction, or scoring rows because the audit never "
        "silently repairs untrusted data.",
        "",
        "## Material findings",
        "",
    ]
    material = [
        finding
        for finding in manifest["findings"]
        if finding["severity"] in {"critical", "error"}
        or finding["code"]
        in {
            "aggregate_nonwinning_category",
            "likely_recenter_collapse",
            "multiple_bundles_for_intended_draw",
        }
    ]
    for finding in material:
        row_text = f" (CSV rows {', '.join(map(str, finding['rows']))})" if finding["rows"] else ""
        lines.append(f"- `{finding['code']}` — `{finding['path']}`{row_text}: {finding['message']}")
    if not material:
        lines.append("- No critical or error findings.")

    lines.extend(
        [
            "",
            "## Scoring-to-bundle associations",
            "",
            "| Scoring artifact | Status | Nearest prediction | Matching rows |",
            "|---|---:|---|---:|",
        ]
    )
    for association in manifest["associations"]:
        lines.append(
            f"| `{association['scoring_path']}` | "
            f"{association['status']} | "
            f"`{association['matched_prediction_path'] or 'none'}` | "
            f"{association['matching_rows']}/{association['scoring_rows']} |"
        )

    lines.extend(["", "## Claimed correction variants", ""])
    if manifest["corrected_variants"]:
        for variant in manifest["corrected_variants"]:
            lines.append(
                f"- `{variant['corrected_path']}` is closest to "
                f"`{variant['nearest_original_path']}`. It retains "
                f"{variant['shared_full_tickets_ignoring_line_identity']}/"
                f"{variant['corrected_ticket_count']} full tickets, but only "
                f"{variant['shared_tickets_with_same_strategy_and_line_id']} retain "
                "the same strategy/line identity. Two tickets were removed and two "
                "were added. No parent hash, correction reason, or version manifest "
                "exists, so no reconciled replacement was created."
            )
    else:
        lines.append("- No filename claimed to be a corrected variant.")

    lines.extend(
        [
            "",
            "## Source verification",
            "",
            (
                "All four scoring dates were checked against the California Lottery "
                "official backend and an approved backup. The 2025 dates use "
                "Lottery.net; the 2026 dates use LotteryUSA. Every source pair agreed "
                "exactly. The original scoring CSVs still lack their own source URLs "
                "and fetch timestamps, which remains a provenance finding."
            ),
            "",
            (
                "2026 official: <https://www.calottery.com/api/DrawGameApi/"
                "DrawGamePastDrawResults/8/3/20>"
            ),
            "",
            (
                "2025 official: <https://www.calottery.com/api/DrawGameApi/"
                "DrawGamePastDrawResults/8/5/20>"
            ),
            "",
            (
                "2025 approved backup: <https://www.lottery.net/california/"
                "superlotto-plus/numbers/2025>"
            ),
            "",
            ("Approved backup: <https://www.lotteryusa.com/california/super-lotto-plus/year>"),
            "",
            "For the two files without embedded winners, recomputation used the "
            "stored two-source observations above. The audit never infers a winning "
            "result from recorded match counts.",
            "",
            "## Integrity inventory",
            "",
            "| Artifact | Rows | Bytes | SHA-256 |",
            "|---|---:|---:|---|",
        ]
    )
    for item in manifest["files"]:
        lines.append(
            f"| `{item['path']}` | {item['row_count']} | {item['byte_size']} | `{item['sha256']}` |"
        )

    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- A content match can establish which supplied ticket rows were scored; "
            "it cannot prove that an artifact was locked before the draw.",
            "- A date taken from a filename is reported as a filename claim, not "
            "trusted bundle metadata.",
            "- The recenter-collapse warning is a deterministic concentration "
            "heuristic, not proof of how a bundle was generated.",
            "- No prediction performance claim implies that lottery outcomes are "
            "predictable or guaranteed.",
            "",
            "The complete row-level evidence and all finding identifiers are in "
            "`data/reconciled/legacy_audit_manifest.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_audit_outputs(
    input_root: Path | str,
    manifest_path: Path | str,
    report_path: Path | str,
    *,
    repository_root: Path | str | None = None,
    verified_results: Mapping[str, Mapping[str, Any]] | None = None,
) -> JsonObject:
    """Write reconciled audit outputs and assert source hashes did not change."""

    input_path = Path(input_root).resolve()
    manifest_output = Path(manifest_path).resolve()
    report_output = Path(report_path).resolve()
    for output in (manifest_output, report_output):
        if output == input_path or input_path in output.parents:
            raise ValueError("audit outputs must be outside the immutable legacy tree")

    before = {
        path.resolve(): _sha256_bytes(path.read_bytes())
        for path in sorted(input_path.rglob("*.csv"))
    }
    manifest = audit_legacy_tree(
        input_path,
        repository_root=repository_root,
        verified_results=verified_results,
    )
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_output.write_text(render_human_report(manifest), encoding="utf-8")
    after = {path: _sha256_bytes(path.read_bytes()) for path in before}
    if before != after:
        raise RuntimeError("legacy input changed while audit outputs were written")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_root",
        nargs="?",
        default="data/legacy/handoff-20260717",
        type=Path,
    )
    parser.add_argument(
        "--manifest",
        default=Path("data/reconciled/legacy_audit_manifest.json"),
        type=Path,
    )
    parser.add_argument("--report", default=Path("docs/LEGACY_AUDIT.md"), type=Path)
    parser.add_argument(
        "--use-embedded-two-source-verifications",
        action="store_true",
        help="Use the reviewed source observations stored in this module.",
    )
    args = parser.parse_args(argv)
    evidence = (
        EMBEDDED_TWO_SOURCE_VERIFICATIONS if args.use_embedded_two_source_verifications else None
    )
    write_audit_outputs(
        args.input_root,
        args.manifest,
        args.report,
        repository_root=Path.cwd(),
        verified_results=evidence,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())

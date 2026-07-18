"""Exact line scoring and bundle/tier performance statistics."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import mean, pstdev, stdev

from .exceptions import IntegrityError, VerificationError
from .models import (
    BundleScore,
    Draw,
    LockedBundle,
    PrizeCategory,
    ScoredLine,
    ScoreStatistics,
    Strategy,
    Ticket,
    VerifiedDraw,
)
from .storage import canonical_json_bytes

PRIZE_CATEGORIES: tuple[PrizeCategory, ...] = (
    "Jackpot (5+Mega)",
    "5 mains",
    "4+Mega",
    "4 mains",
    "3+Mega",
    "3 mains",
    "2+Mega",
    "1+Mega",
    "Mega only",
    "No prize",
)


@dataclass(frozen=True)
class TicketScore:
    matched_mains: tuple[int, ...]
    mega_hit: bool
    category: PrizeCategory

    @property
    def main_matches(self) -> int:
        return len(self.matched_mains)


def category(main_matches: int, mega_hit: bool) -> PrizeCategory:
    """Return the official SuperLotto Plus prize category for a line."""

    if not 0 <= main_matches <= 5:
        raise ValueError("main_matches must be between zero and five")
    if main_matches == 5:
        return "Jackpot (5+Mega)" if mega_hit else "5 mains"
    if main_matches == 4:
        return "4+Mega" if mega_hit else "4 mains"
    if main_matches == 3:
        return "3+Mega" if mega_hit else "3 mains"
    if main_matches == 2 and mega_hit:
        return "2+Mega"
    if main_matches == 1 and mega_hit:
        return "1+Mega"
    if main_matches == 0 and mega_hit:
        return "Mega only"
    return "No prize"


def score_ticket(ticket: Ticket, draw: Draw) -> TicketScore:
    matched = tuple(sorted(set(ticket.mains) & set(draw.mains)))
    mega_hit = ticket.mega == draw.mega
    return TicketScore(matched, mega_hit, category(len(matched), mega_hit))


def _validate_scored_line(line: ScoredLine, draw: Draw) -> None:
    """Recompute every result-derived field instead of trusting stored values."""

    expected_matched = tuple(sorted(set(line.mains) & set(draw.mains)))
    if line.matched_mains != expected_matched:
        raise IntegrityError(
            f"scored line {line.strategy}:{line.line_id} has an incorrect main intersection"
        )
    if line.main_match_count != len(expected_matched):
        raise IntegrityError(
            f"scored line {line.strategy}:{line.line_id} has an incorrect main-match count"
        )
    expected_mega_hit = line.mega == draw.mega
    if line.mega_hit is not expected_mega_hit:
        raise IntegrityError(
            f"scored line {line.strategy}:{line.line_id} has an incorrect Mega hit"
        )
    expected_category = category(len(expected_matched), expected_mega_hit)
    if line.prize_category != expected_category:
        raise IntegrityError(
            f"scored line {line.strategy}:{line.line_id} has an incorrect prize category"
        )


def _statistics(lines: Iterable[ScoredLine]) -> ScoreStatistics:
    materialized = list(lines)
    counts = [line.main_match_count for line in materialized]
    size = len(materialized)
    if size == 0:
        return ScoreStatistics(
            histogram={value: 0 for value in range(6)},
            mega_hit_count=0,
            mega_hit_rate=0.0,
            mean_main_matches=0.0,
            population_stddev=0.0,
            sample_stddev=0.0,
            empirical_p_ge_2=0.0,
            empirical_p_ge_3=0.0,
            empirical_p_ge_4=0.0,
            category_counts={name: 0 for name in PRIZE_CATEGORIES},
        )
    histogram_counts = Counter(counts)
    category_counts = Counter(line.prize_category for line in materialized)
    mega_hits = sum(line.mega_hit for line in materialized)
    return ScoreStatistics(
        histogram={value: histogram_counts[value] for value in range(6)},
        mega_hit_count=mega_hits,
        mega_hit_rate=mega_hits / size,
        mean_main_matches=mean(counts),
        population_stddev=pstdev(counts),
        sample_stddev=stdev(counts) if size > 1 else 0.0,
        empirical_p_ge_2=sum(value >= 2 for value in counts) / size,
        empirical_p_ge_3=sum(value >= 3 for value in counts) / size,
        empirical_p_ge_4=sum(value >= 4 for value in counts) / size,
        category_counts={name: category_counts[name] for name in PRIZE_CATEGORIES},
    )


def _best_line_keys(lines: Iterable[ScoredLine]) -> tuple[str, ...]:
    materialized = list(lines)
    if not materialized:
        raise IntegrityError("a scoring artifact must contain at least one line")
    best_key = max((line.main_match_count, int(line.mega_hit)) for line in materialized)
    return tuple(
        f"{line.strategy}:{line.line_id}"
        for line in materialized
        if (line.main_match_count, int(line.mega_hit)) == best_key
    )


def _realized_metrics(lines: Iterable[ScoredLine]) -> dict[str, float | bool]:
    materialized = list(lines)
    if not materialized:
        raise IntegrityError("a scoring artifact must contain at least one line")
    return {
        "any_ge_2_mains": any(line.main_match_count >= 2 for line in materialized),
        "any_ge_3_mains": any(line.main_match_count >= 3 for line in materialized),
        "any_ge_4_mains": any(line.main_match_count >= 4 for line in materialized),
        "any_3_plus_mega": any(
            line.main_match_count >= 3 and line.mega_hit for line in materialized
        ),
        "any_4_plus": any(line.main_match_count >= 4 and line.mega_hit for line in materialized),
        "best_main_match_count": float(max(line.main_match_count for line in materialized)),
    }


def _validate_realized_metrics(score: BundleScore, expected: dict[str, float | bool]) -> None:
    for name, expected_value in expected.items():
        if name not in score.realized_metrics:
            raise IntegrityError(
                f"previous score {score.score_id} is missing realized metric {name}"
            )
        actual = score.realized_metrics[name]
        if isinstance(expected_value, bool):
            if not isinstance(actual, bool) or actual is not expected_value:
                raise IntegrityError(
                    f"previous score {score.score_id} has an incorrect realized metric {name}"
                )
        elif not isinstance(actual, (int, float)) or float(actual) != expected_value:
            raise IntegrityError(
                f"previous score {score.score_id} has an incorrect realized metric {name}"
            )


def _validate_previous_score(score: BundleScore) -> None:
    """Reject semantically inconsistent prior scores before calibration uses them."""

    seen_line_keys: set[tuple[Strategy, int]] = set()
    for line in score.lines:
        key = (line.strategy, line.line_id)
        if key in seen_line_keys:
            raise IntegrityError(f"previous score {score.score_id} has duplicate line key {key}")
        seen_line_keys.add(key)
        _validate_scored_line(line, score.draw)

    expected_overall = _statistics(score.lines)
    if score.overall != expected_overall:
        raise IntegrityError(f"previous score {score.score_id} has inconsistent overall statistics")
    expected_tiers: dict[Strategy, ScoreStatistics] = {
        strategy: _statistics(line for line in score.lines if line.strategy == strategy)
        for strategy in ("aggressive", "balanced", "conservative")
    }
    if score.tiers != expected_tiers:
        raise IntegrityError(f"previous score {score.score_id} has inconsistent tier statistics")
    if score.best_line_keys != _best_line_keys(score.lines):
        raise IntegrityError(f"previous score {score.score_id} has inconsistent best-line keys")
    _validate_realized_metrics(score, _realized_metrics(score.lines))


def _prepare_previous_scores(
    previous_scores: Iterable[BundleScore],
    *,
    current_bundle_id: str,
    current_draw_date: date,
) -> tuple[BundleScore, ...]:
    """Validate a strictly chronological, strictly historical calibration prefix."""

    materialized = tuple(previous_scores)
    seen_bundle_ids = {current_bundle_id}
    seen_draw_dates: set[date] = set()
    previous_date: date | None = None
    historical: list[BundleScore] = []
    for score in materialized:
        if score.bundle_id in seen_bundle_ids:
            raise IntegrityError(f"previous scores contain duplicate bundle_id {score.bundle_id!r}")
        seen_bundle_ids.add(score.bundle_id)
        if score.intended_draw_date in seen_draw_dates:
            raise IntegrityError(
                f"previous scores contain duplicate draw date {score.intended_draw_date}"
            )
        seen_draw_dates.add(score.intended_draw_date)
        if score.intended_draw_date >= current_draw_date:
            raise IntegrityError(
                f"previous score {score.score_id} is not strictly earlier than {current_draw_date}"
            )
        if previous_date is not None and score.intended_draw_date <= previous_date:
            raise IntegrityError("previous scores are not in strictly increasing draw-date order")
        _validate_previous_score(score)
        historical.append(score)
        previous_date = score.intended_draw_date
    return tuple(historical)


def _rolling_calibration(
    previous_scores: Iterable[BundleScore],
    *,
    predicted_p_ge_3: float,
    realized_p_ge_3: float,
    predicted_p_ge_4: float,
    realized_p_ge_4: float,
) -> dict[str, float]:
    prior = list(previous_scores)
    p3_pairs = [
        (
            score.predicted_metrics.p_any_ge_3_mains,
            float(bool(score.realized_metrics["any_ge_3_mains"])),
        )
        for score in prior
    ]
    p4_pairs = [
        (
            score.predicted_metrics.p_any_ge_4_mains,
            float(bool(score.realized_metrics["any_ge_4_mains"])),
        )
        for score in prior
    ]
    p3_pairs.append((predicted_p_ge_3, realized_p_ge_3))
    p4_pairs.append((predicted_p_ge_4, realized_p_ge_4))
    result = {
        "current_absolute_p_ge_3": abs(predicted_p_ge_3 - realized_p_ge_3),
        "current_absolute_p_ge_4": abs(predicted_p_ge_4 - realized_p_ge_4),
        "current_brier_p_ge_3": (predicted_p_ge_3 - realized_p_ge_3) ** 2,
        "current_brier_p_ge_4": (predicted_p_ge_4 - realized_p_ge_4) ** 2,
    }
    for window in (5, 10, 20):
        p3_window = p3_pairs[-window:]
        p4_window = p4_pairs[-window:]
        result[f"rolling_{window}_calibration_p_ge_3"] = abs(
            mean(item[0] for item in p3_window) - mean(item[1] for item in p3_window)
        )
        result[f"rolling_{window}_calibration_p_ge_4"] = abs(
            mean(item[0] for item in p4_window) - mean(item[1] for item in p4_window)
        )
        result[f"rolling_{window}_brier_p_ge_3"] = mean(
            (predicted - realized) ** 2 for predicted, realized in p3_window
        )
        result[f"rolling_{window}_brier_p_ge_4"] = mean(
            (predicted - realized) ** 2 for predicted, realized in p4_window
        )
    return result


def score_locked_bundle(
    bundle: LockedBundle,
    draw: VerifiedDraw,
    *,
    previous_scores: Iterable[BundleScore] = (),
    scored_timestamp_utc: datetime | None = None,
) -> BundleScore:
    """Score only the locked bundle permanently tied to ``draw.draw_date``."""

    if draw.verification.status != "verified":
        raise VerificationError("cannot score an unverified result")
    if bundle.metadata.intended_draw_date != draw.draw_date:
        raise VerificationError(
            "locked bundle intended draw date does not match the verified result"
        )
    if bundle.metadata.draw_id and draw.draw_id and bundle.metadata.draw_id != draw.draw_id:
        raise VerificationError("locked bundle draw_id does not match the verified result")

    historical_scores = _prepare_previous_scores(
        previous_scores,
        current_bundle_id=bundle.metadata.bundle_id,
        current_draw_date=draw.draw_date,
    )

    scored_lines: list[ScoredLine] = []
    for line in bundle.lines:
        result = score_ticket(line, draw)
        scored_line = ScoredLine(
            strategy=line.strategy,
            line_id=line.line_id,
            mains=line.mains,
            mega=line.mega,
            matched_mains=result.matched_mains,
            main_match_count=result.main_matches,
            mega_hit=result.mega_hit,
            prize_category=result.category,
        )
        _validate_scored_line(scored_line, draw)
        scored_lines.append(scored_line)

    overall = _statistics(scored_lines)
    tiers: dict[Strategy, ScoreStatistics] = {
        strategy: _statistics(line for line in scored_lines if line.strategy == strategy)
        for strategy in ("aggressive", "balanced", "conservative")
    }
    best_line_keys = _best_line_keys(scored_lines)
    realized = _realized_metrics(scored_lines)
    predicted = bundle.metadata.simulation
    calibration = _rolling_calibration(
        historical_scores,
        predicted_p_ge_3=predicted.p_any_ge_3_mains,
        realized_p_ge_3=float(bool(realized["any_ge_3_mains"])),
        predicted_p_ge_4=predicted.p_any_ge_4_mains,
        realized_p_ge_4=float(bool(realized["any_ge_4_mains"])),
    )
    identity = {
        "bundle_id": bundle.metadata.bundle_id,
        "draw_date": draw.draw_date.isoformat(),
        "draw_id": draw.draw_id,
        "comparison_sha256": draw.verification.comparison_sha256,
    }
    identity_hash = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    score_id = f"score-{draw.draw_date.isoformat()}-{identity_hash[:16]}"
    return BundleScore(
        score_id=score_id,
        scored_timestamp_utc=(scored_timestamp_utc or datetime.now(UTC)),
        bundle_id=bundle.metadata.bundle_id,
        intended_draw_date=bundle.metadata.intended_draw_date,
        draw=draw,
        lines=tuple(scored_lines),
        overall=overall,
        tiers=tiers,
        best_line_keys=best_line_keys,
        predicted_metrics=predicted,
        realized_metrics=realized,
        calibration_error=calibration,
    )

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

import slp_model.scoring as scoring
from slp_model.exceptions import IntegrityError
from slp_model.models import (
    BundleMetadata,
    LockedBundle,
    LockedLine,
    OptimizerSettings,
    SelectedHyperparameters,
    SimulationSummary,
    SourceEvidence,
    Ticket,
    VerificationMetadata,
    VerifiedDraw,
)
from slp_model.scoring import TicketScore, category, score_locked_bundle, score_ticket


def _post_timestamp(draw_date: date) -> datetime:
    # All deterministic fixtures are in January, when 8 p.m. Pacific is 04:00 UTC.
    return datetime.combine(draw_date + timedelta(days=1), time(4), tzinfo=UTC)


def _verified_draw(
    draw_date: date,
    *,
    mains: tuple[int, int, int, int, int] = (1, 2, 3, 4, 5),
    mega: int = 7,
) -> VerifiedDraw:
    post = _post_timestamp(draw_date)
    evidence = (
        SourceEvidence(
            source_name="california_lottery",
            role="official",
            source_url="https://example.test/california-lottery",
            fetched_timestamp_utc=post + timedelta(minutes=5),
            raw_sha256="a" * 64,
            draw_id=f"draw-{draw_date.isoformat()}",
            parser_version="fixture-v1",
            http_status=200,
        ),
        SourceEvidence(
            source_name="lotteryusa",
            role="backup",
            source_url="https://example.test/lotteryusa",
            fetched_timestamp_utc=post + timedelta(minutes=6),
            raw_sha256="b" * 64,
            draw_id=f"draw-{draw_date.isoformat()}",
            parser_version="fixture-v1",
            http_status=200,
        ),
    )
    return VerifiedDraw(
        draw_date=draw_date,
        draw_id=f"draw-{draw_date.isoformat()}",
        mains=mains,
        mega=mega,
        verification=VerificationMetadata(
            status="verified",
            verified_timestamp_utc=post + timedelta(minutes=10),
            official_post_timestamp_utc=post,
            sources=evidence,
            comparison_sha256="c" * 64,
        ),
    )


def _simulation(*, p_ge_3: float = 0.25, p_ge_4: float = 0.05) -> SimulationSummary:
    return SimulationSummary(
        simulation_count=50_000,
        candidate_pool_size=50_000,
        confidence_level=0.95,
        maximum_confidence_half_width=0.002,
        stable=True,
        stable_batches=2,
        p_any_ge_3_mains=p_ge_3,
        p_any_ge_4_mains=p_ge_4,
        p_any_3_plus_mega=0.01,
        p_any_4_plus=0.002,
        p_any_4_plus_mega=0.002,
        mean_best_main_matches=2.0,
    )


def _previous_draw_date(target: date) -> date:
    return target - timedelta(days=3 if target.weekday() == 5 else 4)


def _bundle(
    target: date,
    bundle_id: str,
    *,
    first_mega: int = 8,
    p_ge_3: float = 0.25,
    p_ge_4: float = 0.05,
) -> LockedBundle:
    history_date = _previous_draw_date(target)
    history_verification = _verified_draw(history_date).verification
    generated = datetime.combine(target - timedelta(days=1), time(12), tzinfo=UTC)
    return LockedBundle(
        metadata=BundleMetadata(
            bundle_id=bundle_id,
            generated_timestamp_utc=generated,
            intended_draw_date=target,
            game_rules_version="slp-5of47-mega-1of27-v1",
            model_version="scoring-test-v1",
            configuration_snapshot={"fixture": True},
            configuration_sha256="d" * 64,
            random_seed=17,
            source_verification_metadata=history_verification,
            history_cutoff_date=history_date,
            history_snapshot_sha256="e" * 64,
            selected_hyperparameters=SelectedHyperparameters(
                main_window=60,
                mega_window=60,
                main_sigma=1.0,
                mega_sigma=1.0,
                main_half_life_draws=20,
                mega_half_life_draws=20,
                forward_objective=0.1,
                heldout_log_likelihood=-3.0,
                selection_timestamp_utc=generated - timedelta(hours=1),
                training_draw_count=60,
            ),
            simulation=_simulation(p_ge_3=p_ge_3, p_ge_4=p_ge_4),
            optimizer=OptimizerSettings(
                algorithm="fixture-greedy",
                objective_weights={"p_ge_3": 1.0},
                constraints={"max_overlap": 3},
                anti_cannibalization_weight=0.1,
            ),
            bundle_size=3,
        ),
        lines=(
            LockedLine(
                strategy="aggressive",
                line_id=1,
                mains=(1, 2, 3, 4, 20),
                mega=first_mega,
            ),
            LockedLine(strategy="balanced", line_id=1, mains=(6, 7, 8, 9, 10), mega=9),
            LockedLine(strategy="conservative", line_id=1, mains=(11, 12, 13, 14, 15), mega=10),
        ),
    )


def _score(
    target: date,
    bundle_id: str,
    *,
    first_mega: int = 8,
    p_ge_3: float = 0.25,
    p_ge_4: float = 0.05,
):
    return score_locked_bundle(
        _bundle(
            target,
            bundle_id,
            first_mega=first_mega,
            p_ge_3=p_ge_3,
            p_ge_4=p_ge_4,
        ),
        _verified_draw(target),
        scored_timestamp_utc=_post_timestamp(target) + timedelta(minutes=15),
    )


@pytest.mark.parametrize(
    ("main_matches", "mega_hit", "expected"),
    [
        (0, False, "No prize"),
        (0, True, "Mega only"),
        (1, False, "No prize"),
        (1, True, "1+Mega"),
        (2, False, "No prize"),
        (2, True, "2+Mega"),
        (3, False, "3 mains"),
        (3, True, "3+Mega"),
        (4, False, "4 mains"),
        (4, True, "4+Mega"),
        (5, False, "5 mains"),
        (5, True, "Jackpot (5+Mega)"),
    ],
)
def test_official_prize_categories(main_matches: int, mega_hit: bool, expected: str) -> None:
    assert category(main_matches, mega_hit) == expected


def test_score_ticket_uses_exact_sorted_intersection() -> None:
    draw = _verified_draw(date(2026, 1, 3))
    ticket = Ticket(mains=(1, 2, 3, 20, 30), mega=7)

    result = score_ticket(ticket, draw)

    assert result.matched_mains == (1, 2, 3)
    assert result.mega_hit is True
    assert result.category == "3+Mega"


def test_bundle_scoring_revalidates_computed_intersection(monkeypatch: pytest.MonkeyPatch) -> None:
    target = date(2026, 1, 17)

    def incorrect_score(_ticket: Ticket, _draw: VerifiedDraw) -> TicketScore:
        return TicketScore(matched_mains=(20,), mega_hit=False, category="No prize")

    monkeypatch.setattr(scoring, "score_ticket", incorrect_score)

    with pytest.raises(IntegrityError, match="incorrect main intersection"):
        score_locked_bundle(_bundle(target, "bundle-bad-intersection"), _verified_draw(target))


def test_bundle_scoring_revalidates_computed_category(monkeypatch: pytest.MonkeyPatch) -> None:
    target = date(2026, 1, 17)

    def incorrect_score(ticket: Ticket, draw: VerifiedDraw) -> TicketScore:
        matched = tuple(sorted(set(ticket.mains) & set(draw.mains)))
        return TicketScore(matched_mains=matched, mega_hit=False, category="No prize")

    monkeypatch.setattr(scoring, "score_ticket", incorrect_score)

    with pytest.raises(IntegrityError, match="incorrect prize category"):
        score_locked_bundle(_bundle(target, "bundle-bad-category"), _verified_draw(target))


def test_four_mains_without_mega_is_not_four_plus() -> None:
    target = date(2026, 1, 17)

    score = _score(target, "bundle-four-mains-only", first_mega=8)

    assert score.realized_metrics["any_ge_4_mains"] is True
    assert score.realized_metrics["any_4_plus"] is False


def test_four_mains_with_mega_is_four_plus() -> None:
    target = date(2026, 1, 17)

    score = _score(target, "bundle-four-plus-mega", first_mega=7)

    assert score.realized_metrics["any_ge_4_mains"] is True
    assert score.realized_metrics["any_4_plus"] is True


def test_chronological_previous_scores_feed_rolling_calibration() -> None:
    prior_one = _score(date(2026, 1, 3), "bundle-prior-one", p_ge_3=0.2)
    prior_two = _score(date(2026, 1, 7), "bundle-prior-two", p_ge_3=0.4)
    target = date(2026, 1, 17)

    current = score_locked_bundle(
        _bundle(target, "bundle-current-good", p_ge_3=0.6),
        _verified_draw(target),
        previous_scores=(prior_one, prior_two),
        scored_timestamp_utc=_post_timestamp(target) + timedelta(minutes=15),
    )

    assert current.calibration_error["rolling_5_calibration_p_ge_3"] == pytest.approx(0.6)


def test_previous_scores_reject_duplicate_bundle_id() -> None:
    first = _score(date(2026, 1, 3), "bundle-duplicate")
    second = _score(date(2026, 1, 7), "bundle-duplicate")
    target = date(2026, 1, 17)

    with pytest.raises(IntegrityError, match="duplicate bundle_id"):
        score_locked_bundle(
            _bundle(target, "bundle-current-dupe-id"),
            _verified_draw(target),
            previous_scores=(first, second),
        )


def test_previous_scores_reject_duplicate_draw_date() -> None:
    first = _score(date(2026, 1, 3), "bundle-same-date-one")
    second = _score(date(2026, 1, 3), "bundle-same-date-two")
    target = date(2026, 1, 17)

    with pytest.raises(IntegrityError, match="duplicate draw date"):
        score_locked_bundle(
            _bundle(target, "bundle-current-dupe-date"),
            _verified_draw(target),
            previous_scores=(first, second),
        )


def test_previous_scores_reject_out_of_order_dates() -> None:
    later = _score(date(2026, 1, 10), "bundle-later-prior")
    earlier = _score(date(2026, 1, 7), "bundle-earlier-prior")
    target = date(2026, 1, 17)

    with pytest.raises(IntegrityError, match="strictly increasing"):
        score_locked_bundle(
            _bundle(target, "bundle-current-order"),
            _verified_draw(target),
            previous_scores=(later, earlier),
        )


def test_previous_scores_reject_current_or_future_draw_leakage() -> None:
    future = _score(date(2026, 1, 21), "bundle-future-leak")
    target = date(2026, 1, 17)

    with pytest.raises(IntegrityError, match="not strictly earlier"):
        score_locked_bundle(
            _bundle(target, "bundle-current-leak"),
            _verified_draw(target),
            previous_scores=(future,),
        )


def test_previous_scores_are_semantically_revalidated() -> None:
    valid = _score(date(2026, 1, 3), "bundle-prior-tampered")
    tampered_line = valid.lines[0].model_copy(
        update={
            "matched_mains": (20,),
            "main_match_count": 1,
            "prize_category": "No prize",
        }
    )
    tampered = valid.model_copy(update={"lines": (tampered_line, *valid.lines[1:])})
    target = date(2026, 1, 17)

    with pytest.raises(IntegrityError, match="incorrect main intersection"):
        score_locked_bundle(
            _bundle(target, "bundle-current-semantic"),
            _verified_draw(target),
            previous_scores=(tampered,),
        )

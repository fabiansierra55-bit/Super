from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from slp_model.modeling import (
    ComponentParameters,
    ForwardCandidateScore,
    ModelParameters,
    _complete_bundle_outcome,
    _select_with_anchor_rule,
    fit_model,
    select_hyperparameters,
)
from slp_model.models import Draw


def _history(count: int, *, seed: int = 11) -> list[Draw]:
    rng = np.random.default_rng(seed)
    start = date(2023, 1, 1)
    result: list[Draw] = []
    for index in range(count):
        if index >= count - 35:
            weights = np.linspace(0.4, 2.0, 47)
            weights /= weights.sum()
            mains = rng.choice(np.arange(1, 48), size=5, replace=False, p=weights)
            mega_weights = np.linspace(0.4, 2.0, 27)
            mega_weights /= mega_weights.sum()
            mega = int(rng.choice(np.arange(1, 28), p=mega_weights))
        else:
            mains = rng.choice(np.arange(1, 48), size=5, replace=False)
            mega = int(rng.integers(1, 28))
        result.append(
            Draw(
                draw_date=start + timedelta(days=3 * index),
                mains=tuple(int(value) for value in sorted(mains)),
                mega=mega,
            )
        )
    return result


def test_fit_is_normalized_independent_and_cutoff_safe() -> None:
    history = _history(150)
    parameters = ModelParameters(
        mains=ComponentParameters(window=60, sigma=1.125, half_life=16),
        mega=ComponentParameters(window=90, sigma=1.3, half_life=45),
    )
    cutoff = history[129].draw_date
    from_full = fit_model(history, parameters, cutoff_date=cutoff)
    from_prefix = fit_model(history[:130], parameters)

    assert from_full.history_draw_count == 130
    assert from_full.history_cutoff_date == cutoff
    assert from_full.mains_probabilities == pytest.approx(
        from_prefix.mains_probabilities, abs=1e-15
    )
    assert from_full.mega_probabilities == pytest.approx(from_prefix.mega_probabilities, abs=1e-15)
    assert sum(from_full.mains_probabilities) == pytest.approx(1.0)
    assert sum(from_full.mega_probabilities) == pytest.approx(1.0)
    assert all(value > 0 for value in from_full.mains_probabilities)
    assert all(value > 0 for value in from_full.mega_probabilities)


def test_tiers_have_distinct_model_behavior() -> None:
    model = fit_model(
        _history(140),
        ModelParameters(
            ComponentParameters(60, 1.0, 16),
            ComponentParameters(90, 1.15, 36),
        ),
    )
    aggressive_mains, aggressive_mega = model.tier_probabilities("aggressive")
    balanced_mains, balanced_mega = model.tier_probabilities("balanced")
    conservative_mains, conservative_mega = model.tier_probabilities("conservative")

    for distribution in (
        aggressive_mains,
        aggressive_mega,
        balanced_mains,
        balanced_mega,
        conservative_mains,
        conservative_mega,
    ):
        assert distribution.sum() == pytest.approx(1.0)
    assert not np.allclose(aggressive_mains, balanced_mains)
    assert not np.allclose(conservative_mains, balanced_mains)
    assert not np.allclose(aggressive_mega, conservative_mega)


def test_walk_forward_selection_cannot_see_past_cutoff() -> None:
    history = _history(112)
    cutoff = history[105].draw_date
    altered = history[:106] + _history(6, seed=999)
    # Give the altered future unique dates after the cutoff.
    altered = altered[:106] + [
        draw.model_copy(update={"draw_date": history[106 + index].draw_date})
        for index, draw in enumerate(altered[106:])
    ]
    kwargs = dict(
        cutoff_date=cutoff,
        windows=(60, 90),
        main_sigmas=(1.0,),
        mega_sigmas=(1.0,),
        half_lives=(16.0, 36.0),
        validation_draws=4,
        forward_bundle_size=12,
        random_seed=912,
    )
    original = select_hyperparameters(history, **kwargs)
    changed_future = select_hyperparameters(altered, **kwargs)
    repeated = select_hyperparameters(history, **kwargs)

    assert original == repeated
    assert original.model.parameters == changed_future.model.parameters
    assert original.mains_scores == changed_future.mains_scores
    assert original.mega_scores == changed_future.mega_scores
    assert original.joint_forward_bundle_score == changed_future.joint_forward_bundle_score
    assert all(value < cutoff for value in original.fold_training_cutoffs)


def test_complete_ticket_outcome_uses_primary_and_mega_secondary_events() -> None:
    target = Draw(draw_date=date(2026, 7, 15), mains=(1, 2, 3, 4, 5), mega=9)
    outcome = _complete_bundle_outcome(
        (
            (1, 2, 3, 4, 5),
            (1, 2, 3, 6, 7),
            (10, 11, 12, 13, 14),
        ),
        (1, 9, 9),
        target,
    )

    assert outcome.any_three_plus
    assert outcome.any_four_plus
    assert outcome.any_three_plus_mega
    assert not outcome.any_four_plus_mega
    assert outcome.objective == pytest.approx(1.2026)

    # A Mega hit by itself is not the primary bundle objective.
    mega_only = _complete_bundle_outcome(
        ((1, 2, 6, 7, 8),),
        (9,),
        target,
    )
    assert not mega_only.any_three_plus
    assert not mega_only.any_three_plus_mega
    assert mega_only.objective < 0.003


def test_all_candidates_use_same_complete_ticket_forward_folds() -> None:
    result = select_hyperparameters(
        _history(112),
        windows=(60, 90),
        main_sigmas=(1.0,),
        mega_sigmas=(1.0,),
        half_lives=(16.0,),
        validation_draws=30,
        forward_bundle_size=12,
        random_seed=404,
    )

    # The 90-draw candidate needs 90 training draws, leaving 22 common
    # targets.  The 60-draw candidate must not receive eight extra folds.
    assert {score.folds for score in result.mains_scores + result.mega_scores} == {22}
    assert len(result.fold_training_cutoffs) == 22
    # Mega candidates share one prefix-only mains baseline.  Consequently the
    # main-only event rates are identical; Mega affects the full objective only
    # through its ticket pairing and Mega-bearing secondary events.
    assert len({score.primary_hit_rate for score in result.mega_scores}) == 1
    assert len({score.secondary_hit_rate for score in result.mega_scores}) == 1


def test_likelihood_is_a_gate_but_never_a_ranking_tiebreaker() -> None:
    better_forward = ForwardCandidateScore(
        component="mains",
        parameters=ComponentParameters(60, 1.0, 20.0),
        forward_bundle_score=0.4,
        primary_hit_rate=0.3,
        secondary_hit_rate=0.1,
        heldout_log_likelihood=-100.0,
        stable=True,
        folds=12,
    )
    better_likelihood = ForwardCandidateScore(
        component="mains",
        parameters=ComponentParameters(90, 1.3, 60.0),
        forward_bundle_score=0.2,
        primary_hit_rate=0.2,
        secondary_hit_rate=0.0,
        heldout_log_likelihood=-1.0,
        stable=True,
        folds=12,
    )

    assert (
        _select_with_anchor_rule(
            (better_forward, better_likelihood),
            anchor_min_improvement=0.01,
        )
        == better_forward
    )
    assert (
        _select_with_anchor_rule(
            (replace(better_forward, stable=False), better_likelihood),
            anchor_min_improvement=0.01,
        )
        == better_likelihood
    )


def test_duplicate_draw_dates_are_rejected() -> None:
    history = _history(70)
    with pytest.raises(ValueError, match="duplicate draw dates"):
        fit_model(
            history + [history[-1]],
            ModelParameters(
                ComponentParameters(60, 1.0, 20),
                ComponentParameters(60, 1.0, 20),
            ),
        )

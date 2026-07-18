from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

import slp_model.generation as generation
from slp_model.modeling import ComponentParameters, ModelParameters, fit_model
from slp_model.models import Draw
from slp_model.simulation import (
    BundleSimulationMetrics,
    estimate_bundle_metrics,
    generate_candidate_pool,
    simulate_future_draws,
)


def _model():
    rng = np.random.default_rng(81)
    start = date(2022, 1, 1)
    history = [
        Draw(
            draw_date=start + timedelta(days=3 * index),
            mains=tuple(
                int(value) for value in sorted(rng.choice(np.arange(1, 48), size=5, replace=False))
            ),
            mega=int(rng.integers(1, 28)),
        )
        for index in range(130)
    ]
    return fit_model(
        history,
        ModelParameters(
            ComponentParameters(90, 1.15, 24),
            ComponentParameters(60, 1.0, 20),
        ),
    )


def test_production_candidate_minimum_is_guarded() -> None:
    with pytest.raises(ValueError, match="50,000"):
        generate_candidate_pool(_model(), size=49999, seed=1)


def test_candidate_pool_is_unique_valid_and_deterministic() -> None:
    model = _model()
    previous = (1, 2, 3, 4, 5)
    first = generate_candidate_pool(
        model,
        size=900,
        seed=1827,
        previous_draw=previous,
        enforce_production_minimum=False,
    )
    second = generate_candidate_pool(
        model,
        size=900,
        seed=1827,
        previous_draw=previous,
        enforce_production_minimum=False,
    )

    assert [candidate.signature for candidate in first] == [
        candidate.signature for candidate in second
    ]
    assert len({candidate.signature for candidate in first}) == 900
    assert first.tier_counts == {
        "aggressive": 300,
        "balanced": 300,
        "conservative": 300,
    }
    assert all(
        len(set(candidate.ticket.mains) & set(previous)) <= 1
        for candidate in first
        if candidate.tier == "aggressive"
    )
    assert all(len(set(candidate.ticket.mains)) == 5 for candidate in first)


def test_future_draws_and_adaptive_metrics_are_reproducible() -> None:
    model = _model()
    draws_a = simulate_future_draws(model, count=500, seed=44)
    draws_b = simulate_future_draws(model, count=500, seed=44)
    assert np.array_equal(draws_a.mains, draws_b.mains)
    assert np.array_equal(draws_a.mega, draws_b.mega)
    assert all(len(set(int(value) for value in row)) == 5 for row in draws_a.mains)

    pool = generate_candidate_pool(model, size=300, seed=45, enforce_production_minimum=False)
    tickets = [candidate.ticket for candidate in pool[:30]]
    metrics_a = estimate_bundle_metrics(
        tickets,
        model,
        seed=46,
        min_simulations=1_000,
        max_simulations=4_000,
        batch_size=500,
        confidence_tolerance=0.10,
    )
    metrics_b = estimate_bundle_metrics(
        tickets,
        model,
        seed=46,
        min_simulations=1_000,
        max_simulations=4_000,
        batch_size=500,
        confidence_tolerance=0.10,
    )
    assert metrics_a == metrics_b
    assert metrics_a.stable
    assert metrics_a.simulation_count == 1_000
    assert sum(metrics_a.best_match_histogram) == metrics_a.simulation_count
    assert 0 <= metrics_a.p_ge_4 <= metrics_a.p_ge_3 <= metrics_a.p_ge_2 <= 1


def test_locked_four_plus_metric_means_four_plus_mega() -> None:
    metrics = BundleSimulationMetrics(
        simulation_count=10_000,
        stable=True,
        confidence_level=0.95,
        stable_batches=2,
        confidence_tolerance=0.01,
        primary_confidence_half_width=0.005,
        secondary_confidence_half_width=0.004,
        mega_confidence_half_width=0.003,
        p_ge_2=0.5,
        p_ge_3=0.2,
        p_ge_4=0.08,
        p_3_plus_mega=0.02,
        p_4_plus_mega=0.003,
        mean_best_main_matches=1.7,
        population_std_best_main_matches=0.8,
        sample_std_best_main_matches=0.81,
        best_match_histogram=(100, 1_000, 6_900, 1_500, 450, 50),
    )

    summary = generation._simulation_summary(metrics, candidate_pool_size=50_000)
    assert summary.p_any_ge_4_mains == 0.08
    assert summary.p_any_4_plus == 0.003
    assert summary.p_any_4_plus_mega == 0.003

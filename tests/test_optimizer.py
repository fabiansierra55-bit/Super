from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from itertools import combinations

import numpy as np
import pytest

from slp_model.constraints import validate_bundle
from slp_model.modeling import ComponentParameters, ModelParameters, fit_model
from slp_model.models import Draw, Ticket
from slp_model.optimizer import (
    ObjectiveWeights,
    OptimizationError,
    measure_bundle_marginals,
    optimize_bundle,
)
from slp_model.simulation import Candidate, generate_candidate_pool


def _model():
    rng = np.random.default_rng(812)
    start = date(2021, 1, 1)
    history = [
        Draw(
            draw_date=start + timedelta(days=3 * index),
            mains=tuple(
                int(value) for value in sorted(rng.choice(np.arange(1, 48), size=5, replace=False))
            ),
            mega=int(rng.integers(1, 28)),
        )
        for index in range(150)
    ]
    return fit_model(
        history,
        ModelParameters(
            ComponentParameters(90, 1.125, 24),
            ComponentParameters(120, 1.0, 28),
        ),
    )


def test_global_optimizer_enforces_all_bundle_constraints() -> None:
    model = _model()
    previous = (1, 2, 3, 4, 5)
    pool = generate_candidate_pool(
        model,
        size=4_500,
        seed=3001,
        previous_draw=previous,
        enforce_production_minimum=False,
    )
    result = optimize_bundle(
        pool,
        model,
        seed=3002,
        previous_draw=previous,
        optimization_simulations=384,
        estimate_final_metrics=False,
    )

    assert len(result.tickets) == 30
    assert result.tier_counts == {
        "aggressive": 10,
        "balanced": 10,
        "conservative": 10,
    }
    validate_bundle(list(result.tickets))
    assert max(Counter(ticket.mega for ticket in result.tickets).values()) <= 5
    assert all(
        len(set(candidate.ticket.mains) & set(previous)) <= 1
        for candidate in result.candidates
        if candidate.tier == "aggressive"
    )
    assert len(result.marginal_contributions) == 30
    assert any(item.primary_new_coverage > 0 for item in result.marginal_contributions)
    pair_counts = Counter(
        pair for ticket in result.tickets for pair in combinations(ticket.mains, 2)
    )
    triple_counts = Counter(
        triple for ticket in result.tickets for triple in combinations(ticket.mains, 3)
    )
    assert max(pair_counts.values()) <= 2
    assert max(triple_counts.values()) <= 1


def test_optimizer_is_deterministic() -> None:
    model = _model()
    pool = generate_candidate_pool(model, size=2_400, seed=8, enforce_production_minimum=False)
    kwargs = dict(
        seed=9,
        optimization_simulations=256,
        estimate_final_metrics=False,
    )
    first = optimize_bundle(pool, model, **kwargs)
    second = optimize_bundle(pool, model, **kwargs)
    assert [candidate.signature for candidate in first.candidates] == [
        candidate.signature for candidate in second.candidates
    ]
    assert first.marginal_contributions == second.marginal_contributions


def test_infeasible_duplicate_main_pool_stops_safely() -> None:
    model = _model()
    candidates = tuple(
        Candidate(
            ticket=Ticket(mains=(1, 2, 3, 4, 5), mega=index % 27 + 1),
            tier=("aggressive", "balanced", "conservative")[index % 3],
            generation_index=index,
            sampling_log_weight=0.0,
        )
        for index in range(30)
    )
    with pytest.raises(OptimizationError, match="no eligible"):
        optimize_bundle(
            candidates,
            model,
            seed=10,
            optimization_simulations=64,
            estimate_final_metrics=False,
        )


def test_final_line_marginals_are_reproducible_and_penalize_mega_repeats() -> None:
    model = _model()
    pool = generate_candidate_pool(model, size=90, seed=91, enforce_production_minimum=False)
    tickets = tuple(Ticket(mains=candidate.ticket.mains, mega=1) for candidate in pool[:6])
    tiers = ("balanced",) * len(tickets)
    weights = ObjectiveWeights(
        p_ge_3=0,
        p_ge_4=0,
        three_plus_mega=0,
        four_plus_mega=0,
        anti_cannibalization=0,
        mega_repeat_penalty=1,
    )
    first = measure_bundle_marginals(
        tickets,
        tiers,
        model,
        seed=92,
        simulations=512,
        weights=weights,
        mega_soft_cap=1,
        mega_hard_cap=5,
    )
    second = measure_bundle_marginals(
        tickets,
        tiers,
        model,
        seed=92,
        simulations=512,
        weights=weights,
        mega_soft_cap=1,
        mega_hard_cap=5,
    )

    assert first == second
    assert len(first) == len(tickets)
    assert first[0].anti_cannibalization_penalty == 0
    assert all(item.anti_cannibalization_penalty > 0 for item in first[1:])
    assert all(item.weighted_gain < 0 for item in first[1:])


def test_objective_weight_knobs_are_validated() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ObjectiveWeights(aggressive_secondary_multiplier=0.99)
    with pytest.raises(ValueError, match="cannot be negative"):
        ObjectiveWeights(mega_repeat_penalty=-0.01)

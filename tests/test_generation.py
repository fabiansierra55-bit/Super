from __future__ import annotations

import pytest

from slp_model.fair_odds import exact_uniform_metrics
from slp_model.generation import (
    ADAPTIVE_OPTIMIZER_ALGORITHM_VERSION,
    FAIR_OPTIMIZER_ALGORITHM_VERSION,
    _assert_correction_non_regression,
)
from slp_model.models import Ticket


def test_correction_gate_rejects_exact_coverage_regression() -> None:
    incumbent = exact_uniform_metrics(
        [
            Ticket(mains=(1, 2, 3, 4, 5), mega=1),
            Ticket(mains=(6, 7, 8, 9, 10), mega=2),
        ]
    )
    worse = exact_uniform_metrics([Ticket(mains=(1, 2, 3, 4, 5), mega=1)])

    with pytest.raises(ValueError, match="regresses active incumbent"):
        _assert_correction_non_regression(worse, incumbent)
    _assert_correction_non_regression(incumbent, incumbent)


def test_optimizer_algorithm_versions_identify_the_sixty_line_upgrade() -> None:
    assert FAIR_OPTIMIZER_ALGORITHM_VERSION.endswith("lns-exchange-v5")
    assert ADAPTIVE_OPTIMIZER_ALGORITHM_VERSION.startswith("simulation-greedy-submodular-v5")

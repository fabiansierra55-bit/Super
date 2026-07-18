from __future__ import annotations

import pytest

from slp_model.fair_odds import exact_uniform_metrics
from slp_model.generation import _assert_correction_non_regression
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

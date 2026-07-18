from __future__ import annotations

import pytest

from slp_model.objectives import effective_event_weights


def test_grind_weights_are_used_without_transformation() -> None:
    weights = (1.0, 0.15, 0.10, 0.10)
    assert effective_event_weights("grind", weights) == weights


def test_spike_weights_match_the_production_transformation() -> None:
    assert effective_event_weights("spike", (1.0, 0.15, 0.10, 0.10)) == (
        0.35,
        1.0,
        0.25,
        0.50,
    )


def test_event_weights_reject_negative_coefficients() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        effective_event_weights("grind", (1.0, -1.0, 1.0, 1.0))

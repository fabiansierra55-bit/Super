"""Shared event-weight semantics for training, optimization, and reporting."""

from __future__ import annotations

from typing import Literal

ObjectiveMode = Literal["grind", "spike"]


def effective_event_weights(
    mode: ObjectiveMode,
    weights: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Return the coefficients actually used by the configured objective mode."""

    if mode not in ("grind", "spike"):
        raise ValueError("objective mode must be grind or spike")
    if any(value < 0 for value in weights):
        raise ValueError("event weights must be nonnegative")
    if mode == "grind":
        return weights
    p_ge_3, p_ge_4, three_plus_mega, four_plus_mega = weights
    return (
        p_ge_3 * 0.35,
        max(p_ge_4, 1.0),
        max(three_plus_mega, 0.25),
        max(four_plus_mega, 0.50),
    )


__all__ = ["ObjectiveMode", "effective_event_weights"]

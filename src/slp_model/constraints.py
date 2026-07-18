"""Game-rule and bundle-diversity validation used before every lock."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from .exceptions import ConstraintError
from .models import LockedLine, Ticket


@dataclass(frozen=True)
class BundleConstraintReport:
    ticket_count: int
    maximum_pairwise_overlap: int
    minimum_hamming_distance: int
    maximum_pair_repetition: int
    maximum_triple_repetition: int
    maximum_mega_repetition: int
    mega_counts: dict[int, int]


def overlap(a: Ticket, b: Ticket) -> int:
    """Return the count of shared main numbers (Mega is intentionally separate)."""

    return len(set(a.mains) & set(b.mains))


def hamming_distance(a: Ticket, b: Ticket) -> int:
    """Count main-number replacements needed to transform one 5-set into another."""

    return 5 - overlap(a, b)


def validate_ticket(ticket: Ticket) -> None:
    """Reassert the immutable SuperLotto Plus game rules at a trust boundary."""

    if len(ticket.mains) != 5 or len(set(ticket.mains)) != 5:
        raise ConstraintError("ticket must contain five unique main numbers")
    if tuple(sorted(ticket.mains)) != ticket.mains:
        raise ConstraintError("ticket main numbers must be normalized in ascending order")
    if any(number < 1 or number > 47 for number in ticket.mains):
        raise ConstraintError("main number outside 1-47")
    if not 1 <= ticket.mega <= 27:
        raise ConstraintError("Mega number outside 1-27")


def _counter_max(counter: Counter[Any]) -> int:
    return max(counter.values(), default=0)


def validate_bundle(
    tickets: Iterable[Ticket],
    *,
    max_overlap: int = 3,
    min_hamming: int = 2,
    pair_cap: int = 2,
    triple_cap: int = 1,
    mega_hard_cap: int = 5,
    expected_size: int | None = None,
    previous_draw_mains: tuple[int, int, int, int, int] | None = None,
    aggressive_previous_overlap_cap: int = 1,
) -> BundleConstraintReport:
    """Validate global constraints and return measurements for the audit trail."""

    materialized = list(tickets)
    if expected_size is not None and len(materialized) != expected_size:
        raise ConstraintError(
            f"bundle contains {len(materialized)} tickets; expected {expected_size}"
        )
    for ticket in materialized:
        validate_ticket(ticket)

    if len({(ticket.mains, ticket.mega) for ticket in materialized}) != len(materialized):
        raise ConstraintError("duplicate full tickets found")
    if len({ticket.mains for ticket in materialized}) != len(materialized):
        raise ConstraintError("duplicate main-number sets found")

    maximum_overlap = 0
    minimum_hamming = 5 if len(materialized) < 2 else 6
    for index, ticket in enumerate(materialized):
        for other in materialized[index + 1 :]:
            measured_overlap = overlap(ticket, other)
            measured_hamming = hamming_distance(ticket, other)
            maximum_overlap = max(maximum_overlap, measured_overlap)
            minimum_hamming = min(minimum_hamming, measured_hamming)
            if measured_overlap > max_overlap:
                raise ConstraintError(
                    f"pairwise main overlap {measured_overlap} exceeds cap {max_overlap}"
                )
            if measured_hamming < min_hamming:
                raise ConstraintError(
                    f"Hamming distance {measured_hamming} is below minimum {min_hamming}"
                )

    pair_counts: Counter[tuple[int, int]] = Counter()
    triple_counts: Counter[tuple[int, int, int]] = Counter()
    mega_counts: Counter[int] = Counter()
    for ticket in materialized:
        pair_counts.update(combinations(ticket.mains, 2))
        triple_counts.update(combinations(ticket.mains, 3))
        mega_counts[ticket.mega] += 1

    worst_pair = pair_counts.most_common(1)
    if worst_pair and worst_pair[0][1] > pair_cap:
        raise ConstraintError(
            f"two-number pair repetition exceeds cap {pair_cap}: "
            f"{worst_pair[0][0]} appears {worst_pair[0][1]} times"
        )
    worst_triple = triple_counts.most_common(1)
    if worst_triple and worst_triple[0][1] > triple_cap:
        raise ConstraintError(
            f"three-number triple repetition exceeds cap {triple_cap}: "
            f"{worst_triple[0][0]} appears {worst_triple[0][1]} times"
        )
    worst_mega = mega_counts.most_common(1)
    if worst_mega and worst_mega[0][1] > mega_hard_cap:
        raise ConstraintError(
            f"Mega repetition exceeds hard cap {mega_hard_cap}: "
            f"{worst_mega[0][0]} appears {worst_mega[0][1]} times"
        )

    if previous_draw_mains is not None:
        previous = set(previous_draw_mains)
        for ticket in materialized:
            if isinstance(ticket, LockedLine) and ticket.strategy == "aggressive":
                previous_overlap = len(previous & set(ticket.mains))
                if previous_overlap > aggressive_previous_overlap_cap:
                    raise ConstraintError(
                        "aggressive ticket overlaps the previous official draw by "
                        f"{previous_overlap}; cap is {aggressive_previous_overlap_cap}"
                    )

    return BundleConstraintReport(
        ticket_count=len(materialized),
        maximum_pairwise_overlap=maximum_overlap,
        minimum_hamming_distance=minimum_hamming if len(materialized) >= 2 else 5,
        maximum_pair_repetition=_counter_max(pair_counts),
        maximum_triple_repetition=_counter_max(triple_counts),
        maximum_mega_repetition=_counter_max(mega_counts),
        mega_counts=dict(sorted(mega_counts.items())),
    )

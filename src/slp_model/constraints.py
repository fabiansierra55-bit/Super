from __future__ import annotations

from collections import Counter
from itertools import combinations

from .models import Ticket


def overlap(a: Ticket, b: Ticket) -> int:
    return len(set(a.mains) & set(b.mains))


def validate_bundle(
    tickets: list[Ticket],
    *,
    max_overlap: int = 3,
    pair_cap: int = 2,
    triple_cap: int = 1,
) -> None:
    if len({(ticket.mains, ticket.mega) for ticket in tickets}) != len(tickets):
        raise ValueError("duplicate full tickets found")
    if len({ticket.mains for ticket in tickets}) != len(tickets):
        raise ValueError("duplicate main-number sets found")

    for index, ticket in enumerate(tickets):
        for other in tickets[index + 1 :]:
            if overlap(ticket, other) > max_overlap:
                raise ValueError("pairwise overlap exceeds cap")

    pair_counts: Counter[tuple[int, int]] = Counter()
    triple_counts: Counter[tuple[int, int, int]] = Counter()
    for ticket in tickets:
        pair_counts.update(combinations(ticket.mains, 2))
        triple_counts.update(combinations(ticket.mains, 3))

    if pair_counts and max(pair_counts.values()) > pair_cap:
        raise ValueError("two-number pair repetition exceeds cap")
    if triple_counts and max(triple_counts.values()) > triple_cap:
        raise ValueError("three-number triple repetition exceeds cap")

from __future__ import annotations

from dataclasses import dataclass

from .models import Draw, Ticket


@dataclass(frozen=True)
class TicketScore:
    matched_mains: tuple[int, ...]
    mega_hit: bool
    category: str

    @property
    def main_matches(self) -> int:
        return len(self.matched_mains)


def category(main_matches: int, mega_hit: bool) -> str:
    if main_matches == 5:
        return "5+Mega" if mega_hit else "5"
    if main_matches == 4:
        return "4+Mega" if mega_hit else "4"
    if main_matches == 3:
        return "3+Mega" if mega_hit else "3"
    if main_matches == 2 and mega_hit:
        return "2+Mega"
    if main_matches == 1 and mega_hit:
        return "1+Mega"
    if main_matches == 0 and mega_hit:
        return "Mega-only"
    return str(main_matches)


def score_ticket(ticket: Ticket, draw: Draw) -> TicketScore:
    matched = tuple(sorted(set(ticket.mains) & set(draw.mains)))
    mega_hit = ticket.mega == draw.mega
    return TicketScore(matched, mega_hit, category(len(matched), mega_hit))

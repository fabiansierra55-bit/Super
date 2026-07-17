from datetime import date

from slp_model.models import Draw, Ticket
from slp_model.scoring import score_ticket


def test_three_plus_mega():
    draw = Draw(draw_date=date(2026, 1, 1), mains=(1, 2, 3, 4, 5), mega=7)
    ticket = Ticket(mains=(1, 2, 3, 20, 30), mega=7)
    score = score_ticket(ticket, draw)
    assert score.matched_mains == (1, 2, 3)
    assert score.mega_hit is True
    assert score.category == "3+Mega"

import pytest

from slp_model.constraints import validate_bundle
from slp_model.models import Ticket


def test_pair_cap_rejects_repeated_pair():
    tickets = [
        Ticket(mains=(1, 2, 3, 4, 5), mega=1),
        Ticket(mains=(1, 2, 6, 7, 8), mega=2),
        Ticket(mains=(1, 2, 9, 10, 11), mega=3),
    ]
    with pytest.raises(ValueError, match="pair repetition"):
        validate_bundle(tickets, pair_cap=2)

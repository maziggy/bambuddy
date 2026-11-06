"""parse_id_list: the id lists the bulk routes take."""

import pytest
from fastapi import HTTPException

from backend.app.utils.id_list import MAX_IDS, parse_id_list


def test_parses_in_order_without_duplicates():
    assert parse_id_list("3,1,3, 2 ,") == [3, 1, 2]


def test_empty_is_no_ids():
    assert parse_id_list("") == []


@pytest.mark.parametrize("value", ["abc", "1,x", "-1", "0", "1.5", "²", "١٢", "+3"])
def test_rejects_anything_but_positive_ascii_integers(value):
    with pytest.raises(HTTPException) as caught:
        parse_id_list(value)
    assert caught.value.status_code == 422


def test_caps_the_list():
    assert len(parse_id_list(",".join(str(i) for i in range(1, MAX_IDS + 1)))) == MAX_IDS
    with pytest.raises(HTTPException) as caught:
        parse_id_list(",".join(str(i) for i in range(1, MAX_IDS + 2)))
    assert caught.value.status_code == 422

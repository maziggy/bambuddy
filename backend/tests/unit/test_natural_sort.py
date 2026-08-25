"""Storage locations sort naturally, not lexicographically (issue: "Drybox 2"
belongs before "Drybox 10")."""

from backend.app.utils.natural_sort import natural_sort_key


def test_orders_embedded_numbers_by_value_not_by_character():
    names = ["Drybox 10", "Drybox 2", "Drybox 1"]
    assert sorted(names, key=natural_sort_key) == ["Drybox 1", "Drybox 2", "Drybox 10"]


def test_matches_plain_alphabetical_order_when_there_are_no_digits():
    names = ["Shelf B", "Shelf A", "Shelf C"]
    assert sorted(names, key=natural_sort_key) == ["Shelf A", "Shelf B", "Shelf C"]


def test_is_case_insensitive():
    names = ["shelf", "Drybox", "SHELF A"]
    assert sorted(names, key=natural_sort_key) == ["Drybox", "shelf", "SHELF A"]


def test_names_with_no_digits_sort_before_the_same_prefix_with_a_number():
    # "Drybox" (len-1 key) is a prefix of "Drybox 1" (len-3 key); Python
    # tuple comparison puts the shorter, exhausted tuple first.
    names = ["Drybox 1", "Drybox"]
    assert sorted(names, key=natural_sort_key) == ["Drybox", "Drybox 1"]


def test_handles_multiple_number_runs_in_one_name():
    names = ["Row 10 Bin 2", "Row 2 Bin 10", "Row 2 Bin 2"]
    assert sorted(names, key=natural_sort_key) == ["Row 2 Bin 2", "Row 2 Bin 10", "Row 10 Bin 2"]


def test_does_not_raise_when_a_str_and_int_position_would_otherwise_collide():
    # A regression guard for the tuple-comparison hazard described in the
    # module docstring: mixing names where a digit run appears at different
    # positions must not raise "'<' not supported between instances of 'int'
    # and 'str'" — every key's even indices are always str and odd indices
    # are always int, so this must simply sort without error.
    names = ["A1", "1A", "AA", "11"]
    result = sorted(names, key=natural_sort_key)
    assert set(result) == set(names)

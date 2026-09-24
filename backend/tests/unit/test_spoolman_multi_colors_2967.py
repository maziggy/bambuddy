"""Spoolman spools carry their gradient stops into the app (#2967).

An AMS slot card paints the colour of the spool bound to it. Telemetry cannot
supply that for anything with more than one colour -- a tray record carries a
single ``tray_color`` hex -- so the card reads the spool's own
``extra_colors``. Internal inventory has stored that for releases;
``_map_spoolman_spool`` never returned it, so the identical roll registered in
Spoolman rendered as one flat band.

Spoolman does hold the data, in ``filament.multi_color_hexes``. What it has no
field for is a surface *effect*: its only neighbouring field is
``multi_color_direction``, which describes how the stops are laid out, not that
the filament is silk or glitter. So ``effect_type`` is pinned to None for a
Spoolman spool rather than guessed at, and these tests pin that asymmetry so it
reads as a decision rather than an omission.
"""

import pytest

from backend.app.api.routes._spoolman_helpers import (
    _map_spoolman_spool,
    parse_spoolman_multi_colors,
)

pytestmark = pytest.mark.unit


def _spool(**filament) -> dict:
    return {"id": 7, "filament": {"name": "PLA Matte", "material": "PLA", **filament}}


class TestTheParser:
    def test_reads_the_comma_separated_string_form(self):
        assert parse_spoolman_multi_colors({"multi_color_hexes": "FFFF00,00FFFF,FFB6C1"}) == [
            "FFFF00",
            "00FFFF",
            "FFB6C1",
        ]

    def test_reads_the_list_form(self):
        # Spoolman writes the field as a list in some releases.
        assert parse_spoolman_multi_colors({"multi_color_hexes": ["AABBCC", "DDEEFF"]}) == [
            "AABBCC",
            "DDEEFF",
        ]

    def test_strips_hashes_and_whitespace(self):
        # `Spool.extra_colors` and the client's `parseStops` both want bare hex.
        assert parse_spoolman_multi_colors({"multi_color_hexes": " #FFFF00 , #00FFFF "}) == [
            "FFFF00",
            "00FFFF",
        ]

    def test_keeps_the_case_it_was_given(self):
        # CSS does not care, and rewriting it would make a stored value differ
        # from the one Spoolman shows next to it.
        assert parse_spoolman_multi_colors({"multi_color_hexes": "ffb6c1"}) == ["ffb6c1"]

    def test_drops_empty_tokens_rather_than_emitting_blanks(self):
        assert parse_spoolman_multi_colors({"multi_color_hexes": "FFFF00,,  ,00FFFF"}) == [
            "FFFF00",
            "00FFFF",
        ]

    @pytest.mark.parametrize("value", [None, "", "   ", 42, {}, [], [" ", ""]])
    def test_anything_unusable_reads_as_no_stops(self, value):
        assert parse_spoolman_multi_colors({"multi_color_hexes": value}) == []

    def test_a_filament_without_the_field_reads_as_no_stops(self):
        assert parse_spoolman_multi_colors({}) == []


class TestTheMappedSpool:
    def test_multi_colour_stops_reach_extra_colors(self):
        mapped = _map_spoolman_spool(_spool(multi_color_hexes="FFFF00,00FFFF,FFB6C1"))
        assert mapped["extra_colors"] == "FFFF00,00FFFF,FFB6C1"

    def test_a_single_colour_spool_has_no_extra_colors(self):
        # None rather than "": the client tests the field for truthiness before
        # it goes anywhere near the gradient builder.
        assert _map_spoolman_spool(_spool(color_hex="FFB6C1"))["extra_colors"] is None

    def test_effect_type_is_none_because_spoolman_has_no_such_field(self):
        mapped = _map_spoolman_spool(_spool(multi_color_hexes="FFFF00,00FFFF"))
        assert mapped["effect_type"] is None

    def test_multi_color_direction_is_not_mistaken_for_an_effect(self):
        # It describes the layout of the stops, not the surface of the roll.
        mapped = _map_spoolman_spool(_spool(multi_color_hexes="FFFF00,00FFFF", multi_color_direction="longitudinal"))
        assert mapped["effect_type"] is None

    def test_the_base_colour_still_maps_alongside_the_stops(self):
        mapped = _map_spoolman_spool(_spool(color_hex="FFB6C1", multi_color_hexes="FFFF00,00FFFF"))
        assert mapped["rgba"] == "FFB6C1FF"
        assert mapped["extra_colors"] == "FFFF00,00FFFF"

    def test_both_keys_are_always_present_so_the_shape_never_varies(self):
        # The frontend InventorySpool type declares them non-optional; a spool
        # that omitted them would read as `undefined` rather than `null`.
        mapped = _map_spoolman_spool(_spool())
        assert "extra_colors" in mapped
        assert "effect_type" in mapped

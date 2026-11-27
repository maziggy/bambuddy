"""Subtype and colour from a Spoolman filament's name (#3561).

Spoolman has neither field, only the filament's name. Bambuddy writes that as
"<material> <subtype>", and that was all it could read back: a filament named
after the product -- "Matte Scarlet Red" -- got the whole name as its subtype
and no colour, so the spool form showed the colour blank.
"""

import pytest

from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool, split_spoolman_filament_name


@pytest.mark.parametrize(
    ("material", "name", "subtype", "colour"),
    [
        # The three shapes from the report.
        ("PLA", "Matte Scarlet Red", "Matte", "Scarlet Red"),
        ("PLA", "Silk+ Baby Blue", "Silk+", "Baby Blue"),
        ("PLA", "Gold - Basic PLA", "Basic", "Gold"),
        # Punctuation attached to a word is a separator too.
        ("PLA", "Scarlet Red (Matte)", "Matte", "Scarlet Red"),
        ("PLA", "Matte, Scarlet Red", "Matte", "Scarlet Red"),
        # The line in the material, the colour in the name.
        ("PLA Matte", "PLA Matte Grey", None, "Grey"),
        # A material and a finish at the front.
        ("PLA", "PLA Basic Red", "Basic", "Red"),
        ("PETG", "PETG HF Black", "HF", "Black"),
        ("PLA", "PLA Dual Color Red Blue", "Dual Color", "Red Blue"),
        # Only a colour.
        ("PLA", "Scarlet Red", None, "Scarlet Red"),
        ("", "Jade White", None, "Jade White"),
        ("PLA", "PLA", None, None),
        ("PLA", "", None, None),
    ],
)
def test_the_name_splits_into_subtype_and_colour(material, name, subtype, colour):
    assert split_spoolman_filament_name(material, name) == (subtype, colour)


@pytest.mark.parametrize(
    ("material", "name", "subtype"),
    [
        ("PLA", "PLA Matte", "Matte"),
        ("PLA Matte", "PLA Matte Matte", "Matte"),
        # A subtype that is no finish still comes back as the subtype: the name
        # starts with the material, which is how Bambuddy writes it.
        ("PLA", "PLA Engineering", "Engineering"),
        ("PLA", "PLA Jade White", "Jade White"),
    ],
)
def test_a_name_bambuddy_wrote_reads_back_as_it_was_written(material, name, subtype):
    assert split_spoolman_filament_name(material, name) == (subtype, None)


def test_the_material_is_only_taken_as_a_whole_word():
    """PLA-CF is not PLA followed by "-CF"."""
    assert split_spoolman_filament_name("PLA", "Black PLA-CF") == (None, "Black PLA-CF")


def _spool(name, material="PLA", extra=None):
    return {
        "id": 5,
        "filament": {"id": 2, "name": name, "material": material, "color_hex": "C8102E"},
        "extra": extra or {},
    }


class TestTheMappedSpool:
    def test_the_reported_case(self):
        mapped = _map_spoolman_spool(_spool("Matte Scarlet Red"))

        assert mapped["material"] == "PLA"
        assert mapped["subtype"] == "Matte"
        assert mapped["color_name"] == "Scarlet Red"
        # Named by the user, so the edit form shows it rather than leaving it
        # blank as it does for a colour made up from the subtype.
        assert mapped["color_name_is_synthesized"] is False

    def test_a_colour_bambuddy_stored_still_wins(self):
        mapped = _map_spoolman_spool(_spool("Matte Scarlet Red", extra={"bambu_color_name": '"Crimson"'}))

        assert mapped["color_name"] == "Crimson"
        assert mapped["subtype"] == "Matte"

    def test_a_name_without_a_colour_still_synthesises_one(self):
        mapped = _map_spoolman_spool(_spool("PLA Matte"))

        assert mapped["subtype"] == "Matte"
        assert mapped["color_name"] == "Matte"
        assert mapped["color_name_is_synthesized"] is True

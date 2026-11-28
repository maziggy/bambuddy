"""The filament variables and the line a notification shows for a filament (#3641)."""

import pytest

from backend.app.services.notification_content import NotificationFilament, embed_color, filament_variables

PURPLE = "\U0001f7e3"


class TestLine:
    def test_dot_type_name_and_hex(self):
        f = NotificationFilament("PLA", "#B39DDB", "Lavender Purple")
        assert f.line == f"{PURPLE} PLA Lavender Purple (#B39DDB)"

    def test_hex_without_hash_or_alpha_is_the_same_filament(self):
        assert NotificationFilament("PLA", "B39DDBFF", "Lavender Purple").line == (
            f"{PURPLE} PLA Lavender Purple (#B39DDB)"
        )

    def test_unknown_colour_has_no_dot_and_no_hex(self):
        assert NotificationFilament("PLA", "Earth Brown").line == "PLA"
        assert NotificationFilament("PLA").line == "PLA"
        assert NotificationFilament().line == ""

    def test_clear_is_a_white_dot_with_the_word_and_no_hex(self):
        assert NotificationFilament("PETG", "00000000").line == "⚪ PETG clear"
        assert NotificationFilament("PETG", "00000000", "Clear").line == "⚪ PETG Clear"

    def test_translucent_is_said_and_keeps_its_rgb(self):
        assert NotificationFilament("PETG", "FF000080").line == "\U0001f534 PETG translucent (#FF0000)"

    def test_several_colours_are_a_rainbow(self):
        f = NotificationFilament("PLA", "FF0000FF", "Rainbow", extra_colors="00ff00,0000ff")
        assert f.line == "\U0001f308 PLA Rainbow (#FF0000)"


class TestVariables:
    def test_nothing_is_empty_strings(self):
        assert filament_variables([]) == {
            "filament_type": "",
            "color_name": "",
            "color_hex": "",
            "color_emoji": "",
            "slot": "",
            "filament": "",
        }

    def test_one_filament(self):
        v = filament_variables([NotificationFilament("PLA", "#B39DDB", "Lavender Purple", "A1")])
        assert v == {
            "filament_type": "PLA",
            "color_name": "Lavender Purple",
            "color_hex": "#B39DDB",
            "color_emoji": PURPLE,
            "slot": "A1",
            "filament": f"{PURPLE} PLA Lavender Purple (#B39DDB)",
        }

    def test_several_join_in_order_and_skip_what_is_missing(self):
        v = filament_variables(
            [NotificationFilament("PLA", "#B39DDB", "Lavender Purple"), NotificationFilament("PETG", None, None)]
        )
        assert v["filament_type"] == "PLA, PETG"
        assert v["color_name"] == "Lavender Purple"
        assert v["color_hex"] == "#B39DDB"
        assert v["filament"] == f"{PURPLE} PLA Lavender Purple (#B39DDB); PETG"


class TestEmbedColor:
    def test_one_readable_colour(self):
        assert embed_color([NotificationFilament("PLA", "#B39DDB")]) == 0xB39DDB

    @pytest.mark.parametrize("hex_value", [None, "", "Earth Brown", "00000000", "#FFFFFF", "#F5F5F5"])
    def test_unreadable_clear_or_near_white_keeps_the_green(self, hex_value):
        assert embed_color([NotificationFilament("PLA", hex_value)]) is None

    def test_one_multicolour_spool_keeps_the_green(self):
        assert embed_color([NotificationFilament("PLA", "#FF0000", extra_colors="00FF00,0000FF")]) is None

    def test_none_or_several_keep_the_green(self):
        assert embed_color([]) is None
        assert embed_color([NotificationFilament("PLA", "#FF0000"), NotificationFilament("PLA", "#00FF00")]) is None

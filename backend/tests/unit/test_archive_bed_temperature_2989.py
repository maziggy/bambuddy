"""BambuStudio does not write a ``bed_temperature`` key, so we never read one.

The 3MF stores one bed-temperature array per plate type -- ``cool_plate_temp``,
``eng_plate_temp``, ``hot_plate_temp``, ``textured_plate_temp``,
``supertack_plate_temp`` -- and names the plate the project is sliced for in
``curr_bed_type``. ``bed_temperature`` is the Orca/PrusaSlicer spelling. The
extractor looked only for that spelling, so ``PrintArchive.bed_temperature`` was
NULL for every archive produced from a Bambu slice: measured at 0 of 455 real
3MFs on a live install, against 455 of 455 once the plate keys are read.

That NULL is not cosmetic. Preheat reads ``archive.bed_temperature`` to decide
what to heat the bed to, and with nothing there it falls back to the configured
chamber-heating bed temperature (90 by default) on jobs that never wanted it --
which is how #2989's PLA print came to sit behind a 90°C bed.

Key names and the plate mapping are BambuStudio's own ``get_bed_temp_key`` /
``get_bed_temp_1st_layer_key`` (PrintConfig.hpp); the plate names are the
``curr_bed_type`` enum values (PrintConfig.cpp).
"""

import pytest

from backend.app.services.archive import ThreeMFParser


def _extract(config: dict) -> int | None:
    parser = ThreeMFParser.__new__(ThreeMFParser)
    parser.metadata = {}
    parser._extract_print_settings(config)
    return parser.metadata.get("bed_temperature")


class TestThePlateTheProjectIsSlicedFor:
    @pytest.mark.parametrize(
        "bed_type,key",
        [
            ("Cool Plate", "cool_plate_temp"),
            ("Engineering Plate", "eng_plate_temp"),
            ("High Temp Plate", "hot_plate_temp"),
            ("Textured PEI Plate", "textured_plate_temp"),
            ("Supertack Plate", "supertack_plate_temp"),
        ],
    )
    def test_every_plate_type_reads_its_own_array(self, bed_type, key):
        """All five plates BambuStudio can name, each with its own key."""
        assert _extract({"curr_bed_type": bed_type, key: ["65", "65"]}) == 65

    def test_the_fitted_plate_wins_over_the_others(self):
        """The real failure mode this replaces: reading whichever key happened
        to be present. A Textured slice must not take the cool plate's 0."""
        config = {
            "curr_bed_type": "Textured PEI Plate",
            "cool_plate_temp": ["0", "0", "0"],
            "eng_plate_temp": ["90", "90", "90"],
            "hot_plate_temp": ["90", "90", "90"],
            "textured_plate_temp": ["55", "55", "55"],
            "supertack_plate_temp": ["0", "0", "0"],
        }
        assert _extract(config) == 55

    def test_the_first_layer_value_is_preferred(self):
        """It is what the printer heats to before the print starts, which is
        the number preheat is trying to reach."""
        config = {
            "curr_bed_type": "Textured PEI Plate",
            "textured_plate_temp_initial_layer": ["65"],
            "textured_plate_temp": ["60"],
        }
        assert _extract(config) == 65

    def test_the_regular_value_when_there_is_no_first_layer_one(self):
        config = {"curr_bed_type": "Textured PEI Plate", "textured_plate_temp": ["60"]}
        assert _extract(config) == 60


class TestThePerFilamentArray:
    def test_the_highest_entry_wins(self):
        """One bed, several filaments: the print runs at the highest its
        filaments ask for."""
        config = {"curr_bed_type": "High Temp Plate", "hot_plate_temp": ["55", "100", "90"]}
        assert _extract(config) == 100

    def test_a_leading_zero_does_not_win(self):
        """0 means that filament cannot print on this plate. Taking entry 0 --
        what the neighbouring scalar settings do -- would store a cold bed for
        any project whose first filament is not one this plate is heated for."""
        config = {"curr_bed_type": "High Temp Plate", "hot_plate_temp": ["0", "90"]}
        assert _extract(config) == 90

    def test_a_scalar_rather_than_an_array(self):
        assert _extract({"curr_bed_type": "Cool Plate", "cool_plate_temp": 35}) == 35

    def test_numeric_strings_and_floats(self):
        assert _extract({"curr_bed_type": "Cool Plate", "cool_plate_temp": ["35.0"]}) == 35


class TestWhatItRefusesToInvent:
    def test_an_all_zero_plate_array_is_not_a_bed_temperature(self):
        """No filament in the project prints on this plate. Recording the 0
        would read downstream as a deliberate cold bed."""
        config = {"curr_bed_type": "Cool Plate", "cool_plate_temp": ["0", "0"]}
        assert _extract(config) is None

    def test_a_plate_bambustudio_maps_to_no_key(self):
        """``Default Plate`` has no temperature array of its own, and guessing
        another plate's would invent a temperature the slice never specified."""
        config = {"curr_bed_type": "Default Plate", "hot_plate_temp": ["90"]}
        assert _extract(config) is None

    def test_a_plate_name_we_do_not_know(self):
        """A plate a future BambuStudio adds must not silently read another
        plate's array."""
        config = {"curr_bed_type": "Cryo Plate", "textured_plate_temp": ["55"]}
        assert _extract(config) is None

    def test_a_config_with_no_bed_temperature_at_all(self):
        assert _extract({"curr_bed_type": "Textured PEI Plate", "layer_height": ["0.2"]}) is None

    def test_junk_entries_are_stepped_over(self):
        config = {"curr_bed_type": "Cool Plate", "cool_plate_temp": [None, {}, "abc", "35"]}
        assert _extract(config) == 35


class TestOrcaExportsStillWork:
    """The generic spelling is the fallback, not the primary -- Orca-exported
    3MFs and anything else Prusa-shaped keep parsing exactly as before."""

    def test_the_generic_first_layer_key(self):
        assert _extract({"bed_temperature_initial_layer": ["60"]}) == 60

    def test_the_generic_key(self):
        assert _extract({"bed_temperature": 60}) == 60

    def test_the_plate_array_is_preferred_when_both_are_present(self):
        config = {
            "curr_bed_type": "Textured PEI Plate",
            "textured_plate_temp": ["55"],
            "bed_temperature": ["90"],
        }
        assert _extract(config) == 55


class TestTheNeighbouringSettingsAreUntouched:
    """The same method extracts three other values; none of them changed."""

    def test_nozzle_temperature_layer_height_and_diameter(self):
        parser = ThreeMFParser.__new__(ThreeMFParser)
        parser.metadata = {}
        parser._extract_print_settings(
            {
                "curr_bed_type": "Textured PEI Plate",
                "textured_plate_temp": ["55"],
                "nozzle_temperature_initial_layer": ["220", "220"],
                "layer_height": ["0.2"],
                "nozzle_diameter": ["0.4"],
            }
        )
        assert parser.metadata == {
            "bed_type": "Textured PEI Plate",
            "layer_height": 0.2,
            "nozzle_diameter": 0.4,
            "bed_temperature": 55,
            "nozzle_temperature": 220,
        }

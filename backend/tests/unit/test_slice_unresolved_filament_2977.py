"""A filament preset the sidecar could not resolve must not pass unnoticed.

Found while investigating #2977. Bambuddy sends a filament profile as a stub
naming the preset to inherit, and the sidecar's resolver walks that name
against its bundled profile tree. When the name is not in that tree the CLI
does not fail: it inherits nothing, falls back to its compiled-in defaults for
every field, and returns a well-formed success.

Measured against a 02.08.02.61 sidecar, a stub naming a preset that does not
exist slices as::

    filament_type        ["PLA"]
    nozzle_temperature   ["200"]
    filament_ids         [""]
    filament_vendor      ["(Undefined)"]

-- so a PETG preset whose name that sidecar image predates prints at PLA
temperatures with no diagnostic anywhere. Unlike the missing start G-code of
#2838 the file is printable, just wrong, so this is reported and the slice is
kept rather than refused.
"""

import io
import json
import zipfile

import pytest

from backend.app.services.slice_output_check import (
    unresolved_filament_message,
    unresolved_filament_slots,
)

pytestmark = pytest.mark.unit


def _3mf(settings: dict | None, *, valid_zip: bool = True) -> bytes:
    if not valid_zip:
        return b"not a zip file at all"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if settings is not None:
            archive.writestr("Metadata/project_settings.config", json.dumps(settings))
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")
    return buffer.getvalue()


# What the sidecar returns for a stub whose `inherits:` target it resolved.
RESOLVED = {"filament_vendor": ["Generic"], "filament_ids": ["GFL96"]}
# ... and for one it did not.
UNRESOLVED = {"filament_vendor": ["(Undefined)"], "filament_ids": [""]}


class TestItRecognisesTheDefect:
    def test_a_resolved_slot_is_not_flagged(self):
        assert unresolved_filament_slots(_3mf(RESOLVED), export_3mf=True) == []

    def test_an_unresolved_slot_is_flagged_by_its_one_indexed_position(self):
        assert unresolved_filament_slots(_3mf(UNRESOLVED), export_3mf=True) == [1]

    def test_only_the_unresolved_slots_of_a_multi_colour_slice_are_flagged(self):
        settings = {
            "filament_vendor": ["Generic", "(Undefined)", "Bambu"],
            "filament_ids": ["GFL96", "", "GFA00"],
        }
        assert unresolved_filament_slots(_3mf(settings), export_3mf=True) == [2]

    def test_every_slot_can_be_flagged(self):
        settings = {"filament_vendor": ["(Undefined)"] * 3, "filament_ids": [""] * 3}
        assert unresolved_filament_slots(_3mf(settings), export_3mf=True) == [1, 2, 3]


class TestBothSignalsAreRequiredTogether:
    def test_a_vendorless_profile_that_still_resolved_is_not_flagged(self):
        # A hand-written profile may simply never have named a vendor. It
        # inherited fine, which the real filament id proves.
        settings = {"filament_vendor": ["(Undefined)"], "filament_ids": ["GFL96"]}
        assert unresolved_filament_slots(_3mf(settings), export_3mf=True) == []

    def test_a_users_own_cloud_preset_is_not_flagged(self):
        # Cloud presets legitimately carry no bundled filament id; the vendor
        # is what separates them from a slot that inherited nothing.
        settings = {"filament_vendor": ["Bambu"], "filament_ids": [""]}
        assert unresolved_filament_slots(_3mf(settings), export_3mf=True) == []

    def test_whitespace_does_not_read_as_a_real_filament_id(self):
        settings = {"filament_vendor": ["(Undefined)"], "filament_ids": ["  "]}
        assert unresolved_filament_slots(_3mf(settings), export_3mf=True) == [1]


class TestItAnswersEmptyWhenItCannotSeeTheAnswer:
    def test_a_raw_gcode_response_carries_no_per_slot_config(self):
        assert unresolved_filament_slots(_3mf(UNRESOLVED), export_3mf=False) == []

    def test_empty_content(self):
        assert unresolved_filament_slots(b"", export_3mf=True) == []

    def test_an_unreadable_archive(self):
        assert unresolved_filament_slots(_3mf(None, valid_zip=False), export_3mf=True) == []

    def test_a_missing_project_settings(self):
        assert unresolved_filament_slots(_3mf(None), export_3mf=True) == []

    def test_malformed_json(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Metadata/project_settings.config", "{not json")
        assert unresolved_filament_slots(buffer.getvalue(), export_3mf=True) == []

    def test_settings_that_are_not_an_object(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Metadata/project_settings.config", "[1, 2, 3]")
        assert unresolved_filament_slots(buffer.getvalue(), export_3mf=True) == []

    def test_missing_vendor_or_id_arrays(self):
        assert unresolved_filament_slots(_3mf({"filament_ids": [""]}), export_3mf=True) == []
        assert unresolved_filament_slots(_3mf({"filament_vendor": ["(Undefined)"]}), export_3mf=True) == []

    def test_scalar_rather_than_array_fields(self):
        settings = {"filament_vendor": "(Undefined)", "filament_ids": ""}
        assert unresolved_filament_slots(_3mf(settings), export_3mf=True) == []

    def test_mismatched_array_lengths_only_compare_the_overlap(self):
        settings = {"filament_vendor": ["(Undefined)", "(Undefined)"], "filament_ids": [""]}
        assert unresolved_filament_slots(_3mf(settings), export_3mf=True) == [1]


class TestTheMessage:
    def test_it_names_the_slot_and_the_preset_that_was_picked(self):
        message = unresolved_filament_message([2], ["Generic PLA", "Creality PETG DBA"])
        assert "slot 2 (Creality PETG DBA)" in message

    def test_it_names_every_affected_slot(self):
        message = unresolved_filament_message([1, 3], ["A", "B", "C"])
        assert "slot 1 (A)" in message
        assert "slot 3 (C)" in message

    def test_a_slot_with_no_matching_preset_name_is_still_named(self):
        # The names come from the request's preset list, which a caller could
        # send shorter than the slice actually had slots.
        assert "slot 4" in unresolved_filament_message([4], ["A"])

    def test_it_says_the_file_was_kept(self):
        assert "kept" in unresolved_filament_message([1], ["A"])

    def test_it_names_the_wrong_defaults_the_user_should_check(self):
        message = unresolved_filament_message([1], ["A"])
        assert "PLA" in message
        assert "200" in message

    def test_it_is_plain_ascii_so_it_survives_every_log_sink(self):
        unresolved_filament_message([1], ["A"]).encode("ascii")

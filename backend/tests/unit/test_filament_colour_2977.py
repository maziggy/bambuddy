"""Per-slot filament colour on a slice request (#2977).

Neither Bambu Studio nor OrcaSlicer stores a colour on a filament *preset* --
it is a per-project property their GUIs set from the plate -- so a CLI slice
that supplies no colour records the slicer's compiled-in default for every
slot. That default is ``#00AE42``, which is why every internal-slicer output
was Bambu green whatever filament was picked, why the plate thumbnail was
green, and why the print dialog reported a colour mismatch against the AMS
slot the job had just been correctly mapped to.

``default_filament_colour`` is not a substitute and these tests do not treat
it as one. Measured against a 02.08.02.61 sidecar, a profile carrying only
``default_filament_colour: ["#FF00FF"]`` still slices to
``filament_colour: ["#00AE42"]``: the CLI never reads it, because Bambu Studio
consumes it in the GUI when a project is created. It is read here and
rewritten as ``filament_colour``, which the CLI does honour -- the same
sidecar returns ``filament_colour: ["#E8B00C"]`` for a profile patched this
way.
"""

import io
import json
import zipfile

import pytest
from pydantic import ValidationError

from backend.app.api.routes.library import (
    _patch_filament_colours,
    _preset_default_colour,
    _source_plate_colours,
)
from backend.app.schemas.slicer import PresetRef, SliceRequest

pytestmark = pytest.mark.unit


def _filament(name: str, **extra) -> str:
    return json.dumps({"name": name, "inherits": name, "from": "system", "type": "filament", **extra})


def _colour_of(profile_json: str) -> list | None:
    return json.loads(profile_json).get("filament_colour")


def _project_3mf(types: list[str], colours: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps({"filament_type": types, "filament_colour": colours}),
        )
    return buffer.getvalue()


def _request(**kwargs) -> SliceRequest:
    return SliceRequest(
        printer_preset=PresetRef(source="standard", id="Bambu Lab A1 mini 0.4 nozzle"),
        process_preset=PresetRef(source="standard", id="0.20mm Standard @BBL A1M"),
        filament_presets=[PresetRef(source="standard", id="Generic PLA Silk")],
        **kwargs,
    )


class TestTheRequestField:
    def test_absent_by_default_so_older_clients_are_unchanged(self):
        assert _request().filament_colours == []

    def test_accepts_six_and_eight_digit_hex(self):
        # The AMS reports colours with an alpha byte and the slicer writes
        # them without one; a request may legitimately carry either.
        assert _request(filament_colours=["#00AE42", "#AABBCCDD"]).filament_colours == [
            "#00AE42",
            "#AABBCCDD",
        ]

    def test_normalises_case_so_two_equal_slices_do_not_differ_by_a_hex_digit(self):
        assert _request(filament_colours=["#e8b00c"]).filament_colours == ["#E8B00C"]

    def test_strips_surrounding_whitespace(self):
        assert _request(filament_colours=["  #E8B00C  "]).filament_colours == ["#E8B00C"]

    def test_empty_string_survives_as_a_per_slot_opt_out(self):
        # The list is index-aligned with filament_presets, so "no colour for
        # slot 2" has to be expressible without shortening the list and
        # shifting every slot after it.
        assert _request(filament_colours=["#E8B00C", "", "#112233"]).filament_colours == [
            "#E8B00C",
            "",
            "#112233",
        ]

    @pytest.mark.parametrize("bad", ["red", "00AE42", "#00AE4", "#GGHHII", "#00AE42FFFF", "rgb(0,0,0)"])
    def test_rejects_anything_that_is_not_a_hex_colour(self, bad):
        # The value is pasted into a profile the slicer parses, so a malformed
        # one is refused here rather than passed through to the CLI.
        with pytest.raises(ValidationError, match="filament_colours"):
            _request(filament_colours=[bad])

    def test_the_rejection_names_the_offending_slot(self):
        with pytest.raises(ValidationError, match=r"filament_colours\[1\]"):
            _request(filament_colours=["#00AE42", "nope"])


class TestThePresetDefaultReader:
    def test_reads_the_one_element_array_form(self):
        assert _preset_default_colour({"default_filament_colour": ["#123456"]}) == "#123456"

    def test_reads_the_bare_string_form(self):
        # Hand-written and older profiles store a scalar where the slicers
        # store a one-element array.
        assert _preset_default_colour({"default_filament_colour": "#123456"}) == "#123456"

    @pytest.mark.parametrize(
        "profile",
        [{}, {"default_filament_colour": []}, {"default_filament_colour": None}, {"default_filament_colour": "  "}],
    )
    def test_absent_or_empty_reads_as_no_colour(self, profile):
        assert _preset_default_colour(profile) == ""


class TestTheSourcePlateColours:
    def test_reads_the_designed_colours_in_slot_order(self):
        assert _source_plate_colours(_project_3mf(["PLA", "PETG"], ["#AA0000", "#00BB00"])) == [
            "#AA0000",
            "#00BB00",
        ]

    def test_an_stl_has_none(self):
        assert _source_plate_colours(b"solid cube\nendsolid cube\n") == []

    def test_a_mesh_only_3mf_has_none(self):
        # A CAD or Blender export is a valid 3MF with no project settings.
        # This is the case that behaves exactly like an STL, and the reason
        # the fix could not stop at "3MFs carry their colours".
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("3D/3dmodel.model", "<model/>")
        assert _source_plate_colours(buffer.getvalue()) == []

    def test_a_truncated_archive_reads_as_none_rather_than_raising(self):
        assert _source_plate_colours(b"PK\x03\x04 truncated") == []


class TestThePriorityChain:
    def test_the_requested_colour_is_written_to_filament_colour(self):
        patched = _patch_filament_colours([_filament("Generic PLA Silk")], ["#E8B00C"], b"")
        assert _colour_of(patched[0]) == ["#E8B00C"]

    def test_it_is_written_as_a_one_element_array(self):
        # The shape every other per-filament field uses. A bare string parses
        # as JSON but not as a slicer config value.
        patched = _patch_filament_colours([_filament("Generic PLA Silk")], ["#E8B00C"], b"")
        assert isinstance(json.loads(patched[0])["filament_colour"], list)

    def test_the_presets_own_default_is_used_when_the_caller_named_none(self):
        profile = _filament("Vendor PLA", default_filament_colour=["#123456"])
        assert _colour_of(_patch_filament_colours([profile], [], b"")[0]) == ["#123456"]

    def test_an_explicit_colour_outranks_the_presets_default(self):
        profile = _filament("Vendor PLA", default_filament_colour=["#123456"])
        assert _colour_of(_patch_filament_colours([profile], ["#ABCDEF"], b"")[0]) == ["#ABCDEF"]

    def test_the_source_plates_colour_is_the_last_resort(self):
        source = _project_3mf(["PLA", "PETG"], ["#AA0000", "#00BB00"])
        patched = _patch_filament_colours([_filament("A"), _filament("B")], [], source)
        assert [_colour_of(p) for p in patched] == [["#AA0000"], ["#00BB00"]]

    def test_the_presets_default_outranks_the_source_plate(self):
        # The preset is what the user just picked; the source colour is what
        # the file happened to be designed with.
        source = _project_3mf(["PLA"], ["#AA0000"])
        profile = _filament("Vendor PLA", default_filament_colour=["#123456"])
        assert _colour_of(_patch_filament_colours([profile], [], source)[0]) == ["#123456"]

    def test_an_empty_string_falls_through_to_the_next_source(self):
        source = _project_3mf(["PLA"], ["#AA0000"])
        assert _colour_of(_patch_filament_colours([_filament("A")], [""], source)[0]) == ["#AA0000"]

    def test_a_slot_with_no_colour_anywhere_is_left_untouched(self):
        # Not given a guess: the slicer's default is still wrong, but it is
        # the same wrong value the file would have had regardless, and an
        # invented one would be indistinguishable from a real choice.
        patched = _patch_filament_colours([_filament("Generic PLA Silk")], [], b"")
        assert "filament_colour" not in json.loads(patched[0])

    def test_a_short_colour_list_leaves_the_remaining_slots_to_the_chain(self):
        source = _project_3mf(["PLA", "PETG"], ["#AA0000", "#00BB00"])
        patched = _patch_filament_colours([_filament("A"), _filament("B")], ["#E8B00C"], source)
        assert [_colour_of(p) for p in patched] == [["#E8B00C"], ["#00BB00"]]

    def test_more_colours_than_slots_is_not_an_error(self):
        patched = _patch_filament_colours([_filament("A")], ["#E8B00C", "#112233"], b"")
        assert [_colour_of(p) for p in patched] == [["#E8B00C"]]

    def test_slot_order_is_preserved(self):
        patched = _patch_filament_colours(
            [_filament("A"), _filament("B"), _filament("C")],
            ["#110000", "#001100", "#000011"],
            b"",
        )
        assert [_colour_of(p) for p in patched] == [["#110000"], ["#001100"], ["#000011"]]

    def test_every_other_field_of_the_profile_survives(self):
        profile = _filament("Generic PLA Silk", filament_max_volumetric_speed=["7.5"])
        patched = json.loads(_patch_filament_colours([profile], ["#E8B00C"], b"")[0])
        assert patched["name"] == "Generic PLA Silk"
        assert patched["inherits"] == "Generic PLA Silk"
        assert patched["from"] == "system"
        assert patched["type"] == "filament"
        assert patched["filament_max_volumetric_speed"] == ["7.5"]

    def test_an_empty_slot_list_is_a_no_op(self):
        assert _patch_filament_colours([], ["#E8B00C"], b"") == []


class TestItNeverFailsASliceThatWouldOtherwiseSucceed:
    def test_an_unparseable_profile_is_passed_through(self):
        # Same reasoning as the bed-type patch: a colour is not worth losing
        # a slice over. The slicer will reject the profile itself if it is
        # genuinely broken, with a better message than we could write.
        assert _patch_filament_colours(["{not json"], ["#E8B00C"], b"") == ["{not json"]

    def test_a_json_profile_that_is_not_an_object_is_passed_through(self):
        assert _patch_filament_colours(["[1, 2, 3]"], ["#E8B00C"], b"") == ["[1, 2, 3]"]

    def test_an_unreadable_source_still_lets_the_requested_colour_through(self):
        patched = _patch_filament_colours([_filament("A")], ["#E8B00C"], b"not a zip")
        assert _colour_of(patched[0]) == ["#E8B00C"]

"""Regression tests for the #1881 support-settings patch on slice requests.

BambuStudio's shipped process presets ("0.20mm Standard @BBL H2D" etc.)
define `enable_support: 0` because supports are a per-print decision, not
a per-quality one. Bambuddy passes the picked process preset via
`--load-settings`, which is authoritative — every field in the loaded
JSON overrides the source 3MF's embedded `project_settings.config`. So
without patching, a user who exported a source 3MF with supports
configured (PLA in slot 1 + PVA in slot 2 for support_interface,
enable_support on) got a single-material output with the PVA slot loaded
but never used.

The patch reads support-related fields from the source's
project_settings.config and overlays them onto the process preset JSON,
so the source's per-project support intent survives `--load-settings`.

The carry is one-way (#2820): a source can switch supports on, never off.
The original rule was symmetric, which meant any 3MF that shipped with
supports disabled -- i.e. nearly every MakerWorld download -- stripped
them back out of a custom process preset that deliberately enabled them.
"""

import io
import json
import logging
import zipfile

from backend.app.api.routes.library import _declined_source_keys, _patch_process_support_settings
from backend.app.services.design_settings import DesignOverride


def _make_3mf(project_settings: dict | None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        if project_settings is not None:
            zf.writestr("Metadata/project_settings.config", json.dumps(project_settings))
    return buf.getvalue()


class TestPatchProcessSupportSettings:
    def test_preserves_source_enable_support_and_interface_slot(self):
        # Reporter's exact #1881 config: source has supports on with PVA
        # in slot 2 for the interface. Shipped process preset has all four
        # fields off. Post-patch, the source wins for the support keys and
        # the process preset's own layer_height stays untouched.
        source = _make_3mf(
            {
                "enable_support": "1",
                "support_filament": "0",
                "support_interface_filament": "2",
                "support_type": "normal(manual)",
                "filament_type": ["PLA", "PVA"],
            }
        )
        preset = json.dumps(
            {
                "name": "0.20mm Standard @BBL H2D",
                "enable_support": "0",
                "support_filament": "0",
                "support_interface_filament": "0",
                "support_type": "default",
                "layer_height": "0.20",
            }
        )
        result = json.loads(_patch_process_support_settings(preset, source))
        assert result["enable_support"] == "1"
        assert result["support_filament"] == "0"
        assert result["support_interface_filament"] == "2"
        assert result["support_type"] == "normal(manual)"
        # Non-support fields survive.
        assert result["layer_height"] == "0.20"
        assert result["name"] == "0.20mm Standard @BBL H2D"

    def test_preset_supports_on_survives_a_source_with_supports_off(self):
        # #2820: the reporter's own process preset turns supports on with
        # normal(auto); the MakerWorld source they sliced ships them off
        # with tree(auto), like nearly every published 3MF. Carrying the
        # off direction handed them a supportless tree(auto) slice, so the
        # source is now only allowed to switch supports *on*.
        source = _make_3mf(
            {
                "enable_support": "0",
                "support_filament": "0",
                "support_interface_filament": "0",
                "support_type": "tree(auto)",
            }
        )
        preset = json.dumps(
            {
                "name": "Pokeball Fast - Buddy",
                "enable_support": "1",
                "support_filament": "2",
                "support_interface_filament": "2",
                "support_type": "normal(auto)",
                "support_style": "snug",
            }
        )
        result = json.loads(_patch_process_support_settings(preset, source))
        assert result["enable_support"] == "1"
        assert result["support_filament"] == "2"
        assert result["support_interface_filament"] == "2"
        assert result["support_type"] == "normal(auto)"
        assert result["support_style"] == "snug"

    def test_source_without_enable_support_carries_nothing(self):
        # A source that never declares enable_support gives us no support
        # intent to act on, so its slot assignments stay out of the preset
        # — same "supports off" branch, reached via the missing key.
        source = _make_3mf({"support_filament": "3", "support_interface_filament": "3"})
        preset = json.dumps({"support_filament": "0", "support_interface_filament": "0"})
        result = json.loads(_patch_process_support_settings(preset, source))
        assert result == {"support_filament": "0", "support_interface_filament": "0"}

    def test_non_string_enable_support_still_counts_as_on(self):
        # Forks and older BambuStudio builds write real booleans / ints
        # instead of "1" — those must still carry (shared truthiness rule
        # with extract_support_filament_slots_from_3mf).
        for enabled in (True, 1, "1", "true"):
            source = _make_3mf({"enable_support": enabled, "support_interface_filament": "2"})
            preset = json.dumps({"enable_support": "0", "support_interface_filament": "0"})
            result = json.loads(_patch_process_support_settings(preset, source))
            assert result["enable_support"] == enabled, f"failed for {enabled!r}"
            assert result["support_interface_filament"] == "2"

    def test_carry_is_logged_with_the_keys_it_took(self, caplog):
        # The slice modal shows the picked preset's values, so a carried
        # key silently disagrees with what the user saw. #2820's reporter
        # spent the bug report chasing an unrelated sanitiser line because
        # this step logged nothing at all.
        source = _make_3mf({"enable_support": "1", "support_interface_filament": "2"})
        preset = json.dumps({"enable_support": "0", "support_interface_filament": "0"})
        with caplog.at_level(logging.INFO, logger="backend.app.api.routes.library"):
            _patch_process_support_settings(preset, source)
        assert "Carried support settings" in caplog.text
        assert "enable_support" in caplog.text
        assert "support_interface_filament" in caplog.text

    def test_no_log_when_the_source_has_supports_off(self, caplog):
        source = _make_3mf({"enable_support": "0", "support_type": "tree(auto)"})
        preset = json.dumps({"enable_support": "1"})
        with caplog.at_level(logging.INFO, logger="backend.app.api.routes.library"):
            _patch_process_support_settings(preset, source)
        assert "Carried support settings" not in caplog.text

    def test_only_patches_keys_present_in_source(self):
        # Source with a partial support config (e.g. legacy 3MFs from an
        # older BambuStudio) only overrides the keys it defines. Preset's
        # values for the other support keys survive.
        source = _make_3mf({"enable_support": "1"})
        preset = json.dumps(
            {
                "enable_support": "0",
                "support_filament": "2",
                "support_interface_filament": "3",
                "support_type": "tree(auto)",
            }
        )
        result = json.loads(_patch_process_support_settings(preset, source))
        assert result["enable_support"] == "1"
        # Preset's values kept for keys the source didn't define.
        assert result["support_filament"] == "2"
        assert result["support_interface_filament"] == "3"
        assert result["support_type"] == "tree(auto)"

    def test_no_project_settings_in_source_returns_preset_unchanged(self):
        # STL / STEP / a stripped-down 3MF has no project_settings.config;
        # nothing to overlay, preset must pass through untouched.
        source = _make_3mf(None)
        preset = json.dumps({"enable_support": "0", "layer_height": "0.20"})
        result = _patch_process_support_settings(preset, source)
        # Same JSON round-trips.
        assert json.loads(result) == {"enable_support": "0", "layer_height": "0.20"}

    def test_malformed_source_returns_preset_unchanged(self):
        # A malformed source 3MF (or a random blob) can't yield support
        # info; the slice then runs with the preset's own defaults, which
        # is the safe fall-back matching pre-fix behaviour.
        preset = json.dumps({"enable_support": "0"})
        assert json.loads(_patch_process_support_settings(preset, b"not a zip")) == {"enable_support": "0"}

    def test_malformed_project_settings_json_returns_preset_unchanged(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Metadata/project_settings.config", "{not json")
        source = buf.getvalue()
        preset = json.dumps({"enable_support": "0"})
        assert json.loads(_patch_process_support_settings(preset, source)) == {"enable_support": "0"}

    def test_source_project_settings_not_dict_returns_preset_unchanged(self):
        # Defensive: spec says it's a dict, but a source that ships a
        # top-level list (or anything non-dict) shouldn't crash the slice.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Metadata/project_settings.config", json.dumps([]))
        source = buf.getvalue()
        preset = json.dumps({"enable_support": "0"})
        assert json.loads(_patch_process_support_settings(preset, source)) == {"enable_support": "0"}

    def test_malformed_preset_json_returns_input_unchanged(self):
        # Symmetric to test_returns_input_unchanged_when_json_is_invalid
        # in the bed-type patch's test suite. The slicer would error on
        # the preset anyway; the patch is a straight passthrough so
        # failure attributes to the original input.
        source = _make_3mf({"enable_support": "1"})
        bogus = "not a json document"
        assert _patch_process_support_settings(bogus, source) is bogus

    def test_preset_json_not_a_dict_returns_input_unchanged(self):
        source = _make_3mf({"enable_support": "1"})
        not_a_dict = json.dumps(["this", "is", "an", "array"])
        assert _patch_process_support_settings(not_a_dict, source) is not_a_dict


class TestDeclinedKeysAreNotReinstated:
    """What the user unticked stays unticked (#2942).

    The slice dialog offers the file's own settings per key and applies only
    the ones that are on. This carry ran underneath that, unconditionally, so
    four support keys came out of the file whatever the ticks said -- the
    reporter's slice took ``enable_support`` and ``support_type`` from a
    MakerWorld download onto a process preset they had picked deliberately,
    with the dialog's "Use the file's built-in settings" switched off and
    nothing on screen able to stop it.
    """

    def _source(self) -> bytes:
        return _make_3mf(
            {
                "enable_support": "1",
                "support_filament": "0",
                "support_interface_filament": "0",
                "support_type": "normal(auto)",
            }
        )

    def _preset(self) -> str:
        return json.dumps(
            {
                "name": "Pokeball Fast - Buddy",
                "enable_support": "0",
                "support_type": "tree(auto)",
                "layer_height": "0.20",
            }
        )

    def test_declining_everything_leaves_the_preset_alone(self):
        result = json.loads(
            _patch_process_support_settings(
                self._preset(),
                self._source(),
                declined={"enable_support", "support_filament", "support_interface_filament", "support_type"},
            )
        )
        assert result["enable_support"] == "0"
        assert result["support_type"] == "tree(auto)"
        assert result["layer_height"] == "0.20"

    def test_declining_one_key_still_carries_the_others(self):
        # The ticks are per key, so declining the support type is not
        # declining supports.
        result = json.loads(_patch_process_support_settings(self._preset(), self._source(), declined={"support_type"}))
        assert result["enable_support"] == "1"
        assert result["support_type"] == "tree(auto)"

    def test_declining_nothing_is_the_behaviour_it_always_had(self):
        # A source that offers no per-key choice -- an OrcaSlicer export has
        # no `different_settings_to_system` to tick -- keeps #1881 whole.
        result = json.loads(_patch_process_support_settings(self._preset(), self._source()))
        assert result["enable_support"] == "1"
        assert result["support_type"] == "normal(auto)"

    def test_declining_everything_logs_nothing(self, caplog):
        # The log line exists to name the layer the user can't see coming.
        # Nothing was carried, so there is nothing to announce.
        with caplog.at_level(logging.INFO, logger="backend.app.api.routes.library"):
            _patch_process_support_settings(
                self._preset(),
                self._source(),
                declined={"enable_support", "support_filament", "support_interface_filament", "support_type"},
            )
        assert "Carried support settings" not in caplog.text

    def test_declining_a_key_the_source_never_had_changes_nothing(self):
        result = json.loads(_patch_process_support_settings(self._preset(), self._source(), declined={"wall_loops"}))
        assert result["enable_support"] == "1"
        assert result["support_type"] == "normal(auto)"


class TestDeclinedSourceKeys:
    """Reading "the user said no" out of a slice request (#2942)."""

    @staticmethod
    def _offered(*keys: str) -> list[DesignOverride]:
        return [DesignOverride(key=key, value="1", printer_coupled=False) for key in keys]

    def test_no_list_at_all_declines_nothing(self):
        # A client that predates the per-key ticks -- or any API consumer that
        # never sends the field -- cannot have turned anything down, so the
        # support carry-over stays exactly as it was.
        assert _declined_source_keys(self._offered("enable_support"), None) == set()

    def test_an_empty_list_declines_everything_on_offer(self):
        # Not the same answer as None: the panel was shown, and nothing in it
        # was ticked.
        assert _declined_source_keys(self._offered("enable_support", "wall_loops"), []) == {
            "enable_support",
            "wall_loops",
        }

    def test_a_partial_list_declines_only_the_rest(self):
        offered = self._offered("enable_support", "support_type", "wall_loops")
        assert _declined_source_keys(offered, ["wall_loops"]) == {"enable_support", "support_type"}

    def test_a_key_that_was_never_offered_is_not_a_decline(self):
        # Selecting something the file does not list is already ignored when
        # the values are applied; it must not turn into a phantom refusal.
        assert _declined_source_keys(self._offered("wall_loops"), ["wall_loops", "layer_height"]) == set()

    def test_a_file_that_offers_nothing_declines_nothing(self):
        assert _declined_source_keys([], []) == set()

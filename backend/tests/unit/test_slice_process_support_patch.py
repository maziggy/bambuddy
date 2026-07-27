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
"""

import io
import json
import zipfile

from backend.app.api.routes.library import _patch_process_support_settings


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

    def test_source_supports_off_beats_preset_supports_on(self):
        # Symmetric: a source with supports explicitly disabled must win
        # over a process preset that happens to have supports on. Rare in
        # practice (Bambu's presets ship off) but the semantic is "source
        # wins" regardless of direction — a user who exported without
        # supports doesn't want a preset accidentally re-enabling them.
        source = _make_3mf(
            {
                "enable_support": "0",
                "support_filament": "0",
                "support_interface_filament": "0",
            }
        )
        preset = json.dumps({"enable_support": "1", "support_filament": "2", "support_interface_filament": "2"})
        result = json.loads(_patch_process_support_settings(preset, source))
        assert result["enable_support"] == "0"
        assert result["support_filament"] == "0"
        assert result["support_interface_filament"] == "0"

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

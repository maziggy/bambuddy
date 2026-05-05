"""Unit tests for ``_sanitize_project_settings_sentinels`` (#1201).

MakerWorld 3MFs sliced for the P2S (and potentially other Bambu printers)
ship ``Metadata/project_settings.config`` entries with ``"-1"`` values on
fields that BambuStudio's GUI internally interprets as "inherit from the
parent process preset" — but the headless slicer CLI's
``StaticPrintConfig`` validator runs *before* ``--load-settings`` overrides
apply, so the sentinel trips the field's lower-bound range check and the
CLI exits non-zero. The user sees::

    Param values in 3mf/config error:
    raft_first_layer_expansion: -1 not in range [0.0, 3.4e+38]
    tree_support_wall_count: -1 not in range [0.0, 2.0]

Earlier the codebase tried to fix this by stripping
``Metadata/project_settings.config`` (and its sibling configs) entirely.
That broke ``StaticPrintConfig`` initialisation — see the comment block
inside ``_run_slicer_with_fallback`` — so the strip-everything path was
reverted. The current fix is surgical: open the embedded config, drop
*only* the allowlisted keys when their value is exactly ``"-1"``, and
re-zip. The slicer then falls back to the supplied ``--load-settings``
default for the removed keys, while every other entry in the zip stays
byte-identical.

Pinning the contract here rather than via the slicer integration tests
because the fix is purely about the bytes we hand to the sidecar — no
slicer mock needed.
"""

import io
import json
import zipfile

import pytest

from backend.app.api.routes.library import (
    _PROJECT_SETTINGS_SENTINEL_KEYS,
    _sanitize_project_settings_sentinels,
)


def _make_3mf(*, settings: dict | None = None, extra_files: dict | None = None) -> bytes:
    """Build a tiny in-memory 3MF zip with project_settings.config + a model
    payload, plus any caller-supplied extra entries (e.g., model_settings.config)
    that should round-trip byte-identical through the sanitiser.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", "<model><resources/></model>")
        if settings is not None:
            zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _read_settings(zip_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        return json.loads(zf.read("Metadata/project_settings.config").decode("utf-8"))


def _zip_namelist(zip_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        return zf.namelist()


class TestRemovesSentinelValues:
    @pytest.mark.parametrize("key", sorted(_PROJECT_SETTINGS_SENTINEL_KEYS))
    def test_removes_each_allowlisted_key_when_value_is_minus_one(self, key):
        original = _make_3mf(settings={key: "-1", "layer_height": "0.2"})
        sanitised = _sanitize_project_settings_sentinels(original)

        cfg = _read_settings(sanitised)
        assert key not in cfg, f"Sentinel key {key!r} should have been removed"
        # Non-sentinel settings stay untouched so --load-settings can layer
        # cleanly on top of what the user actually configured.
        assert cfg["layer_height"] == "0.2"

    def test_removes_multiple_sentinels_at_once(self):
        original = _make_3mf(
            settings={
                "raft_first_layer_expansion": "-1",
                "tree_support_wall_count": "-1",
                "prime_tower_brim_width": "-1",
                "layer_height": "0.2",
            }
        )
        sanitised = _sanitize_project_settings_sentinels(original)

        cfg = _read_settings(sanitised)
        assert "raft_first_layer_expansion" not in cfg
        assert "tree_support_wall_count" not in cfg
        assert "prime_tower_brim_width" not in cfg
        assert cfg["layer_height"] == "0.2"


class TestPreservesUnaffectedValues:
    def test_preserves_allowlisted_key_with_legitimate_non_sentinel_value(self):
        # A user who deliberately configured raft_first_layer_expansion=0 must
        # see that 0 forwarded to the slicer — only literal "-1" gets stripped.
        original = _make_3mf(settings={"raft_first_layer_expansion": "0"})
        sanitised = _sanitize_project_settings_sentinels(original)
        assert _read_settings(sanitised)["raft_first_layer_expansion"] == "0"

    def test_does_not_touch_non_allowlisted_keys_with_minus_one(self):
        # Non-allowlisted keys are left alone even when they hold "-1".
        # Some Bambu fields legitimately allow negative values (z_offset,
        # translation, etc.) and a blanket "-1" strip would corrupt those.
        original = _make_3mf(settings={"z_offset": "-1", "layer_height": "0.2"})
        sanitised = _sanitize_project_settings_sentinels(original)

        cfg = _read_settings(sanitised)
        assert cfg["z_offset"] == "-1"
        assert cfg["layer_height"] == "0.2"

    def test_returns_original_bytes_when_no_sentinel_present(self):
        # If nothing needs sanitising, return the input identity-equal so
        # the caller's downstream comparisons / hashes don't churn.
        original = _make_3mf(settings={"layer_height": "0.2", "z_offset": "0"})
        sanitised = _sanitize_project_settings_sentinels(original)
        assert sanitised is original

    def test_does_not_strip_array_value_even_if_includes_minus_one(self):
        # Bambu sometimes stores per-filament/per-extruder values as JSON
        # arrays of strings. v1 of the sanitiser deliberately handles only
        # scalar strings — array forms are left alone so a per-filament
        # legitimate "-1" inside a list isn't mistaken for the inherit
        # sentinel and removed wholesale. If a future report shows the CLI
        # rejects array-form sentinels, expand this then.
        original = _make_3mf(settings={"raft_first_layer_expansion": ["-1", "0"]})
        sanitised = _sanitize_project_settings_sentinels(original)
        cfg = _read_settings(sanitised)
        assert cfg["raft_first_layer_expansion"] == ["-1", "0"]


class TestZipPreservation:
    def test_other_zip_entries_pass_through_unchanged(self):
        original = _make_3mf(
            settings={"raft_first_layer_expansion": "-1"},
            extra_files={
                "Metadata/model_settings.config": "<config><object id='1'/></config>",
                "Metadata/slice_info.config": "<config><plate/></config>",
                "Metadata/_rels/model_settings.rels": "<rels/>",
            },
        )
        sanitised = _sanitize_project_settings_sentinels(original)
        assert sanitised is not original

        names = _zip_namelist(sanitised)
        # Every entry from the original zip must survive — the previous
        # full-strip experiment broke StaticPrintConfig by dropping these,
        # so the new sanitiser leaves them alone (#1201).
        for required in (
            "3D/3dmodel.model",
            "Metadata/project_settings.config",
            "Metadata/model_settings.config",
            "Metadata/slice_info.config",
            "Metadata/_rels/model_settings.rels",
        ):
            assert required in names, f"{required} must be preserved in the rebuilt zip"

        # Content of unrelated entries is byte-identical.
        with zipfile.ZipFile(io.BytesIO(sanitised), "r") as zf:
            assert zf.read("Metadata/model_settings.config").decode() == "<config><object id='1'/></config>"
            assert zf.read("3D/3dmodel.model").decode() == "<model><resources/></model>"


class TestDefensiveFallbacks:
    def test_returns_original_when_input_is_not_a_zip(self):
        # An STL or any other non-zip input: pass through. The slicer
        # routing decides whether 3MF sanitisation runs anyway, but
        # defending here means a misrouted call can't corrupt the bytes.
        garbage = b"not a zip file"
        assert _sanitize_project_settings_sentinels(garbage) is garbage

    def test_returns_original_when_settings_config_absent(self):
        # 3MF without an embedded project_settings.config — nothing to do.
        original = _make_3mf(settings=None)
        assert _sanitize_project_settings_sentinels(original) is original

    def test_returns_original_on_malformed_json(self):
        # Settings file present but not valid JSON. We don't risk rebuilding
        # the zip with synthesised content; the CLI will surface its own
        # error and that's better than silent corruption.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
            zf.writestr("Metadata/project_settings.config", "{not valid json")
        original = buf.getvalue()
        assert _sanitize_project_settings_sentinels(original) is original

    def test_returns_original_when_settings_root_is_not_a_dict(self):
        # Real-world configs are objects, but defend against an array root
        # (some legacy tooling produced these). Returning unchanged is
        # safer than fabricating a dict.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
            zf.writestr("Metadata/project_settings.config", "[]")
        original = buf.getvalue()
        assert _sanitize_project_settings_sentinels(original) is original

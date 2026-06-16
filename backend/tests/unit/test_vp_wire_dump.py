"""Tests for the env-flagged VP wire-payload dump helper used to triage
shape-of-payload bugs like #1622."""

import json
import os
from unittest.mock import patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.services.virtual_printer import _debug


@pytest.fixture
def _isolated_log_dir(tmp_path, monkeypatch):
    with patch.object(app_settings, "log_dir", tmp_path):
        yield tmp_path


def test_disabled_by_default_writes_nothing(_isolated_log_dir, monkeypatch):
    monkeypatch.delenv("BAMBUDDY_VP_DUMP_WIRE", raising=False)
    _debug.dump_wire("VP1", "out", {"hello": "world"})
    assert not (_isolated_log_dir / "vp_wire").exists()


def test_enabled_writes_dict_as_pretty_json(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    payload = {"print": {"ams": {"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}]}}}
    _debug.dump_wire("Bambuddy P1S", "out", payload)
    out = _isolated_log_dir / "vp_wire" / "Bambuddy_P1S_out.json"
    assert out.is_file()
    assert json.loads(out.read_text()) == payload


def test_overwrites_on_repeat_call(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.dump_wire("VP1", "out", {"v": 1})
    _debug.dump_wire("VP1", "out", {"v": 2})
    out = _isolated_log_dir / "vp_wire" / "VP1_out.json"
    assert json.loads(out.read_text()) == {"v": 2}


def test_separate_files_per_direction(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.dump_wire("VP1", "in", {"src": "printer"})
    _debug.dump_wire("VP1", "out", {"src": "slicer"})
    assert (_isolated_log_dir / "vp_wire" / "VP1_in.json").is_file()
    assert (_isolated_log_dir / "vp_wire" / "VP1_out.json").is_file()


def test_sanitizes_path_traversal_in_vp_name(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.dump_wire("../../etc/passwd", "out", {"x": 1})
    vp_wire = _isolated_log_dir / "vp_wire"
    files = list(vp_wire.glob("*"))
    assert len(files) == 1
    # The actual safety property: the written file is inside vp_wire/.
    # `..` as a substring of a single filename component is harmless because
    # the path separator (/) is collapsed to _ before construction.
    assert files[0].resolve().parent == vp_wire.resolve()
    assert "/" not in files[0].name


def test_empty_vp_name_falls_back_to_default(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.dump_wire("", "out", {"x": 1})
    assert (_isolated_log_dir / "vp_wire" / "vp_out.json").is_file()


def test_bytes_payload_decoded(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.dump_wire("VP1", "in", b'{"raw": true}')
    out = _isolated_log_dir / "vp_wire" / "VP1_in.json"
    assert out.read_text() == '{"raw": true}'


def test_unwritable_dir_is_swallowed(_isolated_log_dir, monkeypatch):
    """A debug-instrumentation failure must not crash the bridge or 1Hz loop."""
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    # Point log_dir at a location that mkdir refuses (a regular file occupying
    # the path). Failure must be swallowed.
    blocker = _isolated_log_dir / "blocker"
    blocker.write_text("not a dir")
    with patch.object(app_settings, "log_dir", blocker):
        _debug.dump_wire("VP1", "out", {"x": 1})  # must not raise


@pytest.mark.parametrize("flag_value", ["0", "false", "off", "", "no"])
def test_falsy_flag_values_disable(_isolated_log_dir, monkeypatch, flag_value):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", flag_value)
    _debug.dump_wire("VP1", "out", {"x": 1})
    assert not (_isolated_log_dir / "vp_wire").exists()


@pytest.mark.parametrize("flag_value", ["1", "true", "TRUE", "yes", "on", "On"])
def test_truthy_flag_values_enable(_isolated_log_dir, monkeypatch, flag_value):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", flag_value)
    _debug.dump_wire("VP1", "out", {"x": 1})
    assert (_isolated_log_dir / "vp_wire" / "VP1_out.json").is_file()


def test_idempotent_atomic_no_partial_file_visible(_isolated_log_dir, monkeypatch):
    """tmp+rename pattern means a reader never sees a half-written .json file."""
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.dump_wire("VP1", "out", {"x": 1})
    files = sorted(p.name for p in (_isolated_log_dir / "vp_wire").iterdir())
    # No leftover .tmp file after a successful write.
    assert files == ["VP1_out.json"]


def test_env_check_is_per_call_not_module_load(_isolated_log_dir, monkeypatch):
    """Flag toggle must take effect on the next call without restarting; we
    re-read the env var inside ``dump_wire`` rather than caching at import."""
    monkeypatch.delenv("BAMBUDDY_VP_DUMP_WIRE", raising=False)
    _debug.dump_wire("VP1", "out", {"v": 1})
    assert not (_isolated_log_dir / "vp_wire").exists()

    os.environ["BAMBUDDY_VP_DUMP_WIRE"] = "1"
    try:
        _debug.dump_wire("VP1", "out", {"v": 2})
        assert (_isolated_log_dir / "vp_wire" / "VP1_out.json").is_file()
    finally:
        os.environ.pop("BAMBUDDY_VP_DUMP_WIRE", None)


# --- append_event (command-flow trace) --------------------------------------


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_append_event_disabled_by_default_writes_nothing(_isolated_log_dir, monkeypatch):
    monkeypatch.delenv("BAMBUDDY_VP_DUMP_WIRE", raising=False)
    _debug.append_event("VP1", "slicer_to_bridge", "device/X/request", {"hello": "world"})
    assert not (_isolated_log_dir / "vp_wire").exists()


def test_append_event_appends_one_jsonl_line_per_call(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.append_event("VP1", "slicer_to_bridge", "device/X/request", {"print": {"command": "ams_filament_setting"}})
    _debug.append_event(
        "VP1", "printer_to_slicer", "device/X/report", {"print": {"command": "ams_filament_setting", "result": "ok"}}
    )
    path = _isolated_log_dir / "vp_wire" / "VP1_cmd.jsonl"
    rows = _read_jsonl(path)
    assert len(rows) == 2
    assert rows[0]["dir"] == "slicer_to_bridge"
    assert rows[0]["topic"] == "device/X/request"
    assert rows[0]["cmd"] == "print.ams_filament_setting"
    assert rows[0]["payload"] == {"print": {"command": "ams_filament_setting"}}
    assert rows[1]["dir"] == "printer_to_slicer"
    assert rows[1]["cmd"] == "print.ams_filament_setting"


def test_append_event_parses_bytes_payload(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    raw = b'{"info": {"command": "get_version", "sequence_id": "0"}}'
    _debug.append_event("VP1", "slicer_to_bridge", "device/X/request", raw)
    rows = _read_jsonl(_isolated_log_dir / "vp_wire" / "VP1_cmd.jsonl")
    assert rows[0]["payload"] == {"info": {"command": "get_version", "sequence_id": "0"}}
    assert rows[0]["cmd"] == "info.get_version"


def test_append_event_unparseable_payload_kept_as_raw(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.append_event("VP1", "printer_to_slicer", "device/X/report", b"not-json-just-bytes")
    rows = _read_jsonl(_isolated_log_dir / "vp_wire" / "VP1_cmd.jsonl")
    assert rows[0]["payload"] == {"raw": "not-json-just-bytes"}
    assert rows[0]["cmd"] == "?"


def test_append_event_handles_trailing_null_from_orca(_isolated_log_dir, monkeypatch):
    """Same #927 quirk as ``_handle_publish``: OrcaSlicer can ship publishes with a
    trailing C-string null. The trace must still parse so the dump matches what
    the bridge actually saw, not raw text."""
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.append_event("VP1", "slicer_to_bridge", "device/X/request", b'{"info":{"command":"get_version"}}\x00')
    rows = _read_jsonl(_isolated_log_dir / "vp_wire" / "VP1_cmd.jsonl")
    assert rows[0]["payload"] == {"info": {"command": "get_version"}}


def test_append_event_sanitizes_vp_name(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.append_event("../../etc/passwd", "slicer_to_bridge", "device/X/request", {"x": 1})
    files = list((_isolated_log_dir / "vp_wire").glob("*_cmd.jsonl"))
    assert len(files) == 1
    assert "/" not in files[0].name


def test_append_event_includes_iso_timestamp(_isolated_log_dir, monkeypatch):
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    _debug.append_event("VP1", "slicer_to_bridge", "device/X/request", {"x": 1})
    rows = _read_jsonl(_isolated_log_dir / "vp_wire" / "VP1_cmd.jsonl")
    ts = rows[0]["ts"]
    # ISO-8601 with timezone (Z or +00:00 suffix from UTC).
    assert "T" in ts and (ts.endswith("+00:00") or ts.endswith("Z"))


def test_append_event_failure_swallowed(_isolated_log_dir, monkeypatch):
    """Debug instrumentation must never crash the bridge or slicer loop."""
    monkeypatch.setenv("BAMBUDDY_VP_DUMP_WIRE", "1")
    blocker = _isolated_log_dir / "blocker"
    blocker.write_text("not a dir")
    with patch.object(app_settings, "log_dir", blocker):
        _debug.append_event("VP1", "slicer_to_bridge", "device/X/request", {"x": 1})  # must not raise

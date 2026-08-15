"""Unit tests for the preview-slice cache.

The preview-slice runs the sidecar's `slice_without_profiles` on an unsliced
project file to extract the per-plate filament list. Results are cached by
``(kind, source_id, plate_id, content_hash)`` with LRU eviction so repeat
modal opens on the same plate are instant.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Any
from unittest.mock import patch

import pytest

from backend.app.services import slice_preview
from backend.app.services.slice_preview import (
    _PREVIEW_CACHE_MAX,
    _parse_filaments_from_sliced_3mf,
    get_preview_filaments,
)
from backend.app.services.slicer_api import (
    SlicerApiServerError,
    SlicerApiUnavailableError,
    SliceResult,
)


def _make_sliced_3mf(plate_id: int, filaments: list[dict[str, str]]) -> bytes:
    """Build a fake sliced-3MF zip whose Metadata/slice_info.config has one
    plate matching ``plate_id`` with the given filament rows."""
    fil_xml = "".join(
        f'<filament id="{f["id"]}" type="{f["type"]}" color="{f["color"]}"'
        f' used_g="{f.get("used_g", "0")}" used_m="{f.get("used_m", "0")}"'
        f' tray_info_idx="{f.get("tray_info_idx", "")}"/>'
        for f in filaments
    )
    slice_info = (
        f'<?xml version="1.0"?><config><plate><metadata key="index" value="{plate_id}"/>{fil_xml}</plate></config>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", slice_info)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test gets an empty cache + lock dict to keep them independent."""
    slice_preview._preview_cache.clear()
    slice_preview._preview_locks.clear()
    yield
    slice_preview._preview_cache.clear()
    slice_preview._preview_locks.clear()


class _StubService:
    """Mimics SlicerApiService just enough for these tests. Records every
    `slice_without_profiles` call so we can assert call counts."""

    def __init__(self, response_bytes: bytes | None = None, raise_exc: BaseException | None = None) -> None:
        self.response_bytes = response_bytes
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def slice_without_profiles(self, **kw):
        self.calls.append({"method": "slice_without_profiles", **kw})
        if self.raise_exc is not None:
            raise self.raise_exc
        return SliceResult(
            content=self.response_bytes or b"",
            print_time_seconds=0,
            filament_used_g=0.0,
            filament_used_mm=0.0,
        )


# ---------------------------------------------------------------------------
# _parse_filaments_from_sliced_3mf — pure-function parsing tests.
# ---------------------------------------------------------------------------


class TestParseFilamentsFromSliced3mf:
    def test_happy_path(self):
        body = _make_sliced_3mf(
            plate_id=22,
            filaments=[
                {"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "33.9"},
                {"id": "6", "type": "PLA", "color": "#FF0000", "used_g": "37.7"},
            ],
        )
        result = _parse_filaments_from_sliced_3mf(body, 22)
        assert result is not None
        assert [(f["slot_id"], f["color"]) for f in result] == [(1, "#FFFFFF"), (6, "#FF0000")]
        assert result[0]["used_grams"] == 33.9

    def test_missing_slice_info_returns_none(self):
        empty_zip = io.BytesIO()
        with zipfile.ZipFile(empty_zip, "w") as zf:
            zf.writestr("placeholder.txt", "x")
        assert _parse_filaments_from_sliced_3mf(empty_zip.getvalue(), 1) is None

    def test_plate_not_in_slice_info_returns_none(self):
        body = _make_sliced_3mf(plate_id=1, filaments=[{"id": "1", "type": "PLA", "color": "#000"}])
        assert _parse_filaments_from_sliced_3mf(body, plate_id=99) is None

    def test_corrupt_zip_returns_none(self):
        assert _parse_filaments_from_sliced_3mf(b"not a zip file", 1) is None


# ---------------------------------------------------------------------------
# get_preview_filaments — cache + concurrency behaviour.
# ---------------------------------------------------------------------------


class TestGetPreviewFilaments:
    @pytest.mark.asyncio
    async def test_happy_path_caches_result(self):
        body = _make_sliced_3mf(plate_id=1, filaments=[{"id": "1", "type": "PLA", "color": "#000"}])
        stub = _StubService(response_bytes=body)
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            first = await get_preview_filaments(
                kind="archive",
                source_id=1,
                plate_id=1,
                file_bytes=b"abc",
                file_name="x.3mf",
                api_url="http://sidecar",
            )
            second = await get_preview_filaments(
                kind="archive",
                source_id=1,
                plate_id=1,
                file_bytes=b"abc",
                file_name="x.3mf",
                api_url="http://sidecar",
            )
        assert first is not None
        assert first[0]["slot_id"] == 1
        assert second == first
        # Cache hit — only one slice was actually run.
        assert len(stub.calls) == 1

    @pytest.mark.asyncio
    async def test_different_content_hash_misses_cache(self):
        body = _make_sliced_3mf(plate_id=1, filaments=[{"id": "1", "type": "PLA", "color": "#000"}])
        stub = _StubService(response_bytes=body)
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            await get_preview_filaments(
                kind="archive",
                source_id=1,
                plate_id=1,
                file_bytes=b"v1",
                file_name="x.3mf",
                api_url="http://sidecar",
            )
            await get_preview_filaments(
                kind="archive",
                source_id=1,
                plate_id=1,
                file_bytes=b"v2",  # Same archive, but content changed
                file_name="x.3mf",
                api_url="http://sidecar",
            )
        # Hash differs → cache miss → fresh slice.
        assert len(stub.calls) == 2

    @pytest.mark.asyncio
    async def test_sidecar_unavailable_returns_none_no_cache(self):
        # Transient sidecar failure must NOT poison the cache — the next
        # request retries cleanly.
        stub = _StubService(raise_exc=SlicerApiUnavailableError("boom"))
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            first = await get_preview_filaments(
                kind="archive",
                source_id=1,
                plate_id=1,
                file_bytes=b"abc",
                file_name="x.3mf",
                api_url="http://sidecar",
            )
            assert first is None
            # Second call hits the sidecar again (no cached failure).
            await get_preview_filaments(
                kind="archive",
                source_id=1,
                plate_id=1,
                file_bytes=b"abc",
                file_name="x.3mf",
                api_url="http://sidecar",
            )
        assert len(stub.calls) == 2

    @pytest.mark.asyncio
    async def test_concurrent_calls_share_one_slice(self):
        body = _make_sliced_3mf(plate_id=1, filaments=[{"id": "1", "type": "PLA", "color": "#000"}])

        # Slow stub so we can observe N coroutines piling up on the lock.
        class _SlowStub(_StubService):
            async def slice_without_profiles(self, **kw):
                self.calls.append(kw)
                await asyncio.sleep(0.05)
                return SliceResult(
                    content=self.response_bytes or b"",
                    print_time_seconds=0,
                    filament_used_g=0.0,
                    filament_used_mm=0.0,
                )

        stub = _SlowStub(response_bytes=body)
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            results = await asyncio.gather(
                *(
                    get_preview_filaments(
                        kind="archive",
                        source_id=1,
                        plate_id=1,
                        file_bytes=b"abc",
                        file_name="x.3mf",
                        api_url="http://sidecar",
                    )
                    for _ in range(8)
                ),
            )
        # All 8 callers got the same result, but only ONE slice ran.
        assert all(r == results[0] for r in results)
        assert len(stub.calls) == 1

    @pytest.mark.asyncio
    async def test_lru_eviction_drops_lock(self):
        # Fill cache past the bound; oldest should evict, including its lock.
        body = _make_sliced_3mf(plate_id=1, filaments=[{"id": "1", "type": "PLA", "color": "#000"}])
        stub = _StubService(response_bytes=body)
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            # Each call has a unique source_id → unique cache key.
            for i in range(_PREVIEW_CACHE_MAX + 5):
                await get_preview_filaments(
                    kind="archive",
                    source_id=i,
                    plate_id=1,
                    file_bytes=b"abc",
                    file_name="x.3mf",
                    api_url="http://sidecar",
                )
        # Cache is bounded — older entries fell off.
        assert len(slice_preview._preview_cache) == _PREVIEW_CACHE_MAX
        # Lock dict is also pruned (no leak): same size as cache.
        assert len(slice_preview._preview_locks) == _PREVIEW_CACHE_MAX


# ---------------------------------------------------------------------------
# Unparsable custom G-code — a 3MF from a Studio newer than the sidecar.
#
# Studio 2.8 writes `{if timelapse_inline_photo}` into `time_lapse_gcode`
# without exporting a definition for that variable, so an older sidecar dies
# on a placeholder parse error before any slice_info exists. Blanking just
# that template lets the slice finish on the file's own settings.
# ---------------------------------------------------------------------------


# Reproduced verbatim from a Bambu Studio 2.7.1.62 sidecar refusing an H2D
# project saved by Studio 02.08.00.50. Note the slicer says `timelapse_gcode`
# while the 3MF stores the field as `time_lapse_gcode`.
_TIMELAPSE_PARSE_ERROR = (
    "Slicer CLI failed (500): Slicing failed with error from slicer: Failed slicing the model.: "
    "Slicer process failed (exit code 156)\n"
    "stderr: Failed to generate gcode for invalid custom G-code.\n\n"
    "timelapse_gcode Parsing error at line 13: Not a variable name\n"
    "    {if timelapse_inline_photo}\n"
    "        ^\n"
)


def _make_project_3mf(settings: dict[str, Any], extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _settings_of(file_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        return json.loads(zf.read("Metadata/project_settings.config").decode())


class TestUnparsableGcodeOption:
    def test_names_the_field_the_slicer_choked_on(self):
        assert slice_preview._unparsable_gcode_option(_TIMELAPSE_PARSE_ERROR) == "timelapsegcode"

    def test_unrelated_failure_is_not_a_gcode_problem(self):
        err = "Slicer CLI failed (500): raft_first_layer_expansion: -1 not in range [0, 340282346638]"
        assert slice_preview._unparsable_gcode_option(err) is None

    def test_refuses_a_field_that_extrudes(self):
        # The whole safety argument for this retry: silencing a template that
        # lays a prime line or purges would change the grams the preview
        # exists to report. Returning nothing beats returning wrong numbers.
        for field in ("machine_start_gcode", "change_filament_gcode", "filament_start_gcode"):
            err = f"{field} Parsing error at line 3: Not a variable name\n    {{if whatever}}\n"
            assert slice_preview._unparsable_gcode_option(err) is None, field


class TestBlankCustomGcode:
    def test_blanks_the_matching_field_despite_the_spelling_difference(self):
        original = _make_project_3mf(
            {
                "time_lapse_gcode": "M971 S11 C10\n{if timelapse_inline_photo}\n",
                "machine_start_gcode": "G28 ; home",
                "filament_colour": ["#FFFFFF", "#000000"],
            }
        )
        out = slice_preview._blank_custom_gcode(original, "timelapsegcode")
        assert out is not None
        settings = _settings_of(out)
        assert settings["time_lapse_gcode"] == ""
        # Everything else survives untouched — the preview's accuracy depends
        # on the file's own process/support/filament settings being intact.
        assert settings["machine_start_gcode"] == "G28 ; home"
        assert settings["filament_colour"] == ["#FFFFFF", "#000000"]

    def test_keeps_every_other_archive_member(self):
        original = _make_project_3mf(
            {"time_lapse_gcode": "x"},
            extra={"3D/3dmodel.model": b"<model/>", "Metadata/plate_1.png": b"\x89PNG"},
        )
        out = slice_preview._blank_custom_gcode(original, "timelapsegcode")
        assert out is not None
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            assert zf.read("3D/3dmodel.model") == b"<model/>"
            assert zf.read("Metadata/plate_1.png") == b"\x89PNG"

    def test_preserves_a_list_valued_template(self):
        original = _make_project_3mf({"layer_change_gcode": ["a", "b", "c"]})
        out = slice_preview._blank_custom_gcode(original, "layerchangegcode")
        assert out is not None
        assert _settings_of(out)["layer_change_gcode"] == ["", "", ""]

    def test_no_retry_when_the_field_is_already_empty(self):
        # Blanking an empty field would produce a byte-identical request and
        # the identical failure, so the caller must be told not to bother.
        assert slice_preview._blank_custom_gcode(_make_project_3mf({"time_lapse_gcode": ""}), "timelapsegcode") is None

    def test_no_match_no_retry(self):
        assert slice_preview._blank_custom_gcode(_make_project_3mf({"other": "x"}), "timelapsegcode") is None

    def test_only_gcode_keys_are_eligible(self):
        # The normalising fold must not let a same-stem non-template setting
        # be silently rewritten.
        original = _make_project_3mf({"timelapse_type": "0", "timelapse_gcode_extra": "keep"})
        assert slice_preview._blank_custom_gcode(original, "timelapsetype") is None

    def test_non_3mf_input_is_not_a_crash(self):
        assert slice_preview._blank_custom_gcode(b"not a zip", "timelapsegcode") is None

    def test_3mf_without_embedded_settings(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
        assert slice_preview._blank_custom_gcode(buf.getvalue(), "timelapsegcode") is None


class _FailThenSucceedService:
    """Fails the first slice with ``first_error``, then succeeds."""

    def __init__(self, first_error: BaseException, response_bytes: bytes) -> None:
        self.first_error = first_error
        self.response_bytes = response_bytes
        self.calls: list[bytes] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def slice_without_profiles(self, **kw):
        self.calls.append(kw["model_bytes"])
        if len(self.calls) == 1:
            raise self.first_error
        return SliceResult(
            content=self.response_bytes,
            print_time_seconds=0,
            filament_used_g=0.0,
            filament_used_mm=0.0,
        )


class TestPreviewRetriesUnparsableGcode:
    @pytest.mark.asyncio
    async def test_retries_once_with_the_template_blanked(self):
        original = _make_project_3mf({"time_lapse_gcode": "{if timelapse_inline_photo}"})
        body = _make_sliced_3mf(
            plate_id=1,
            filaments=[
                {"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "77.9"},
                {"id": "4", "type": "PLA-S", "color": "#0F80FF", "used_g": "11.5"},
            ],
        )
        stub = _FailThenSucceedService(SlicerApiServerError(_TIMELAPSE_PARSE_ERROR), body)
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            result = await get_preview_filaments(
                kind="library_file",
                source_id=18000,
                plate_id=1,
                file_bytes=original,
                file_name="x.3mf",
                api_url="http://sidecar",
            )
        assert result is not None
        # The support slot must survive — losing it is exactly the failure a
        # profile-override fallback would have introduced.
        assert [f["slot_id"] for f in result] == [1, 4]
        assert len(stub.calls) == 2
        # The retry sent a modified file, not the original.
        assert stub.calls[1] != original
        assert _settings_of(stub.calls[1])["time_lapse_gcode"] == ""

    @pytest.mark.asyncio
    async def test_result_is_cached_under_the_original_file_hash(self):
        original = _make_project_3mf({"time_lapse_gcode": "{if timelapse_inline_photo}"})
        body = _make_sliced_3mf(plate_id=1, filaments=[{"id": "1", "type": "PLA", "color": "#000"}])
        stub = _FailThenSucceedService(SlicerApiServerError(_TIMELAPSE_PARSE_ERROR), body)
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            first = await get_preview_filaments(
                kind="library_file",
                source_id=1,
                plate_id=1,
                file_bytes=original,
                file_name="x.3mf",
                api_url="http://sidecar",
            )
            second = await get_preview_filaments(
                kind="library_file",
                source_id=1,
                plate_id=1,
                file_bytes=original,
                file_name="x.3mf",
                api_url="http://sidecar",
            )
        assert second == first
        # Two slices for the first call, none for the second.
        assert len(stub.calls) == 2

    @pytest.mark.asyncio
    async def test_no_retry_for_an_unrelated_failure(self):
        original = _make_project_3mf({"time_lapse_gcode": "{if timelapse_inline_photo}"})
        stub = _FailThenSucceedService(
            SlicerApiUnavailableError("Slicer sidecar unreachable"),
            _make_sliced_3mf(plate_id=1, filaments=[{"id": "1", "type": "PLA", "color": "#000"}]),
        )
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            result = await get_preview_filaments(
                kind="library_file",
                source_id=1,
                plate_id=1,
                file_bytes=original,
                file_name="x.3mf",
                api_url="http://sidecar",
            )
        assert result is None
        assert len(stub.calls) == 1

    @pytest.mark.asyncio
    async def test_a_failing_retry_falls_through_rather_than_raising(self):
        original = _make_project_3mf({"time_lapse_gcode": "{if timelapse_inline_photo}"})

        class _AlwaysFails(_FailThenSucceedService):
            async def slice_without_profiles(self, **kw):
                self.calls.append(kw["model_bytes"])
                raise self.first_error

        stub = _AlwaysFails(SlicerApiServerError(_TIMELAPSE_PARSE_ERROR), b"")
        with patch.object(slice_preview, "SlicerApiService", lambda **kw: stub):
            result = await get_preview_filaments(
                kind="library_file",
                source_id=1,
                plate_id=1,
                file_bytes=original,
                file_name="x.3mf",
                api_url="http://sidecar",
            )
        assert result is None
        assert len(stub.calls) == 2
        # A failed retry must not be cached — the sidecar may be upgraded.
        assert not slice_preview._preview_cache

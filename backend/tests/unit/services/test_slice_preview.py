"""Unit tests for the preview-slice cache.

The preview-slice runs the sidecar's `slice_without_profiles` on an unsliced
project file to extract the per-plate filament list. Results are cached by
``(kind, source_id, plate_id, content_hash)`` with LRU eviction so repeat
modal opens on the same plate are instant.
"""

from __future__ import annotations

import asyncio
import io
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
        self.calls.append(kw)
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

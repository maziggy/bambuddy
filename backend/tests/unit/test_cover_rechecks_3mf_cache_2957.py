"""The cover endpoint stops re-fetching a 3MF another flow already has (#2957).

Both the cover endpoint and the print-start archive flow want the running
print's 3MF, and #972 gave them a shared cache so whichever gets it first hands
it to the other. The cover endpoint looked in that cache exactly once, on the
way in, and then fell into a retry loop that never looked again.

On a P1S the two flows overlap for minutes. The reporter's log has the cover
request starting at 13:31:09, its first attempt burning the whole 90-second
path-walk cap, the archive flow publishing the file to the cache at 13:32:47 --
and the cover's third attempt pulling its own 5,250,969-byte copy of that same
file at 13:33:29, off a printer that was mid-print on the same SD card.

These tests pin the re-check: the file is picked up between attempts, the
retries still happen when there is genuinely nothing to pick up, and a file that
came from the cache is neither re-registered under this endpoint's own name nor
deleted on the way out -- it belongs to the archive flow.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import backend.app.api.routes.printers as printers_mod
from backend.app.api.routes.printers import _produce_cover_image

pytestmark = pytest.mark.asyncio

SUBTASK = "bambu_lab_spool"
COVER_BYTES = b"\x89PNG\r\n\x1a\nplate-1-thumbnail"


def _write_3mf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/plate_1.png", COVER_BYTES)
    return path


@pytest.fixture(autouse=True)
def _clear_cover_state():
    printers_mod._cover_cache.clear()
    printers_mod._cover_404_cache.clear()
    printers_mod._cover_inflight.clear()
    yield
    printers_mod._cover_cache.clear()
    printers_mod._cover_404_cache.clear()
    printers_mod._cover_inflight.clear()


class _Harness:
    """The cover endpoint with its FTP, storage verdict and cache faked out."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.downloads = 0
        self.cache: dict[str, Path] = {}
        self.registered: list[tuple[int, str, Path]] = []
        self.on_download = None
        self.serves_the_file = False
        self.printer = SimpleNamespace(id=1, ip_address="172.25.12.149", access_code="x", model="P1S", name="P1S")

    def _get_cached(self, printer_id, name):
        return self.cache.get("path")

    async def _download(self, ip_address, access_code, remote_paths, local_path, **kwargs):
        self.downloads += 1
        if self.on_download is not None:
            self.on_download(self)
        if self.serves_the_file:
            _write_3mf(local_path)
            return remote_paths[0]
        return None

    async def run(self, **kwargs):
        async def _no_recovery(printer_id, name, path):
            return False

        with (
            patch.object(printers_mod.settings, "archive_dir", self.tmp_path / "archive"),
            patch.object(printers_mod.printer_manager, "get_status", MagicMock(return_value=SimpleNamespace())),
            patch.object(
                printers_mod,
                "print_file_reachable_over_ftp",
                MagicMock(return_value=SimpleNamespace(reachable=True, probe_filename=None, reason="")),
            ),
            patch.object(printers_mod, "get_cached_3mf", self._get_cached),
            patch.object(
                printers_mod,
                "cache_3mf_download",
                lambda pid, name, path: self.registered.append((pid, name, path)),
            ),
            patch.object(printers_mod, "download_file_try_paths_async", self._download),
            patch("backend.app.main.try_recover_fallback_archive", _no_recovery),
            patch.object(printers_mod.asyncio, "sleep", lambda *_: _noop()),
        ):
            return await _produce_cover_image(
                self.printer, 1, SUBTASK, None, "default", None, (SUBTASK, "default"), **kwargs
            )


async def _noop():
    return None


class TestItLooksAgainBetweenAttempts:
    async def test_a_file_published_mid_retry_is_picked_up(self, tmp_path):
        """The reported sequence: the archive flow finishes while this endpoint
        is between retries, and the retry must not spend a second transfer."""
        harness = _Harness(tmp_path)
        source = _write_3mf(tmp_path / "archive" / "temp" / f"{SUBTASK}.gcode.3mf")

        def publish(h):
            h.cache["path"] = source  # the archive flow's download lands

        harness.on_download = publish

        assert await harness.run() == COVER_BYTES
        assert harness.downloads == 1, "the cover re-downloaded a 3MF the cache already held"

    async def test_the_cached_file_is_left_to_its_owner(self, tmp_path):
        """It is the archive flow's temp file. Re-registering it under this
        endpoint's own key would point the cache at bytes it does not own, and
        deleting it would force the archive flow to fetch it again."""
        harness = _Harness(tmp_path)
        source = _write_3mf(tmp_path / "archive" / "temp" / f"{SUBTASK}.gcode.3mf")
        harness.on_download = lambda h: h.cache.__setitem__("path", source)

        await harness.run()

        assert harness.registered == []
        assert source.exists()


class TestWhatItMustNotChange:
    async def test_retries_still_run_when_there_is_nothing_to_pick_up(self, tmp_path):
        """max_retries + 1 attempts, exactly as before -- the re-check must not
        become an early exit for a printer that simply has not answered yet."""
        harness = _Harness(tmp_path)

        with pytest.raises(HTTPException) as exc:
            await harness.run()

        assert exc.value.status_code == 404
        assert harness.downloads == 3

    async def test_a_hit_on_the_way_in_still_skips_ftp_entirely(self, tmp_path):
        harness = _Harness(tmp_path)
        harness.cache["path"] = _write_3mf(tmp_path / "archive" / "temp" / f"{SUBTASK}.gcode.3mf")

        assert await harness.run() == COVER_BYTES
        assert harness.downloads == 0

    async def test_its_own_download_is_still_shared(self, tmp_path):
        """The other half of #972: a cover that really did fetch the bytes must
        still publish them, or the archive flow refetches the same file."""
        harness = _Harness(tmp_path)
        harness.serves_the_file = True

        assert await harness.run() == COVER_BYTES
        assert harness.downloads == 1
        assert [name for _, name, _ in harness.registered] == [f"{SUBTASK}.gcode.3mf"]

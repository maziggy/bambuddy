"""Concurrent cover requests for the same print coalesce into one download (#2572).

The farm dashboard mounts a cover tile per printer card, so several browsers
request the same printer's cover in the same instant. Before this fix each miss
ran the full multi-path FTP lookup + 3MF extraction independently (one observed
live transfer pulled an 81 MB 3MF while real uploads were in flight). Now the
first miss becomes the leader and the rest await its result, then serve from the
cache it filled.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import backend.app.api.routes.printers as printers_mod
from backend.app.api.routes.printers import get_printer_cover


class _FakeSession:
    def __init__(self, printer):
        self._printer = printer

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: self._printer)


@pytest.fixture(autouse=True)
def _clear_cover_state():
    printers_mod._cover_cache.clear()
    printers_mod._cover_404_cache.clear()
    printers_mod._cover_inflight.clear()
    yield
    printers_mod._cover_cache.clear()
    printers_mod._cover_404_cache.clear()
    printers_mod._cover_inflight.clear()


@pytest.mark.asyncio
async def test_concurrent_cover_requests_download_once():
    printer = SimpleNamespace(id=1, ip_address="127.0.0.1", access_code="x", model="X1C", name="P")
    state = SimpleNamespace(subtask_name="job", state="RUNNING")

    produce_calls = {"n": 0}

    async def slow_produce(printer_row, printer_id, subtask_name, view, view_key, plate_num, cache_key):
        produce_calls["n"] += 1
        await asyncio.sleep(0.1)  # hold leadership long enough for followers to attach
        printers_mod._cover_cache.setdefault(printer_id, {})[cache_key] = b"PNGDATA"
        return b"PNGDATA"

    with (
        patch("backend.app.core.database.async_session", lambda: _FakeSession(printer)),
        patch.object(printers_mod.printer_manager, "get_status", MagicMock(return_value=state)),
        patch.object(printers_mod, "resolve_plate_id", MagicMock(return_value=1)),
        patch.object(printers_mod, "_produce_cover_image", slow_produce),
    ):
        responses = await asyncio.gather(*[get_printer_cover(1, None, None) for _ in range(5)])

    assert produce_calls["n"] == 1, "concurrent cover requests each ran their own FTP download"
    assert {bytes(r.body) for r in responses} == {b"PNGDATA"}


@pytest.mark.asyncio
async def test_second_request_serves_from_positive_cache():
    """A follower arriving after the leader filled the cache serves it directly."""
    printer = SimpleNamespace(id=1, ip_address="127.0.0.1", access_code="x", model="X1C", name="P")
    state = SimpleNamespace(subtask_name="job", state="RUNNING")

    produce_calls = {"n": 0}

    async def produce(printer_row, printer_id, subtask_name, view, view_key, plate_num, cache_key):
        produce_calls["n"] += 1
        printers_mod._cover_cache.setdefault(printer_id, {})[cache_key] = b"PNGDATA"
        return b"PNGDATA"

    with (
        patch("backend.app.core.database.async_session", lambda: _FakeSession(printer)),
        patch.object(printers_mod.printer_manager, "get_status", MagicMock(return_value=state)),
        patch.object(printers_mod, "resolve_plate_id", MagicMock(return_value=1)),
        patch.object(printers_mod, "_produce_cover_image", produce),
    ):
        first = await get_printer_cover(1, None, None)
        second = await get_printer_cover(1, None, None)

    assert produce_calls["n"] == 1  # second hit the positive cache
    assert bytes(first.body) == bytes(second.body) == b"PNGDATA"

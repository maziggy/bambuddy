"""One download of a printer file serves the plate picker (TO-C2).

The plate picker asks for the plate list and then one thumbnail per plate,
and each request downloaded the whole 3MF from the printer again: ten plates
of a 60 MB file on an A1 meant eleven full downloads, all at once. Requests for
the same file now share one download, and its bytes serve the requests that
follow for a minute.
"""

import asyncio

import pytest

from backend.app.services import printer_file_cache as cache_mod
from backend.app.services.printer_file_cache import PrinterFileCache

pytestmark = pytest.mark.asyncio


class _Printer:
    """Counts downloads; each one can be held open until released."""

    def __init__(self, data: bytes = b"3mf-bytes") -> None:
        self.data = data
        self.calls = 0
        self.release = asyncio.Event()
        self.release.set()

    async def fetch(self) -> bytes:
        self.calls += 1
        await self.release.wait()
        return self.data


async def test_requests_at_the_same_time_share_one_download():
    cache, printer = PrinterFileCache(), _Printer()
    printer.release.clear()

    tasks = [asyncio.create_task(cache.get(1, "/cache/a.3mf", printer.fetch)) for _ in range(10)]
    await asyncio.sleep(0)
    printer.release.set()

    assert await asyncio.gather(*tasks) == [b"3mf-bytes"] * 10
    assert printer.calls == 1


async def test_a_request_soon_after_is_served_from_memory():
    cache, printer = PrinterFileCache(), _Printer()

    await cache.get(1, "/cache/a.3mf", printer.fetch)
    await cache.get(1, "/cache/a.3mf", printer.fetch)

    assert printer.calls == 1


async def test_it_is_downloaded_again_once_the_minute_is_up(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: now[0])
    cache, printer = PrinterFileCache(), _Printer()

    await cache.get(1, "/cache/a.3mf", printer.fetch)
    now[0] += cache_mod.CACHE_SECONDS + 1
    await cache.get(1, "/cache/a.3mf", printer.fetch)

    assert printer.calls == 2


async def test_files_and_printers_are_kept_apart():
    cache, printer = PrinterFileCache(), _Printer()

    await cache.get(1, "/cache/a.3mf", printer.fetch)
    await cache.get(1, "/cache/b.3mf", printer.fetch)
    await cache.get(2, "/cache/a.3mf", printer.fetch)

    assert printer.calls == 3


async def test_a_failure_is_not_kept():
    cache, printer = PrinterFileCache(), _Printer()
    attempts = []

    async def failing():
        attempts.append(1)
        raise RuntimeError("printer busy")

    with pytest.raises(RuntimeError):
        await cache.get(1, "/cache/a.3mf", failing)
    assert await cache.get(1, "/cache/a.3mf", printer.fetch) == b"3mf-bytes"
    assert len(attempts) == 1 and printer.calls == 1


async def test_a_waiter_that_leaves_does_not_end_the_download_for_the_others():
    cache, printer = PrinterFileCache(), _Printer()
    printer.release.clear()

    first = asyncio.create_task(cache.get(1, "/cache/a.3mf", printer.fetch))
    second = asyncio.create_task(cache.get(1, "/cache/a.3mf", printer.fetch))
    await asyncio.sleep(0)
    first.cancel()
    printer.release.set()

    assert await second == b"3mf-bytes"
    assert printer.calls == 1


async def test_a_deleted_file_is_forgotten():
    cache, printer = PrinterFileCache(), _Printer()

    await cache.get(1, "/cache/a.3mf", printer.fetch)
    cache.forget(1, "/cache/a.3mf")
    await cache.get(1, "/cache/a.3mf", printer.fetch)

    assert printer.calls == 2


async def test_a_deleted_printer_is_forgotten():
    cache, printer = PrinterFileCache(), _Printer()

    await cache.get(1, "/cache/a.3mf", printer.fetch)
    await cache.get(2, "/cache/a.3mf", printer.fetch)
    cache.forget_printer(1)
    await cache.get(1, "/cache/a.3mf", printer.fetch)
    await cache.get(2, "/cache/a.3mf", printer.fetch)

    assert printer.calls == 3


async def test_a_file_too_big_to_keep_is_still_shared_while_it_downloads(monkeypatch):
    monkeypatch.setattr(cache_mod, "MAX_ENTRY_BYTES", 4)
    cache, printer = PrinterFileCache(), _Printer(b"too-big")
    printer.release.clear()

    tasks = [asyncio.create_task(cache.get(1, "/cache/a.3mf", printer.fetch)) for _ in range(3)]
    await asyncio.sleep(0)
    printer.release.set()
    await asyncio.gather(*tasks)
    await cache.get(1, "/cache/a.3mf", printer.fetch)

    assert printer.calls == 2


async def test_the_oldest_files_make_room(monkeypatch):
    monkeypatch.setattr(cache_mod, "MAX_TOTAL_BYTES", 20)
    cache = PrinterFileCache()
    printer = _Printer(b"x" * 8)

    await cache.get(1, "/a", printer.fetch)
    await cache.get(1, "/b", printer.fetch)
    await cache.get(1, "/a", printer.fetch)  # used again: /b is now the oldest
    await cache.get(1, "/c", printer.fetch)  # 24 bytes: /b goes
    assert printer.calls == 3

    await cache.get(1, "/a", printer.fetch)
    assert printer.calls == 3
    await cache.get(1, "/b", printer.fetch)
    assert printer.calls == 4


async def test_a_file_deleted_while_downloading_is_not_kept():
    cache, printer = PrinterFileCache(), _Printer()
    printer.release.clear()

    old = asyncio.create_task(cache.get(1, "/cache/a.3mf", printer.fetch))
    await asyncio.sleep(0)
    cache.forget(1, "/cache/a.3mf")
    printer.release.set()
    await old
    await cache.get(1, "/cache/a.3mf", printer.fetch)

    assert printer.calls == 2


async def test_expired_files_free_their_memory(monkeypatch):
    """Not only when the same file is asked for again: two large previews
    must not stay resident until something pushes them out."""
    now = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: now[0])
    cache = PrinterFileCache()

    await cache.get(1, "/a", _Printer(b"x" * 10).fetch)
    await cache.get(1, "/b", _Printer(b"y" * 10).fetch)
    now[0] += cache_mod.CACHE_SECONDS + 1
    await cache.get(1, "/c", _Printer(b"z" * 3).fetch)

    assert list(cache._entries) == [(1, "/c")]
    assert cache._total == 3


async def test_a_failure_reaches_every_request_waiting_on_it():
    cache, printer = PrinterFileCache(), _Printer()
    printer.release.clear()

    async def failing():
        await printer.release.wait()
        raise RuntimeError("printer busy")

    tasks = [asyncio.create_task(cache.get(1, "/cache/a.3mf", failing)) for _ in range(3)]
    await asyncio.sleep(0)
    printer.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, RuntimeError) for r in results)
    assert cache._total == 0


async def test_a_printer_deleted_during_a_download_keeps_nothing():
    cache, printer = PrinterFileCache(), _Printer()
    printer.release.clear()

    task = asyncio.create_task(cache.get(1, "/cache/a.3mf", printer.fetch))
    await asyncio.sleep(0)
    cache.forget_printer(1)
    printer.release.set()
    await task

    assert cache._entries == {} and cache._total == 0


async def test_files_that_expire_during_a_download_are_freed_when_it_lands(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: now[0])
    cache = PrinterFileCache()
    await cache.get(1, "/a", _Printer(b"x" * 10).fetch)
    slow = _Printer(b"z" * 3)
    slow.release.clear()

    task = asyncio.create_task(cache.get(1, "/c", slow.fetch))
    await asyncio.sleep(0)
    now[0] += cache_mod.CACHE_SECONDS + 1
    slow.release.set()
    await task

    assert list(cache._entries) == [(1, "/c")]
    assert cache._total == 3

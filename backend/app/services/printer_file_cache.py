"""A short-lived, shared copy of printer files for the file browser's previews.

The plate picker asks for the plate list and then one thumbnail per plate, and
each request downloaded the whole 3MF from the printer again: ten plates of a
60 MB file meant eleven full downloads, all opened at once over FTP. Requests
for the same file now share one download, and its bytes serve the requests
that follow for ``CACHE_SECONDS``.

Kept in memory, bounded in total (least recently used goes first) and per file:
a file too big to keep is still shared by the requests waiting on it. A failed
download is never kept. Entries go when the file is deleted through Bambuddy
and when the printer is deleted. A file replaced on the printer by other means
can be served from the old copy for at most ``CACHE_SECONDS``.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

# Long enough for the plate picker's burst of requests and a look at the
# plates, short enough that a file replaced on the printer is soon re-read.
CACHE_SECONDS = 60.0
# Modest, for the 1-2 GB appliance: held only while someone is previewing.
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024

Key = tuple[int, str]


class PrinterFileCache:
    def __init__(self) -> None:
        self._entries: OrderedDict[Key, tuple[float, bytes]] = OrderedDict()
        self._total = 0
        self._in_flight: dict[Key, asyncio.Task[bytes]] = {}
        # Bumped by forget(): a download that started before the file was
        # deleted must not put the old bytes back when it finishes.
        self._generation: dict[Key, int] = {}

    async def get(self, printer_id: int, path: str, fetch: Callable[[], Awaitable[bytes]]) -> bytes:
        """The file's bytes: kept, already downloading, or fetched now.

        ``fetch`` raises on failure; every request waiting on that download
        sees the same exception, and nothing is kept.
        """
        key = (printer_id, path)
        self._sweep()
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            return entry[1]

        task = self._in_flight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(key, fetch, self._generation.get(key, 0)))
            self._in_flight[key] = task
        # Shielded: a request that leaves (its client closed the page) must not
        # end the download the others are waiting on.
        return await asyncio.shield(task)

    async def _fetch(self, key: Key, fetch: Callable[[], Awaitable[bytes]], generation: int) -> bytes:
        # ``generation`` is read when the download is created, not when this
        # first runs: a forget() in between must still win.
        try:
            data = await fetch()
        finally:
            if self._in_flight.get(key) is asyncio.current_task():
                self._in_flight.pop(key, None)
        if self._generation.get(key, 0) == generation:
            self._keep(key, data)
        return data

    def _keep(self, key: Key, data: bytes) -> None:
        size = len(data)
        if size > MAX_ENTRY_BYTES or size > MAX_TOTAL_BYTES:
            return
        self._drop(key)
        self._sweep()
        while self._entries and self._total + size > MAX_TOTAL_BYTES:
            oldest = next(iter(self._entries))
            self._drop(oldest)
        self._entries[key] = (time.monotonic() + CACHE_SECONDS, data)
        self._total += size

    def _sweep(self) -> None:
        """Free every entry whose minute is up, not only the one asked for."""
        now = time.monotonic()
        for key in [key for key, (expires, _data) in self._entries.items() if expires <= now]:
            self._drop(key)

    def _drop(self, key: Key) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total -= len(entry[1])

    def forget(self, printer_id: int, path: str) -> None:
        """Drop a file that changed on the printer (deleted through Bambuddy)."""
        self._forget_key((printer_id, path))

    def forget_printer(self, printer_id: int) -> None:
        """Drop everything kept for a deleted printer."""
        for key in {key for key in [*self._entries, *self._in_flight] if key[0] == printer_id}:
            self._forget_key(key)

    def clear(self) -> None:
        """Drop everything (tests)."""
        self._entries.clear()
        self._total = 0
        self._in_flight.clear()
        self._generation.clear()

    def _forget_key(self, key: Key) -> None:
        self._drop(key)
        # A download still running finishes for its waiters but is not kept,
        # and the next request starts a new one.
        self._in_flight.pop(key, None)
        self._generation[key] = self._generation.get(key, 0) + 1


printer_file_cache = PrinterFileCache()

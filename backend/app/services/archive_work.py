"""Archives that work running in this process holds by id (archive redesign P2).

A merge moves an archive's runs, files and references to another and removes
its folder. Work that holds the archive by its id in memory, and not in the
database, must not have that happen under it: a queue dispatch between
choosing the archive and recording it on its queue item, a timelapse
conversion. Each holds the archive here for as long as it runs; a merge
leaves a held archive out, and marks the archives it merges so a dispatch
leaves them out in turn. Both happen on the one event loop with no await
between the check and the mark, so they never pass each other.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

_held: Counter[int] = Counter()
_merging: set[int] = set()


def hold(archive_id: int) -> None:
    _held[archive_id] += 1


def release(archive_id: int) -> None:
    if _held[archive_id] <= 1:
        _held.pop(archive_id, None)
    else:
        _held[archive_id] -= 1


@contextmanager
def held(archive_id: int) -> Iterator[None]:
    hold(archive_id)
    try:
        yield
    finally:
        release(archive_id)


def is_held(archive_id: int) -> bool:
    return archive_id in _held


def is_merging(archive_id: int) -> bool:
    return archive_id in _merging


@contextmanager
def merging(archive_ids: Iterable[int]) -> Iterator[bool]:
    """Mark ``archive_ids`` as being merged; yields False (and marks nothing)
    when one is held or already being merged."""
    ids = set(archive_ids)
    if any(i in _held or i in _merging for i in ids):
        yield False
        return
    _merging.update(ids)
    try:
        yield True
    finally:
        _merging.difference_update(ids)


def held_while(archive_id: int, work):
    """``work`` (a coroutine) with the archive held until it ends. Held now,
    before the task it is spawned as starts, so no merge between the spawn
    and its first step can take the archive."""
    hold(archive_id)
    return _release_after(archive_id, work)


async def _release_after(archive_id: int, work):
    try:
        return await work
    finally:
        release(archive_id)

"""Per-printer lock for writes to a printer's spool assignments (AMS slots).

on_ams_change serialises its spool-assignment side (auto-unlink stale +
auto-assign new) when MQTT bursts deliver multiple AMS updates for the same
printer in quick succession (~30 ms apart, observed in the wild on H2D + dual
AMS).

Without this serialisation, two concurrent on_ams_change callbacks each read
"no assignment for (printer, ams, tray)", each call auto_assign_spool, and
the second commit hits
  IntegrityError: duplicate key value violates unique constraint
                  "spool_assignment_printer_id_ams_id_tray_id_key"
SQLite's WAL serial-write semantics had been silently swallowing the race
until optional Postgres support landed (asyncpg allows true concurrent
transactions and surfaces the constraint violation).

The assign, unassign and create-from-slot routes write the same rows and take
the same lock (SA-5), before their first write: on SQLite a session that
already holds the write lock would otherwise wait here on an AMS update that
waits for that write lock.

Scope is intentionally narrow: only the DB-mutating blocks are inside the lock.
The Spoolman sync block in on_ams_change stays concurrent because it's
network-bound and idempotent.

A move of a spool (#1574) also takes a per-spool lock, before any printer lock;
see get_spool_move_lock.
"""

import asyncio

ams_assignment_locks: dict[int, asyncio.Lock] = {}
spool_move_locks: dict[tuple[str, int], asyncio.Lock] = {}


def get_ams_assignment_lock(printer_id: int) -> asyncio.Lock:
    """Return the per-printer assignment lock, creating it on first use."""
    lock = ams_assignment_locks.get(printer_id)
    if lock is None:
        lock = asyncio.Lock()
        ams_assignment_locks[printer_id] = lock
    return lock


def get_spool_move_lock(inventory: str, spool_id: int) -> asyncio.Lock:
    """Return the lock moves of one spool take turns on, creating it on first use.

    A move takes the spool out of every slot it holds, and which slots those are
    is only known once read. Two moves of one spool at once each read them
    before the other committed, and both assigned it: the printer locks keep
    them apart only where they share a printer, and on PostgreSQL a DELETE
    doesn't see the row another transaction commits after the DELETE began.
    ``inventory`` ("inventory" or "spoolman") keeps the two id spaces apart.
    Taken before any printer lock and before the move's first write.
    """
    key = (inventory, spool_id)
    lock = spool_move_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        spool_move_locks[key] = lock
    return lock

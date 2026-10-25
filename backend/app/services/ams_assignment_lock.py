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
"""

import asyncio

ams_assignment_locks: dict[int, asyncio.Lock] = {}


def get_ams_assignment_lock(printer_id: int) -> asyncio.Lock:
    """Return the per-printer assignment lock, creating it on first use."""
    lock = ams_assignment_locks.get(printer_id)
    if lock is None:
        lock = asyncio.Lock()
        ams_assignment_locks[printer_id] = lock
    return lock

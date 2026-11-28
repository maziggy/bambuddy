"""Pending loads: assign the spool a SpoolBuddy device holds to the AMS slot it goes into (#3756).

A device that has just identified a spool says so: "if one slot on printer P
is loaded in the next N seconds, this spool went there". Bambuddy then does
the matching every client used to do on its own, once and with the
assignment lock and rules it already has.

How a load is matched:

* When the pending load is made, the printer's empty slots are recorded.
  Only those slots, or slots seen to empty afterwards, can match. So a
  presence blip, a reconnect or the first push after a restart doesn't count,
  and a spool swapped in the same slot does.
* A slot is loaded when its presence bit goes from absent to present
  (``on_ams_presence_change``). The bit changes at once; the AMS change hash
  may change 20 s later or never for an untagged spool.
* Exactly one slot may load. A second one loading while the first is still
  settling ends the pending load without assigning anything.
* After the bit flips, the slot is given time to settle: until its tray
  state is 11 (loaded), or ``SETTLE_TIMEOUT_S``. The AMS reads a Bambu tag
  only after feeding, 15.6 s after insertion on an H2C. Then the tray is read
  again: a slot that now reports a tag is left to the RFID path, one emptied
  again is left alone, and on a printing printer a slot that already has an
  assignment is never replaced. An AMS that is drying doesn't feed, so a
  slot that hasn't settled while its unit dries waits until the pending load
  expires and then ends ``failed``.
* Otherwise the spool is assigned through ``services.slot_assignment``, with
  ``move`` so it leaves any other slot, and the slot gets the spool's own
  settings, as ``POST /inventory/assignments`` gives it: the filament the
  printer fills in for an untagged slot is the slot's last one, not this
  spool's.

Pending loads live in memory only. One printer has at most one, so two
devices can't both be waiting for the same slot; one device has at most one,
and a new one replaces it. After a restart they are gone, and asking for one
answers ``expired``. A finished one keeps its result for ``RESULT_KEEP_S``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from backend.app.core.printer_scope import PrinterScope
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.ams_assignment_lock import get_ams_assignment_lock
from backend.app.services.ams_slot_presence import spool_present
from backend.app.services.spool_tag_matcher import is_valid_tag
from backend.app.utils.ams_drying import is_drying_active

logger = logging.getLogger(__name__)

TIMEOUT_MIN_S = 10
TIMEOUT_MAX_S = 600
TIMEOUT_DEFAULT_S = 60
# How long a loaded slot gets to reach tray state 11 before it is read again.
SETTLE_TIMEOUT_S = 30.0
SETTLE_POLL_S = 1.0
# How long a finished pending load keeps its result for GET.
RESULT_KEEP_S = 300.0

TRAY_LOADED_STATE = 11
PRINTING_STATES = frozenset({"RUNNING", "PAUSE", "PREPARE", "SLICING"})

PENDING = "pending"
ASSIGNED = "assigned"
EXPIRED = "expired"
CANCELLED = "cancelled"
FAILED = "failed"

Slot = tuple[int, int]


class PendingLoadConflict(Exception):
    """Another device already has a pending load on the printer."""

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.device_id = device_id


@dataclass
class PendingLoad:
    device_id: str
    printer_id: int
    spool_id: int
    spoolman: bool
    printer_scope: PrinterScope
    preset_user_id: int | None
    expires_at: float
    # Slots that can match: empty when the load was made, or emptied since.
    candidates: set[Slot]
    state: str = PENDING
    reason: str | None = None
    ams_id: int | None = None
    tray_id: int | None = None
    # Set when assigned: whether the slot's settings reached the printer, and
    # whether they wait for it to be back, as the assign routes report it.
    configured: bool | None = None
    printer_offline: bool | None = None
    ended_at: float | None = None
    # Candidate slots whose presence bit went to present.
    loaded: set[Slot] = field(default_factory=set)
    settle_task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self.state == PENDING

    def as_dict(self, now: float) -> dict[str, Any]:
        out: dict[str, Any] = {
            "device_id": self.device_id,
            "state": self.state,
            "spool_id": self.spool_id,
            "printer_id": self.printer_id,
        }
        if self.state == PENDING:
            out["expires_in_s"] = max(0, round(self.expires_at - now))
        if self.state == ASSIGNED:
            out["ams_id"] = self.ams_id
            out["tray_id"] = self.tray_id
            out["configured"] = self.configured
            out["printer_offline"] = self.printer_offline
        if self.reason:
            out["reason"] = self.reason
        return out


def empty_slots(raw_data: dict | None) -> set[Slot]:
    """The AMS slots whose presence bit says empty. A slot without a bit isn't one."""
    slots: set[Slot] = set()
    units = (raw_data or {}).get("ams") or []
    if isinstance(units, dict):
        units = units.get("ams") or []
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict):
            continue
        for tray in unit.get("tray") or []:
            if spool_present(tray) is False:
                try:
                    slots.add((int(unit["id"]), int(tray["id"])))
                except (KeyError, TypeError, ValueError):
                    continue
    return slots


def _live_tray(raw_data: dict | None, ams_id: int, tray_id: int) -> tuple[dict | None, dict | None]:
    """The unit and the tray the printer reports for a slot now."""
    units = (raw_data or {}).get("ams") or []
    if isinstance(units, dict):
        units = units.get("ams") or []
    for unit in units if isinstance(units, list) else []:
        if isinstance(unit, dict) and str(unit.get("id")) == str(ams_id):
            for tray in unit.get("tray") or []:
                if isinstance(tray, dict) and str(tray.get("id")) == str(tray_id):
                    return unit, tray
            return unit, None
    return None, None


class PendingLoads:
    """Every pending load, by device. Runs on the event loop only."""

    def __init__(self) -> None:
        self._by_device: dict[str, PendingLoad] = {}
        self.clock = time.monotonic

    # ── For the routes ──────────────────────────────────────────────────

    def create(
        self,
        *,
        device_id: str,
        printer_id: int,
        spool_id: int,
        spoolman: bool,
        timeout_s: int,
        printer_scope: PrinterScope,
        preset_user_id: int | None,
        raw_data: dict | None,
    ) -> PendingLoad:
        """Make a pending load, replacing the device's own one.

        Raises PendingLoadConflict when another device has a pending load on
        the printer.
        """
        now = self.clock()
        for other in self._by_device.values():
            self._expire_if_due(other, now)
            if other.active and other.printer_id == printer_id and other.device_id != device_id:
                raise PendingLoadConflict(other.device_id)
        old = self._by_device.get(device_id)
        if old is not None and old.active:
            self._end(old, CANCELLED, "replaced")
        load = PendingLoad(
            device_id=device_id,
            printer_id=printer_id,
            spool_id=spool_id,
            spoolman=spoolman,
            printer_scope=printer_scope,
            preset_user_id=preset_user_id,
            expires_at=now + timeout_s,
            candidates=empty_slots(raw_data),
        )
        self._by_device[device_id] = load
        logger.info(
            "Pending load: device %s, spool %d, printer %d, %d s, %d empty slot(s)",
            device_id,
            spool_id,
            printer_id,
            timeout_s,
            len(load.candidates),
        )
        return load

    def get(self, device_id: str) -> PendingLoad | None:
        load = self._by_device.get(device_id)
        if load is None:
            return None
        now = self.clock()
        self._expire_if_due(load, now)
        if not load.active and load.ended_at is not None and now - load.ended_at > RESULT_KEEP_S:
            del self._by_device[device_id]
            return None
        return load

    def cancel(self, device_id: str) -> PendingLoad | None:
        load = self.get(device_id)
        if load is not None and load.active:
            self._end(load, CANCELLED)
        return load

    def clear(self) -> None:
        for load in self._by_device.values():
            if load.settle_task is not None:
                load.settle_task.cancel()
        self._by_device.clear()

    # ── From the printer ────────────────────────────────────────────────

    async def on_presence_change(self, printer_id: int, ams_id: int, tray_id: int, present: bool) -> None:
        """A spool went into (present) or came out of a slot on a printer."""
        now = self.clock()
        load = next((ld for ld in self._by_device.values() if ld.printer_id == printer_id and ld.active), None)
        if load is None:
            return
        self._expire_if_due(load, now)
        if not load.active:
            return
        slot = (ams_id, tray_id)
        if not present:
            # A slot emptied while waiting can take the spool, as in a swap. A
            # spool pulled out again before its slot settled is not loaded.
            load.candidates.add(slot)
            if slot in load.loaded:
                load.loaded.discard(slot)
                if load.settle_task is not None:
                    load.settle_task.cancel()
                    load.settle_task = None
            return
        if slot not in load.candidates:
            return
        load.loaded.add(slot)
        if len(load.loaded) > 1:
            self._end(load, FAILED, "several_slots_loaded")
            logger.info(
                "Pending load of device %s: %d slots loaded on printer %d, assigning none",
                load.device_id,
                len(load.loaded),
                printer_id,
            )
            return
        from backend.app.core.tasks import spawn_background_task  # noqa: PLC0415

        load.settle_task = spawn_background_task(
            self._settle_and_assign(load, ams_id, tray_id), name=f"pending-load-{load.device_id}"
        )

    # ── Internals ───────────────────────────────────────────────────────

    def _expire_if_due(self, load: PendingLoad, now: float) -> None:
        # A slot already loaded is seen through; the window is for the load.
        if load.active and not load.loaded and now >= load.expires_at:
            self._end(load, EXPIRED)

    def _end(self, load: PendingLoad, state: str, reason: str | None = None) -> None:
        load.state = state
        load.reason = reason
        load.ended_at = self.clock()
        task = load.settle_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _settle_and_assign(self, load: PendingLoad, ams_id: int, tray_id: int) -> None:
        from backend.app.services.printer_manager import printer_manager  # noqa: PLC0415

        settle_until = self.clock() + SETTLE_TIMEOUT_S
        while True:
            await asyncio.sleep(SETTLE_POLL_S)
            if not load.active:
                return
            status = printer_manager.get_status(load.printer_id)
            unit, tray = _live_tray(getattr(status, "raw_data", None), ams_id, tray_id)
            if tray is not None and tray.get("state") == TRAY_LOADED_STATE:
                break
            now = self.clock()
            if now < settle_until:
                continue
            if is_drying_active(unit):
                # A drying AMS doesn't feed; wait for the load, up to the window.
                if now < load.expires_at:
                    continue
                self._end(load, FAILED, "not_loaded")
                logger.info("Pending load of device %s: slot never loaded (AMS drying)", load.device_id)
                return
            break
        try:
            await self._assign(load, ams_id, tray_id)
        except Exception:
            logger.exception("Pending load of device %s: assigning failed", load.device_id)
            if load.active:
                self._end(load, FAILED, "error")

    async def _assign(self, load: PendingLoad, ams_id: int, tray_id: int) -> None:
        from backend.app.core.database import async_session  # noqa: PLC0415
        from backend.app.services.printer_manager import printer_manager  # noqa: PLC0415

        async with async_session() as db:
            # Read the slot again under the lock the AMS change handler and the
            # RFID auto-assign write it with.
            async with get_ams_assignment_lock(load.printer_id):
                if not load.active:
                    return
                status = printer_manager.get_status(load.printer_id)
                _, tray = _live_tray(getattr(status, "raw_data", None), ams_id, tray_id)
                skip = None
                if tray is None or spool_present(tray) is not True:
                    skip = "slot_emptied"
                elif is_valid_tag(str(tray.get("tag_uid") or ""), str(tray.get("tray_uuid") or "")):
                    skip = "slot_has_tag"
                elif getattr(status, "state", None) in PRINTING_STATES and await self._slot_assigned(
                    db, load, ams_id, tray_id
                ):
                    skip = "printing"
                if skip:
                    self._end(load, FAILED, skip)
                    logger.info(
                        "Pending load of device %s: printer %d AMS%d-T%d not assigned (%s)",
                        load.device_id,
                        load.printer_id,
                        ams_id,
                        tray_id,
                        skip,
                    )
                    return
            try:
                configured, printer_offline = await self._assign_spool(db, load, ams_id, tray_id)
            except HTTPException as exc:
                self._end(load, FAILED, str(exc.detail))
                logger.info("Pending load of device %s: assigning refused: %s", load.device_id, exc.detail)
                return
        if not load.active:
            return
        load.ams_id, load.tray_id = ams_id, tray_id
        load.configured, load.printer_offline = configured, printer_offline
        self._end(load, ASSIGNED)
        logger.info(
            "Pending load of device %s assigned spool %d to printer %d AMS%d-T%d",
            load.device_id,
            load.spool_id,
            load.printer_id,
            ams_id,
            tray_id,
        )
        from backend.app.core.websocket import ws_manager  # noqa: PLC0415

        await ws_manager.broadcast(
            {
                "type": "spool_auto_assigned",
                "printer_id": load.printer_id,
                "ams_id": ams_id,
                "tray_id": tray_id,
                "spool_id": load.spool_id,
                "device_id": load.device_id,
                "source": "pending_load",
            }
        )

    @staticmethod
    async def _slot_assigned(db, load: PendingLoad, ams_id: int, tray_id: int) -> bool:
        model = SpoolmanSlotAssignment if load.spoolman else SpoolAssignment
        row = await db.execute(
            select(model.printer_id).where(
                model.printer_id == load.printer_id, model.ams_id == ams_id, model.tray_id == tray_id
            )
        )
        return row.first() is not None

    @staticmethod
    async def _assign_spool(db, load: PendingLoad, ams_id: int, tray_id: int) -> tuple[bool, bool]:
        """Assign the spool; returns (configured, printer_offline)."""
        from backend.app.api.routes.inventory import load_preset_lookup_user  # noqa: PLC0415
        from backend.app.services.slot_assignment import assign_local_spool, assign_spoolman_spool  # noqa: PLC0415

        preset_user = await load_preset_lookup_user(db, load.preset_user_id)
        if load.spoolman:
            from backend.app.api.routes.spoolman_inventory import _get_client  # noqa: PLC0415

            client = await _get_client(db)
            result = await assign_spoolman_spool(
                db,
                client,
                spoolman_spool_id=load.spool_id,
                printer_id=load.printer_id,
                ams_id=ams_id,
                tray_id=tray_id,
                move=True,
                printer_scope=load.printer_scope,
                current_user=preset_user,
            )
            return result.configured, result.printer_offline
        outcome = await assign_local_spool(
            db,
            spool_id=load.spool_id,
            printer_id=load.printer_id,
            ams_id=ams_id,
            tray_id=tray_id,
            move=True,
            printer_scope=load.printer_scope,
            preset_user=preset_user,
        )
        return outcome.configured, outcome.printer_offline


pending_loads = PendingLoads()

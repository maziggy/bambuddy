"""Print scheduler service - processes the print queue."""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, false, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.core.database import async_session, run_with_retry
from backend.app.core.tasks import spawn_background_task
from backend.app.core.websocket import ws_manager
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.printer import Printer
from backend.app.models.scheduled_drying import ScheduledDrying
from backend.app.models.settings import Settings
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services import drying_preflight, print_dispatch_context
from backend.app.services.bambu_ftp import (
    FtpFailureReport,
    UploadCancelled,
    cache_3mf_download,
    delete_file_async,
    describe_upload_failure,
    get_ftp_retry_settings,
    upload_file_async,
    with_ftp_retry,
)
from backend.app.services.bambu_mqtt import _RACK_NOZZLE_IDS, HMS_MQTT_VERIFY_FAILED, resolve_rack_plan_mapping
from backend.app.services.filament_deficit import compute_deficit_for_queue_item
from backend.app.services.finance_budget import (
    create_budget_reservation,
    release_budget_reservation,
    validate_print_budget,
)
from backend.app.services.ha_sensor_manager import ha_sensor_manager
from backend.app.services.notification_service import notification_service
from backend.app.services.print_cost_estimate import estimate_queue_source_cost
from backend.app.services.printer_manager import (
    printer_manager,
    supports_airduct,
    supports_chamber_heater,
    supports_chamber_temp,
    supports_drying,
    supports_drying_while_printing,
)
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.utils.color_utils import perceptual_color_distance
from backend.app.utils.filament_types import canonical_filament_type
from backend.app.utils.filename import derive_remote_filename
from backend.app.utils.local_time import utcnow_naive
from backend.app.utils.printer_models import (
    is_gcode_compatible,
    is_nozzle_rack_model,
    normalize_printer_model,
)
from backend.app.utils.threemf_tools import (
    extract_rack_plan_from_3mf,
    extract_slot_extruders_from_3mf,
)

logger = logging.getLogger(__name__)

# Dispatch-toast progress throttling (#1625 follow-up). Mirrors the legacy
# background_dispatch.py upload_progress_callback (200 ms time gate + 256 KB
# byte gate) from before the scheduler unification. Time gate keeps small
# files from going silent (a single 8 KB chunk fires once and that's it);
# byte gate caps the broadcast rate on slow LAN where 200 ms covers many
# chunks. uploaded >= total always emits so the bar closes cleanly even on
# sub-200 ms files.
_DISPATCH_PROGRESS_BYTE_STEP = 256 * 1024
_DISPATCH_PROGRESS_MIN_INTERVAL_SECS = 0.2
# How far back chamber temperature samples are retained. 2h comfortably spans
# any soak a user can configure (capped at 30 min) plus the plate-clearing gap
# before the next print.
_CHAMBER_HISTORY_TTL_SECONDS = 7200
# Fallback for `queue_keep_warm_max_minutes` — how long the bed may be held
# warm on a printer sitting in FINISH before the heaters are shut off. Users
# who clear plates promptly will want far less than this; it is deliberately
# the cautious end, since the cost of it being too short is only a re-soak.
_KEEP_WARM_MAX_MINUTES_DEFAULT = 120
# Max acceptable gap between two consecutive chamber samples before we treat
# the older one as belonging to a separate observation run (printer went
# offline and came back). Above ~30s cadence with a safety margin.
_CHAMBER_SAMPLE_MAX_GAP_SECONDS = 60.0
# How long the chamber must read below target before we accept that it really
# cooled. An enclosed chamber's thermal mass cannot lose and regain several
# degrees quickly: measured on an X1C, falling from ~55°C to below 48°C took
# 23-73 minutes (~0.2 C/min), while the fastest drop ever recorded was
# 27 C/min — impossible for that mass, i.e. a sensor artifact. Brief
# sub-target readings are therefore a door opening or noise, not lost soak,
# and a plate swap (exactly when keep-warm is running) produces one. Six
# minutes clears the longest such artifact observed (~5 min once bracketed by
# its neighbouring samples) and still sits far below the 23-minute floor for
# real cooling.
_CHAMBER_DIP_GRACE_SECONDS = 360.0
# How often the preheat stage re-checks that the item it is heating for still
# wants to be printed. Cancelling only writes `status` to the database — it
# cannot interrupt a coroutine parked in `asyncio.sleep` — so without this the
# heaters run for the rest of max_wait + soak (45 min at the default settings)
# and the printer stays in `busy_printers`, blocking every other queued item
# behind a print that is not happening.
_PREHEAT_CANCEL_CHECK_SECONDS = 10.0
# set_airduct_mode modeId values (bambu_mqtt.py:5937 — 0 cooling, 1 heating).
_AIRDUCT_MODE_COOLING = 0
_AIRDUCT_MODE_HEATING = 1

# How long a queue row may stay 'printing' while its printer sits in a terminal
# state before the scheduler closes it itself (#2829).
#
# A real completion arrives within seconds of the printer going terminal, so
# five minutes is far outside the normal path — this only ever sees a row whose
# completion was refused or never delivered. It is the whole cost of the
# failure to the user, though: a stranded row blocks every later job for that
# printer, so it should not be raised without reason.
_STRANDED_PRINTING_GRACE_SECONDS = 300.0

# gcode_state values that mean the print is over, mapped to the queue status
# they imply. Mirrors the mapping in bambu_mqtt's completion detection
# (FINISH -> completed, FAILED -> failed, anything else terminal -> aborted,
# which the queue calls cancelled) so a recovered row cannot disagree with one
# closed by the normal path.
_TERMINAL_STATE_QUEUE_STATUS = {
    "FINISH": "completed",
    "FAILED": "failed",
    "IDLE": "cancelled",
}


def _terminal_queue_status(state) -> str | None:
    """Queue status implied by *state*, or None if the print is not over.

    None for a disconnected printer as well as a busy one: a printer we are not
    talking to has a stale ``state`` field that proves nothing about what it is
    doing now.
    """
    if state is None or not getattr(state, "connected", False):
        return None
    return _TERMINAL_STATE_QUEUE_STATUS.get(getattr(state, "state", None))


@dataclass
class _KeepWarmEntry:
    """Per-printer keep-warm state.

    - ``started``: monotonic time when keep-warm first fired for this printer.
      Used by the max-duration timeout.
    - ``held_target``: last bed target we successfully published. On release
      we only send bed-off when firmware still reports this value, so a user
      or subsequent print that changed the target isn't clobbered.
    - ``expired``: latched True when the max-duration timeout fires. Prevents
      re-engagement (and re-seeding of ``started``) on subsequent ticks. The
      release sweep drops the entry entirely once the printer leaves the
      candidate set.
    """

    started: float
    held_target: int
    expired: bool = False


# Auto-drying re-arm guards (#2770).
#
# An AMS reports HIGHER relative humidity while it is warm than once it has
# cooled: measured on an H2D/AMS 2 Pro at 10-13% cold against 15-20% throughout
# every drying cycle, and the same unit read 16-20% across a full 12 h dry. A
# threshold set inside that band therefore cannot be satisfied while the box is
# hot, and the firmware is free to end a cycle whenever it decides the filament
# is dry — so the next 30 s pass sees dry_time 0 with the reading still above
# the threshold and arms another cycle. The reporter's log has five 12-hour
# cycles armed inside four hours, one of them six seconds after the previous
# ended.
#
# The cooldown stops the six-second re-arm; the unproductive-cycle cap stops the
# loop. Neither ever stops a running cycle — both only gate STARTING one, so a
# manual or firmware-run dry is untouched.
AUTO_DRY_REARM_COOLDOWN_SECONDS = 30 * 60
AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES = 2

# How long a finished scheduled drying row is kept before it is pruned.
SCHEDULED_DRYING_RETENTION_DAYS = 7
# How often that prune actually runs. The check itself is called on every queue
# pass — every 3s while dispatching — and issuing the DELETE is what begins a
# write transaction, which SQLite serialises against every other writer. Rows
# only become prunable a week after they finish, so anything short of hourly is
# paying that cost for nothing.
SCHEDULED_DRYING_PRUNE_INTERVAL_SECONDS = 60 * 60


class _UploadProgressBridge:
    """Thread-safe bridge from ``upload_file_async`` to the WS broadcaster.

    ``upload_file_async`` runs the FTP transfer in an executor thread and
    invokes its ``progress_callback`` from that thread, so the callback
    body cannot ``await`` directly. This bridge captures the asyncio loop
    at construction (on the scheduler thread) and uses
    ``run_coroutine_threadsafe`` to hop back. The byte/time throttle
    matches the legacy background_dispatch.py path 1:1 so the toast feels
    identical to the pre-#1625 experience.

    Failures inside the emit are swallowed — progress is a UX nicety, the
    upload itself must not fail because of a WS hiccup.
    """

    def __init__(self, user_id: int | None, queue_item_id: int):
        self._user_id = user_id
        self._queue_item_id = queue_item_id
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._last_emit_bytes = 0
        self._last_emit_monotonic = 0.0
        self._has_emitted = False

    def __call__(self, bytes_transferred: int, total_bytes: int) -> None:
        if self._loop is None or total_bytes <= 0:
            return
        now = time.monotonic()
        # Mirrors legacy bg-dispatch: emit if first call OR upload complete
        # OR 200 ms elapsed OR ≥256 KB transferred since last emit. Two of
        # the four matter most: first-call so the user sees something even
        # for sub-chunk-size files; uploaded >= total so the bar locks at
        # 100% even when the throttle would otherwise eat it.
        should_emit = (
            not self._has_emitted
            or bytes_transferred >= total_bytes
            or now - self._last_emit_monotonic >= _DISPATCH_PROGRESS_MIN_INTERVAL_SECS
            or bytes_transferred - self._last_emit_bytes >= _DISPATCH_PROGRESS_BYTE_STEP
        )
        if not should_emit:
            return
        self._has_emitted = True
        self._last_emit_bytes = bytes_transferred
        self._last_emit_monotonic = now
        try:
            asyncio.run_coroutine_threadsafe(
                ws_manager.send_queue_item_upload_progress(
                    user_id=self._user_id,
                    queue_item_id=self._queue_item_id,
                    bytes_transferred=bytes_transferred,
                    total_bytes=total_bytes,
                ),
                self._loop,
            )
        except Exception:
            pass  # progress is best-effort, never block the upload


# Bambu firmware states that mean the project_file has actually been accepted
# and the printer is now processing / running / paused mid-print. Used by the
# dispatch watchdog (#1370): a transition into one of these states means the
# print landed, anything else (e.g. FINISH -> IDLE after the user dismisses
# a post-print prompt) is NOT a valid "command landed" signal even though the
# state value did change. SLICING is included because some firmwares park
# briefly in SLICING between PREPARE and RUNNING while parsing the g-code.
_ACTIVE_PRINT_STATES: frozenset[str] = frozenset({"PREPARE", "SLICING", "RUNNING", "PAUSE"})

# How many times the start-watchdog may revert an item to 'pending' before it
# gives up and fails the row instead (#2555). Each attempt costs a full 3MF
# re-upload plus the watchdog's wait, so a wedged printer left to retry forever
# both never recovers and starves the other printers of dispatch slots. Three
# is chosen to clear the transient causes the watchdog already recovers from —
# a lost MQTT publish on a half-broken session (#887/#936) is fixed by the
# force-reconnect on the very next attempt — while still bounding the loop.
DISPATCH_MAX_ATTEMPTS = 3


@dataclass(slots=True)
class _ModelCandidate:
    """One (file, printer model) pair the model-based matcher may try.

    Model-based assignment used to have exactly one of these per item, held
    directly in the item's own columns. Cross-model queue items (#671) have
    several, held in ``print_queue_variants``. Both shapes are normalised into
    this so the matching, the cross-model gate and the waiting-reason handling
    are written once and an item without variants provably takes the same path
    it took before variants existed.

    ``variant`` is None for the item's own columns and set for a real variant
    row, which is what :meth:`PrintScheduler._resolve_variant` writes onto the
    item once that candidate wins.
    """

    target_model: str | None
    sliced_for: str | None
    required_filament_types: str | None
    filament_overrides: str | None
    variant: "PrintQueueVariant | None" = None


def _sliced_for_model(archive, library_file) -> str | None:
    """Model a 3MF declares it was sliced for, from whichever source holds it."""
    if archive is not None:
        return archive.sliced_for_model
    if library_file is not None and library_file.file_metadata:
        return library_file.file_metadata.get("sliced_for_model")
    return None


def _filament_constraints(candidate: _ModelCandidate) -> tuple[list[str] | None, list[dict] | None]:
    """The filament a candidate needs, as ``(types, overrides)``.

    Both columns are JSON text written by the slicer step. Malformed content is
    treated as no constraint rather than as an error: a job whose overrides
    cannot be parsed still prints, it just gets no filament-based narrowing.

    Overrides carry their own types, so the returned type list is the union of
    the two — an override on one slot must not drop the requirements of the
    slots it says nothing about.

    Shared by the matcher and by the smart-plug wake step so both ask a printer
    for the same filament (#2876). Waking a printer the matcher would then
    reject on colour is the bug this exists to prevent.
    """
    required_types = None
    if candidate.required_filament_types:
        try:
            required_types = json.loads(candidate.required_filament_types)
        except json.JSONDecodeError:
            pass

    filament_overrides = None
    if candidate.filament_overrides:
        try:
            filament_overrides = json.loads(candidate.filament_overrides)
        except json.JSONDecodeError:
            pass

    effective_types = required_types
    if filament_overrides:
        override_types = sorted({o["type"] for o in filament_overrides if "type" in o})
        if override_types:
            effective_types = sorted(set(required_types or []) | set(override_types))

    return effective_types, filament_overrides


def _candidates_for(item: PrintQueueItem) -> list[_ModelCandidate]:
    """Candidate files for ``item``, best first.

    An item with no variant rows yields exactly one candidate built from its own
    columns — the pre-#671 behaviour, unchanged.

    Variants come back least-attempted first, ties broken by the user's
    ``position``. On the first pass every count is zero, so this is purely the
    user's priority order. After a start-watchdog bounce the printer that failed
    drops behind, so the next lap tries the other machine rather than spending the
    item's whole retry budget on the one that is wedged. Once every candidate has
    been tried equally often they cycle again, which keeps the item-level
    ``DISPATCH_MAX_ATTEMPTS`` bound from #2555 intact — a job with alternatives
    still gives up, it just does not give up without trying them.
    """
    if not item.variants:
        if not item.archive_id and not item.library_file_id:
            # Nothing to print at all. Dispatching would fail deep in the upload
            # on "No archive_id or library_file_id"; the caller holds the item
            # with an explanation instead.
            return []
        return [
            _ModelCandidate(
                target_model=item.target_model,
                sliced_for=_sliced_for_model(item.archive, item.library_file),
                required_filament_types=item.required_filament_types,
                filament_overrides=item.filament_overrides,
            )
        ]

    # Drop candidates whose file is gone or in the trash. Both are reachable and
    # neither is covered by the schema: library deletes are soft (the row lives
    # on with ``deleted_at`` set, which no foreign key can express), and SQLite
    # ships with ``PRAGMA foreign_keys`` off, so the ON DELETE CASCADE never
    # fires there and a hard delete leaves the variant row pointing at nothing.
    usable = [v for v in item.variants if v.library_file is not None and v.library_file.deleted_at is None]

    ordered = sorted(usable, key=lambda v: (v.attempt_count or 0, v.position, v.id))
    return [
        _ModelCandidate(
            target_model=v.target_model,
            sliced_for=_sliced_for_model(None, v.library_file),
            required_filament_types=v.required_filament_types,
            filament_overrides=v.filament_overrides,
            variant=v,
        )
        for v in ordered
    ]


def _collapse_waiting_reasons(per_model: list[tuple[str | None, str]]) -> str | None:
    """Fold one waiting reason per candidate into a single line for the item.

    A cross-model item produces a reason per candidate, and pasting them
    together unlabelled reads as gibberish ("No idle printer; PETG not loaded"
    — on which machine?). Each reason is prefixed with its model, except in the
    single-candidate case where the item already displays its target model and
    the prefix would be noise.

    Identical reasons collapse rather than repeat, so three idle-less models
    read as one clause.

    When *every* candidate is merely busy the parts are joined with the ``" | "``
    separator :meth:`PrintScheduler._is_busy_only` already parses, and left
    unprefixed. That case must keep testing busy-only: a fleet that is simply
    printing needs no user action, and labelling the clauses would turn each pass
    over a two-model item into a "job waiting" notification.
    """
    reasons = [(model, reason) for model, reason in per_model if reason]
    if not reasons:
        return None
    if len(reasons) == 1:
        return reasons[0][1]

    distinct = list(dict.fromkeys(reason for _model, reason in reasons))
    if len(distinct) == 1:
        return distinct[0]

    if all(PrintScheduler._is_busy_only(reason) for _model, reason in reasons):
        return " | ".join(distinct)

    return "; ".join(f"{model or 'unassigned'}: {reason}" for model, reason in reasons)


def _candidate_model_label(candidates: list[_ModelCandidate]) -> str | None:
    """Human label for the models an item is waiting on ("H2S or H2C").

    Notifications take a single target model. For a cross-model item the item's
    own ``target_model`` is whichever variant happens to be first, which reads as
    a lie once it is the H2C that actually runs — so name all of them.
    """
    models = list(dict.fromkeys(c.target_model for c in candidates if c.target_model))
    if not models:
        return None
    return " or ".join(models)


def _mapping_is_all_unresolved(mapping: list | None) -> bool:
    """True if ``mapping`` is a non-empty list whose every entry is the
    unresolved sentinel (-1 / None) — i.e. no required slot ever matched a tray.

    Such a mapping is a bug artifact: a frontend status-load race can serialize
    ``[-1]`` before the printer's AMS trays are known (#2589). It must be
    recomputed from live status at dispatch rather than trusted, otherwise it
    reaches the print command and is silently downgraded to external-spool mode.

    A partially-resolved mapping (``[-1, -1, 5]`` where slot 3 matched, or a
    padding ``-1`` for a slot this plate does not print) is NOT unresolved. An
    explicit external selection (``>= 254``) is NOT unresolved either — those
    keep their meaning.
    """
    if not isinstance(mapping, list) or not mapping:
        return False
    return all(t is None or (isinstance(t, int) and t < 0) for t in mapping)


def _mqtt_commands_rejected(status) -> bool:
    """True when the printer is currently reporting that it refused a command.

    ``HMS_MQTT_VERIFY_FAILED`` means the firmware's authorization check rejected
    a control command it could not verify. Queries still answer, so the printer
    looks connected and idle while project_file, gcode_line and
    ams_change_filament are all dropped — no amount of waiting or re-uploading
    changes that (#2732).

    Tolerates a missing status and errors without a ``full_code`` (the 8-char
    ``print_error`` path builds HMSError differently), so this is safe to call on
    every watchdog poll.
    """
    for err in getattr(status, "hms_errors", None) or []:
        if getattr(err, "full_code", "") == HMS_MQTT_VERIFY_FAILED:
            return True
    return False


def _drying_ams_ids(status) -> list[int]:
    """AMS unit ids currently running a drying cycle, per firmware telemetry.

    ``dry_time`` is minutes remaining, so >0 is the firmware's own statement that
    a cycle is active. Used by the dispatch watchdog to say *why* a print never
    started (#2758) — it is a diagnostic, not a gate.

    Deliberately not used to block or stop drying before dispatch. This printer
    class supports drying concurrently with an active print
    (``supports_drying_while_printing``), so drying is not incompatible with
    printing in general; what #2758 shows is one X2D refusing to *begin* a print
    while two AMS units were drying, one of them without its external PSU. Until
    it is known whether the blocker is drying itself or the power budget
    (``dry_sf_reason`` 1 / 8), acting on this would tear down drying that the
    hardware is perfectly happy to continue.
    """
    ids: list[int] = []
    for unit in (getattr(status, "raw_data", None) or {}).get("ams") or []:
        if not isinstance(unit, dict):
            continue
        try:
            if int(unit.get("dry_time") or 0) > 0:
                ids.append(int(unit.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return ids


def _parse_diameter(raw) -> float | None:
    """``"0.4"`` → ``0.4``; anything unparseable or non-positive → None."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _nozzle_info_by_id(status) -> dict[int, dict]:
    """Index ``PrinterState.nozzle_rack`` by nozzle id.

    The field name is historical: on the H2 series ``nozzle_info`` carries an
    entry for *every* nozzle the printer knows about — the L/R hotends under
    ids 0/1 and, on a rack model, the dock positions under ids 16-21. Only the
    latter are the rack proper; :func:`_rack_nozzle_diameters` and
    :func:`_installed_nozzle_diameters` each take the half they need.
    """
    by_id: dict[int, dict] = {}
    for entry in getattr(status, "nozzle_rack", None) or []:
        if not isinstance(entry, dict):
            continue
        try:
            by_id[int(entry.get("id"))] = entry
        except (TypeError, ValueError):
            continue
    return by_id


# What the firmware puts in a nozzle's serial number when the carriage or dock
# is empty. Measured on an H2C at idle, where the rack-side hotend had parked
# its nozzle back in the rack (#2885).
_EMPTY_NOZZLE_SERIAL = "N/A"


def _nozzle_is_mounted(entry: dict | None) -> bool:
    """Whether a ``nozzle_info`` entry describes hardware that is actually there.

    A hotend entry is reported whether or not a nozzle is mounted, and an empty
    carriage keeps the *last* nozzle's diameter — measured on an H2C at idle,
    where id 1 read ``diameter "0.4"`` with ``max_temp 0``, ``serial_number
    "N/A"`` and ``wear 0`` because that hotend had parked its nozzle back in
    the dock. So the diameter is not a presence signal (#2885).

    Emptiness has to be stated, not merely unstated. The serial must be the
    firmware's explicit ``"N/A"`` marker *and* the temperature rating must be
    absent — an empty serial only means the printer didn't say, and a firmware
    that reports neither field normalises to exactly that. Treating "didn't
    say" as "empty" would silently switch the #1899 guard off on any such
    machine, so everything we cannot positively call empty counts as mounted.
    """
    if entry is None:
        return True
    serial = str(entry.get("serial_number") or "").strip().upper()
    if serial != _EMPTY_NOZZLE_SERIAL:
        return True
    try:
        max_temp = float(entry.get("max_temp") or 0)
    except (TypeError, ValueError):
        return True
    return max_temp > 0


def _installed_nozzle_diameters(status) -> list[float]:
    """Parse the mounted nozzle diameters from a PrinterState (#1899).

    Returns the diameters the printer actually reports (e.g. [0.4] single-nozzle,
    [0.4, 0.6] dual-nozzle), skipping the empty-string defaults that populate a
    NozzleInfo before MQTT fills it in. An empty list means "the printer hasn't
    told us its nozzle hardware" — callers must treat that as unknown, not as a
    mismatch, so we never block a print on missing data.

    A hotend whose ``nozzle_info`` entry says nothing is mounted is skipped even
    though ``nozzles`` still carries a diameter for it: that value is stale, and
    counting it would let a slice match a nozzle the machine does not have
    (#2885). Printers that report no ``nozzle_info`` at all are unaffected.
    """
    info = _nozzle_info_by_id(status)
    diameters: list[float] = []
    for index, nozzle in enumerate(getattr(status, "nozzles", None) or []):
        raw = getattr(nozzle, "nozzle_diameter", "") or ""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and _nozzle_is_mounted(info.get(index)):
            diameters.append(value)
    return diameters


def _rack_nozzle_diameters(status) -> list[float]:
    """Diameters sitting in the tool-changer rack, nearest dock first (#2885).

    Keyed off the nozzle ids themselves rather than the printer model: only a
    rack machine ever reports ids 16-21, so there is no model registry to keep
    in sync. An empty dock is simply absent from the payload — measured on an
    H2C whose R2 (id 17) was empty and unlisted while R1/R3/R4/R5/R6 were all
    present — so appearing here already means "a nozzle is in that dock".

    ``stat`` is deliberately not interpreted: its values aren't known, and
    reading it wrongly could hide a nozzle the printer would happily fetch.
    """
    diameters: list[float] = []
    for nozzle_id, entry in sorted(_nozzle_info_by_id(status).items()):
        if nozzle_id not in _RACK_NOZZLE_IDS:
            continue
        # PrinterState spells it "diameter"; the REST schema renames it to
        # "nozzle_diameter". Accept either so a caller holding the serialised
        # shape gets the same answer.
        value = _parse_diameter(entry.get("diameter") or entry.get("nozzle_diameter"))
        if value is not None:
            diameters.append(value)
    return diameters


def _format_diameters(diameters: list[float]) -> str:
    """``[0.4, 0.4, 0.6]`` → ``"0.4mm / 0.6mm"``, in first-seen order.

    Deduplicated because a loaded rack holds several nozzles of the same size,
    and "0.4mm / 0.4mm / 0.4mm / 0.6mm / 0.2mm" tells the reader nothing the
    short form doesn't. Matching still runs over the full list.
    """
    return " / ".join(f"{d:g}mm" for d in dict.fromkeys(diameters))


def _nozzle_mismatch_message(
    sliced_nozzle: float | None,
    installed: list[float],
    rack: list[float] | None = None,
) -> str | None:
    """Return an actionable error message when the sliced nozzle can't be
    printed on any nozzle the machine can reach, else None (#1899).

    Fail-safe: returns None whenever we lack the data to judge — no sliced
    diameter, or the printer reported no nozzles — so a print is only ever
    blocked on a POSITIVE mismatch. On dual-nozzle printers a match against
    EITHER installed nozzle passes (a 0.6 slice is fine if one hotend is 0.6).
    The 0.05 tolerance absorbs float noise while staying well inside the 0.2
    gap between adjacent nozzle sizes (0.2/0.4/0.6/0.8).

    *rack* holds the diameters parked in a tool-changer dock (H2C). Those count
    as reachable: the printer fetches one as part of starting the print, so a
    slice that matches a docked nozzle is not a mismatch. Without this the
    guard blocked every job whose nozzle happened not to be on a hotend at
    dispatch time — on a rack loaded with 0.2/0.4/0.6 that meant only the
    diameter already mounted could ever print, and the user had to fetch the
    nozzle by hand on the printer's own UI first (#2885).
    """
    reachable = [*installed, *(rack or [])]
    if not sliced_nozzle or not reachable:
        return None
    if any(abs(d - sliced_nozzle) < 0.05 for d in reachable):
        return None
    where = f"{_format_diameters(installed)} installed" if installed else "no nozzle mounted"
    if rack:
        where += f" and {_format_diameters(rack)} in the nozzle rack"
    return (
        f"File sliced for a {sliced_nozzle:g}mm nozzle, but the printer has "
        f"{where}. Re-slice for an available nozzle, or fit the matching "
        f"nozzle before printing."
    )


def _describe_filament(entry: dict, nozzle_key: str) -> str:
    """One-line "PETG #000000 (left nozzle)" for an error message (#2771).

    Shared by the required and loaded sides, which name their extruder
    differently: a 3MF requirement carries ``nozzle_id``, a loaded tray carries
    ``extruder_id``. Both are MQTT extruder ids — 0 is the right/main nozzle,
    1 the left/deputy — and both are absent on single-nozzle printers, where
    naming a nozzle would be noise.
    """
    parts = [(entry.get("type") or "filament").upper()]
    if entry.get("color"):
        parts.append(str(entry["color"]))
    nozzle = entry.get(nozzle_key)
    if nozzle == 0:
        parts.append("(right nozzle)")
    elif nozzle == 1:
        parts.append("(left nozzle)")
    return " ".join(parts)


def _unmatched_filament_message(required: list[dict], loaded: list[dict]) -> str:
    """Explain that nothing loaded matches what the file needs (#2771).

    Only ever built for a printer with no AMS, where the loaded list is short
    enough to quote in full and there is no "load another spool and hit Resume"
    recovery — the external spool holder is all there is, so the user needs to
    be told which filament to put on it.
    """
    want = ", ".join(_describe_filament(r, "nozzle_id") for r in required)
    have = ", ".join(_describe_filament(f, "extruder_id") for f in loaded)
    return (
        f"No filament loaded on this printer matches the file. It needs {want}; "
        f"the printer has {have} and no AMS. Load the required filament on the "
        f"external spool holder, or send this job to a printer that has it."
    )


class PrintScheduler:
    """Background scheduler that processes the print queue."""

    # Built-in drying presets per filament type (from BambuStudio filament profiles)
    # Format: { n3f_temp, n3s_temp, n3f_hours, n3s_hours }
    DEFAULT_DRYING_PRESETS: dict[str, dict[str, int]] = {
        "PLA": {"n3f": 45, "n3s": 45, "n3f_hours": 12, "n3s_hours": 12},
        "PETG": {"n3f": 65, "n3s": 65, "n3f_hours": 12, "n3s_hours": 12},
        "TPU": {"n3f": 65, "n3s": 75, "n3f_hours": 12, "n3s_hours": 18},
        "ABS": {"n3f": 65, "n3s": 80, "n3f_hours": 12, "n3s_hours": 8},
        "ASA": {"n3f": 65, "n3s": 80, "n3f_hours": 12, "n3s_hours": 8},
        "PA": {"n3f": 65, "n3s": 85, "n3f_hours": 12, "n3s_hours": 12},
        "PC": {"n3f": 65, "n3s": 80, "n3f_hours": 12, "n3s_hours": 8},
        "PVA": {"n3f": 65, "n3s": 85, "n3f_hours": 12, "n3s_hours": 18},
    }

    def __init__(self):
        self._running = False
        self._check_interval = 30  # seconds
        # After a pass that actually dispatched something, loop again almost
        # immediately instead of sleeping the full interval (#2555). A dispatch
        # changes printer state — a batch launch fans out over several passes as
        # printers free up, a wedged head-of-line job reverts to pending, an
        # upload slot opens — and the next batch of ready work should not have to
        # wait 30 s behind an idle sleep. When a pass dispatches nothing (all
        # pending items are behind printers that are genuinely busy printing),
        # there is nothing to react to, so we fall back to the normal interval;
        # that also means this can never tight-loop, since fast ticks only
        # continue while dispatches keep happening and the queue is draining.
        self._fast_check_interval = 3  # seconds
        self._power_on_wait_time = 180  # seconds to wait for printer after power on (3 min)
        self._power_on_check_interval = 10  # seconds between connection checks
        # Printers whose class-target power-on failed, mapped to the monotonic
        # time their cool-off expires (#2786).
        #
        # Without this, one printer with an unreachable plug starves every
        # sibling of its model forever: the wake step walks candidates in id
        # order, spends the pass's single attempt on the same broken printer
        # every time, and the healthy one two slots down is never reached. It
        # also costs a full ``_power_on_wait_time`` out of every 30 s pass,
        # which delays the whole queue, not just this job.
        #
        # Entries expire on read rather than being cleared on success: a printer
        # inside its cool-off is skipped before the power-on is reached, so a
        # live entry can never be overwritten by a success anyway. A printer
        # that comes back by any other route stops being a wake candidate the
        # moment it connects.
        self._wake_failures: dict[int, float] = {}
        self._wake_failure_cooloff = 600  # seconds
        # Track which printers are currently auto-drying (printer_id -> start timestamp)
        self._drying_in_progress: dict[int, float] = {}
        # Per-AMS memory of the auto-drying cycles WE armed, keyed by
        # (printer_id, ams_id) (#2770). Entries only exist between arming a
        # cycle and the humidity finally coming down, so the normal steady
        # state is an empty dict. Fields:
        #   running      — a cycle we armed is (or should be) on the firmware
        #   ended_at     — monotonic when we observed that cycle end
        #   unproductive — consecutive armed cycles that ended with the reading
        #                  still above the threshold
        #   suspended    — we have stopped arming this unit and said so
        self._auto_dry_units: dict[tuple[int, int], dict[str, object]] = {}
        # Printers with a "running" scheduled drying row (#2638). Rebuilt from the
        # DB on every _check_scheduled_dryings call so route-side cancels show up.
        # Auto-drying's stop-all branches must not stop or untrack these printers;
        # both features share _drying_in_progress.
        self._scheduled_drying_printer_ids: set[int] = set()
        # Monotonic stamp of the last scheduled-drying prune. None = never, so
        # the first pass after a restart reaps anything left behind.
        self._last_scheduled_drying_prune: float | None = None
        # Defensive in-memory dispatch hold (#1157): a printer that just received
        # a project_file command must not get a second dispatch until either it
        # transitions out of pre_state OR the hard timeout expires. The H2D Pro
        # can take 80–210 s to flip FINISH→PREPARE after project_file, and
        # during that window the DB busy_printers seed is empirically unreliable
        # (multi-plate batches double-/triple-dispatched onto the same printer
        # 30 s apart). Keyed by printer_id; cleared by the watchdog on success
        # or revert.
        # printer_id -> (monotonic_started_at, pre_state, pre_subtask_id)
        self._dispatch_holds: dict[int, tuple[float, str, str | None]] = {}
        # Minimum cooldown between dispatches to the same printer (covers the
        # H2D's project_file digestion window).
        self._dispatch_min_cooldown = 60.0
        # Hard timeout — drop the hold even if we never observed a transition,
        # so a lost MQTT session can't lock a printer out of the queue forever.
        # Matches the watchdog timeout (90 s) plus a safety margin so the
        # watchdog runs first on the unhappy path.
        self._dispatch_max_hold = 180.0
        # Refillable upload pool (#2602). Items whose FTP upload was launched by
        # an earlier pass and is still running. `_start_print` flips the row
        # pending -> printing only *after* the upload completes, so until then
        # the row stays `pending`: each tick, check_queue excludes these
        # item_ids from re-selection and their printers from new dispatch /
        # auto-drying, and launches only `limit - len(_inflight)` new uploads so
        # freed slots refill on the next fast tick. check_queue is the sole,
        # sequential caller and the prune done-callbacks run in the same
        # event-loop thread, so this dict needs no lock.
        # item_id -> (task, printer_id)
        self._inflight: dict[int, tuple[asyncio.Task, int | None]] = {}
        # Expected prints registered by `_start_print` that have not yet had a
        # print command sent. Populated at registration, dropped once
        # `start_print()` succeeds, and rolled back by `_dispatch_one` on every
        # other exit. Same threading argument as `_inflight` above: one
        # sequential caller, callbacks on the same loop, so no lock.
        # item_id -> (printer_id, remote_filename, archive_id)
        self._unconfirmed_expected_print: dict[int, tuple[int, str, int]] = {}
        # Budget reservations created for a dispatch whose print command has
        # not been confirmed yet. `_dispatch_one` releases these on every
        # unsuccessful exit; a successful start removes the item id and leaves
        # the reservation for finance_billing to consume with the archive.
        self._unconfirmed_budget_reservations: set[int] = set()
        # Chamber temperature history for smart soak-time reduction.
        # printer_id -> deque of (monotonic_timestamp, celsius) sampled each scheduler tick.
        # Entries older than _chamber_history_ttl are pruned on write.
        self._chamber_history: dict[int, deque[tuple[float, float]]] = {}
        self._chamber_history_ttl = _CHAMBER_HISTORY_TTL_SECONDS
        # Per-printer keep-warm state (see `_KeepWarmEntry` at module top).
        # Populated on engagement in `_apply_keep_warm`, cleared by
        # `_sweep_keep_warm` when the printer leaves the candidate set (or
        # when a gate setting toggles off mid-hold — the release publishes
        # bed → 0 first).
        self._keep_warm: dict[int, _KeepWarmEntry] = {}
        # Preheat rollback registry: printer_id -> subset of
        # {"bed", "chamber", "airduct"} listing which preheat commands
        # actually fired for the in-flight dispatch. `_dispatch_one` unwinds
        # every entry still present at exit unless the print successfully
        # started, so a failed upload / cancel / exception never leaves the
        # printer heating for a job that isn't happening.
        self._preheat_pin: dict[int, set[str]] = {}
        # Bed target (°C) that the pinned "bed" entry above actually set, so the
        # rollback can tell its own target from one someone else has since
        # chosen — the same guard `_release_keep_warm` applies to a keep-warm
        # hold. Written wherever `"bed"` joins the pin, evicted alongside it.
        self._preheat_pin_bed: dict[int, int] = {}
        # Item ids whose in-flight dispatch has been cancelled or deleted while
        # its preheat was still holding at temperature. Set by
        # `notify_dispatch_cancelled` from the queue routes, consumed by
        # `_preheat_sleep`, and cleared when the dispatch exits.
        self._cancelled_dispatches: set[int] = set()
        # printer_id -> monotonic time it was first seen terminal while one of
        # its queue rows was still 'printing'. Reset by any non-terminal
        # observation, so it measures an unbroken run rather than a total.
        # In-memory on purpose: a restart re-arms the grace period, which only
        # delays a recovery that is already the exceptional path (#2829).
        self._terminal_since: dict[int, float] = {}

    async def run(self):
        """Main loop - check queue every interval."""
        self._running = True
        logger.info("Print scheduler started")

        await self._clear_stale_dispatch_claims(at_startup=True)

        while self._running:
            dispatched = False
            try:
                self._sample_chamber_temps()
                # No-op while any upload is in flight; on a quiet tick it releases
                # a claim whose best-effort clear failed (e.g. the database was
                # briefly unreachable), instead of leaving the row wedged until
                # the next restart.
                await self._clear_stale_dispatch_claims()
                await self._close_stranded_printing_items()
                dispatched = await self.check_queue()
            except Exception as e:
                logger.error("Scheduler error: %s", e)

            # Re-check quickly after a productive pass so a draining batch does
            # not stall behind the idle interval; otherwise sleep normally (#2555).
            await asyncio.sleep(self._fast_check_interval if dispatched else self._check_interval)

    async def _close_stranded_printing_items(self) -> None:
        """Close a ``printing`` row the completion event never closed (#2829).

        ``on_print_complete`` refuses to close a row when the completion's
        subtask name disagrees with the file the row was dispatched with, so a
        completion meant for something else (the printer's own
        ``auto_pa_line_calib_mode`` run, say) cannot end someone's job early.
        The refusal has no way back, though: nothing else ever closes the row,
        and ``check_queue`` treats every ``printing`` row as a busy printer, so
        one bad comparison wedges that printer's queue until a human presses
        cancel. That is what #2829's reporters hit, and the guard's own
        docstring already called stranding the worse of the two failures.

        This is the way back. When a row has been ``printing`` while its
        printer sat in a terminal state for the whole grace period, the print
        is over however the event was read, and the row is closed with the
        status the printer's own state implies.

        Deliberately conservative:

        * Only a connected printer counts. A disconnected one has a stale
          ``state`` and proves nothing.
        * The clock is reset by any non-terminal observation, so this cannot
          fire on a printer that is merely between stages.
        * The grace period is far longer than the gap between a printer
          finishing and its completion arriving, so the normal path always
          wins the race and this only ever sees genuine strandings.

        What it does *not* do is replay the completion's side effects --
        notifications, billing, auto-off. It restores the queue, which is the
        harm being undone; the archive was updated by the normal path
        regardless, since only the queue block refuses. A recovery that
        silently re-fired notifications minutes late would be its own bug.
        """
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(PrintQueueItem)
                    .where(PrintQueueItem.status == "printing")
                    .where(PrintQueueItem.printer_id.is_not(None))
                )
                items = list(result.scalars().all())
                if not items:
                    self._terminal_since.clear()
                    return

                now = time.monotonic()
                seen_printers: set[int] = set()
                closed = False
                for item in items:
                    printer_id = item.printer_id
                    seen_printers.add(printer_id)
                    state = printer_manager.get_status(printer_id)
                    status = _terminal_queue_status(state)
                    if status is None:
                        self._terminal_since.pop(printer_id, None)
                        continue
                    since = self._terminal_since.setdefault(printer_id, now)
                    if now - since < _STRANDED_PRINTING_GRACE_SECONDS:
                        continue

                    item.status = status
                    item.completed_at = datetime.now(timezone.utc)
                    closed = True
                    logger.warning(
                        "Queue item %s was still 'printing' after printer %s reported %s for %.0fs — "
                        "closing it as %s. Its completion event was never matched to it, which blocks "
                        "every later job for this printer (#2829).",
                        item.id,
                        printer_id,
                        getattr(state, "state", None),
                        now - since,
                        status,
                    )
                if closed:
                    await db.commit()
                for printer_id in list(self._terminal_since):
                    if printer_id not in seen_printers:
                        del self._terminal_since[printer_id]
        except Exception as e:
            # Best-effort, same as the claim sweep beside it: a recovery path
            # that can itself break the scheduler loop is worse than the strand.
            logger.error("Stranded-item sweep failed: %s", e)

    async def _clear_stale_dispatch_claims(self, *, at_startup: bool = False) -> None:
        """Clear dispatch claims with no live dispatch coroutine behind them (#2615).

        A claim is only ever held by a live dispatch coroutine, so when this
        process has nothing in ``_inflight`` every ``dispatching_at`` in the table
        is stale. At startup that is trivially true — no coroutine survives a
        restart. It is equally true on any later tick where no upload is running,
        which is what makes this safe to repeat rather than only run once.

        Repeating it matters because ``_clear_dispatch_claim`` is best-effort: if
        the database is briefly unreachable at exactly the moment dispatch ends,
        the claim survives and the row is wedged out of the selection query. That
        used to last until the next restart (#2702 follow-up, seen when
        PostgreSQL refused a connection mid-dispatch).

        ``_inflight`` is populated when the task is spawned, before the coroutine
        claims its row, and pruned by a done-callback that cannot run before the
        coroutine's own ``finally`` — so "claim present, nothing in flight" has no
        race window and needs no age threshold. A size-derived upload deadline
        (``max(600s, size/25KB/s)``) has no safe fixed bound anyway.
        """
        if self._inflight:
            return
        try:
            async with async_session() as db:
                res = await db.execute(
                    update(PrintQueueItem).where(PrintQueueItem.dispatching_at.is_not(None)).values(dispatching_at=None)
                )
                await db.commit()
                if res.rowcount:
                    logger.info(
                        "Cleared %d orphaned dispatch claim(s)%s (#2615)",
                        res.rowcount,
                        " at startup" if at_startup else "",
                    )
        except Exception as exc:
            logger.error("Failed to clear orphaned dispatch claims: %s", exc)

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        logger.info("Print scheduler stopped")

    async def check_queue(self) -> bool:
        """Check for prints ready to start.

        Returns True if this pass dispatched at least one item, so the caller
        can loop again quickly instead of sleeping the full interval (#2555).
        """
        async with async_session() as db:
            # Check if shortest-job-first scheduling is enabled
            sjf_enabled = await self._get_bool_setting(db, "queue_shortest_first")

            # Get all pending items, ordered by printer and position (or SJF order)
            if sjf_enabled:
                # SJF: group by printer (and target_model for model-based jobs),
                # then items already jumped get top priority (starvation guard),
                # then sort by print_time ascending. Items with no print time go last.
                result = await db.execute(
                    select(PrintQueueItem)
                    .where(PrintQueueItem.status == "pending")
                    # Never re-select a row a dispatch worker has already claimed
                    # (#2615) — belt-and-suspenders with the _inflight exclusion
                    # below, and the guard that lets an orphaned claim be ignored
                    # until startup reconciliation clears it.
                    .where(PrintQueueItem.dispatching_at.is_(None))
                    # archive/library_file are read by the cross-model gate
                    # (#2578); eager-load once per pass instead of a lazy-load
                    # (which would raise in async) per item.
                    .options(
                        selectinload(PrintQueueItem.archive),
                        selectinload(PrintQueueItem.library_file),
                        # Cross-model candidates (#671), plus each candidate's file
                        # for the same cross-model gate. Lazy-loading either would
                        # raise in async.
                        selectinload(PrintQueueItem.variants).selectinload(PrintQueueVariant.library_file),
                    )
                    .order_by(
                        PrintQueueItem.printer_id,
                        PrintQueueItem.target_model,
                        PrintQueueItem.been_jumped.desc(),
                        PrintQueueItem.print_time_seconds.asc().nullslast(),
                        PrintQueueItem.position,
                    )
                )
            else:
                result = await db.execute(
                    select(PrintQueueItem)
                    .where(PrintQueueItem.status == "pending")
                    # Skip rows already claimed by a dispatch worker (#2615).
                    .where(PrintQueueItem.dispatching_at.is_(None))
                    .options(
                        selectinload(PrintQueueItem.archive),
                        selectinload(PrintQueueItem.library_file),
                        # Cross-model candidates (#671), plus each candidate's file
                        # for the same cross-model gate. Lazy-loading either would
                        # raise in async.
                        selectinload(PrintQueueItem.variants).selectinload(PrintQueueVariant.library_file),
                    )
                    .order_by(PrintQueueItem.printer_id, PrintQueueItem.position)
                )
            items = list(result.scalars().all())

            # Drop rows whose upload is still in flight from an earlier pass
            # (#2602). They stay `pending` until the upload finishes, so without
            # this a fast tick would re-select and re-dispatch the same row.
            # Belt-and-suspenders with the printer exclusion below.
            if self._inflight:
                inflight_ids = set(self._inflight)
                items = [it for it in items if it.id not in inflight_ids]

            # Read plate-clear setting once per queue check. Default MUST be
            # False to match the schema (SettingsSchema.require_plate_clear
            # defaults False) and the frontend (toggle + card badge both treat a
            # missing value as off). When no settings row exists, a True default
            # here re-enabled the plate-clear gate the UI showed as disabled,
            # blocking dispatch to FINISH-state printers forever with no UI path
            # to clear it (#1865).
            require_plate_clear = await self._get_bool_setting(db, "require_plate_clear", default=False)

            # Dispatch and track scheduled drying runs (#2638)
            await self._check_scheduled_dryings(db)

            if not items:
                # No dispatchable pending items — still check auto-drying on idle
                # printers, but keep any printer with an upload still in flight
                # from an earlier pass out of it (#2602): its print is imminent,
                # so it must not be auto-dried in the gap before the row flips to
                # printing. Report the pass as productive while uploads run so the
                # loop stays on the fast interval.
                #
                # Also release any keep-warm holds that got orphaned by the queue
                # emptying — the normal sweep in `_apply_keep_warm` is skipped by
                # this early return, so call it directly with an empty candidate
                # set. Without this, a printer whose queued item was cancelled or
                # deleted would keep its bed at target until the max-duration
                # timeout expired.
                self._sweep_keep_warm(active_candidates=set(), dispatched=set())
                inflight_printers = {pid for (_task, pid) in self._inflight.values() if pid is not None}
                await self._check_auto_drying(db, [], inflight_printers)
                return bool(self._inflight)

            logger.info(
                "Queue check: found %d pending items: %s",
                len(items),
                [(i.id, i.printer_id, i.archive_id, i.library_file_id) for i in items],
            )

            # Seed busy_printers with printers that already have an item in 'printing'
            # status. _is_printer_idle() alone is not sufficient as a dispatch gate —
            # on H2D / P1 series the MQTT state transition from IDLE to RUNNING can
            # lag several seconds behind the print command, so the next check_queue
            # tick still sees IDLE and would double-dispatch onto the same printer.
            # Without this guard, two pending items targeting the same printer
            # (e.g. a batch with quantity>1) both end up in 'printing' status —
            # surfaced via the "BUG: Multiple queue items" warning in on_print_complete.
            busy_result = await db.execute(
                select(PrintQueueItem.printer_id)
                .where(PrintQueueItem.status == "printing")
                .where(PrintQueueItem.printer_id.is_not(None))
            )
            busy_printers: set[int] = {pid for (pid,) in busy_result.all() if pid is not None}

            # Defense-in-depth (#1157): augment busy_printers with any printer
            # still in its post-dispatch hold window. Empirically, the DB seed
            # above can miss in-flight items in a multi-plate batch — same-file
            # plates were being dispatched 30 s apart while the H2D was still
            # digesting the first project_file. The hold is keyed in-memory and
            # released by the watchdog on the success path, so it adds a layer
            # that doesn't depend on DB row visibility or completion-callback
            # timing.
            for held_printer_id in list(self._dispatch_holds.keys()):
                if self._printer_in_dispatch_hold(held_printer_id):
                    busy_printers.add(held_printer_id)

            # Exclude printers whose upload is still in flight from an earlier
            # pass (#2602). The row is `pending` until the upload finishes and
            # the printing-state seed / dispatch hold above only arm once the
            # upload completes, so this is what holds the printer (and, via
            # busy_printers, its auto-drying) out of the pass during the upload.
            for _task, inflight_pid in self._inflight.values():
                if inflight_pid is not None:
                    busy_printers.add(inflight_pid)

            # Snapshot taken here, before the item loop adds anything (#2801).
            #
            # The three sources above all mean the same thing: a print on this
            # printer is running or imminent. Everything the loop adds below
            # means only "the queue could not dispatch to it this pass", which
            # is a different statement -- a printer waiting on a plate-clear
            # acknowledgment, an offline printer, one with no matching file.
            #
            # Auto-drying must only see the first kind. Reading the whole set
            # as "is currently printing" is what put a plate-held printer down
            # the mid-print path: it capped the drying temperature, logged the
            # cycle as (mid-print), and skipped the very gate that was supposed
            # to hold it. The interlock block below already documents the same
            # hazard and works around it by staying out of busy_printers; this
            # generalises that workaround instead of repeating it per case.
            dispatching_printers: set[int] = set(busy_printers)

            # Printers held by a Home Assistant sensor interlock (#1148) — an
            # enclosure door left open, say. The fixed-printer branch turns
            # this into a waiting_reason the user can act on; the model-based
            # branch hides these printers from the matcher so an "Any <model>"
            # job runs on a sibling instead of queueing behind the held one.
            #
            # Deliberately NOT merged into busy_printers, even though that set
            # already means "unavailable this pass". _check_auto_drying reads
            # it as "is currently printing" and would put an idle-but-held
            # printer down the mid-print drying path, which caps the drying
            # temperature and skips the queue-only gating. A held printer is
            # idle; it should dry exactly as it did before.
            #
            # Only sensors we actually read and found alerting appear here; see
            # ha_sensor_manager.blocked_printers. A Home Assistant that is down
            # holds nothing.
            interlocked: dict[int, str] = {}
            try:
                interlocked = await ha_sensor_manager.blocked_printers(db)
            except Exception as e:
                # Never let the interlock stop the queue running. A broken
                # lookup means no holds, not no dispatches.
                logger.warning("Home Assistant interlock check failed: %s", e)
                interlocked = {}

            # Printers a smart plug can bring back, read once for the whole pass
            # (#2786). Used by the model-based branch both to word "Offline" in
            # the waiting reason and to decide what the wake step may switch on.
            wakeable_printer_ids = await self._wakeable_printer_ids(db)

            # At most one power-on per queue check. Each one blocks this loop
            # for the boot wait, so a queue of ten class-targeted jobs must not
            # switch on ten printers inside a single pass — the next pass wakes
            # the next one (#2786).
            power_on_attempted = False

            # Log skip reasons once per queue check (not per item)
            skip_reasons: dict[str, int] = {}

            # Items selected for dispatch in this pass, one per printer. The
            # loop below only *decides* — the uploads happen afterwards, in
            # parallel (#2555). See _dispatch_selected().
            dispatch_ids: list[int] = []

            # Library rows queued with `cleanup_library_after_dispatch` (the
            # printer-card "upload and print" flow) are CONSUMED by the dispatch
            # that prints them: the row is deleted and the 3MF is unlinked from
            # disk. That was safe only because dispatch was serial. Run two of
            # them against the same row at once and the second DELETE matches no
            # row (StaleDataError), and the winner's unlink can pull the file out
            # from under the loser's in-flight upload.
            #
            # Only the cleanup flag mutates the row. An ordinary library print
            # just reads it, so the common fan-out — one file, many printers,
            # which is exactly the reporter's workload — still goes out fully in
            # parallel. Narrow the guard to the mutating case; do not serialise
            # the case the whole fix exists for.
            dispatch_libs: set[int] = set()
            consumed_libs: set[int] = set()

            def _library_row_conflict(candidate: PrintQueueItem) -> bool:
                """True if dispatching `candidate` now would race another item's cleanup."""
                lib_id = candidate.library_file_id
                if lib_id is None:
                    return False
                if candidate.cleanup_library_after_dispatch:
                    # We would delete a row someone else in this pass is reading.
                    return lib_id in dispatch_libs
                # Someone else in this pass will delete the row out from under us.
                return lib_id in consumed_libs

            def _claim_library_row(candidate: PrintQueueItem) -> None:
                lib_id = candidate.library_file_id
                if lib_id is None:
                    return
                dispatch_libs.add(lib_id)
                if candidate.cleanup_library_after_dispatch:
                    consumed_libs.add(lib_id)

            for item in items:
                # Check scheduled time first (scheduled_time is stored in UTC from ISO string)
                if item.scheduled_time:
                    sched = item.scheduled_time
                    if sched.tzinfo is None:
                        sched = sched.replace(tzinfo=timezone.utc)
                    if sched > datetime.now(timezone.utc):
                        skip_reasons["scheduled_future"] = skip_reasons.get("scheduled_future", 0) + 1
                        continue

                # Skip items that require manual start
                if item.manual_start:
                    skip_reasons["manual_start"] = skip_reasons.get("manual_start", 0) + 1
                    continue

                if item.printer_id:
                    # Held by a sensor interlock (#1148). Checked before the
                    # busy_printers test that would otherwise swallow it
                    # silently — "waiting for a printer" and "waiting for you
                    # to shut the enclosure" need to read differently, and only
                    # one of them is something the user can fix.
                    #
                    # The interlock is the only thing that writes a
                    # waiting_reason on this branch — the model-based branch
                    # nulls it at the moment it assigns a printer — so any
                    # reason still standing once the hold lifts is stale and is
                    # cleared here. Doing it at dispatch instead would leave a
                    # shut door reading "Waiting on Enclosure Door" for as long
                    # as the printer stayed busy with something else.
                    interlock_reason = interlocked.get(item.printer_id)
                    reason = f"Waiting on {interlock_reason}" if interlock_reason else None
                    if item.waiting_reason != reason:
                        item.waiting_reason = reason
                        await db.commit()
                    if interlock_reason:
                        skip_reasons["sensor_interlock"] = skip_reasons.get("sensor_interlock", 0) + 1
                        continue

                    # Specific printer assignment (existing behavior)
                    if item.printer_id in busy_printers:
                        continue

                    # Check if printer is idle
                    printer_idle = self._is_printer_idle(item.printer_id, require_plate_clear)
                    printer_connected = printer_manager.is_connected(item.printer_id)

                    # If printer not connected, try to power on via smart plug
                    if not printer_connected:
                        plugs = await self._get_smart_plugs(db, item.printer_id)
                        auto_on_plugs = [p for p in plugs if p.auto_on and p.enabled]
                        if auto_on_plugs:
                            logger.info("Printer %s offline, attempting to power on via smart plug(s)", item.printer_id)
                            # Power on using the plug that actually feeds the printer, and
                            # wait for it to boot on that one only (#2629).
                            primary_plug = self._pick_power_plug(auto_on_plugs)
                            powered_on = await self._power_on_and_wait(primary_plug, item.printer_id, db)
                            if powered_on:
                                # Also turn on any remaining auto_on plugs (e.g., filter)
                                for extra_plug in [p for p in auto_on_plugs if p.id != primary_plug.id]:
                                    try:
                                        service = await smart_plug_manager.get_service_for_plug(extra_plug, db)
                                        await service.turn_on(extra_plug)
                                        logger.info(
                                            "Also powered on plug '%s' for printer %s", extra_plug.name, item.printer_id
                                        )
                                    except Exception as e:
                                        logger.warning("Failed to power on extra plug '%s': %s", extra_plug.name, e)
                                printer_connected = True
                                printer_idle = self._is_printer_idle(item.printer_id, require_plate_clear)
                            else:
                                logger.warning("Could not power on printer %s via smart plug", item.printer_id)
                                busy_printers.add(item.printer_id)
                                continue
                        else:
                            # No plug or auto_on disabled
                            busy_printers.add(item.printer_id)
                            continue

                    # Check if printer is idle (busy with another print)
                    if not printer_idle:
                        busy_printers.add(item.printer_id)
                        continue

                    # Drying blocks the queue, if the user asked it to. A hold
                    # is a skip like any other, so it belongs here with the
                    # rest of the availability checks.
                    if self._drying_in_progress.get(item.printer_id) and await self._get_bool_setting(
                        db, "queue_drying_block"
                    ):
                        busy_printers.add(item.printer_id)
                        continue

                    # Check condition (previous print success)
                    if item.require_previous_success:
                        if not await self._check_previous_success(db, item):
                            item.status = "skipped"
                            item.error_message = "Previous print failed or was aborted"
                            item.completed_at = datetime.now(timezone.utc)
                            await db.commit()
                            logger.info("Skipped queue item %s - previous print failed", item.id)

                            # Send notification
                            job_name = await self._get_job_name(db, item)
                            printer = await self._get_printer(db, item.printer_id)
                            await notification_service.on_queue_job_skipped(
                                job_name=job_name,
                                printer_id=item.printer_id,
                                printer_name=printer.name if printer else "Unknown",
                                reason="Previous print failed or was aborted",
                                db=db,
                            )
                            continue

                    # Resolve the AMS mapping when it's missing OR unresolved
                    # (all -1). A stored all-[-1] mapping is a bug artifact — a
                    # frontend status-load race can persist [-1] (#2589) — and
                    # must be recomputed from live trays rather than trusted.
                    unmappable = await self._ensure_ams_mapping(db, item.printer_id, item)
                    if unmappable:
                        await self._fail_unmappable_item(db, item, item.printer_id, unmappable)
                        continue

                    # Filament-deficit pre-dispatch check (#1496). If the
                    # assigned spool can't satisfy any required slot grams,
                    # promote the item to manual_start so the user must
                    # acknowledge via the ▶ button (which re-checks live).
                    if await self._block_on_filament_deficit(db, item):
                        continue

                    # Hold this item back for the next pass rather than racing
                    # another dispatch over the same transient library row. The
                    # printer is still marked busy so a later item does not jump
                    # its place in this printer's queue.
                    if _library_row_conflict(item):
                        skip_reasons["library_row_in_use"] = skip_reasons.get("library_row_in_use", 0) + 1
                        busy_printers.add(item.printer_id)
                        continue

                    # Print takes priority: stop a cycle Bambuddy armed, now
                    # that this item is definitely going out.
                    #
                    # Placement is the whole point (#2801). This used to sit up
                    # with the availability checks, inside the not-idle branch
                    # -- so it fired only on the passes where the print was NOT
                    # going to start, and never on the ones where it was.
                    # Drying is not one of the things `_is_printer_idle` looks
                    # at, so a stop could never have unblocked that printer
                    # anyway; the cycle was spent for nothing, auto-drying
                    # re-armed on the next tick, and a plate left
                    # unacknowledged turned that into a loop on the scheduler
                    # interval. Every skip between there and here -- a failed
                    # previous print, an unmappable item, a filament deficit, a
                    # contested library row -- is another way to lose a cycle
                    # for a print that never happens, which is why this waits
                    # until the decision is actually made.
                    if self._drying_in_progress.get(
                        item.printer_id
                    ) and not await self._drying_may_continue_through_print(db, item.printer_id):
                        await self._stop_drying(item.printer_id)

                    # Queue the dispatch instead of running it here — see
                    # _dispatch_selected(). busy_printers still gets the printer
                    # immediately, so nothing else in this pass can target it.
                    _claim_library_row(item)
                    dispatch_ids.append(item.id)
                    busy_printers.add(item.printer_id)

                    # SJF starvation guard: mark items that were jumped
                    if sjf_enabled and item.print_time_seconds is not None:
                        for other in items:
                            if (
                                other.id != item.id
                                and other.status == "pending"
                                and other.printer_id == item.printer_id
                                and not other.been_jumped
                                and other.position < item.position
                                and (
                                    other.print_time_seconds is None
                                    or other.print_time_seconds > item.print_time_seconds
                                )
                            ):
                                other.been_jumped = True
                        await db.commit()

                elif item.target_model or item.variants:
                    # Model-based assignment - find any idle printer of matching model.
                    # A plain model-based item has exactly one candidate, built from
                    # its own columns. A cross-model item (#671) has one per sliced
                    # variant and takes the first that matches, walking them in the
                    # user's priority order so the pick is reproducible when more
                    # than one printer is free in the same pass.
                    candidates = _candidates_for(item)
                    printer_id = None
                    chosen: _ModelCandidate | None = None
                    per_model_reasons: list[tuple[str | None, str]] = []
                    # Candidates that cleared the cross-model gate below. The
                    # smart-plug wake step may only consider these — waking a
                    # printer for a file that can never legally run on it is
                    # worse than not waking at all (#2786).
                    wakeable_candidates: list[_ModelCandidate] = []

                    if not candidates:
                        # Every candidate file has been deleted or trashed out from
                        # under this item. Hold it with something the user can act
                        # on rather than letting it look dispatchable forever.
                        per_model_reasons.append(
                            (
                                item.target_model,
                                "Every file for this job has been deleted — add a file back or remove the item",
                            )
                        )

                    for candidate in candidates:
                        effective_types, filament_overrides = _filament_constraints(candidate)

                        # Cross-model safety gate (#2578): never hand a 3MF sliced
                        # for an incompatible model to a printer, no matter how the
                        # row got into the DB (old rows, direct API writes). Held
                        # as pending with an actionable waiting_reason — the user
                        # fixes it by editing the item's target model.
                        if not is_gcode_compatible(candidate.sliced_for, candidate.target_model):
                            per_model_reasons.append(
                                (
                                    candidate.target_model,
                                    f"File was sliced for {candidate.sliced_for}, which is not compatible with "
                                    f"{candidate.target_model} — edit the item and fix its target model",
                                )
                            )
                            skip_reasons["sliced_model_mismatch"] = skip_reasons.get("sliced_model_mismatch", 0) + 1
                            continue

                        wakeable_candidates.append(candidate)
                        match_id, match_reason = await self._find_idle_printer_for_model(
                            db,
                            candidate.target_model,
                            # Sensor-held printers are unavailable to the
                            # matcher but stay out of busy_printers itself
                            # (#1148) — see where `interlocked` is built.
                            busy_printers | interlocked.keys(),
                            effective_types,
                            item.target_location,
                            filament_overrides=filament_overrides,
                            require_plate_clear=require_plate_clear,
                            wakeable_ids=wakeable_printer_ids,
                        )
                        if match_id:
                            printer_id = match_id
                            chosen = candidate
                            break
                        per_model_reasons.append((candidate.target_model, match_reason or ""))

                    # Nothing is available and nothing has been woken this pass:
                    # switch one matching printer on. Assignment is left to the
                    # next pass, which sees the booted printer's live state
                    # instead of guessing at it seconds after connect (#2786).
                    if printer_id is None and not power_on_attempted and wakeable_candidates:
                        woken_id, attempted_id = await self._wake_printer_for_model(
                            db,
                            wakeable_candidates,
                            item.target_location,
                            busy_printers | interlocked.keys(),
                            wakeable_printer_ids,
                            require_plate_clear,
                        )
                        # An attempt spends the pass's one wake whether or not
                        # it worked: it has already blocked the queue loop for
                        # the boot wait. A failed printer is held out of later
                        # passes by its own cool-off, deliberately NOT by
                        # busy_printers — it is off, not busy, and labelling it
                        # busy would both misdescribe it in every later item's
                        # waiting reason and suppress the notification, since
                        # an all-busy reason is treated as needing no action.
                        power_on_attempted = attempted_id is not None
                        if woken_id is not None:
                            # Hold this item back rather than dispatching onto a
                            # printer whose AMS has not reported yet.
                            skip_reasons["powered_on_printer"] = skip_reasons.get("powered_on_printer", 0) + 1
                            continue

                    waiting_reason = None if printer_id else _collapse_waiting_reasons(per_model_reasons)

                    # Fold the winning variant's file and settings onto the item
                    # before anything else looks at them — the guards below and
                    # every step of the dispatch read the item's own columns.
                    if chosen is not None:
                        self._resolve_variant(item, chosen)

                    # Update waiting_reason if changed and send notification when first waiting
                    if item.waiting_reason != waiting_reason:
                        was_waiting = item.waiting_reason is not None
                        item.waiting_reason = waiting_reason
                        await db.commit()

                        # Send waiting notification only when transitioning to waiting state
                        # and the reason requires user action (not just "all printers busy")
                        if waiting_reason and not was_waiting and not self._is_busy_only(waiting_reason):
                            job_name = await self._get_job_name(db, item)
                            await notification_service.on_queue_job_waiting(
                                job_name=job_name,
                                target_model=_candidate_model_label(candidates) or item.target_model,
                                waiting_reason=waiting_reason,
                                db=db,
                            )

                    if printer_id:
                        # Before claiming the printer: hold back rather than race
                        # another dispatch over the same transient library row.
                        # Checked here so a held item does not get a printer
                        # assigned and then sit on it. See _library_row_conflict().
                        #
                        # No busy_printers.add() here, unlike the fixed-printer
                        # branch above: that one protects its printer's own queue
                        # ordering, but this item was never assigned to `printer_id`
                        # — the matcher merely offered it. Marking it busy would
                        # strand an idle printer for the rest of the pass.
                        if _library_row_conflict(item):
                            skip_reasons["library_row_in_use"] = skip_reasons.get("library_row_in_use", 0) + 1
                            continue

                        # Check condition (previous print success) before assigning
                        if item.require_previous_success:
                            if not await self._check_previous_success(db, item):
                                item.status = "skipped"
                                item.error_message = "Previous print failed or was aborted"
                                item.completed_at = datetime.now(timezone.utc)
                                await db.commit()
                                logger.info("Skipped queue item %s - previous print failed", item.id)

                                # Send notification
                                job_name = await self._get_job_name(db, item)
                                printer = await self._get_printer(db, printer_id)
                                await notification_service.on_queue_job_skipped(
                                    job_name=job_name,
                                    printer_id=printer_id,
                                    printer_name=printer.name if printer else "Unknown",
                                    reason="Previous print failed or was aborted",
                                    db=db,
                                )
                                continue

                        # Assign printer and start - clear waiting reason
                        item.printer_id = printer_id
                        item.waiting_reason = None
                        logger.info("Model-based assignment: queue item %s assigned to printer %s", item.id, printer_id)

                        # Send assignment notification
                        job_name = await self._get_job_name(db, item)
                        printer = await self._get_printer(db, printer_id)
                        await notification_service.on_queue_job_assigned(
                            job_name=job_name,
                            printer_id=printer_id,
                            printer_name=printer.name if printer else "Unknown",
                            target_model=item.target_model,
                            db=db,
                        )

                        # Resolve the AMS mapping for the assigned printer when it's
                        # missing OR unresolved (all -1). Critical for model-based
                        # jobs where mapping wasn't computed upfront, and it also
                        # self-heals a bogus stored [-1] (#2589).
                        unmappable = await self._ensure_ams_mapping(db, printer_id, item)
                        if unmappable:
                            await self._fail_unmappable_item(db, item, printer_id, unmappable)
                            continue

                        # Filament-deficit pre-dispatch check (#1496).
                        if await self._block_on_filament_deficit(db, item):
                            continue

                        _claim_library_row(item)
                        dispatch_ids.append(item.id)
                        busy_printers.add(printer_id)

                        # SJF starvation guard: mark model-based items that were jumped
                        if sjf_enabled and item.print_time_seconds is not None:
                            for other in items:
                                if (
                                    other.id != item.id
                                    and other.status == "pending"
                                    and other.printer_id is None
                                    and other.target_model
                                    and other.target_model.upper() == item.target_model.upper()
                                    and not other.been_jumped
                                    and other.position < item.position
                                    and (
                                        other.print_time_seconds is None
                                        or other.print_time_seconds > item.print_time_seconds
                                    )
                                ):
                                    other.been_jumped = True
                            await db.commit()

            # Log the decisions BEFORE dispatching. The dispatch below blocks for
            # as long as the slowest upload takes (minutes on a big 3MF), and a
            # skip summary that only lands after the transfers have finished is
            # useless for working out why an item did not go out.
            if skip_reasons:
                logger.info("Queue skip summary: %s", skip_reasons)
            if busy_printers:
                # Log why each printer was busy (first time it was checked)
                for pid in busy_printers:
                    state = printer_manager.get_status(pid)
                    connected = printer_manager.is_connected(pid)
                    awaiting = printer_manager.is_awaiting_plate_clear(pid)
                    state_name = state.state if state else "NO_STATUS"
                    logger.info(
                        "Queue: printer %d not available — connected=%s, state=%s, awaiting_plate_clear=%s",
                        pid,
                        connected,
                        state_name,
                        awaiting,
                    )

            # Keep-warm is a comfort feature; dispatch is not. It sits between
            # selection and `_launch_uploads`, so anything raising here would
            # discard this tick's selections — computed AMS mappings and all —
            # and, on a persistent fault, stop the queue dispatching entirely.
            # Same reasoning as the deficit check's guard below: never let an
            # auxiliary check wedge the queue. The bed simply stays wherever it
            # was, and the next tick tries again.
            try:
                await self._apply_keep_warm(db, items, dispatch_ids, busy_printers, require_plate_clear)
            except Exception as e:
                logger.warning("Keep-warm pass failed, continuing with dispatch: %s", e, exc_info=True)

            # Read the concurrency limit BEFORE the commit below, not inside
            # _dispatch_selected(). A SELECT on this session after the commit
            # implicitly opens a fresh transaction that nothing then closes, and
            # it would stay open for the whole dispatch — minutes of "idle in
            # transaction" on Postgres (pinned MVCC snapshot, vacuum blocked),
            # and on SQLite a pinned WAL read snapshot that stops the WAL being
            # checkpointed while every dispatch is writing to it.
            upload_limit = max(1, await self._get_int_setting(db, "queue_max_concurrent_uploads", default=4))

            # Selection is done; every decision above is recorded on `db`
            # (model-based printer assignment, computed ams_mapping). Flush it
            # before the dispatch tasks open their own sessions, or they will
            # read a row that still says printer_id=None. This also releases the
            # connection back to the pool for the duration of the dispatch.
            await db.commit()

            if dispatch_ids:
                item_printers = {it.id: it.printer_id for it in items}
                self._launch_uploads(dispatch_ids, item_printers, upload_limit)

            # Auto-drying: start drying on idle printers that have no pending queue items
            await self._check_auto_drying(db, items, dispatching_printers)

            # Keep the loop on the fast interval while any upload is in flight so
            # a slot freed mid-tick refills within seconds rather than after the
            # 30 s idle sleep (#2602). Selecting anything this pass (launched or
            # deferred because the pool was full) also counts as productive.
            return bool(dispatch_ids) or bool(self._inflight)

    def _launch_uploads(self, item_ids: list[int], item_printers: dict[int, int | None], limit: int) -> None:
        """Launch selected uploads as a refillable pool, capped at ``limit`` (#2602).

        Dispatch used to happen inline in the selection loop: ``await
        _start_print(db, item)`` per item in turn. Since ``_start_print``
        performs the FTP upload, that serialized every printer behind every
        other printer's transfer even though the printers are independent
        machines; #2555 moved it to a parallel ``asyncio.gather()``. But that
        gather was awaited before ``check_queue`` returned, so the run loop
        stayed blocked until the *slowest* upload in the batch finished — a
        513 s upload left 15 of 16 configured slots idle for 8.5 minutes on a
        93-printer farm even as other printers came free (#2602).

        Each upload now runs as an independent background task tracked in
        ``self._inflight``. check_queue excludes in-flight item_ids (still
        `pending` until their upload completes) and their printers from the
        next pass's selection, and this method launches at most
        ``limit - len(self._inflight)`` new uploads, so a freed slot refills on
        the next fast tick instead of waiting out the whole batch. The bound
        exists because the printers are independent but the host is not: each
        in-flight upload holds a thread in the FTP pool, a TLS session and a
        file handle.

        The no-overlapping-dispatch invariant the batch-await used to provide
        is now carried by the in-flight exclusion in check_queue. Everything
        else — the pending->printing CAS, the busy-printer guard (#2598), the
        per-printer hold, and each item's independent failure handling — still
        lives in ``_start_print`` and runs per task exactly as before.

        Synchronous on purpose: it registers every launched task into
        ``self._inflight`` before returning, so the next (sequential) tick sees
        an accurate in-flight count with no interleaving await.
        """
        free = limit - len(self._inflight)
        if free <= 0:
            logger.info(
                "Upload pool full (%d/%d in flight) — deferring %d item(s) to a later tick: %s",
                len(self._inflight),
                limit,
                len(item_ids),
                item_ids,
            )
            return

        to_launch = item_ids[:free]
        deferred = item_ids[free:]
        logger.info(
            "Launching %d upload(s) (pool %d/%d in flight)%s",
            len(to_launch),
            len(self._inflight),
            limit,
            f" — deferring {deferred} to a later tick" if deferred else "",
        )

        for item_id in to_launch:
            task = spawn_background_task(
                self._dispatch_one(item_id, item_printers.get(item_id)),
                name=f"queue-upload-{item_id}",
            )
            self._inflight[item_id] = (task, item_printers.get(item_id))
            # Prune on completion so the freed slot is refillable next tick.
            # spawn_background_task already logs any uncaught exception; this
            # only reclaims the pool slot (fires on success, failure, or cancel).
            task.add_done_callback(lambda _t, iid=item_id: self._inflight.pop(iid, None))

    async def _dispatch_one(self, item_id: int, selected_printer_id: int | None = None) -> None:
        """Upload + start one queue item in its own session (pool worker, #2602).

        Its own session: pool workers run concurrently and an AsyncSession is
        not safe to share across tasks; it also keeps a slow upload from pinning
        the scheduler's session (and, on SQLite, its transaction) open for the
        transfer's duration.

        ``selected_printer_id`` is the printer this item was selected for, taken
        from the same snapshot the caller used. It exists so the preheat pin can
        be unwound on the paths that never reach the ``finally`` below — see the
        claim failure a few lines down. Optional so the direct-call tests keep
        working; when it is absent those paths simply behave as they did before.
        """
        async with async_session() as item_db:
            # Claim the row for dispatch BEFORE reading the printer snapshot or
            # touching any slow I/O (#2615). The claim is an atomic CAS on
            # (status='pending', dispatching_at IS NULL); while it's held the edit
            # routes reject reassignment (409), so printer_id can't change out from
            # under the in-flight upload and split the queue row from the
            # archive/expected-print/physical command.
            if not await self._claim_for_dispatch(item_db, item_id):
                logger.info(
                    "Queue item %s not claimable for dispatch (cancelled, removed, or already claimed) — skipping",
                    item_id,
                )
                # This return is outside the try/finally below, so the rollback
                # has to happen here. Selecting this item already handed any
                # keep-warm hold on its printer over to the preheat pin
                # (`_sweep_keep_warm`), on the promise that this dispatch would
                # unwind it. Bailing without doing so leaves the bed hot with
                # nothing tracking it: the keep-warm entry is gone, so the
                # max-duration cap no longer applies, and if this was the
                # printer's last pending item nothing else will ever turn it
                # off. Reachable whenever a cancel or delete lands between
                # selection and the claim.
                if selected_printer_id is not None:
                    self._rollback_preheat_pin(item_id, selected_printer_id)
                return
            # Seeded from the caller's snapshot so the `item vanished` return
            # below still unwinds the pin; overwritten with the row's own
            # printer_id as soon as we have it.
            item_printer_id: int | None = selected_printer_id
            try:
                item = await item_db.get(PrintQueueItem, item_id)
                if not item:
                    logger.info("Queue item %s vanished after claim — skipping", item_id)
                    return
                item_printer_id = item.printer_id
                await self._start_print(item_db, item)
            finally:
                # Undo an expected-print registration whose print command never
                # went out. One choke point covers every way `_start_print` can
                # end without sending: a raised exception (a DB failure mid-
                # dispatch is the reported case), an early return, a cancel
                # winning the #1853 CAS, or `start_print()` returning False.
                # A confirmed send removes the entry itself, so this is a no-op
                # on the happy path.
                self._rollback_unconfirmed_expected_print(item_id)
                # Mirror the pre-#1625 background-dispatch lifecycle: a
                # reservation survives only after start_print() accepted the
                # command. Failure, cancellation, deferral, and exceptions all
                # release it here.
                await asyncio.shield(self._release_unconfirmed_budget_reservation(item_id))
                # Unwind preheat state (bed/chamber/airduct) if the
                # dispatch aborted before the print's own gcode took over.
                # `_start_print` clears the pin on successful `start_print()`;
                # anything still present here is by definition an aborted
                # dispatch and gets rolled back so the printer isn't left
                # heating for a job that isn't happening.
                if item_printer_id is not None:
                    self._rollback_preheat_pin(item_id, item_printer_id)
                # The cancellation flag only has meaning while this dispatch is
                # running; drop it so the set cannot grow without bound and a
                # re-queued item never inherits a stale cancellation.
                self._cancelled_dispatches.discard(item_id)
                # Release the claim on every exit. Once dispatch has finished the
                # row's status carries the lock (printing/failed/cancelled are all
                # != pending), so the token is only needed for the duration of the
                # upload. A row left pending (e.g. busy-printer deferral) becomes
                # dispatchable again on the next tick.
                await self._clear_dispatch_claim(item_db, item_id)

    def _rollback_unconfirmed_expected_print(self, item_id: int) -> None:
        """Drop an expectation for a print command that was never sent.

        Best-effort and never raises: this runs in the ``finally`` of dispatch,
        where the interesting exception is usually the one already propagating.
        """
        pending = self._unconfirmed_expected_print.pop(item_id, None)
        if pending is None:
            return
        printer_id, remote_filename, archive_id = pending
        try:
            from backend.app.main import unregister_expected_print

            unregister_expected_print(printer_id, remote_filename, archive_id)
        except Exception:
            logger.warning(
                "Queue item %s: failed to unregister expected print (printer=%s, file=%s, archive=%s)",
                item_id,
                printer_id,
                remote_filename,
                archive_id,
                exc_info=True,
            )

    async def _release_unconfirmed_budget_reservation(self, item_id: int) -> None:
        """Release a queue reservation without touching the dispatch session."""
        if item_id not in self._unconfirmed_budget_reservations:
            return

        for attempt in range(1, 4):
            async with async_session() as cleanup_db:
                try:
                    await release_budget_reservation(
                        cleanup_db,
                        source_type="print_queue",
                        source_id=item_id,
                        status="released",
                    )
                    await cleanup_db.commit()
                    self._unconfirmed_budget_reservations.discard(item_id)
                    return
                except Exception as exc:
                    try:
                        await cleanup_db.rollback()
                    except Exception:
                        pass
                    if attempt == 3:
                        logger.error(
                            "Queue item %s: failed to release budget reservation after %d attempts: %s",
                            item_id,
                            attempt,
                            exc,
                        )
                        return
                    await asyncio.sleep(0.5 * attempt)

    @staticmethod
    def _reported_bed_target(printer_id: int) -> int | None:
        """The bed target firmware currently reports, or None if it can't be read.

        None means "no evidence", not "zero" — callers must not treat it as a
        temperature. Deliberately total: this feeds cleanup paths that run in a
        ``finally``, where a malformed status must not become the exception the
        caller sees.
        """
        try:
            state = printer_manager.get_status(printer_id)
            if state is None:
                return None
            temps = state.temperatures
            if not isinstance(temps, dict):
                return None
            return int(float(temps.get("bed_target", 0) or 0))
        except (TypeError, ValueError, AttributeError):
            return None

    def _rollback_preheat_pin(self, item_id: int, printer_id: int) -> None:
        """Unwind everything preheat set when dispatch did NOT hand off to a running print.

        Turns the bed heater off, the chamber heater off, and opens the
        airduct flap back to cooling — for whichever of those preheat
        actually applied. `_start_print` clears the pin on successful
        `start_print()`; anything still present when `_dispatch_one` exits
        is by definition an aborted dispatch and gets rolled back here.

        The bed is the one action that can be declined: if firmware has since
        been given a target other than the one we pinned, it belongs to someone
        else and is left alone. See the comment at that branch.

        Also called directly from `_dispatch_one`'s claim-failure return, which
        never reaches the ``finally``.

        Best-effort and never raises — this runs in the ``finally`` of dispatch.
        """
        pin = self._preheat_pin.pop(printer_id, set())
        pinned_bed = self._preheat_pin_bed.pop(printer_id, None)
        if not pin:
            return
        client = printer_manager.get_client(printer_id)
        if client is None:
            logger.info(
                "Dispatch item %s (printer %d): preheat rollback skipped — no client",
                item_id,
                printer_id,
            )
            return
        if "bed" in pin:
            # Only undo our own target. If firmware reports something else, the
            # user or another writer owns the bed now and zeroing it would
            # clobber their choice -- the same guard `_release_keep_warm`
            # applies to a keep-warm hold.
            #
            # Every uncertain case switches the bed off rather than leaving it:
            # no recorded target (a pin written before this bookkeeping, or a
            # setter that raised after pinning) and an unreadable status both
            # fall through. A bed left hot with no owner is the worse failure,
            # and this runs in a `finally` where raising would mask the real
            # exception.
            cur_bed_target = self._reported_bed_target(printer_id) if pinned_bed is not None else None
            if cur_bed_target is not None and cur_bed_target != pinned_bed:
                logger.info(
                    "Dispatch item %s (printer %d): rollback skipped bed → 0 (firmware target %d != pinned %d)",
                    item_id,
                    printer_id,
                    cur_bed_target,
                    pinned_bed,
                )
            else:
                try:
                    client.set_bed_temperature(0)
                except Exception as exc:
                    logger.warning("Dispatch item %s: rollback bed → 0 failed: %s", item_id, exc)
        if "chamber" in pin:
            try:
                client.set_chamber_temperature(0)
            except Exception as exc:
                logger.warning("Dispatch item %s: rollback chamber → 0 failed: %s", item_id, exc)
        if "airduct" in pin:
            try:
                client.set_airduct_mode("cooling")
            except Exception as exc:
                logger.warning("Dispatch item %s: rollback airduct → cooling failed: %s", item_id, exc)
        logger.info(
            "Dispatch item %s (printer %d): preheat rollback → %s",
            item_id,
            printer_id,
            sorted(pin),
        )

    async def _claim_for_dispatch(self, db: AsyncSession, item_id: int) -> bool:
        """Atomically stamp ``dispatching_at`` on a still-pending, unclaimed row.

        Returns True if this call won the claim, False if the row was already
        claimed, no longer pending (cancelled mid-tick), or removed. The CAS is
        the load-bearing guard against reassign-during-dispatch (#2615)."""
        res = await db.execute(
            update(PrintQueueItem)
            .where(PrintQueueItem.id == item_id)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.dispatching_at.is_(None))
            .values(dispatching_at=datetime.now(timezone.utc))
        )
        await db.commit()
        return res.rowcount > 0

    async def _clear_dispatch_claim(self, db: AsyncSession, item_id: int) -> None:
        """Clear the dispatch claim (#2615). Best-effort: a failure here must not
        mask the dispatch outcome.

        Retried, because the failure mode in practice is transient and narrow: a
        database that is momentarily unreachable — PostgreSQL out of connection
        slots is the observed case — refuses this write for a second or two while
        the dispatch that just ended is still holding the row out of the selection
        query. One attempt was enough to wedge the item; a couple of spaced
        attempts clear it. Each attempt rolls back first, since a failed write
        leaves the session needing it before it can be reused.

        If every attempt fails, ``_clear_stale_dispatch_claims`` picks the row up
        on the next quiet tick.
        """
        for attempt in range(1, 4):
            try:
                await db.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(dispatching_at=None))
                await db.commit()
                return
            except Exception as exc:
                try:
                    await db.rollback()
                except Exception:
                    pass
                if attempt == 3:
                    logger.warning(
                        "Queue item %s: failed to clear dispatch claim after %d attempts: %s "
                        "— a later quiet tick will release it",
                        item_id,
                        attempt,
                        exc,
                    )
                    return
                await asyncio.sleep(0.5 * attempt)

    async def _printers_for_model(
        self,
        db: AsyncSession,
        model: str,
        target_location: str | None = None,
    ) -> list[Printer]:
        """Active printers of *model*, optionally narrowed to one location.

        Shared by the matcher and by the smart-plug wake step (#2786) so both
        answer "which printers can this job run on" from one query — a job can
        only be woken onto a printer the matcher would also have considered.
        """
        normalized_model = normalize_printer_model(model) or model
        query = (
            select(Printer)
            .where(func.lower(Printer.model) == normalized_model.lower())
            .where(Printer.is_active == True)  # noqa: E712
        )
        if target_location:
            query = query.where(Printer.location == target_location)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _wakeable_printer_ids(self, db: AsyncSession) -> set[int]:
        """Printer IDs that at least one enabled ``auto_on`` plug can power on.

        Read once per queue check rather than per printer: it decides both
        whether the wake step has anything to do and how an offline printer is
        worded in the waiting reason — "Offline" and "offline with no Auto On
        plug" are different problems, and the second is the one the user has to
        fix themselves (#2786).
        """
        result = await db.execute(
            select(SmartPlug.printer_id)
            .where(SmartPlug.printer_id.is_not(None))
            .where(SmartPlug.enabled == True)  # noqa: E712
            .where(SmartPlug.auto_on == True)  # noqa: E712
        )
        return {pid for (pid,) in result.all() if pid is not None}

    def _wake_recently_failed(self, printer_id: int) -> bool:
        """True while this printer's failed power-on is still cooling off (#2786)."""
        deadline = self._wake_failures.get(printer_id)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            del self._wake_failures[printer_id]
            return False
        return True

    async def _wake_printer_for_model(
        self,
        db: AsyncSession,
        candidates: list[_ModelCandidate],
        target_location: str | None,
        exclude_ids: set[int],
        wakeable_ids: set[int],
        require_plate_clear: bool,
    ) -> tuple[int | None, int | None]:
        """Power on one offline printer a model-based item could run on (#2786).

        The fixed-printer branch has powered a printer on since smart plugs
        existed. The model-based branch never could: its matcher drops an
        offline printer into the "Offline:" waiting reason and nothing looks at
        its plugs, so a class-targeted job with every matching printer switched
        off sat pending forever. The reporter's log is the controlled
        experiment — the same item, same plug, same Auto On setting, dispatched
        the moment they edited it onto a specific printer.

        Returns ``(woken_id, attempted_id)``. ``attempted_id`` is set whenever a
        power-on was actually tried, so the caller can tell "nothing here was
        wakeable" (both None — cheap, other items may still find something)
        from "we tried and it did not come up" (only ``attempted_id`` — the
        boot timeout has already been spent).

        A printer whose last known trays cannot satisfy the job is passed over
        rather than woken (#2876): the colours are readable while it is off, so
        switching a farm on one machine at a time to discover them wakes
        printers that could never have taken the job.

        Deliberately does NOT go on to match the job once a printer is up: AMS
        trays arrive with the first status push after connect, so a filament
        check against a printer that booted seconds ago can reject the printer
        we just woke. The next queue pass matches it with live state.

        At most one printer per pass. Each wake blocks the queue loop for the
        boot wait, and a queue of ten class-targeted jobs must not switch on
        ten printers inside one check.
        """
        for candidate in candidates:
            if not candidate.target_model:
                continue
            required_types, filament_overrides = _filament_constraints(candidate)
            printers = await self._printers_for_model(db, candidate.target_model, target_location)
            for printer in sorted(printers, key=lambda p: p.id):
                if printer.id in exclude_ids or printer.id not in wakeable_ids:
                    continue
                if printer_manager.is_connected(printer.id):
                    continue
                if self._wake_recently_failed(printer.id):
                    # Its plug did not bring it back a moment ago. Move on to a
                    # sibling instead of spending this pass — and every pass —
                    # on the same printer.
                    continue
                if require_plate_clear and printer_manager.is_awaiting_plate_clear(printer.id):
                    # Waking this one buys nothing: it would boot into IDLE and
                    # then be held by the plate-clear gate, which is exactly
                    # what the reporter's log shows happening for 80 minutes
                    # after a fixed-printer wake. The flag is Bambuddy-side and
                    # persisted, so it is readable while the printer is off.
                    logger.info(
                        "Not powering on printer %s for a %s job: it is awaiting plate-clear acknowledgment",
                        printer.id,
                        candidate.target_model,
                    )
                    continue

                shortfall = self._cached_filament_shortfall(printer.id, required_types, filament_overrides)
                if shortfall:
                    logger.info(
                        "Not powering on printer %s for a %s job: last known filament cannot satisfy it (needs %s)",
                        printer.id,
                        candidate.target_model,
                        ", ".join(shortfall),
                    )
                    continue

                plugs = await self._get_smart_plugs(db, printer.id)
                auto_on_plugs = [p for p in plugs if p.auto_on and p.enabled]
                if not auto_on_plugs:
                    # wakeable_ids said otherwise — the plug changed under us
                    # mid-pass. Nothing to do but move on.
                    continue

                logger.info(
                    "No %s printer available for a queued job; powering on offline printer %s via smart plug(s)",
                    candidate.target_model,
                    printer.id,
                )
                primary_plug = self._pick_power_plug(auto_on_plugs)
                if not await self._power_on_and_wait(primary_plug, printer.id, db):
                    logger.warning(
                        "Could not power on printer %s via smart plug; not trying it again for %ss",
                        printer.id,
                        self._wake_failure_cooloff,
                    )
                    self._wake_failures[printer.id] = time.monotonic() + self._wake_failure_cooloff
                    return None, printer.id

                for extra_plug in [p for p in auto_on_plugs if p.id != primary_plug.id]:
                    try:
                        service = await smart_plug_manager.get_service_for_plug(extra_plug, db)
                        await service.turn_on(extra_plug)
                        logger.info("Also powered on plug '%s' for printer %s", extra_plug.name, printer.id)
                    except Exception as e:
                        logger.warning("Failed to power on extra plug '%s': %s", extra_plug.name, e)
                return printer.id, printer.id
        return None, None

    async def _find_idle_printer_for_model(
        self,
        db: AsyncSession,
        model: str,
        exclude_ids: set[int],
        required_filament_types: list[str] | None = None,
        target_location: str | None = None,
        filament_overrides: list[dict] | None = None,
        require_plate_clear: bool = True,
        wakeable_ids: set[int] | None = None,
    ) -> tuple[int | None, str | None]:
        """Find an idle, connected printer matching the model with compatible filaments.

        Args:
            db: Database session
            model: Printer model to match (e.g., "X1C", "P1S")
            exclude_ids: Printer IDs to exclude (already busy)
            required_filament_types: Optional list of filament types needed (e.g., ["PLA", "PETG"])
                                     If provided, only printers with all required types loaded will match.
            target_location: Optional location filter. If provided, only printers in this location are considered.
            filament_overrides: Optional list of override dicts. Each entry may include
                                 ``force_color_match: true`` to require an exact type+color match
                                 on the printer for that slot. Without the flag the existing
                                 colour-preference logic applies.
            wakeable_ids: Printers a smart plug can power on (#2786). Only changes how an
                          offline printer is worded: one Bambuddy will switch on reads
                          differently from one the user has to go and switch on themselves.

        Returns:
            Tuple of (printer_id, waiting_reason):
            - (printer_id, None) if a matching printer was found
            - (None, reason) if no printer is available, with explanation
        """
        normalized_model = normalize_printer_model(model) or model
        printers = await self._printers_for_model(db, model, target_location)

        location_suffix = f" in {target_location}" if target_location else ""
        if not printers:
            return None, f"No active {normalized_model} printers{location_suffix} configured"

        # Separate force-matched overrides from preference-only overrides
        force_overrides = [o for o in (filament_overrides or []) if o.get("force_color_match")]
        pref_overrides = [o for o in (filament_overrides or []) if not o.get("force_color_match")]

        # Track reasons for skipping printers
        printers_busy = []
        printers_offline = []
        printers_offline_no_plug = []
        printers_missing_filament: list[tuple[str, list[str]]] = []
        candidates: list[tuple[int, int]] = []  # (printer_id, color_match_count)

        for printer in printers:
            if printer.id in exclude_ids:
                # Printer is already claimed by another job in this scheduling run.
                # For force-color jobs, still check if the color would match — if not,
                # report it as a color mismatch rather than plain "Busy" so the user
                # knows the job needs a filament change, not just to wait for availability.
                if force_overrides and not pref_overrides:
                    missing_colors = self._get_missing_force_color_slots(printer.id, force_overrides)
                    if missing_colors:
                        printers_missing_filament.append((printer.name, missing_colors))
                        continue
                printers_busy.append(printer.name)
                continue

            is_connected = printer_manager.is_connected(printer.id)
            is_idle = self._is_printer_idle(printer.id, require_plate_clear) if is_connected else False

            if not is_connected:
                # An offline printer whose last known filament cannot run this
                # job is reported as needing filament rather than as offline
                # (#2876). It is also the printer the smart-plug step will now
                # decline to switch on, and "Offline:" on its own would leave
                # that decision looking like nothing happening at all.
                shortfall = self._cached_filament_shortfall(printer.id, required_filament_types, filament_overrides)
                if shortfall:
                    printers_missing_filament.append((printer.name, shortfall))
                elif wakeable_ids is not None and printer.id not in wakeable_ids:
                    printers_offline_no_plug.append(printer.name)
                else:
                    printers_offline.append(printer.name)
                continue

            if not is_idle:
                # Printer is currently printing.  For force-color jobs, check whether the
                # loaded color would satisfy the requirement — if not, surface it as a
                # color-mismatch reason rather than plain "Busy" so the user understands
                # that the job is waiting for a filament change, not just printer availability.
                if force_overrides and not pref_overrides:
                    missing_colors = self._get_missing_force_color_slots(printer.id, force_overrides)
                    if missing_colors:
                        printers_missing_filament.append((printer.name, missing_colors))
                        logger.debug(
                            "Printer %s (%s) is busy but also has wrong force-color: %s",
                            printer.id,
                            printer.name,
                            missing_colors,
                        )
                        continue
                printers_busy.append(printer.name)
                continue

            # Validate filament compatibility if required types are specified
            if required_filament_types:
                missing = self._get_missing_filament_types(printer.id, required_filament_types)
                if missing:
                    # When force_overrides are present, enrich missing entries with color info
                    # so the "Waiting on" message includes "TYPE (color)" instead of just "TYPE"
                    if force_overrides:
                        force_color_map = {
                            (o.get("type") or "").upper(): o.get("color_name") or o.get("color", "?")
                            for o in force_overrides
                        }
                        missing_enriched = [
                            f"{t} ({force_color_map[t_upper]})" if (t_upper := t.upper()) in force_color_map else t
                            for t in missing
                        ]
                        printers_missing_filament.append((printer.name, missing_enriched))
                    else:
                        printers_missing_filament.append((printer.name, missing))
                    logger.debug("Skipping printer %s (%s) - missing filaments: %s", printer.id, printer.name, missing)
                    continue

            # Force color match: ALL flagged slots must have an exact type+color match
            if force_overrides:
                missing_colors = self._get_missing_force_color_slots(printer.id, force_overrides)
                if missing_colors:
                    printers_missing_filament.append((printer.name, missing_colors))
                    logger.debug(
                        "Skipping printer %s (%s) - missing force-matched colors: %s",
                        printer.id,
                        printer.name,
                        missing_colors,
                    )
                    continue

            # If preference-only overrides exist, rank by color matches (existing behaviour)
            if pref_overrides:
                color_matches = self._count_override_color_matches(printer.id, pref_overrides)
                if color_matches > 0:
                    candidates.append((printer.id, color_matches))
                else:
                    override_colors = [f"{o.get('type', '?')} ({o.get('color', '?')})" for o in pref_overrides]
                    printers_missing_filament.append((printer.name, override_colors))
                    logger.debug("Skipping printer %s (%s) - no matching override colors", printer.id, printer.name)
                    continue
            elif force_overrides:
                # Passed all force checks — immediately eligible (no preference ordering needed)
                return printer.id, None
            else:
                # No overrides at all - take first available (existing behavior)
                return printer.id, None

        # If we have candidates from preference override matching, pick the one with most color matches
        if candidates:
            candidates.sort(key=lambda c: c[1], reverse=True)
            return candidates[0][0], None

        # Build waiting reason from what we found
        reasons = []
        if printers_missing_filament:
            # Filament/color mismatch is most actionable - show first
            if force_overrides and not pref_overrides:
                # All mismatches are force-color failures — use descriptive message only;
                # but only if there are no busy printers that DO have the matching color.
                # If a printer has the right color but is busy, surface "Busy" instead so
                # the user knows the job will start automatically once that printer is free.
                # Same for a printer that is merely offline: Bambuddy switches that one on
                # by itself, so the job is not actually waiting on anybody to change a
                # spool (#2876 — offline printers reach this list now that a switched-off
                # printer's own filament is read).
                if not printers_busy and not printers_offline:
                    all_missing = sorted({c for _, cols in printers_missing_filament for c in cols})
                    return None, f"No matching material/color. Waiting on {', '.join(all_missing)}"
                # else: fall through — the self-resolving entries are appended below
            else:
                names_and_missing = [
                    f"{name} (needs {', '.join(missing)})" for name, missing in printers_missing_filament
                ]
                reasons.append(f"Waiting for filament: {'; '.join(names_and_missing)}")
        if printers_busy:
            reasons.append(f"Busy: {', '.join(printers_busy)}")
        if printers_offline:
            reasons.append(f"Offline: {', '.join(printers_offline)}")
        if printers_offline_no_plug:
            # Named separately because it is the one entry on this list the
            # user has to act on: no enabled Auto On plug means Bambuddy will
            # never power this printer on for the queue (#2786).
            reasons.append(f"Offline, no Auto On smart plug: {', '.join(printers_offline_no_plug)}")

        return None, " | ".join(reasons) if reasons else f"No available {model} printers{location_suffix}"

    @staticmethod
    def _is_busy_only(waiting_reason: str) -> bool:
        """Check if the waiting reason only contains 'Busy' entries.

        When all matching printers are simply busy printing, the queued job
        will start automatically once a printer finishes — no user action
        is required, so we skip the notification.
        """
        parts = [p.strip() for p in waiting_reason.split(" | ")]
        return all(p.startswith("Busy:") for p in parts)

    def _get_missing_force_color_slots(
        self, printer_id: int, force_overrides: list[dict], raw_data: dict | None = None
    ) -> list[str]:
        """Return descriptive strings for force_color_match slots not satisfied by the printer.

        Each entry in ``force_overrides`` must have ``type`` and ``color`` fields and is expected
        to carry ``force_color_match: True``.  The printer must have **every** such slot loaded
        with an exact type+color match.

        When both the override and a candidate tray carry a ``tray_info_idx``, they must also
        match on it: Bambu reports every PLA variant as ``tray_type == "PLA"``, so the
        Basic/Matte/Silk distinction lives only in ``tray_info_idx`` (GFA00/GFA01/GFA06/...).
        Without this, a job sliced for PLA Matte matched every white PLA regardless of variant
        (#2650). If either side lacks an idx (custom/third-party spools report a blank one, and
        older 3MFs carry none) we fall back to the historical type+colour behaviour so those
        setups are unaffected.

        Returns:
            List of ``"TYPE (color)"`` strings for unmatched slots (empty list means all match).
        """
        if raw_data is None:
            status = printer_manager.get_status(printer_id)
            if not status:
                return [f"{o.get('type', '?')} ({o.get('color_name') or o.get('color', '?')})" for o in force_overrides]
            raw_data = status.raw_data

        # Build loaded (type, colour, tray_info_idx) triples from AMS and external spool.
        loaded: list[tuple[str, str, str]] = []
        for ams_unit in raw_data.get("ams", []):
            for tray in ams_unit.get("tray", []):
                tray_type = tray.get("tray_type")
                if tray_type:
                    color_norm = (tray.get("tray_color", "") or "").replace("#", "").lower()[:6]
                    loaded.append((canonical_filament_type(tray_type), color_norm, tray.get("tray_info_idx", "") or ""))
        for vt in raw_data.get("vt_tray") or []:
            vt_type = vt.get("tray_type")
            if vt_type:
                color_norm = (vt.get("tray_color", "") or "").replace("#", "").lower()[:6]
                loaded.append((canonical_filament_type(vt_type), color_norm, vt.get("tray_info_idx", "") or ""))

        missing = []
        for o in force_overrides:
            o_type = canonical_filament_type(o.get("type") or "")
            o_color = (o.get("color") or "").replace("#", "").lower()[:6]
            o_idx = o.get("tray_info_idx") or ""
            satisfied = any(
                t_type == o_type and t_color == o_color and (not o_idx or not t_idx or o_idx == t_idx)
                for t_type, t_color, t_idx in loaded
            )
            if not satisfied:
                color_label = o.get("color_name") or o.get("color", "?")
                missing.append(f"{o_type} ({color_label})")
        return missing

    def _get_missing_filament_types(
        self, printer_id: int, required_types: list[str], raw_data: dict | None = None
    ) -> list[str]:
        """Get the list of required filament types that are not loaded on the printer.

        Args:
            printer_id: The printer ID
            required_types: List of filament types needed (e.g., ["PLA", "PETG"])

        Returns:
            List of missing filament types (empty if all are loaded)
        """
        if raw_data is None:
            status = printer_manager.get_status(printer_id)
            if not status:
                return required_types  # Can't determine, assume all missing
            raw_data = status.raw_data

        # Collect all filament types loaded on this printer (AMS units + external spool)
        # Use canonical types so equivalence groups (e.g. PA-CF/PA12-CF/PAHT-CF) match.
        loaded_types: set[str] = set()

        # Check AMS units (stored in raw_data["ams"])
        ams_data = raw_data.get("ams", [])
        if ams_data:
            for ams_unit in ams_data:
                for tray in ams_unit.get("tray", []):
                    tray_type = tray.get("tray_type")
                    if tray_type:
                        loaded_types.add(canonical_filament_type(tray_type))

        # Check external spool(s) (virtual tray, stored in raw_data["vt_tray"] as list)
        for vt in raw_data.get("vt_tray") or []:
            vt_type = vt.get("tray_type")
            if vt_type:
                loaded_types.add(canonical_filament_type(vt_type))

        # Find which required types are missing (using canonical type for equivalence)
        missing = []
        for req_type in required_types:
            if canonical_filament_type(req_type) not in loaded_types:
                missing.append(req_type)

        return missing

    def _count_override_color_matches(
        self, printer_id: int, overrides: list[dict], raw_data: dict | None = None
    ) -> int:
        """Count how many filament overrides have an exact color match on the printer.

        Used to prefer printers that already have the desired override colors loaded.
        """
        if raw_data is None:
            status = printer_manager.get_status(printer_id)
            if not status:
                return 0
            raw_data = status.raw_data

        # Collect loaded filaments' type+color pairs
        loaded: set[tuple[str, str]] = set()
        for ams_unit in raw_data.get("ams", []):
            for tray in ams_unit.get("tray", []):
                tray_type = tray.get("tray_type")
                # `or ""`, not a dict default: a slot can carry the key with a
                # null value, and this now runs against switched-off printers
                # too, where nobody is watching for the AttributeError.
                tray_color = tray.get("tray_color") or ""
                if tray_type:
                    color_norm = tray_color.replace("#", "").lower()[:6]
                    loaded.add((tray_type.upper(), color_norm))
        for vt in raw_data.get("vt_tray") or []:
            vt_type = vt.get("tray_type")
            if vt_type:
                color_norm = (vt.get("tray_color", "") or "").replace("#", "").lower()[:6]
                loaded.add((vt_type.upper(), color_norm))

        matches = 0
        for o in overrides:
            o_type = (o.get("type") or "").upper()
            o_color = (o.get("color") or "").replace("#", "").lower()[:6]
            if (o_type, o_color) in loaded:
                matches += 1
        return matches

    @staticmethod
    def _tray_reading(printer_id: int) -> dict:
        """The best tray reading available for a printer that is not printing.

        Live status first: a printer keeps its last status after the power
        goes, because ``mark_power_off`` blanks ``connected`` and ``state`` and
        leaves ``raw_data`` alone. The manager's own record is the fallback,
        for when the client itself has been dropped and taken its status with
        it — which is what every power-on attempt does.

        An empty result means "we have never heard", not "nothing is loaded":
        the two are indistinguishable from here, and only the second would be
        safe to act on.
        """
        status = printer_manager.get_status(printer_id)
        raw = (status.raw_data if status else None) or {}
        for ams_unit in raw.get("ams") or []:
            if any(tray.get("tray_type") for tray in ams_unit.get("tray", [])):
                return raw
        if any(vt.get("tray_type") for vt in raw.get("vt_tray") or []):
            return raw
        return printer_manager.last_known_trays(printer_id)

    def _cached_filament_shortfall(
        self,
        printer_id: int,
        required_types: list[str] | None,
        filament_overrides: list[dict] | None,
    ) -> list[str]:
        """What a switched-off printer's last known filament cannot provide (#2876).

        The smart-plug wake step used to consider only the model, so a job for a
        colour loaded on the last printer in ID order switched on every earlier
        one in turn, evaluated it, rejected it on colour and left it running.
        Bambuddy knew those colours the whole time. This asks the same three
        questions the matcher asks a live printer — required types, forced
        colours, preferred colours — of the trays it last reported, and returns
        the answers in the same shape the "Waiting for filament" reason uses.

        Empty means the printer may still be able to take the job.

        Fails open, and deliberately: with no tray reading (never connected
        since Bambuddy started, or the cache dropped by a reconnect) this
        returns nothing to report and the printer is treated as it was before.
        A farm restarted while its printers were off must not conclude that
        none of them can print.
        """
        if not required_types and not filament_overrides:
            return []

        raw_data = self._tray_reading(printer_id)
        if not raw_data:
            return []

        force_overrides = [o for o in (filament_overrides or []) if o.get("force_color_match")]
        pref_overrides = [o for o in (filament_overrides or []) if not o.get("force_color_match")]

        if required_types:
            missing = self._get_missing_filament_types(printer_id, required_types, raw_data)
            if missing:
                # Same enrichment the live path applies: a bare "PLA" is not
                # much help when what is missing is a particular PLA.
                force_color_map = {
                    (o.get("type") or "").upper(): o.get("color_name") or o.get("color", "?") for o in force_overrides
                }
                return [
                    f"{t} ({force_color_map[t_upper]})" if (t_upper := t.upper()) in force_color_map else t
                    for t in missing
                ]

        if force_overrides:
            missing_colors = self._get_missing_force_color_slots(printer_id, force_overrides, raw_data)
            if missing_colors:
                return missing_colors

        # Preference overrides read as a preference but the matcher treats zero
        # matches as a skip, so a printer with none of the wanted colours is
        # rejected there too. Waking it would only produce that same rejection.
        if pref_overrides and self._count_override_color_matches(printer_id, pref_overrides, raw_data) == 0:
            return [f"{o.get('type', '?')} ({o.get('color_name') or o.get('color', '?')})" for o in pref_overrides]

        return []

    def _resolve_variant(self, item: PrintQueueItem, candidate: _ModelCandidate) -> None:
        """Fold the winning candidate's file and settings onto the queue row (#671).

        This is the whole trick that keeps cross-model items cheap: the many-to-many
        never escapes the selection loop. By the time the pass commits, the row
        looks exactly like an ordinary single-file model-based item, so the upload,
        archive creation, expected-print registration, print history and reprint
        paths need no knowledge that variants exist.

        No-ops for a non-variant candidate, which is already the item's own columns.

        Safe to run and re-run: the item's file columns are only ever *read* when it
        has no variants, so an item that gets resolved and then skipped (library-row
        conflict, previous-print gate) is simply resolved again on the next pass.
        """
        variant = candidate.variant
        if variant is None:
            return

        item.library_file_id = variant.library_file_id
        item.library_file = variant.library_file
        # The dispatcher checks archive_id first and would print that instead of
        # the file we just picked. Creation refuses to combine the two, so this
        # only ever fires on a hand-written row — clear it rather than silently
        # dispatch something the matcher never considered.
        item.archive_id = None
        item.archive = None

        item.target_model = variant.target_model
        item.plate_id = variant.plate_id
        item.ams_mapping = variant.ams_mapping
        item.nozzle_mapping = variant.nozzle_mapping
        item.nozzle_rack_choice = variant.nozzle_rack_choice
        item.filament_overrides = variant.filament_overrides
        item.required_filament_types = variant.required_filament_types
        if variant.print_time_seconds is not None:
            # The row carried the shortest candidate's estimate so SJF could order
            # it before a printer was known; now that one is chosen, record what is
            # actually going to run so history and the ETA agree with reality.
            item.print_time_seconds = variant.print_time_seconds

    async def _ensure_ams_mapping(self, db: AsyncSession, printer_id: int, item: PrintQueueItem) -> str | None:
        """Ensure the queue item carries a usable AMS mapping before dispatch.

        Recomputes from live printer status when the stored mapping is missing OR
        unresolved (all -1). A stored all-[-1] mapping is a bug artifact — a
        frontend status-load race can serialize [-1] before the printer's AMS
        trays are known (#2589) — and must not be trusted: downstream it would be
        silently downgraded to external-spool mode and print against an empty
        feed. A resolved mapping (including manual overrides, or a partially
        padded one) is left untouched.

        When recompute cannot resolve it either (no compatible tray loaded), the
        bogus [-1] is cleared to None so it is not later mistaken for an explicit
        external selection; the print command then keeps use_ams=True and the
        firmware surfaces a clear AMS-mapping error instead of silently printing
        to the empty external feed.

        Returns an actionable message when that firmware error is the only
        possible outcome — the matcher ran, matched nothing, and the printer has
        no AMS to load a different spool into (#2771). The caller fails the item
        on it instead of spending an upload on a print that cannot start.
        Returns None everywhere else, including every case where we simply lack
        the data to judge, so dispatch is only ever blocked on a positive
        finding.
        """
        stored_mapping: list | None = None
        if item.ams_mapping:
            try:
                stored_mapping = json.loads(item.ams_mapping)
            except (json.JSONDecodeError, TypeError):
                stored_mapping = None

        # Already resolved (present and not all-unresolved) — keep as-is so a
        # user's manual mapping is never overwritten.
        if item.ams_mapping and not _mapping_is_all_unresolved(stored_mapping):
            return None

        computed_mapping = await self._compute_ams_mapping_for_printer(db, printer_id, item)
        if computed_mapping and not _mapping_is_all_unresolved(computed_mapping):
            item.ams_mapping = json.dumps(computed_mapping)
            logger.info(
                "Queue item %s: Computed AMS mapping for printer %s: %s",
                item.id,
                printer_id,
                computed_mapping,
            )
            await db.commit()
            return None

        if _mapping_is_all_unresolved(stored_mapping):
            logger.warning(
                "Queue item %s: stored ams_mapping %s is unresolved and could not be recomputed "
                "from live status on printer %s; clearing it so dispatch does not treat it as external",
                item.id,
                stored_mapping,
                printer_id,
            )
            item.ams_mapping = None
            await db.commit()

        return await self._unmappable_without_ams_message(db, printer_id, item, computed_mapping)

    async def _unmappable_without_ams_message(
        self,
        db: AsyncSession,
        printer_id: int,
        item: PrintQueueItem,
        computed_mapping: list[int] | None,
    ) -> str | None:
        """Message for a mapping that resolved nothing on an AMS-less printer (#2771).

        A print dispatched with no mapping goes out as ``use_ams: true`` with no
        ``ams_mapping`` and no ``ams_mapping2``, which the firmware rejects with
        0700_8012 "Failed to get AMS mapping table" — after Bambuddy has already
        uploaded several megabytes and burned its dispatch retries. With an AMS
        attached that error is worth reaching: the user can load the right spool
        and press Resume, so this returns None and today's behaviour stands. With
        no AMS there is nothing to resume into — the external spool holder is the
        whole inventory — so the useful answer is to say which filament is
        missing and stop.

        Fail-safe by construction, mirroring the nozzle-diameter guard (#1899):
        every branch that lacks the evidence to be sure returns None.
        """
        # None means the matcher never ran (no requirements parsed from the 3MF,
        # or nothing loaded at all) rather than "ran and matched nothing". Those
        # dispatch as they always have.
        if not _mapping_is_all_unresolved(computed_mapping):
            return None

        status = printer_manager.get_status(printer_id)
        if status is None:
            return None

        # "No AMS" has to be a fact the printer stated, not the absence of a
        # statement. `raw_data["ams"]` is written only once an AMS push has been
        # handled and is preserved across partial pushes thereafter, so a missing
        # key means we have not heard yet — most likely a reconnect, where the
        # trays of a fully loaded AMS would be invisible for a few seconds. An
        # empty list is the positive report of a printer with no AMS.
        ams_units = status.raw_data.get("ams")
        if not isinstance(ams_units, list) or ams_units:
            return None

        required = await self._get_filament_requirements(db, item)
        loaded = self._build_loaded_filaments(status)
        if not required or not loaded:
            # Both were non-empty moments ago or the matcher could not have run.
            # If the picture changed under us, say nothing rather than fail an
            # item on stale evidence.
            return None
        self._apply_filament_overrides(item, required)
        return _unmatched_filament_message(required, loaded)

    async def _fail_unmappable_item(
        self, db: AsyncSession, item: PrintQueueItem, printer_id: int, message: str
    ) -> None:
        """Fail a queue item whose filament mapping cannot resolve (#2771).

        This replaces a failure, not a success: without it the item is uploaded,
        rejected by the firmware with 0700_8012, retried twice more and failed
        anyway with "never started the print after N dispatch attempts". So this
        applies on the model-based path too, even though it means an "Any <model>"
        job stops at the first printer offered rather than trying its siblings —
        deferring instead would need the check to move inside
        ``_find_printer_for_model``'s candidate loop, since un-assigning here just
        re-assigns the same printer on the next tick.
        """
        item.status = "failed"
        item.error_message = message
        item.completed_at = datetime.now(timezone.utc)
        item.waiting_reason = None
        await db.commit()
        logger.warning(
            "Queue item %s: no usable AMS mapping on printer %s — %s",
            item.id,
            printer_id,
            message,
        )

        job_name = await self._get_job_name(db, item)
        printer = await self._get_printer(db, printer_id)
        await notification_service.on_queue_job_failed(
            job_name=job_name,
            printer_id=printer_id,
            printer_name=printer.name if printer else "Unknown",
            reason=message,
            db=db,
        )
        try:
            await ws_manager.send_queue_item_failed(
                user_id=item.created_by_id,
                queue_item_id=item.id,
                printer_id=printer_id,
                reason="filament_unmappable",
            )
        except Exception:
            pass

    async def _compute_ams_mapping_for_printer(
        self, db: AsyncSession, printer_id: int, item: PrintQueueItem
    ) -> list[int] | None:
        """Compute AMS mapping for a printer based on filament requirements.

        Called when a queue item has no ams_mapping set — either for model-based
        items after printer assignment, or printer-specific items (e.g. from VP).

        Args:
            db: Database session
            printer_id: The assigned printer ID
            item: The queue item (contains archive_id or library_file_id)

        Returns:
            AMS mapping array or None if no mapping needed/possible
        """
        # Get printer status
        status = printer_manager.get_status(printer_id)
        if not status:
            logger.warning("Cannot compute AMS mapping: printer %s status unavailable", printer_id)
            return None

        # Filament Track Switch (FTS): when installed it routes any AMS slot to
        # either extruder, so the per-nozzle hard filter below must NOT apply.
        # Otherwise a print on one nozzle can't use a spool physically loaded in
        # an AMS on the *other* nozzle, and the matcher falls through to a
        # same-type wrong-colour spool on the target nozzle — the H2C + FTS
        # wrong-filament bug (#2186). Mirrors the frontend skip added for #1162.
        fts_installed = bool(getattr(getattr(status, "fila_switch", None), "installed", False))

        # Get filament requirements from source file
        filament_reqs = await self._get_filament_requirements(db, item)
        if not filament_reqs:
            # When the 3MF can't be read but force-color overrides are present, build a
            # direct mapping from the overrides so the printer uses the correct AMS slot.
            if item.filament_overrides:
                try:
                    overrides = json.loads(item.filament_overrides)
                    force_overrides = [o for o in overrides if o.get("force_color_match")]
                    if force_overrides:
                        logger.info(
                            "Queue item %s: No filament reqs from 3MF; building AMS mapping from %d "
                            "force-color override(s)",
                            item.id,
                            len(force_overrides),
                        )
                        return self._build_override_direct_mapping(force_overrides, status)
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning("Queue item %s: Force-color fallback mapping failed: %s", item.id, e)
            logger.debug("No filament requirements found for queue item %s", item.id)
            return None

        self._apply_filament_overrides(item, filament_reqs)

        # Build loaded filaments from printer status
        loaded_filaments = self._build_loaded_filaments(status)
        if not loaded_filaments:
            logger.debug("No filaments loaded on printer %s", printer_id)
            return None

        # Check if user prefers lowest remaining filament when multiple spools match
        prefer_lowest = await self._get_bool_setting(db, "prefer_lowest_filament")

        # Gate prefer_lowest on the printer's AMS Filament Backup state (#1766).
        # Without backup, the printer will not switch to a second spool when the
        # picked one runs out — so sorting toward the lowest leaves the print
        # at risk of running dry mid-job. None (unknown / A1 family) preserves
        # today's behaviour intentionally.
        if prefer_lowest and status.ams_filament_backup is False:
            logger.info("[prefer-lowest] skipped (AMS Backup OFF on printer %s)", printer_id)
            prefer_lowest = False

        # When the preference is on, surface Bambuddy's inventory-side
        # remaining for each slot that's bound to a tracked spool, so the
        # sort beats the MQTT-only blind spot (#1508). Skip the lookup
        # entirely when the preference is off — no behaviour change for
        # users who haven't opted in.
        inventory_remain_overrides: dict[int, float] | None = None
        if prefer_lowest:
            inventory_remain_overrides = await self._build_inventory_remain_overrides(db, printer_id, loaded_filaments)

        # Compute mapping: match required filaments to available slots
        return self._match_filaments_to_slots(
            filament_reqs, loaded_filaments, prefer_lowest, inventory_remain_overrides, fts_installed
        )

    def _apply_filament_overrides(self, item: PrintQueueItem, filament_reqs: list[dict]) -> None:
        """Rewrite ``filament_reqs`` in place with the item's per-slot overrides.

        Extracted from ``_compute_ams_mapping_for_printer`` so the unmappable
        diagnosis (#2771) describes the filament the matcher actually looked
        for, not the one the 3MF was sliced with — naming the pre-override
        filament in a user-facing error would send the user to load the wrong
        spool.
        """
        if not item.filament_overrides:
            return
        try:
            overrides = json.loads(item.filament_overrides)
            override_map = {o["slot_id"]: o for o in overrides}
            for req in filament_reqs:
                if req["slot_id"] in override_map:
                    override = override_map[req["slot_id"]]
                    req["type"] = override["type"]
                    req["color"] = override["color"]
                    # A manual/preference override SWAPS the slot's filament, so the
                    # 3MF's original tray_info_idx now points at the old spool and must
                    # be cleared — matching then falls back to type+colour. A
                    # force_color_match override is not a swap: it carries the 3MF's
                    # intended variant (Basic GFA00 / Matte GFA01 / Silk GFA06), so keep
                    # it here too, letting the matcher pin the correct variant slot on a
                    # printer holding two same-colour spools of different variants (#2650).
                    # If that variant isn't loaded the matcher falls back to type+colour,
                    # so an eligible printer never fails to map.
                    req["tray_info_idx"] = (
                        override.get("tray_info_idx", "") if override.get("force_color_match") else ""
                    )
                    logger.debug(
                        "Queue item %s: Override slot %d -> %s %s",
                        item.id,
                        req["slot_id"],
                        override["type"],
                        override["color"],
                    )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to apply filament overrides for queue item %s: %s", item.id, e)

    def _build_override_direct_mapping(self, force_overrides: list[dict], status) -> list[int] | None:
        """Build an AMS mapping directly from force-color overrides without a 3MF.

        Used when ``_get_filament_requirements`` returns nothing (e.g. the 3MF's
        slice_info is missing or unreadable) but ``force_color_match`` overrides
        are present. Each override's ``slot_id``, ``type``, and ``color`` are
        treated as the filament requirement for that slot and matched against the
        current AMS state of the printer.

        Returns the same format as ``_match_filaments_to_slots``, or None when
        the AMS has no loaded filaments.
        """
        loaded = self._build_loaded_filaments(status)
        if not loaded:
            return None

        reqs = [
            {
                "slot_id": o["slot_id"],
                "type": o.get("type", ""),
                "color": o.get("color", ""),
                # These are all force_color_match overrides, so the idx (when the
                # 3MF carried one) is the intended variant, not a stale swap —
                # keep it so the matcher pins the right variant slot, falling back
                # to type+colour when it isn't loaded (#2650).
                "tray_info_idx": o.get("tray_info_idx", ""),
            }
            for o in force_overrides
        ]
        return self._match_filaments_to_slots(reqs, loaded)

    async def _get_filament_requirements(self, db: AsyncSession, item: PrintQueueItem) -> list[dict] | None:
        """Resolve the queue item's source 3MF and parse the per-slot
        filament requirements out of it. Thin DB-resolver wrapper around
        ``filament_requirements.extract_filament_requirements`` so the VP
        queue-mode write path (#1188) can reuse the same parser at upload
        time.
        """
        from backend.app.services.filament_requirements import extract_filament_requirements

        file_path: Path | None = None
        if item.archive_id:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if archive:
                file_path = settings.base_dir / archive.file_path
        elif item.library_file_id:
            result = await db.execute(LibraryFile.active().where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if library_file:
                lib_path = Path(library_file.file_path)
                file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path

        if not file_path or not file_path.exists():
            return None

        filaments = extract_filament_requirements(file_path, plate_id=item.plate_id)
        return filaments if filaments else None

    def _build_loaded_filaments(self, status) -> list[dict]:
        """Build list of loaded filaments from printer status.

        Args:
            status: PrinterState from printer_manager

        Returns:
            List of loaded filament dicts with type, color, ams_id, tray_id, global_tray_id
        """
        filaments = []

        # Get ams_extruder_map for dual-nozzle printers (H2D, H2D Pro)
        ams_extruder_map = status.raw_data.get("ams_extruder_map", {})

        # Dual-nozzle detection, used below to route external spools to an
        # extruder (#2771). Mirrors `buildLoadedFilaments` in the frontend,
        # which was corrected for #1257 while this copy kept the old signal.
        #
        # `ams_extruder_map` is derived from AMS info bits, so a dual-nozzle
        # printer with zero AMS units reports an empty map — and every external
        # spool then got `extruder_id=None`, which the nozzle-aware filter in
        # `_match_filaments_to_slots` rejects outright because `None` equals
        # neither 0 nor 1. On an X2D feeding from external spools only that left
        # nothing to match, the mapping came back all -1, and the print went out
        # with `use_ams: true` and no mapping table at all — firmware 0700_8012,
        # "Failed to get AMS mapping table".
        #
        # `nozzles` is always a two-entry list (the state seeds it with two empty
        # NozzleInfo stubs), so its length proves nothing; only a populated
        # diameter on the second entry means real hardware. The other two signals
        # are fallbacks for firmware revisions that surface one but not the
        # other: a populated `ams_extruder_map` is dual-nozzle by construction,
        # and so is more than one `vt_tray` entry, since single-nozzle printers
        # expose exactly one external feed.
        nozzles = getattr(status, "nozzles", None) or []
        vt_trays = status.raw_data.get("vt_tray") or []
        is_dual_nozzle = bool(
            (len(nozzles) > 1 and getattr(nozzles[1], "nozzle_diameter", ""))
            or ams_extruder_map
            # isinstance, because a dict here would count its ~30 keys as trays.
            # bambu_mqtt normalises vt_tray to a list before it reaches raw_data,
            # so this is unreachable — but the loop below would raise on a dict
            # and that is the pre-existing behaviour to keep, not to paper over.
            or (isinstance(vt_trays, list) and len(vt_trays) > 1)
        )

        # Parse AMS units from raw_data
        ams_data = status.raw_data.get("ams", [])
        for ams_unit in ams_data:
            ams_id = int(ams_unit.get("id", 0))
            trays = ams_unit.get("tray", [])
            is_ht = len(trays) == 1  # AMS-HT has single tray

            for tray in trays:
                tray_type = tray.get("tray_type")
                if tray_type:
                    tray_id = int(tray.get("id", 0))
                    tray_color = tray.get("tray_color", "")
                    # tray_info_idx identifies the specific spool (e.g., "GFA00", "P4d64437")
                    tray_info_idx = tray.get("tray_info_idx", "")
                    # Normalize color: remove alpha, add hash
                    color = self._normalize_color(tray_color)
                    # Calculate global tray ID
                    # AMS-HT units have IDs starting at 128 with a single tray
                    global_tray_id = ams_id if ams_id >= 128 else ams_id * 4 + tray_id

                    filaments.append(
                        {
                            "type": tray_type,
                            "color": color,
                            "tray_info_idx": tray_info_idx,
                            "ams_id": ams_id,
                            "tray_id": tray_id,
                            "is_ht": is_ht,
                            "is_external": False,
                            "global_tray_id": global_tray_id,
                            "extruder_id": ams_extruder_map.get(str(ams_id)),
                            "remain": tray.get("remain", -1),
                        }
                    )

        # Check external spool(s) (vt_tray is a list)
        for idx, vt in enumerate(vt_trays):
            if vt.get("tray_type"):
                color = self._normalize_color(vt.get("tray_color", ""))
                tray_id = int(vt.get("id", 254))
                filaments.append(
                    {
                        "type": vt["tray_type"],
                        "color": color,
                        "tray_info_idx": vt.get("tray_info_idx", ""),
                        "ams_id": -1,
                        "tray_id": idx,
                        "is_ht": False,
                        "is_external": True,
                        "global_tray_id": tray_id,
                        # 254 = VIRTUAL_TRAY_DEPUTY_ID feeds extruder 1 (left),
                        # 255 = VIRTUAL_TRAY_MAIN_ID feeds extruder 0 (right).
                        "extruder_id": (255 - tray_id) if is_dual_nozzle else None,
                        "remain": vt.get("remain", -1),
                    }
                )

        return filaments

    def _normalize_color(self, color: str | None) -> str:
        """Normalize color to #RRGGBB format."""
        if not color:
            return "#808080"
        hex_color = color.replace("#", "")[:6]
        return f"#{hex_color}"

    def _normalize_color_for_compare(self, color: str | None) -> str:
        """Normalize color for comparison (lowercase, no hash)."""
        if not color:
            return ""
        return color.replace("#", "").lower()[:6]

    def _color_distance(self, color1: str | None, color2: str | None) -> float | None:
        """Perceptual (CIEDE2000) distance, or None when either colour is unusable.

        Ranks the candidates ``_colors_are_similar`` admits (#2804). Eligibility
        stays the per-channel box that shipped — this only decides which of
        several eligible spools is closest, so nothing becomes usable or
        unusable because of it.

        It ranks by how far apart the colours *look*, not how far apart their
        numbers are. RGB distance overweights blue badly enough to invert the
        answer: against a required ``#1E4821`` green, a purple ``#38202F`` is
        the nearer of two eligible spools by RGB and the further by a factor of
        four once measured perceptually.

        Alpha is ignored, deliberately: the alpha a slicer writes for a
        transparent filament is not a colour the user chose, and counting it
        would stop a transparent filament matching itself.
        """
        return perceptual_color_distance(color1, color2)

    def _colors_are_similar(self, color1: str | None, color2: str | None, threshold: int = 40) -> bool:
        """Check if two colors are visually similar within a threshold."""
        hex1 = self._normalize_color_for_compare(color1)
        hex2 = self._normalize_color_for_compare(color2)
        if not hex1 or not hex2 or len(hex1) < 6 or len(hex2) < 6:
            return False

        try:
            r1 = int(hex1[0:2], 16)
            g1 = int(hex1[2:4], 16)
            b1 = int(hex1[4:6], 16)
            r2 = int(hex2[0:2], 16)
            g2 = int(hex2[2:4], 16)
            b2 = int(hex2[4:6], 16)
            return abs(r1 - r2) <= threshold and abs(g1 - g2) <= threshold and abs(b1 - b2) <= threshold
        except ValueError:
            return False

    async def _build_inventory_remain_overrides(
        self, db: AsyncSession, printer_id: int, loaded: list[dict]
    ) -> dict[int, float]:
        """Return ``{global_tray_id: remaining_grams}`` for AMS slots the user
        has bound to an inventory spool — Bambuddy-side or Spoolman-side.

        The MQTT ``remain`` field on a tray is the printer firmware's
        RFID-decremented value, which has two limitations the "Prefer Lowest
        Remaining Filament" feature has been ignoring (#1508):

        - it's only meaningful for Bambu RFID spools; everything else reports
          ``-1`` (then clamped to a sentinel), so multiple non-RFID trays
          compare equal and the sort collapses to AMS-slot order — the user
          who's curating inventory weights gets the lower-slot pick instead
          of the lower-remaining pick;
        - even when set, it's the *printer's* counter, not Bambuddy's
          ``label_weight - weight_used`` (internal mode) or Spoolman's
          ``remaining_weight`` (Spoolman mode) — the two diverge any time the
          user re-spools, swaps cardboard, or runs a print outside Bambuddy.

        When the user has bound a spool to a slot, their own inventory
        tracking is authoritative; this helper surfaces that value so the
        sort can prefer it. Slots without a binding are absent from the
        returned map — the caller then falls back to MQTT ``remain`` for
        those, preserving the pre-#1508 behaviour for un-tracked spools.

        Returns an empty map on any failure (no inventory bindings, DB
        error, Spoolman unreachable). A best-effort lookup; "Prefer Lowest"
        is a preference, not a guarantee.
        """
        if not loaded:
            return {}
        # External / virtual-tray slots are tracked separately from AMS — skip
        # them so a VT-loaded spool doesn't accidentally inherit a tracked
        # AMS binding (the tables use ams_id 254/255 for VT, but the cross
        # match is fiddly and out of scope for this fix).
        tracked_slots = [(f["ams_id"], f["tray_id"], f["global_tray_id"]) for f in loaded if not f.get("is_external")]
        if not tracked_slots:
            return {}

        is_spoolman = await self._is_spoolman_mode(db)
        overrides: dict[int, float] = {}

        if is_spoolman:
            result = await db.execute(
                select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == printer_id)
            )
            assignments = list(result.scalars().all())
            by_slot = {(a.ams_id, a.tray_id): a.spoolman_spool_id for a in assignments}
            from backend.app.services.filament_deficit import _spoolman_remaining_grams

            for ams_id, tray_id, gtid in tracked_slots:
                spoolman_id = by_slot.get((ams_id, tray_id))
                if spoolman_id is None:
                    continue
                grams = await _spoolman_remaining_grams(spoolman_id)
                if grams is not None:
                    overrides[gtid] = grams
            return overrides

        # Internal inventory mode (default). selectinload matches the pattern
        # used elsewhere (inventory.py, spoolman.py routes) — a single query
        # plus an eager-loaded relationship rather than an explicit join, so
        # the row-attribute shape is exactly what those routes already rely on.
        result = await db.execute(
            select(SpoolAssignment)
            .options(selectinload(SpoolAssignment.spool))
            .where(SpoolAssignment.printer_id == printer_id)
        )
        assignments = list(result.scalars().all())
        by_slot = {(a.ams_id, a.tray_id): a.spool for a in assignments}
        for ams_id, tray_id, gtid in tracked_slots:
            spool = by_slot.get((ams_id, tray_id))
            if spool is None:
                continue
            label = float(spool.label_weight or 0)
            used = float(spool.weight_used or 0)
            overrides[gtid] = max(0.0, label - used)
        return overrides

    @staticmethod
    async def _is_spoolman_mode(db: AsyncSession) -> bool:
        """Mirror of ``filament_deficit._is_spoolman_mode`` — kept private
        here to avoid making this module import-dependent on that private
        helper's signature."""
        try:
            from backend.app.api.routes.settings import get_setting

            v = await get_setting(db, "spoolman_enabled")
            return bool(v) and v.lower() == "true"
        except Exception:
            return False

    @staticmethod
    def _slot_priority(ams_id: int | None, tray_id: int | None) -> int:
        """Deterministic slot-position tie-breaker for the prefer-lowest sort.

        Three bands, matched to the emission order in ``_build_loaded_filaments``
        so a tied sort produces the same physical-position order the pre-#1508
        stable sort did (preserves the regression-free baseline):

        - Regular AMS (``ams_id`` 0..7): ``ams_id * 4 + tray_id`` → 0..31
        - AMS-HT (``ams_id`` >= 128, single tray): ``1000 + (ams_id - 128) * 4``
        - External / VT (``ams_id`` < 0, or ``None``): ``10_000``

        Banding ensures regular AMS < AMS-HT < external on ties, regardless of
        what the raw ``ams_id`` happens to be (in particular, ``ams_id = -1``
        for VT must NOT sort to a negative number or it would beat AMS slot 0).
        """
        if ams_id is None or ams_id < 0:
            return 10_000
        if ams_id >= 128:
            return 1_000 + (ams_id - 128) * 4 + (tray_id or 0)
        return ams_id * 4 + (tray_id or 0)

    @staticmethod
    def _prefer_lowest_sort_key(f: dict, overrides: dict[int, float] | None) -> tuple[int, float, int]:
        """Sort key for the "Prefer Lowest Remaining Filament" preference.

        Two-tier ordering: inventory-tracked spools always sort BEFORE
        non-tracked spools (the user has told us they care about these
        specifically), then ascending by remaining within each tier, then
        ascending by AMS slot position as the deterministic tie-breaker.

        Tiers are flagged by the first tuple element (0 = inventory-tracked,
        1 = MQTT-only / unknown). Cross-tier value comparisons never run
        because the tier flag dominates — which is what lets us mix grams
        (inventory) and percent (MQTT) without a unit conversion.

        Within the MQTT tier ``remain = -1`` (unknown) is mapped to 101 so
        spools the printer DOES know something about sort ahead of those
        it knows nothing about — preserves pre-#1508 behaviour for the
        no-inventory-binding case.

        Slot tie-breaker via ``_slot_priority`` so regular AMS < AMS-HT <
        external on ties, matching the legacy emission-order stable sort.
        """
        gtid = f.get("global_tray_id")
        slot_order = PrintScheduler._slot_priority(f.get("ams_id"), f.get("tray_id"))
        if overrides and gtid in overrides:
            return (0, overrides[gtid], slot_order)
        remain = f.get("remain", -1)
        return (1, float(remain) if remain is not None and remain >= 0 else 101.0, slot_order)

    def _match_filaments_to_slots(
        self,
        required: list[dict],
        loaded: list[dict],
        prefer_lowest: bool = False,
        inventory_remain_overrides: dict[int, float] | None = None,
        fts_installed: bool = False,
    ) -> list[int] | None:
        """Match required filaments to loaded filaments and build AMS mapping.

        Priority: unique tray_info_idx match > exact color match > similar color match > type-only match

        The tray_info_idx is a filament type identifier stored in the 3MF file when the user
        slices (e.g., "GFA00" for generic PLA, "P4d64437" for custom presets). If the same
        tray_info_idx appears in only ONE available tray, we use that tray. If multiple trays
        have the same tray_info_idx (e.g., two spools of generic PLA), we fall back to color
        matching among those trays.

        Args:
            required: List of required filaments with slot_id, type, color, tray_info_idx
            loaded: List of loaded filaments with type, color, tray_info_idx, global_tray_id

        Returns:
            AMS mapping array (position = slot_id - 1, value = global_tray_id or -1)
        """
        if not required:
            return None

        # Track used trays to avoid duplicate assignment
        used_tray_ids: set[int] = set()
        comparisons = []

        for req in required:
            req_type = (req.get("type") or "").upper()
            req_color = req.get("color", "")
            req_tray_info_idx = req.get("tray_info_idx", "")

            # Find best match: unique tray_info_idx > exact color > similar color > type-only
            idx_match = None
            exact_match = None
            similar_match = None
            similar_distance = float("inf")
            type_only_match = None

            # Get available trays (not already used)
            available = [f for f in loaded if f["global_tray_id"] not in used_tray_ids]

            # Nozzle-aware filtering: restrict to trays on the correct nozzle.
            # Hard filter — cross-nozzle assignment causes print failures
            # ("position of left hotend is abnormal"), so never fall back.
            # Skipped when an FTS is installed: it routes any AMS slot to either
            # extruder, so restricting to one nozzle would wrongly exclude the
            # correct spool sitting in the other nozzle's AMS (#2186).
            req_nozzle_id = req.get("nozzle_id")
            if req_nozzle_id is not None and not fts_installed:
                available = [f for f in available if f.get("extruder_id") == req_nozzle_id]

            # Sort by remaining filament (ascending) so lowest-remain spool wins .find().
            # Inventory-tracked spools sort before MQTT-only ones (#1508); see
            # _prefer_lowest_sort_key for the full rationale.
            if prefer_lowest:
                available.sort(key=lambda f: self._prefer_lowest_sort_key(f, inventory_remain_overrides))
                # INFO-level decision trace for "Prefer Lowest Filament" #1766.
                # One line per filament req so a bug report can be diagnosed
                # without enabling debug logging: shows what the matcher saw
                # (req shape + sorted candidate trays with their remain values
                # and any inventory override that was applied). Mirrored by
                # the picked-match log at the bottom of the loop.
                logger.info(
                    "[prefer-lowest] req slot=%s type=%r color=%r tii=%r nozzle=%s; available (sorted lowest-first): %s",
                    req.get("slot_id"),
                    req_type,
                    req_color,
                    req_tray_info_idx,
                    req_nozzle_id,
                    [
                        {
                            "gtid": f.get("global_tray_id"),
                            "type": f.get("type"),
                            "color": f.get("color"),
                            "tii": f.get("tray_info_idx"),
                            "remain": f.get("remain"),
                            "inv_g": (
                                inventory_remain_overrides.get(f.get("global_tray_id"))
                                if inventory_remain_overrides
                                else None
                            ),
                        }
                        for f in available
                    ],
                )

            # Check if tray_info_idx is unique among available trays
            if req_tray_info_idx:
                idx_matches = [f for f in available if f.get("tray_info_idx") == req_tray_info_idx]
                if len(idx_matches) == 1:
                    # Unique tray_info_idx - use it as definitive match
                    idx_match = idx_matches[0]
                    logger.debug(
                        f"Matched filament slot {req.get('slot_id')} by unique tray_info_idx={req_tray_info_idx} "
                        f"-> tray {idx_match['global_tray_id']}"
                    )
                elif len(idx_matches) > 1:
                    # Multiple trays with same tray_info_idx - use color matching among them
                    logger.debug(
                        f"Non-unique tray_info_idx={req_tray_info_idx} found in {len(idx_matches)} trays, "
                        f"using color matching among trays: {[f['global_tray_id'] for f in idx_matches]}"
                    )
                    if prefer_lowest:
                        idx_matches.sort(key=lambda f: self._prefer_lowest_sort_key(f, inventory_remain_overrides))
                    # Use color matching within this subset
                    for f in idx_matches:
                        f_color = f.get("color", "")
                        if self._normalize_color_for_compare(f_color) == self._normalize_color_for_compare(req_color):
                            if not exact_match:
                                exact_match = f
                        elif self._colors_are_similar(f_color, req_color):
                            distance = self._color_distance(f_color, req_color)
                            if distance is not None and distance < similar_distance:
                                similar_match = f
                                similar_distance = distance
                        elif not type_only_match:
                            type_only_match = f

            # If no idx_match yet, do standard type/color matching on all available trays
            if not idx_match and not exact_match and not similar_match and not type_only_match:
                for f in available:
                    f_type = (f.get("type") or "").upper()
                    if canonical_filament_type(f_type) != canonical_filament_type(req_type):
                        continue

                    # Type matches - check color
                    f_color = f.get("color", "")
                    if self._normalize_color_for_compare(f_color) == self._normalize_color_for_compare(req_color):
                        if not exact_match:
                            exact_match = f
                    elif self._colors_are_similar(f_color, req_color):
                        # Nearest wins, not first-in-tray-order. `available` is
                        # already in the caller's order (slot order, or the
                        # prefer-lowest sort), and `<` keeps the earliest of
                        # equally close spools — so that order survives as the
                        # tie-break (#2804).
                        distance = self._color_distance(f_color, req_color)
                        if distance is not None and distance < similar_distance:
                            similar_match = f
                            similar_distance = distance
                    elif not type_only_match:
                        type_only_match = f

            match = idx_match or exact_match or similar_match or type_only_match
            if match:
                used_tray_ids.add(match["global_tray_id"])
                comparisons.append({"slot_id": req.get("slot_id", 0), "global_tray_id": match["global_tray_id"]})
            else:
                comparisons.append({"slot_id": req.get("slot_id", 0), "global_tray_id": -1})
            # Which bucket won, always — not only under Prefer Lowest (#2804).
            # "Why did it pick that spool" is the question every wrong-filament
            # report starts with, and a `similar_color` win is now a ranked
            # choice among several eligible spools rather than whichever tray
            # came first, so it is worth being able to see after the fact.
            # Pairs with the "available (sorted)" log above when Prefer Lowest
            # is on (#1766).
            if match:
                bucket = (
                    "idx"
                    if idx_match is not None
                    else "exact_color"
                    if exact_match is not None
                    else "similar_color"
                    if similar_match is not None
                    else "type_only"
                )
                logger.info(
                    "[ams-match] picked gtid=%s via %s for req slot=%s%s",
                    match["global_tray_id"],
                    bucket,
                    req.get("slot_id"),
                    f" (deltaE {similar_distance:.2f})" if bucket == "similar_color" else "",
                )
            else:
                logger.info(
                    "[ams-match] NO MATCH for req slot=%s (type=%r color=%r tii=%r)",
                    req.get("slot_id"),
                    req_type,
                    req_color,
                    req_tray_info_idx,
                )

        # Build mapping array
        if not comparisons:
            return None

        max_slot_id = max(c["slot_id"] for c in comparisons)
        if max_slot_id <= 0:
            return None

        mapping = [-1] * max_slot_id
        for c in comparisons:
            slot_id = c["slot_id"]
            if slot_id and slot_id > 0:
                mapping[slot_id - 1] = c["global_tray_id"]

        return mapping

    def _mark_printer_dispatched(
        self,
        printer_id: int,
        pre_state: str | None,
        pre_subtask_id: str | None,
    ) -> None:
        """Record that a print command was just sent to ``printer_id``.

        Held until either the watchdog observes a state/subtask transition
        (success path) or the hard timeout expires. See ``_dispatch_holds``.
        """
        if not pre_state:
            # No pre_state means we can't detect a transition — fall back to a
            # pure time-based hold using empty string as a sentinel that won't
            # match any real printer state.
            pre_state = ""
        self._dispatch_holds[printer_id] = (time.monotonic(), pre_state, pre_subtask_id)

    def _release_dispatch_hold(self, printer_id: int) -> None:
        """Drop the dispatch hold for ``printer_id`` (called by the watchdog)."""
        self._dispatch_holds.pop(printer_id, None)

    def _printer_in_dispatch_hold(self, printer_id: int) -> bool:
        """True if ``printer_id`` is still inside its post-dispatch hold window.

        Returns False (and clears the hold) once any of these are true:
          - hard timeout (``_dispatch_max_hold``) has elapsed
          - the printer has transitioned out of pre_state and we're past the
            minimum cooldown
          - the printer's subtask_id has advanced past pre_subtask_id and we're
            past the minimum cooldown
        Otherwise the printer is held — caller should treat it as busy.
        """
        entry = self._dispatch_holds.get(printer_id)
        if not entry:
            return False
        started_at, pre_state, pre_subtask_id = entry
        elapsed = time.monotonic() - started_at

        if elapsed >= self._dispatch_max_hold:
            self._dispatch_holds.pop(printer_id, None)
            return False

        # Without a pre_state we can't detect a transition — fall back to the
        # min cooldown alone, then drop the hold.
        if not pre_state:
            if elapsed >= self._dispatch_min_cooldown:
                self._dispatch_holds.pop(printer_id, None)
                return False
            return True

        status = printer_manager.get_status(printer_id)
        current_state = getattr(status, "state", None) if status else None
        current_subtask_id = getattr(status, "subtask_id", None) if status else None
        transitioned = (current_state is not None and current_state != pre_state) or (
            pre_subtask_id is not None and current_subtask_id is not None and current_subtask_id != pre_subtask_id
        )

        if transitioned and elapsed >= self._dispatch_min_cooldown:
            self._dispatch_holds.pop(printer_id, None)
            return False

        return True

    def _is_printer_idle(self, printer_id: int, require_plate_clear: bool = True) -> bool:
        """Check if a printer is connected and idle."""
        if not printer_manager.is_connected(printer_id):
            logger.debug("Printer %d: not connected", printer_id)
            return False

        state = printer_manager.get_status(printer_id)
        if not state:
            logger.debug("Printer %d: no status available", printer_id)
            return False

        # Plate-clear gate: if the printer finished/failed a previous print and the user
        # hasn't acknowledged the plate was cleared, the queue must not dispatch the next
        # job — even if the printer currently reports IDLE. After Auto Off cycles the
        # printer, it boots back into IDLE with no memory of the previous finish; without
        # the persisted awaiting flag we'd bypass the confirmation prompt (#961).
        if require_plate_clear and printer_manager.is_awaiting_plate_clear(printer_id):
            logger.debug(
                "Printer %d: not idle — awaiting plate-clear acknowledgment (state=%s)",
                printer_id,
                state.state,
            )
            return False

        idle = state.state in ("IDLE", "FINISH", "FAILED")
        if not idle:
            logger.debug("Printer %d: not idle — state=%s", printer_id, state.state)
        return idle

    async def _get_setting(self, db: AsyncSession, key: str) -> str | None:
        """Read a setting value from the database."""
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        return setting.value if setting else None

    async def _get_bool_setting(self, db: AsyncSession, key: str, default: bool = False) -> bool:
        """Read a boolean setting from the database."""
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            return setting.value.lower() == "true"
        return default

    async def _get_int_setting(self, db: AsyncSession, key: str, default: int) -> int:
        """Read an int setting; falls back to default on missing/unparseable rows."""
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            try:
                return int(setting.value)
            except ValueError:
                pass
        return default

    async def _get_drying_presets(self, db: AsyncSession) -> dict[str, dict[str, int]]:
        """Get drying presets (user-configured or built-in defaults)."""
        result = await db.execute(select(Settings).where(Settings.key == "drying_presets"))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            try:
                presets = json.loads(setting.value)
                if isinstance(presets, dict) and presets:
                    return presets
            except json.JSONDecodeError:
                pass
        return self.DEFAULT_DRYING_PRESETS

    async def _get_humidity_thresholds(self, db: AsyncSession) -> dict[str, int]:
        """Per-filament humidity thresholds (#1605).

        Returns the user-configured overrides map keyed by normalized filament
        type (uppercase base, e.g. ``PLA``, ``ASA``) plus a ``default`` key for
        unknown / unmapped types. Empty / unset → empty dict, in which case
        callers fall back to ``ams_humidity_fair``.
        """
        result = await db.execute(select(Settings).where(Settings.key == "ams_humidity_thresholds"))
        setting = result.scalar_one_or_none()
        if not setting or not setting.value:
            return {}
        try:
            data = json.loads(setting.value)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, int] = {}
        for key, value in data.items():
            try:
                out[str(key).upper() if key != "default" else "default"] = int(value)
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def resolve_humidity_threshold(trays: list[dict], thresholds: dict[str, int], fallback: int) -> int:
        """Resolve the effective humidity threshold for an AMS unit (#1605).

        For mixed filament types loaded into one AMS, returns the most
        restrictive (lowest) threshold across all loaded tray types — matches
        the conservative-params strategy already used for drying temp/hours.
        Empty / unloaded trays contribute no constraint. Unknown types use the
        ``default`` key, falling through to ``fallback`` (= ``ams_humidity_fair``)
        when no per-type map is configured at all.
        """
        default = thresholds.get("default", fallback)
        if not thresholds:
            return fallback
        candidates: list[int] = []
        for tray in trays:
            tray_type = str(tray.get("tray_type") or "").strip()
            if not tray_type:
                continue
            base_type = tray_type.split()[0].upper()
            candidates.append(thresholds.get(base_type, default))
        if not candidates:
            return default
        return min(candidates)

    def _get_conservative_drying_params(
        self, trays: list[dict], module_type: str, presets: dict[str, dict[str, int]]
    ) -> tuple[int, int, str] | None:
        """Get the most conservative drying params for mixed filament types in an AMS unit.

        Returns (temp, duration_hours, filament_type) or None if no drying-eligible filaments.
        """
        temp_key = module_type if module_type in ("n3f", "n3s") else "n3f"
        hours_key = f"{temp_key}_hours"

        min_temp = None
        max_hours = None
        filament_type = ""

        for tray in trays:
            tray_type = tray.get("tray_type", "")
            if not tray_type:
                continue
            # Normalize filament type for preset lookup (e.g., "PLA Basic" -> "PLA")
            base_type = tray_type.split()[0].upper()
            preset = presets.get(base_type)
            if not preset:
                continue

            temp = preset.get(temp_key, 55)
            hours = preset.get(hours_key, 12)

            # Conservative: lowest temp, longest duration
            if min_temp is None or temp < min_temp:
                min_temp = temp
            if max_hours is None or hours > max_hours:
                max_hours = hours
            if not filament_type:
                filament_type = base_type

        if min_temp is None:
            return None
        return (min_temp, max_hours or 12, filament_type)

    async def _check_auto_drying(
        self,
        db: AsyncSession,
        queue_items: list[PrintQueueItem],
        dispatching_printers: set[int],
    ):
        """Start drying on idle printers based on humidity.

        Three modes (can all be enabled independently):
        - queue_drying_enabled: Dry between scheduled queue prints
        - ambient_drying_enabled: Dry any idle printer when humidity is high, regardless of queue
        - print_drying_enabled: Also evaluate printers that are currently printing,
          when model+firmware supports "Print While Drying" (gated by
          supports_drying_while_printing). Drying temperature is capped at
          max(40, preset_temp - 5) to protect spools mid-print.
        """
        queue_drying_enabled = await self._get_bool_setting(db, "queue_drying_enabled")
        ambient_drying_enabled = await self._get_bool_setting(db, "ambient_drying_enabled")
        print_drying_enabled = await self._get_bool_setting(db, "print_drying_enabled")
        if not queue_drying_enabled and not ambient_drying_enabled:
            # Stop active drying on all printers if both features disabled
            if self._drying_in_progress:
                for pid in list(self._drying_in_progress):
                    if pid in self._scheduled_drying_printer_ids:
                        continue
                    logger.info("Auto-drying: printer %d — stopping, auto-drying disabled", pid)
                    await self._stop_drying(pid)
            return

        # Update drying state from printer status (handles backend restart)
        self._sync_drying_state()

        # Find printers with scheduled items (for queue drying mode)
        printers_with_scheduled: set[int] = set()
        printers_with_items: set[int] = set()
        for item in queue_items:
            if item.printer_id:
                printers_with_items.add(item.printer_id)
                if item.scheduled_time and not item.manual_start:
                    printers_with_scheduled.add(item.printer_id)

        # If only queue mode is on and no printers have scheduled items, stop drying
        # (but skip this short-circuit when print_drying_enabled is on — busy printers
        # may still be eligible for mid-print drying regardless of queue state).
        if not ambient_drying_enabled and not printers_with_scheduled and not print_drying_enabled:
            for pid in list(self._drying_in_progress):
                if pid in self._scheduled_drying_printer_ids:
                    continue
                logger.info("Auto-drying: printer %d — stopping, no scheduled prints in queue", pid)
                await self._stop_drying(pid)
            return

        # Get humidity threshold (global fallback)
        result = await db.execute(select(Settings).where(Settings.key == "ams_humidity_fair"))
        setting = result.scalar_one_or_none()
        global_humidity_threshold = int(setting.value) if setting else 60

        # Per-filament humidity threshold overrides (#1605). Empty → fall back
        # to the global threshold for every AMS unit.
        per_type_thresholds = await self._get_humidity_thresholds(db)

        # Get drying presets
        presets = await self._get_drying_presets(db)

        # Determine if drying should be skipped for printers with pending items
        block_for_drying = await self._get_bool_setting(db, "queue_drying_block")

        # Get all active printers
        all_printers = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
        for printer in all_printers.scalars():
            pid = printer.id

            # Resolve model+firmware up front — needed to decide whether this printer
            # qualifies for mid-print drying (busy printer on capable hardware).
            state = printer_manager.get_status(pid)
            if not state:
                logger.debug("Auto-drying: printer %d skipped — no state", pid)
                continue
            model = printer_manager.get_model(pid)
            firmware = state.firmware_version

            # "Mid-print" has to mean the printer is actually printing (#2801).
            # It used to be inferred from the dispatch set, which also holds
            # printers that merely could not be dispatched to -- so a printer
            # sitting in FINISH behind an unacknowledged plate was treated as
            # printing, had its drying temperature capped by the mid-print
            # spool protection, and was logged as (mid-print) while idle.
            is_printing = state.state in _ACTIVE_PRINT_STATES
            mid_print = is_printing and print_drying_enabled and supports_drying_while_printing(model, firmware)

            # A printer whose print is running or imminent is left alone unless
            # it can dry through it. `dispatching_printers` is deliberately the
            # narrow set: running, held post-dispatch, or mid-upload.
            if (is_printing or pid in dispatching_printers) and not mid_print:
                logger.debug("Auto-drying: printer %d skipped — printing or about to", pid)
                continue

            if not mid_print:
                # In queue-only mode, only dry printers that have scheduled prints
                if not ambient_drying_enabled and pid not in printers_with_scheduled:
                    if self._drying_in_progress.get(pid) and pid not in self._scheduled_drying_printer_ids:
                        logger.info("Auto-drying: printer %d — stopping, no scheduled prints for this printer", pid)
                        await self._stop_drying(pid)
                    logger.debug("Auto-drying: printer %d skipped — no scheduled prints", pid)
                    continue
                # When block mode is on, don't START new drying on printers with pending items.
                # But allow already-drying printers through so humidity auto-stop logic still runs.
                if block_for_drying and pid in printers_with_items and not self._drying_in_progress.get(pid):
                    logger.debug("Auto-drying: printer %d skipped — has pending items (block mode)", pid)
                    continue
            if not printer_manager.is_connected(pid):
                logger.debug("Auto-drying: printer %d skipped — not connected", pid)
                continue
            # Plate-clear is deliberately ignored here (#2801). It answers
            # "is the bed ready for the next job", which says nothing about
            # whether the AMS may heat -- and the gap between a finished print
            # and the acknowledgment is exactly when drying is most useful,
            # because the printer is free and nobody is waiting on it. Leaving
            # the plate unacknowledged is also how people hold the queue by
            # hand, and that hold should not cost them their drying.
            if not mid_print and not self._is_printer_idle(pid, require_plate_clear=False):
                logger.debug("Auto-drying: printer %d skipped — not idle", pid)
                continue

            # Check drying capability. For mid-print path, supports_drying_while_printing
            # was already verified when computing mid_print above.
            if not mid_print and not supports_drying(model, firmware):
                logger.debug("Auto-drying: printer %d skipped — model %s does not support drying", pid, model)
                continue

            # Check each AMS unit from raw_data
            ams_list = state.raw_data.get("ams", [])
            logger.debug("Auto-drying: printer %d — checking %d AMS units", pid, len(ams_list))
            for ams_data in ams_list:
                module_type = str(ams_data.get("module_type") or "")
                ams_id = int(ams_data.get("id", 0))
                # Only n3f/n3s support drying
                if module_type not in ("n3f", "n3s"):
                    logger.debug("Auto-drying: printer %d AMS %d skipped — module_type=%s", pid, ams_id, module_type)
                    continue

                # Resolve per-filament humidity threshold for this AMS unit (#1605).
                # Most-restrictive of all loaded tray types; falls back to the
                # global threshold when no overrides are configured.
                trays = ams_data.get("tray", []) or []
                humidity_threshold = self.resolve_humidity_threshold(
                    trays, per_type_thresholds, global_humidity_threshold
                )

                dry_time = int(ams_data.get("dry_time") or 0)

                # Read humidity — prefer humidity_raw (actual %) over humidity (index 1-5)
                humidity = None
                h_raw = ams_data.get("humidity_raw")
                if h_raw is not None:
                    try:
                        humidity = int(h_raw)
                    except (ValueError, TypeError):
                        pass
                if humidity is None:
                    h_idx = ams_data.get("humidity")
                    if h_idx is not None:
                        try:
                            humidity = int(h_idx)
                        except (ValueError, TypeError):
                            pass
                unit_key = (pid, ams_id)
                unit_state = self._auto_dry_units.get(unit_key)

                # Already drying — let it run to its configured duration (#1892).
                #
                # We deliberately do NOT stop drying from a humidity re-check here.
                # Relative humidity drops steeply in heated air, so the AMS sensor
                # reads ~15-20% within minutes of the dryer starting even while the
                # filament is still saturated. A humidity-based early-stop therefore
                # always fires at the minimum-time floor, truncating both user-started
                # manual cycles and Bambuddy's own preset-duration dries to ~30 min.
                # The firmware stops when the configured duration elapses; scheduling
                # stops (print takes priority, queue no longer needs drying) are
                # handled separately via _stop_drying().
                if dry_time > 0:
                    if pid not in self._drying_in_progress:
                        # Drying we didn't start (manual or from before restart) —
                        # track it so scheduling stops still apply; never auto-stop it.
                        self._drying_in_progress[pid] = time.monotonic()
                    if unit_state is not None:
                        unit_state["running"] = True
                    logger.debug(
                        "Auto-drying: printer %d AMS %d — drying (%dm left, humidity %s%%), letting it run",
                        pid,
                        ams_id,
                        dry_time,
                        humidity,
                    )
                    continue

                # Nothing is drying. Close out a cycle we armed ourselves and
                # judge whether it achieved anything (#2770).
                #
                # "Achieved anything" is measured against the LOWEST reading any
                # cycle on this unit has ended at, not against the threshold and
                # not against the previous cycle. A spool that is genuinely wet
                # in a humid room comes down slowly — 40, 37, 35 — and must be
                # allowed to keep going for as long as it is still coming down,
                # however far it still is from the threshold. What must not
                # continue is a cycle that ends exactly where the last one did,
                # which is the reporter's signature: 15, 15, 16, 15, forever.
                # Comparing against the running minimum rather than the previous
                # end is what stops a sensor oscillating by one point between two
                # values from reading as progress every other cycle.
                if unit_state is not None and unit_state.pop("running", False):
                    unit_state["ended_at"] = time.monotonic()
                    if humidity is not None and humidity > humidity_threshold:
                        best = unit_state.get("best_end_humidity")
                        if isinstance(best, int) and humidity < best:
                            # Still coming down. Keep going, however far the
                            # threshold still is.
                            unproductive = 0
                        else:
                            unproductive = int(unit_state.get("unproductive", 0)) + 1
                        if not isinstance(best, int) or humidity < best:
                            unit_state["best_end_humidity"] = humidity
                        unit_state["unproductive"] = unproductive
                        logger.info(
                            "Auto-drying: printer %d AMS %d — cycle ended with humidity still %d%% > "
                            "threshold %d%% (best so far %s%%, %d unproductive in a row)",
                            pid,
                            ams_id,
                            humidity,
                            humidity_threshold,
                            unit_state.get("best_end_humidity"),
                            unproductive,
                        )
                    # A cycle that ended at or below the threshold needs no
                    # counter reset here: the branch below drops the whole entry.

                # Humidity below threshold — no need to start drying. This is
                # also the only thing that lifts a suspension: the reading we
                # gave up on has come down, so auto-drying works again and the
                # unit goes back to having no history at all.
                if humidity is None or humidity <= humidity_threshold:
                    if unit_state is not None and unit_state.get("suspended"):
                        logger.info(
                            "Auto-drying: printer %d AMS %d — humidity %s%% is back at or below the %d%% "
                            "threshold, resuming automatic drying",
                            pid,
                            ams_id,
                            humidity,
                            humidity_threshold,
                        )
                    # Clear the judgement, keep the clock (#2801). Dropping the
                    # whole entry also dropped `ended_at`, and with it the
                    # 30-minute cooldown -- so a reading that dips to the
                    # threshold as the AMS cools and comes back above it once
                    # warm wiped its own history and re-armed immediately. That
                    # oscillation is the very thing #2770's cooldown exists to
                    # ride out, and it is worst at exactly the margin that makes
                    # a unit dry repeatedly: a point or two above the threshold.
                    if unit_state is not None:
                        unit_state.pop("suspended", None)
                        unit_state.pop("unproductive", None)
                        unit_state.pop("best_end_humidity", None)
                    logger.debug(
                        "Auto-drying: printer %d AMS %d skipped — humidity %s <= threshold %d",
                        pid,
                        ams_id,
                        humidity,
                        humidity_threshold,
                    )
                    continue

                if unit_state is not None:
                    if unit_state.get("suspended"):
                        logger.debug(
                            "Auto-drying: printer %d AMS %d skipped — suspended, %d cycles left humidity "
                            "above the %d%% threshold",
                            pid,
                            ams_id,
                            int(unit_state.get("unproductive", 0)),
                            humidity_threshold,
                        )
                        continue

                    if int(unit_state.get("unproductive", 0)) >= AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES:
                        unit_state["suspended"] = True
                        logger.warning(
                            "Auto-drying: printer %d AMS %d — suspending automatic drying. %d cycles in a "
                            "row ended with humidity at %d%%, still above the %d%% threshold. The AMS reads "
                            "higher while it is warm, so a threshold in that range can never be reached and "
                            "re-arming would loop. Raise the threshold or dry the spools off the printer.",
                            pid,
                            ams_id,
                            int(unit_state.get("unproductive", 0)),
                            humidity,
                            humidity_threshold,
                        )
                        await self._notify_auto_drying_suspended(
                            db, printer, ams_id, humidity, humidity_threshold, int(unit_state.get("unproductive", 0))
                        )
                        continue

                    ended_at = unit_state.get("ended_at")
                    if isinstance(ended_at, float) and time.monotonic() - ended_at < AUTO_DRY_REARM_COOLDOWN_SECONDS:
                        logger.debug(
                            "Auto-drying: printer %d AMS %d skipped — cooling off for %ds after the last "
                            "cycle before the humidity reading is worth acting on",
                            pid,
                            ams_id,
                            AUTO_DRY_REARM_COOLDOWN_SECONDS,
                        )
                        continue

                # Check cannot-dry reasons (power constraints etc.)
                sf_reasons = ams_data.get("dry_sf_reason", [])
                if sf_reasons:
                    logger.debug(
                        "Auto-drying: printer %d AMS %d skipped — cannot dry reasons: %s",
                        pid,
                        ams_id,
                        sf_reasons,
                    )
                    continue

                # Get conservative drying params for mixed filaments
                params = self._get_conservative_drying_params(trays, module_type, presets)
                if not params:
                    logger.debug(
                        "Auto-drying: printer %d AMS %d skipped — no drying-eligible filaments in trays", pid, ams_id
                    )
                    continue

                temp, duration_hours, filament_type = params

                # Mid-print drying: cap drying temperature to protect spools (Bambu warns
                # "drying temperature must not exceed the filament's softening temperature"
                # for Print While Drying). Floor at 40 degC — below that the dryer is
                # ineffective and firmware will reject anyway.
                if mid_print:
                    temp = max(40, temp - 5)

                # Start drying
                logger.info(
                    "Auto-drying: printer %d AMS %d — humidity %d%% > threshold %d%%, "
                    "starting %s drying at %d°C for %dh%s",
                    pid,
                    ams_id,
                    humidity,
                    humidity_threshold,
                    filament_type,
                    temp,
                    duration_hours,
                    " (mid-print)" if mid_print else "",
                )
                success = printer_manager.send_drying_command(
                    pid, ams_id, temp, duration_hours, mode=1, filament=filament_type
                )
                if success:
                    self._drying_in_progress[pid] = time.monotonic()
                    armed = self._auto_dry_units.setdefault(
                        unit_key, {"unproductive": 0, "suspended": False, "ended_at": None}
                    )
                    armed["running"] = True

    async def _notify_auto_drying_suspended(
        self,
        db: AsyncSession,
        printer: Printer,
        ams_id: int,
        humidity: int,
        threshold: int,
        cycles: int,
    ) -> None:
        """Tell the user auto-drying has given up on one AMS unit (#2770).

        Fires once per suspension — the caller sets ``suspended`` before calling
        and every later pass short-circuits on it — because the whole point is
        that Bambuddy has stopped acting. Somebody whose printer sits in another
        building needs that to reach them, and the hourly humidity alarm they
        are already getting says the opposite of what happened here.

        Never raises: a notification provider being down must not stop the
        suspension itself from taking effect.
        """
        ams_label = f"HT-{chr(65 + (ams_id - 128))}" if ams_id >= 128 else f"AMS-{chr(65 + ams_id)}"
        try:
            await notification_service.on_ams_drying_suspended(
                printer.id,
                printer.name,
                ams_label,
                float(humidity),
                float(threshold),
                cycles,
                db,
            )
        except Exception as e:
            logger.warning("Failed to send auto-drying suspended notification: %s", e)

    def forget_auto_dry_cycle(self, printer_id: int, ams_id: int) -> None:
        """Stop judging the drying cycle currently on this AMS unit (#2770).

        The unproductive-cycle counter exists to notice that *drying* is not
        moving the humidity reading. A cycle that ended because somebody sent a
        stop says nothing about that — it was cut short before it had a chance —
        so counting it would suspend auto-drying for a reason that has nothing to
        do with the loop the counter is there to break.

        Two callers, both of them a stop Bambuddy is responsible for: the
        print-takes-priority stop below, and the manual Stop button. Left
        uncalled, an install that dries between queue jobs would suspend its own
        auto-drying after two prints interrupted a dry — exactly the install
        queue-drying exists for.

        The rest of the unit's history is kept: the cooldown before re-arming
        still applies, and an earlier count still stands.
        """
        state = self._auto_dry_units.get((printer_id, ams_id))
        if state is not None:
            state.pop("running", None)
            state["ended_at"] = time.monotonic()

    def _sync_drying_state(self):
        """Drop printers from ``_drying_in_progress`` that are no longer drying.

        One direction only: it prunes, it never adds. A printer drying without an
        entry here — because the user started the cycle from Studio, the printer's
        screen or Bambuddy's own manual Dry button, or because Bambuddy restarted
        mid-cycle — stays unknown to the scheduler, so the "print takes priority"
        stop at ``check_queue`` only ever applies to cycles Bambuddy itself began.

        That is deliberate for now rather than an oversight: populating this from
        telemetry would hand the scheduler authority to stop drying a user started
        by hand. It also means the backend-restart case this used to claim to
        handle is not handled.
        """
        to_remove = []
        for pid in self._drying_in_progress:
            state = printer_manager.get_status(pid)
            if not state:
                to_remove.append(pid)
                continue
            # Check if any AMS unit is still drying
            ams_list = state.raw_data.get("ams", [])
            any_drying = any(int(a.get("dry_time") or 0) > 0 for a in ams_list)
            if not any_drying:
                to_remove.append(pid)
        for pid in to_remove:
            self._drying_in_progress.pop(pid, None)

        # A printer that has gone away entirely takes its per-AMS auto-drying
        # history with it (#2770), so a printer deleted and re-added does not
        # inherit a suspension it never earned.
        for key in [k for k in self._auto_dry_units if printer_manager.get_status(k[0]) is None]:
            self._auto_dry_units.pop(key, None)

    async def _drying_may_continue_through_print(self, db: AsyncSession, printer_id: int) -> bool:
        """True when a running cycle can be left alone while the next print runs.

        Some hardware dries happily through a print and #2758 settled that we
        should not tear those cycles down: the X2D there was refusing to
        *start* a job, which is a different problem, and stopping drying before
        every dispatch would throw away cycles the printer was content to run.
        Where the model cannot do it, or the user has not enabled it, the print
        takes priority and the cycle stops -- which is what the queue_drying_block
        setting has always promised in its off position.
        """
        if not await self._get_bool_setting(db, "print_drying_enabled"):
            return False
        status = printer_manager.get_status(printer_id)
        return supports_drying_while_printing(
            printer_manager.get_model(printer_id),
            status.firmware_version if status else None,
        )

    async def _stop_drying(self, printer_id: int):
        """Stop drying cycles Bambuddy armed on a printer (print takes priority).

        Scoped to units in ``_auto_dry_units``. It used to send a stop to every
        AMS reporting ``dry_time > 0``, which meant one auto-dried unit was
        enough to kill a cycle the user had started by hand on a *different*
        unit of the same printer (#2801). That contradicted the contract
        ``_sync_drying_state`` already documents -- the entry gate deliberately
        only knows about cycles Bambuddy began, so the action must not reach
        past them either.
        """
        state = printer_manager.get_status(printer_id)
        if not state:
            self._drying_in_progress.pop(printer_id, None)
            return

        ams_list = state.raw_data.get("ams", [])
        for ams_data in ams_list:
            dry_time = int(ams_data.get("dry_time") or 0)
            if dry_time > 0:
                ams_id = int(ams_data.get("id", 0))
                if (printer_id, ams_id) not in self._auto_dry_units:
                    logger.debug(
                        "Auto-drying: leaving printer %d AMS %d alone — not a cycle Bambuddy started",
                        printer_id,
                        ams_id,
                    )
                    continue
                logger.info(
                    "Auto-drying: stopping drying on printer %d AMS %d — print takes priority",
                    printer_id,
                    ams_id,
                )
                printer_manager.send_drying_command(printer_id, ams_id, 0, 0, mode=0)
                self.forget_auto_dry_cycle(printer_id, ams_id)
        self._drying_in_progress.pop(printer_id, None)

    # Scheduled manual drying (#2638) -----------------------------------

    SCHEDULED_DRYING_GRACE_SECONDS = 120  # firmware needs time to report dry_time
    SCHEDULED_DRYING_COMPLETE_FRACTION = 0.9  # dry_time==0 earlier than this = interrupted

    async def _check_scheduled_dryings(self, db: AsyncSession):
        """Dispatch due scheduled drying runs and track running ones."""
        now = utcnow_naive()

        # Hourly, not every pass: see SCHEDULED_DRYING_PRUNE_INTERVAL_SECONDS.
        # Monotonic, so a clock adjustment cannot park the prune for hours.
        since_prune = time.monotonic()
        if (
            self._last_scheduled_drying_prune is None
            or since_prune - self._last_scheduled_drying_prune >= SCHEDULED_DRYING_PRUNE_INTERVAL_SECONDS
        ):
            self._last_scheduled_drying_prune = since_prune
            await db.execute(
                delete(ScheduledDrying).where(
                    ScheduledDrying.status.in_(("completed", "cancelled", "failed")),
                    ScheduledDrying.completed_at.is_not(None),
                    ScheduledDrying.completed_at < now - timedelta(days=SCHEDULED_DRYING_RETENTION_DAYS),
                )
            )

        # Same order as the list route: with two rows due on one printer the
        # earliest scheduled wins rather than whatever the DB hands back first.
        result = await db.execute(
            select(ScheduledDrying)
            .where(ScheduledDrying.status.in_(("pending", "running")))
            .order_by(ScheduledDrying.start_after.asc().nullsfirst(), ScheduledDrying.id.asc())
        )
        rows = list(result.scalars().all())

        # Rebuild from the DB every tick so route-side cancels and completions
        # show up. Auto-drying's stop-all branches check this set before
        # stopping anything (#2638).
        # Kept from the previous pass so a run that ended between passes — a
        # cancel through the route, say — can still be released below.
        previously_running = self._scheduled_drying_printer_ids
        self._scheduled_drying_printer_ids = {row.printer_id for row in rows if row.status == "running"}
        running_printer_ids = set(self._scheduled_drying_printer_ids)

        # Model and firmware come from the printer row, not the live state.
        printer_ids = {row.printer_id for row in rows}
        printers_by_id: dict[int, Printer] = {}
        if printer_ids:
            printer_rows = await db.execute(select(Printer).where(Printer.id.in_(printer_ids)))
            printers_by_id = {p.id: p for p in printer_rows.scalars()}

        for row in rows:
            if row.status == "running":
                self._update_running_scheduled_drying(row, now)
                continue

            if row.start_after is not None and row.start_after > now:
                continue

            state = printer_manager.get_status(row.printer_id)
            if not state:
                row.waiting_reason = "printer_offline"
                continue

            # Same preflight the immediate endpoint runs. Without it the publish
            # succeeds, the row goes to running, the printer ignores the command
            # and the run silently cancels itself after the grace window.
            printer = printers_by_id.get(row.printer_id)
            unsupported = drying_preflight.check_drying_supported(
                printer.model if printer else None, state.firmware_version
            )
            if unsupported:
                row.status = "failed"
                row.error_message = unsupported
                row.completed_at = now
                logger.warning("Scheduled drying %d: %s", row.id, unsupported)
                continue

            if self._drying_in_progress.get(row.printer_id) or row.printer_id in running_printer_ids:
                row.waiting_reason = "already_drying"
                continue
            if not self._is_printer_idle(row.printer_id, require_plate_clear=False):
                row.waiting_reason = "printer_busy"
                continue

            target = drying_preflight.find_ams_unit(state, row.ams_id)
            if target is None:
                row.waiting_reason = "ams_not_found"
                continue
            blocking = drying_preflight.blocking_reason_codes(target)
            if blocking:
                # Keep the power case distinct; it needs the user to act, so the
                # card can say so instead of waiting silently.
                row.waiting_reason = drying_preflight.waiting_reason_for_codes(blocking)
                continue

            filament = drying_preflight.resolve_filament(target, row.filament)
            logger.info(
                "Scheduled drying %d: starting on printer %d AMS %d at %d°C for %dh",
                row.id,
                row.printer_id,
                row.ams_id,
                row.temp,
                row.duration_hours,
            )
            success = printer_manager.send_drying_command(
                row.printer_id,
                row.ams_id,
                row.temp,
                row.duration_hours,
                mode=1,
                filament=filament,
                rotate_tray=row.rotate_tray,
            )
            if success:
                row.status = "running"
                row.started_at = now
                row.waiting_reason = None
                row.filament = filament
                self._drying_in_progress[row.printer_id] = time.monotonic()
                self._scheduled_drying_printer_ids.add(row.printer_id)
                running_printer_ids.add(row.printer_id)
            else:
                row.waiting_reason = "printer_offline"

        # Release the printers whose run has ended. `_drying_in_progress` is
        # shared with auto-drying, which prunes it in `_sync_drying_state()` —
        # but that call sits behind the auto-drying enabled check, and this
        # method is the one writer that runs whether auto-drying is on or not.
        # With it off, nothing would ever drop the entry short of a print being
        # dispatched to the same printer, so the next scheduled run would wait
        # on "already_drying" forever and `queue_drying_block` would hold the
        # printer's prints too. Covers a run that ended during this pass and one
        # cancelled through the route between passes.
        self._scheduled_drying_printer_ids = {row.printer_id for row in rows if row.status == "running"}
        for printer_id in (previously_running | running_printer_ids) - self._scheduled_drying_printer_ids:
            self._drying_in_progress.pop(printer_id, None)

        await db.commit()

    def _update_running_scheduled_drying(self, row: ScheduledDrying, now: datetime):
        """Detect completion or interruption of a running scheduled drying.

        The firmware reports remaining minutes in ams.dry_time; 0 means not
        drying. Within the grace window after start we ignore dry_time==0
        (the status lags the command). After that, dry_time==0 near the end
        of the configured duration means completed. Much earlier means the
        run was stopped: re-queue it if a print preempted the dryer, but a
        stop while the printer is idle was deliberate, so cancel the row
        rather than restart drying the user just stopped.
        """
        if row.started_at is None:
            row.started_at = now
            return
        elapsed = (now - row.started_at).total_seconds()
        if elapsed < self.SCHEDULED_DRYING_GRACE_SECONDS:
            return

        state = printer_manager.get_status(row.printer_id)
        if not state:
            return  # offline mid-dry; resolve when it reconnects

        # find_ams_unit, not a local lookup: this runs inside check_queue, so a
        # throw on a malformed id would cost the whole pass including print
        # dispatch, every tick.
        target = drying_preflight.find_ams_unit(state, row.ams_id)
        try:
            dry_time = int(target.get("dry_time") or 0) if target else 0
        except (TypeError, ValueError):
            dry_time = 0
        if dry_time > 0:
            return

        if elapsed >= row.duration_hours * 3600 * self.SCHEDULED_DRYING_COMPLETE_FRACTION:
            row.status = "completed"
            row.completed_at = now
        elif not self._is_printer_idle(row.printer_id, require_plate_clear=False):
            row.status = "pending"
            row.started_at = None
            row.waiting_reason = "interrupted"
        else:
            row.status = "cancelled"
            row.completed_at = now

    async def _get_smart_plugs(self, db: AsyncSession, printer_id: int) -> list[SmartPlug]:
        """Get all smart plugs associated with a printer."""
        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        return list(result.scalars().all())

    @staticmethod
    def _pick_power_plug(auto_on_plugs: list[SmartPlug]) -> SmartPlug:
        """Pick the plug to power-cycle a printer back online with (#2629).

        Only a plug flagged ``controls_printer_power`` can actually bring the
        printer back; waiting for a boot on an accessory (filter fan, lights)
        just burns the power-on timeout and fails the dispatch. Falls back to
        the first plug when none is flagged, which is the pre-#2629 behaviour.
        Callers must pass a non-empty list.
        """
        for plug in auto_on_plugs:
            if plug.controls_printer_power:
                return plug
        return auto_on_plugs[0]

    # Bundled defaults for preheat_filament_targets (#1468). Values are the
    # chamber-temperature recommendations BambuStudio ships for the matching
    # filament profile; users can override via Settings → Workflow → Preheat
    # card. "default" applies when a loaded tray's normalised type isn't in
    # the map (rare — Bambu RFID-tagged spools always carry a known type).
    DEFAULT_PREHEAT_FILAMENT_TARGETS: dict[str, int] = {
        "PLA": 0,
        "PETG": 0,
        "PETG-CF": 40,
        "ABS": 45,
        "ASA": 45,
        "PA": 50,
        "PA-CF": 55,
        "PC": 50,
        "PC-FR": 50,
        "TPU": 0,
        "PVA": 0,
        "default": 0,
    }

    async def _get_preheat_filament_targets(self, db: AsyncSession) -> dict[str, int]:
        """Parse the user-configured filament→chamber-target map, falling back
        to DEFAULT_PREHEAT_FILAMENT_TARGETS on missing / malformed JSON. Keys
        are uppercased and the 'default' fallback is always present in the
        returned dict so the resolution loop can index it unconditionally."""
        raw = await self._get_setting(db, "preheat_filament_targets")
        if not raw:
            return dict(self.DEFAULT_PREHEAT_FILAMENT_TARGETS)
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("preheat_filament_targets unparseable, using defaults: %s", exc)
            return dict(self.DEFAULT_PREHEAT_FILAMENT_TARGETS)
        # Coerce values to int; drop unparseable rows so a stray string
        # doesn't crash the loop.
        out: dict[str, int] = {}
        for key, value in parsed.items():
            try:
                out[str(key).upper()] = int(value)
            except (TypeError, ValueError):
                continue
        if "DEFAULT" not in out:
            out["DEFAULT"] = self.DEFAULT_PREHEAT_FILAMENT_TARGETS["default"]
        return out

    @staticmethod
    def _normalize_filament_type(tray_type: str) -> str:
        """Reduce the printer's tray_type to a preset-lookup key. Mirrors the
        existing drying-preset normalisation (split-at-space, upper-case) so
        the two maps share vocabulary — "PLA Basic" → "PLA", "PA-CF" stays
        "PA-CF" (no space to split on)."""
        return tray_type.split()[0].upper() if tray_type else ""

    def _derive_chamber_target(
        self,
        printer: Printer,
        targets: dict[str, int],
    ) -> int:
        """Look up the chamber target for each loaded AMS tray and return the
        max. Returns 0 when no AMS data is available (e.g. external-spool
        prints) or when every loaded slot maps to 0 — the chamber phase then
        short-circuits in the main loop.

        Reads from `printer_manager.get_status(...).raw_data['ams']`, which is
        the same source the dispatcher uses for AMS slot mapping. Empty / RFID-
        less slots have empty `tray_type` and contribute nothing."""
        state = printer_manager.get_status(printer.id)
        if state is None:
            return 0
        ams_list = (state.raw_data or {}).get("ams") if state.raw_data else None
        # Older Bambu firmware nests AMS as {"ams": {"ams": [...]}} — try both.
        if isinstance(ams_list, dict):
            ams_list = ams_list.get("ams") or []
        if not isinstance(ams_list, list):
            return 0
        best = 0
        for ams in ams_list:
            for tray in (ams.get("tray") or []) if isinstance(ams, dict) else []:
                normalised = self._normalize_filament_type(tray.get("tray_type") or "")
                if not normalised:
                    continue
                target = targets.get(normalised, targets.get("DEFAULT", 0))
                if target > best:
                    best = target
        return best

    def _release_keep_warm(self, pid: int) -> None:
        """Release keep-warm on a printer that left the candidate set.

        Publishes ``set_bed_temperature(0)`` once — but only if firmware still
        reports the target we set (``entry.held_target``), so a user or
        subsequent print that changed the bed target since is not clobbered.
        Best-effort, never raises.

        The entry is kept, not dropped, when the printer cannot be reached
        right now: a printer that is briefly offline still has a hot bed, and
        holding the entry is what keeps the max-duration timeout applying and
        lets a later tick retry the release. Only a printer that has left the
        manager entirely gives up on that, in ``_sample_chamber_temps``.
        """
        entry = self._keep_warm.get(pid)
        if entry is None:
            return
        state = printer_manager.get_status(pid)
        client = printer_manager.get_client(pid)
        if state is None or client is None:
            logger.debug(
                "Queue: keep-warm release for printer %d deferred — printer unreachable, entry kept",
                pid,
            )
            return
        cur_bed_target = float((state.temperatures or {}).get("bed_target", 0) or 0)
        if int(cur_bed_target) != entry.held_target:
            # Someone else owns the bed now, so there is nothing of ours to
            # undo and nothing left to track.
            logger.info(
                "Queue: keep-warm release for printer %d skipped bed-off (firmware target %d != held %d)",
                pid,
                int(cur_bed_target),
                entry.held_target,
            )
            self._keep_warm.pop(pid, None)
            return
        try:
            client.set_bed_temperature(0)
            logger.info("Queue: keep-warm released for printer %d (bed → 0)", pid)
            self._keep_warm.pop(pid, None)
        except Exception as exc:
            # Keep the entry so the next tick tries again rather than leaving
            # the bed hot with nothing tracking it.
            logger.warning("Queue: keep-warm release for printer %d failed: %s", pid, exc)

    def _sweep_keep_warm(self, active_candidates: set[int], dispatched: set[int]) -> None:
        """Release printers that dropped out of the keep-warm candidate set.

        Called from ``_apply_keep_warm`` on every tick (with the current
        candidate set), and from ``check_queue``'s no-pending-items early
        return (with an empty candidate set) so orphaned holds still get
        released when the queue empties. Also called with an empty candidate
        set when any of the three gate settings toggles off, so a printer
        whose feature was disabled mid-hold gets its bed released.

        Printers being dispatched this tick are excluded from the bed-off
        publish: ``_preheat_and_soak`` owns the bed from that tick on, so a
        transient 0 in between would just churn against preheat. Ownership of
        the hot bed transfers to the preheat rollback pin instead — if the
        dispatch aborts before the print starts (failed upload, cancelled
        item), `_rollback_preheat_pin` turns the bed off; if preheat itself
        skips (e.g. the item has no bed_temperature metadata) the pin entry
        is the ONLY thing standing between an aborted dispatch and a bed
        left hot with no owner. A successful print start clears the pin and
        the print's own gcode takes over, as usual.
        """
        for _pid in list(self._keep_warm):
            if _pid in active_candidates:
                continue
            if _pid in dispatched:
                handed_over = self._keep_warm.pop(_pid, None)
                self._preheat_pin.setdefault(_pid, set()).add("bed")
                if handed_over is not None:
                    self._preheat_pin_bed[_pid] = handed_over.held_target
                continue
            self._release_keep_warm(_pid)

    async def _apply_keep_warm(
        self,
        db: AsyncSession,
        items: list[PrintQueueItem],
        dispatch_ids: list[int] | set[int],
        busy_printers: set[int],
        require_plate_clear: bool,
    ) -> None:
        """Hold the bed warm on FINISH printers whose next queued item needs chamber heat.

        When a printer just finished a job (FINISH state) and the next queued
        item needs chamber heating, hold the bed hot so the chamber stays warm
        during the bed-clearing window. The bed is the chamber's heating
        element here, not a print surface — nothing is printing during the
        hold and the dispatched print's own preheat/gcode re-targets the bed —
        so the hold temperature is ``queue_keep_warm_bed_temp`` (default 90°C,
        chosen to sustain chamber warmth and to satisfy bed-threshold-linked
        aftermarket chamber heaters), raised to the item's own parsed
        bed_temperature when that is higher. Items whose archive metadata has
        no bed temperature (e.g. OrcaSlicer gcode.3mf exports) therefore still
        get a hold — chamber need is what gates the feature, not metadata.
        Skips entirely for filaments that map to a 0°C chamber target
        (PLA, PETG, etc.). Printers being dispatched this cycle are excluded:
        ``_preheat_and_soak`` already handles their bed temperature.

        Bounded by ``queue_keep_warm_max_minutes`` — on timeout the bed is
        released to 0 and the entry is latched ``expired=True`` so
        subsequent ticks neither re-engage nor re-seed the clock. Idempotent
        MQTT: publish is skipped when firmware already has the target.

        The release sweep runs BEFORE the engagement gate so a printer that
        was owned by keep-warm still gets its bed released when any of the
        three gate settings is toggled off mid-hold. The
        ``check_queue`` early-return-when-no-items path also calls
        ``_sweep_keep_warm`` directly to release orphaned holds.
        """
        dispatch_set = set(dispatch_ids)
        dispatched_printers = {it.printer_id for it in items if it.id in dispatch_set and it.printer_id}
        pending_printer_ids = {it.printer_id for it in items if it.printer_id}
        warm_candidates = (pending_printer_ids & busy_printers) - dispatched_printers

        keep_warm_enabled = await self._get_bool_setting(db, "queue_keep_bed_warm", default=False)
        preheat_on = await self._get_bool_setting(db, "preheat_enabled", default=False)
        gate_open = keep_warm_enabled and require_plate_clear and preheat_on

        # Release sweep first — must run even when gate_open is False so a
        # printer owned by keep-warm when a gate toggles off gets released.
        self._sweep_keep_warm(
            active_candidates=warm_candidates if gate_open else set(),
            dispatched=dispatched_printers,
        )

        if not gate_open:
            return

        hold_temp = await self._get_int_setting(db, "queue_keep_warm_bed_temp", default=90)
        max_hold_seconds = (
            await self._get_int_setting(db, "queue_keep_warm_max_minutes", default=_KEEP_WARM_MAX_MINUTES_DEFAULT) * 60
        )
        now_mono = time.monotonic()
        filament_targets: dict[str, int] | None = None
        for pid in warm_candidates:
            entry = self._keep_warm.get(pid)
            # Latched-expired: max-duration timeout already fired for this
            # printer. Skip until the release sweep drops the entry (i.e.
            # until the printer leaves the candidate set).
            if entry is not None and entry.expired:
                continue
            # These two guards sit ahead of the max-duration check below, so an
            # engaged hold only ages out while its printer is still reachable
            # and still in FINISH. That is deliberate rather than a hole: with
            # no status or no client there is no M140 to send anyway, and the
            # elapsed check runs off `entry.started` so it fires on the first
            # tick after the printer comes back. Leaving FINISH means the plate
            # was cleared, which drops the printer out of `warm_candidates` and
            # hands it to `_release_keep_warm` instead. The invariant worth
            # preserving if this is ever reordered: every path out of an
            # engaged hold ends in a bed-off, whether by timeout or release.
            state = printer_manager.get_status(pid)
            if state is None or state.state != "FINISH":
                continue
            client = printer_manager.get_client(pid)
            if client is None:
                continue
            next_item = next((it for it in items if it.printer_id == pid), None)
            if next_item is None:
                continue
            # Hold temperature: the configured keep-warm temp, raised to the
            # item's own bed temp when the metadata reports a higher one. A
            # missing bed_temperature (Orca gcode.3mf exports parse without
            # one) does NOT skip the hold — chamber need gates the feature.
            archive = next_item.archive
            item_bed = int(archive.bed_temperature) if archive and archive.bed_temperature else 0
            bed_target = max(item_bed, hold_temp)
            if bed_target <= 0:
                continue
            explicit = getattr(next_item, "preheat_chamber_target_override", None)
            if explicit is not None:
                chamber_needed = int(explicit) > 0
            else:
                if filament_targets is None:
                    filament_targets = await self._get_preheat_filament_targets(db)
                printer_obj = await self._get_printer(db, pid)
                chamber_needed = (
                    printer_obj is not None and self._derive_chamber_target(printer_obj, filament_targets) > 0
                )
            if not chamber_needed:
                continue

            # Seed the timer on first engagement; keep it on subsequent ticks
            # (never re-seed — that would defeat the max-duration cap).
            if entry is None:
                entry = _KeepWarmEntry(started=now_mono, held_target=bed_target)
                self._keep_warm[pid] = entry

            elapsed = now_mono - entry.started
            if elapsed > max_hold_seconds:
                # Timeout: publish bed → 0 once (if firmware still holds our
                # target) and latch expired. The entry stays until the release
                # sweep drops it, preventing the next tick from re-seeding.
                logger.warning(
                    "Queue: keep-warm timeout for printer %d (held for %.0fs) — publishing bed → 0",
                    pid,
                    elapsed,
                )
                cur_bed_target = float((state.temperatures or {}).get("bed_target", 0) or 0)
                if int(cur_bed_target) == entry.held_target:
                    try:
                        client.set_bed_temperature(0)
                    except Exception as exc:
                        logger.warning(
                            "Queue: keep-warm timeout bed-off failed for printer %d: %s",
                            pid,
                            exc,
                        )
                else:
                    logger.info(
                        "Queue: keep-warm timeout for printer %d skipped bed-off (firmware target %d != held %d)",
                        pid,
                        int(cur_bed_target),
                        entry.held_target,
                    )
                entry.expired = True
                continue

            # Idempotence: skip publish when firmware already has our target.
            cur_bed_target = float((state.temperatures or {}).get("bed_target", 0) or 0)
            if int(cur_bed_target) == bed_target:
                entry.held_target = bed_target
                continue
            try:
                client.set_bed_temperature(bed_target)
                entry.held_target = bed_target
                logger.info(
                    "Queue: keeping bed warm at %d°C for printer %d (FINISH, next item needs chamber heat)",
                    bed_target,
                    pid,
                )
            except Exception as exc:
                logger.warning("Queue: keep-warm bed command failed for printer %d: %s", pid, exc)

    def _sample_chamber_temps(self) -> None:
        """Record a chamber temperature sample for every connected printer.

        Called once per scheduler tick (every 3–30 s). Entries older than
        _chamber_history_ttl are pruned on each write so the deques stay bounded.
        Also evicts per-printer state whose printer_id is no longer registered
        (e.g. deleted from the DB), so nothing accumulates for gone printers.
        """
        now = time.monotonic()
        cutoff = now - self._chamber_history_ttl
        statuses = printer_manager.get_all_statuses()
        known_pids = set(statuses.keys())
        for pid, status in statuses.items():
            if status is None or not status.connected:
                continue
            temps = status.temperatures or {}
            chamber = temps.get("chamber")
            if chamber is None:
                continue
            hist = self._chamber_history.setdefault(pid, deque())
            hist.append((now, float(chamber)))
            while hist and hist[0][0] < cutoff:
                hist.popleft()
        # Evict state for printers that are no longer registered with the manager.
        # This is the one place a keep-warm entry is dropped without releasing
        # the bed: the printer is gone from the manager, so there is no client
        # left to send M140 to. `_release_keep_warm` deliberately keeps entries
        # for printers that are merely unreachable, which is what makes this
        # the terminal case rather than a silent leak.
        for pid in list(self._chamber_history):
            if pid not in known_pids:
                self._chamber_history.pop(pid, None)
        for pid in list(self._keep_warm):
            if pid not in known_pids:
                logger.info(
                    "Queue: dropping keep-warm state for printer %d — no longer registered",
                    pid,
                )
                self._keep_warm.pop(pid, None)
        for pid in list(self._preheat_pin):
            if pid not in known_pids:
                self._preheat_pin.pop(pid, None)
                self._preheat_pin_bed.pop(pid, None)

    def _chamber_soak_remaining(
        self,
        printer_id: int,
        chamber_target: float,
        soak_seconds: int,
        tolerance: float = 2.0,
    ) -> int:
        """Return how many seconds of soak time are still needed.

        Credits the time the chamber has already spent at temperature against
        the configured soak. The credit may not start earlier than any of:

        * **The newest sample.** Nothing recent means the printer stopped
          reporting mid-observation and the chamber may have cooled unseen, so
          the full soak is required. (A 2 h history whose last reading is half
          an hour old is not evidence of anything — the measured cooling rate
          is fast enough to cross the threshold in that time.)
        * **The most recent contiguous run of samples.** A gap wider than
          ``_CHAMBER_SAMPLE_MAX_GAP_SECONDS`` is a disconnect, and time on its
          far side is not evidence of temperature.
        * **The end of the most recent real dip below the threshold.**

        A dip only counts as real once it lasts ``_CHAMBER_DIP_GRACE_SECONDS``
        — see that constant for the thermal reasoning. A stray low reading is
        an artifact, and treating it as cooling would discard a soak that
        actually happened.

        Returns ``soak_seconds`` when nothing can be credited (no history,
        stale history, or the chamber is below the threshold right now) and 0
        once the credited time covers the whole soak.
        """
        hist = self._chamber_history.get(printer_id)
        if not hist:
            return soak_seconds

        now = time.monotonic()
        newest_ts, newest_temp = hist[-1]
        if now - newest_ts > _CHAMBER_SAMPLE_MAX_GAP_SECONDS:
            return soak_seconds  # stale — no fresh evidence to credit

        threshold = chamber_target - tolerance
        if newest_temp < threshold:
            return soak_seconds  # below target right now; nothing is soaked

        samples = list(hist)

        # Earliest point we have unbroken observations for.
        credit_from = samples[-1][0]
        for i in range(len(samples) - 1, 0, -1):
            if samples[i][0] - samples[i - 1][0] > _CHAMBER_SAMPLE_MAX_GAP_SECONDS:
                break
            credit_from = samples[i - 1][0]

        # Pull the credit forward to the end of the last significant dip. Each
        # excursion is measured between the in-range readings that bracket it,
        # so a lone stray sample is charged one sampling interval rather than
        # zero, and the comparison errs towards calling a dip real.
        i = 0
        while i < len(samples):
            if samples[i][1] >= threshold:
                i += 1
                continue
            j = i
            while j < len(samples) and samples[j][1] < threshold:
                j += 1
            # `newest_temp >= threshold` was checked above, so j is in range.
            opened_at = samples[i - 1][0] if i > 0 else samples[i][0]
            if samples[j][0] - opened_at >= _CHAMBER_DIP_GRACE_SECONDS:
                # Credit resumes at the last below-threshold sample rather than
                # the first good one after it, so a recovered dip over-credits
                # by up to one sampling interval — the opposite lean to the
                # bracketing above. Both are bounded by the sample cadence and
                # dwarfed by the grace period, so neither is worth the extra
                # arithmetic to remove.
                credit_from = max(credit_from, samples[j - 1][0])
            i = j

        return max(0, soak_seconds - int(now - credit_from))

    def notify_dispatch_cancelled(self, item_id: int) -> None:
        """Tell an in-flight dispatch that its item no longer wants to print.

        Called by the queue's cancel and delete routes. Those only write to the
        database, which a dispatch coroutine parked in ``asyncio.sleep`` cannot
        observe — so preheat would keep heating for the rest of max_wait + soak
        (45 minutes at the defaults) and keep the printer in ``busy_printers``,
        blocking every other queued item behind a print that is not happening.

        Signalling in memory rather than re-reading the row keeps this off the
        database entirely: no second session, no transaction held across a long
        sleep, and no snapshot staleness deciding whether a print goes ahead.
        Bambuddy serves from a single uvicorn process with one scheduler task,
        so the route and the dispatch always share this object. The flag is
        advisory — dropping it (e.g. after a restart) only costs a wasted
        preheat, never a wrongly-abandoned print.

        Only ids with a dispatch actually in flight are recorded, so the set
        stays bounded by the upload pool rather than growing once per cancelled
        item for the life of the process. Skipping the rest loses nothing: an
        item that is not in flight cannot start heating later either, because
        ``_claim_for_dispatch`` only claims rows that are still ``pending`` and
        the caller has already committed a terminal status (or deleted the row)
        before calling this.
        """
        if item_id in self._inflight:
            self._cancelled_dispatches.add(item_id)

    async def _preheat_sleep(self, item_id: int, seconds: float) -> bool:
        """Sleep in slices, returning False as soon as the item stops wanting preheat.

        A single long ``asyncio.sleep`` cannot notice a cancellation that lands
        while it is parked, so the wait is chopped into
        ``_PREHEAT_CANCEL_CHECK_SECONDS`` slices with a check after each.
        """
        remaining = float(seconds)
        while remaining > 0:
            slice_secs = min(_PREHEAT_CANCEL_CHECK_SECONDS, remaining)
            await asyncio.sleep(slice_secs)
            remaining -= slice_secs
            if item_id in self._cancelled_dispatches:
                return False
        return True

    async def _preheat_and_soak(
        self,
        db: AsyncSession,
        item: PrintQueueItem,
        printer: Printer,
        archive: PrintArchive | None,
    ) -> bool:
        """Run the per-printer preheat + heat-soak stage before FTP upload (#1468).

        Returns True when the dispatch should carry on to the upload — including
        every case where preheat is skipped, since a skipped preheat is not a
        reason to abandon the print. Returns False only when the item stopped
        wanting to be printed while the stage was waiting (cancelled or
        deleted); the caller must then abandon the dispatch, and
        ``_dispatch_one``'s rollback shuts the heaters off on the way out.

        Resolution order:
          1. `item.preheat_override` — 'off' skips entirely; 'inherit' falls back
             to the global `preheat_enabled` setting; 'on' forces the stage on
             even if the global is off.
          2. Chamber target — `item.preheat_chamber_target_override` if non-null;
             else max of `preheat_filament_targets[normalize(t.tray_type)]`
             across loaded AMS slots; else 0 (skips chamber phase, keeps bed
             phase + soak timer).
          3. Three hardware tiers branch the wait loop:
             - Chamber heater (H2C/H2D/H2DPro/H2S/X2D/X1E via supports_chamber_heater):
               send M141 to the resolved target, then wait for the chamber sensor
               to reach it (or the max-wait timeout to elapse).
             - Chamber sensor only (X1C/P2S via supports_chamber_temp ∧ ¬supports_chamber_heater):
               no M141; the bed is the only heat source, so we wait for the chamber
               sensor to rise via bed radiation OR fall through on timeout.
             - No chamber sensor (P1S/P1P/A1/A1 Mini): no way to verify chamber
               temperature; the function just heats the bed and holds for the
               configured soak duration.

        The bed target comes from the archive's parsed metadata
        (`bed_temperature`); if missing the preheat stage logs and returns
        without dispatching anything, rather than guessing at a default that
        might wreck filament setup.

        Failures are logged but never re-raised — preheat is best-effort. A
        printer that goes offline mid-soak, a refused gcode command, or a
        missing temperature reading must not turn into a failed queue item; the
        normal upload + start path runs immediately after this method returns.
        """
        override = (getattr(item, "preheat_override", None) or "inherit").lower()
        if override == "off":
            return True
        if override == "inherit":
            enabled = await self._get_bool_setting(db, "preheat_enabled", default=False)
            if not enabled:
                return True
        # override == "on" forces the stage on regardless of the global setting.

        max_wait = await self._get_int_setting(db, "preheat_max_wait_seconds", default=900)
        soak_seconds = await self._get_int_setting(db, "preheat_soak_seconds", default=300)

        # Chamber target resolution:
        #   1. Explicit per-item override beats everything (user knows best).
        #   2. Otherwise derive from loaded AMS filament types via the per-
        #      filament target map. PLA-only print derives 0 → chamber phase
        #      auto-skips without the user touching anything.
        explicit_target = getattr(item, "preheat_chamber_target_override", None)
        if explicit_target is not None and explicit_target > 0:
            chamber_target = int(explicit_target)
            chamber_source = "item-override"
        elif explicit_target == 0:
            chamber_target = 0  # explicit 0 means "no chamber, even if filament wants it"
            chamber_source = "item-override-zero"
        else:
            targets = await self._get_preheat_filament_targets(db)
            chamber_target = self._derive_chamber_target(printer, targets)
            chamber_source = "filament-map"

        bed_target = int(archive.bed_temperature) if archive and archive.bed_temperature else 0
        if bed_target <= 0:
            # No bed temperature in the slicer metadata. When the print needs a
            # hot chamber the bed is simply how we heat it, so fall back to the
            # configured chamber-heating bed temperature rather than skipping
            # the whole stage — otherwise the print starts with a cold chamber,
            # which is exactly what preheat exists to prevent. Without a chamber
            # requirement there is nothing to preheat *for*, so skip as before
            # rather than guess a bed temperature for the print itself.
            if chamber_target <= 0:
                logger.info(
                    "Queue item %s: preheat skipped — archive has no bed_temperature metadata and no chamber target",
                    item.id,
                )
                return True
            bed_target = await self._get_int_setting(db, "queue_keep_warm_bed_temp", default=90)
            logger.info(
                "Queue item %s: archive has no bed_temperature metadata — heating the bed to "
                "%d°C to drive the chamber to %d°C",
                item.id,
                bed_target,
                chamber_target,
            )

        client = printer_manager.get_client(printer.id)
        if client is None:
            logger.warning("Queue item %s: preheat skipped — printer client unavailable", item.id)
            return True

        model = printer.model or ""
        has_heater = supports_chamber_heater(model)
        has_sensor = supports_chamber_temp(model)
        do_chamber = chamber_target > 0 and (has_heater or has_sensor)

        # Fast path: if the chamber has been continuously above target for at
        # least soak_seconds and the bed is already at temperature, skip the
        # entire preheat stage. Typical case: keep-warm held the bed between
        # consecutive same-material prints and the chamber never dropped.
        if do_chamber and has_sensor and soak_seconds > 0:
            remaining = self._chamber_soak_remaining(printer.id, float(chamber_target), soak_seconds)
            if remaining == 0:
                cur = printer_manager.get_status(printer.id)
                if cur:
                    cur_temps = cur.temperatures or {}
                    if (
                        float(cur_temps.get("bed", 0) or 0) >= bed_target - 2.0
                        and float(cur_temps.get("chamber", 0) or 0) >= chamber_target - 2.0
                    ):
                        logger.info(
                            "Queue item %s: preheat skipped — chamber has been above %d°C for ≥%ds "
                            "and bed is already at temperature (chamber history fast-path)",
                            item.id,
                            chamber_target,
                            soak_seconds,
                        )
                        # Still set targets to prevent cooling during the 3MF upload window.
                        # Register each successful set in the preheat pin so `_dispatch_one`
                        # unwinds them on any non-success exit.
                        pin = self._preheat_pin.setdefault(printer.id, set())
                        try:
                            client.set_bed_temperature(bed_target)
                            pin.add("bed")
                            self._preheat_pin_bed[printer.id] = bed_target
                        except Exception as exc:
                            logger.warning("Queue item %s: fast-path bed M140 failed: %s", item.id, exc)
                        if supports_airduct(model):
                            cur_airduct = getattr(cur, "airduct_mode", None)
                            if cur_airduct != _AIRDUCT_MODE_HEATING:
                                try:
                                    client.set_airduct_mode("heating")
                                    # Only undo what we can see we replaced. `None`
                                    # means no mode has been observed yet, and
                                    # rolling that back to cooling would assert a
                                    # state the printer never reported.
                                    if cur_airduct == _AIRDUCT_MODE_COOLING:
                                        pin.add("airduct")
                                except Exception as exc:
                                    logger.warning("Queue item %s: fast-path airduct failed: %s", item.id, exc)
                        if has_heater:
                            try:
                                client.set_chamber_temperature(chamber_target)
                                pin.add("chamber")
                            except Exception as exc:
                                logger.warning("Queue item %s: fast-path chamber M141 failed: %s", item.id, exc)
                        return True

        logger.info(
            "Queue item %s: preheat starting — bed=%d°C chamber_target=%d°C (source=%s override=%s "
            "model=%s has_heater=%s has_sensor=%s) max_wait=%ds soak=%ds",
            item.id,
            bed_target,
            chamber_target if do_chamber else 0,
            chamber_source,
            override,
            model,
            has_heater,
            has_sensor,
            max_wait,
            soak_seconds,
        )

        # Preheat rollback registry: everything we set below is recorded here so
        # `_dispatch_one`'s finally clause can unwind the whole heating regime
        # (bed off, chamber off, airduct back to cooling) on any non-success
        # exit. Populated as each command succeeds; consumed and cleared by
        # `_dispatch_one`.
        pin = self._preheat_pin.setdefault(printer.id, set())

        # Dispatch heaters. set_bed_temperature / set_chamber_temperature already
        # cache the target locally so the polling reads below see consistent
        # state (firmware MQTT echoes lag by ~1s).
        try:
            client.set_bed_temperature(bed_target)
            pin.add("bed")
            self._preheat_pin_bed[printer.id] = bed_target
        except Exception as exc:
            logger.warning("Queue item %s: preheat bed M140 failed: %s", item.id, exc)
            return True

        # Airduct mode (#1468 follow-up). Models with the cooling/heating flap
        # (H2C/H2D/H2D Pro/H2S/X2D/P2S) keep the flap whatever the user last
        # left it on, regardless of M141. Default cooling actively vents the
        # chamber, so a `chamber_target > 0` print with the flap stuck in
        # cooling never converges — the heater fights the open exhaust. We
        # flip the flap BEFORE M141 to "heating" when the preheat wants
        # chamber heat, and back to "cooling" when it doesn't (PLA-only print
        # on an H2D that was previously running ABS would otherwise stay in
        # heating mode and overheat PLA). The current-state read keeps the
        # command idempotent — no MQTT chatter when the flap is already where
        # we want it.
        if supports_airduct(model):
            desired_airduct = "heating" if chamber_target > 0 else "cooling"
            desired_id = _AIRDUCT_MODE_HEATING if desired_airduct == "heating" else _AIRDUCT_MODE_COOLING
            current_state = printer_manager.get_status(printer.id)
            current_airduct = getattr(current_state, "airduct_mode", None) if current_state else None
            if current_airduct != desired_id:
                try:
                    client.set_airduct_mode(desired_airduct)
                    # As in the fast path: only pin a rollback for a flap we
                    # saw in cooling. `current_airduct` of None means no mode
                    # has been observed, so there is nothing to restore to.
                    if desired_airduct == "heating" and current_airduct == _AIRDUCT_MODE_COOLING:
                        pin.add("airduct")
                except Exception as exc:
                    logger.warning(
                        "Queue item %s: preheat airduct %s mode failed: %s",
                        item.id,
                        desired_airduct,
                        exc,
                    )

        if do_chamber and has_heater:
            try:
                client.set_chamber_temperature(chamber_target)
                pin.add("chamber")
            except Exception as exc:
                logger.warning("Queue item %s: preheat chamber M141 failed: %s", item.id, exc)

        # Release the pooled DB connection before the (potentially many-minute)
        # heat-soak wait below (#2572). Every setting this method needs is read
        # above; the wait/soak loop only polls printer_manager state and sleeps —
        # it never touches the DB. Without this the caller's transaction sat
        # "idle in transaction" for the whole soak, pinning one pooled connection
        # per preheating printer. expire_on_commit=False keeps item/printer
        # readable afterwards; there are no pending writes to lose here.
        await db.commit()

        # Wait for convergence. Bed warm-up is fast (~5 min from cold); chamber
        # via M141 takes a few minutes; chamber via bed radiation can take 20+.
        # Poll every 3s — frequent enough for responsive logging without
        # spamming the MQTT state stream. The "converged" predicate is:
        #   bed reached target (within 2°C tolerance for floating-point + heater hysteresis),
        #   AND
        #   chamber phase satisfied (no chamber phase, no sensor, or sensor reached target).
        BED_TOLERANCE = 2.0
        CHAMBER_TOLERANCE = 2.0
        POLL_INTERVAL = 3.0
        deadline = asyncio.get_event_loop().time() + max_wait

        while True:
            state = printer_manager.get_status(printer.id)
            if state is None:
                logger.warning("Queue item %s: preheat lost state during wait", item.id)
                break

            temps = state.temperatures or {}
            bed_now = float(temps.get("bed", 0) or 0)
            chamber_now = float(temps.get("chamber", 0) or 0)
            bed_ok = bed_now >= bed_target - BED_TOLERANCE

            if not do_chamber:
                chamber_ok = True  # phase disabled or model has neither sensor nor heater
            elif not has_sensor:
                chamber_ok = True  # P1S etc — can't read, rely on soak timer only
            else:
                chamber_ok = chamber_now >= chamber_target - CHAMBER_TOLERANCE

            if bed_ok and chamber_ok:
                logger.info(
                    "Queue item %s: preheat target reached (bed=%.1f chamber=%.1f) — entering soak",
                    item.id,
                    bed_now,
                    chamber_now,
                )
                break

            if asyncio.get_event_loop().time() >= deadline:
                logger.info(
                    "Queue item %s: preheat max_wait reached (bed=%.1f/%d chamber=%.1f/%d) — falling through to soak",
                    item.id,
                    bed_now,
                    bed_target,
                    chamber_now,
                    chamber_target if do_chamber else 0,
                )
                break

            if not await self._preheat_sleep(item.id, POLL_INTERVAL):
                logger.info(
                    "Queue item %s: preheat aborted — item cancelled or deleted while waiting for temperature",
                    item.id,
                )
                return False

        if soak_seconds > 0:
            if do_chamber and has_sensor:
                remaining = self._chamber_soak_remaining(printer.id, float(chamber_target), soak_seconds)
            else:
                remaining = soak_seconds  # no sensor — can't verify history, run full soak
            if remaining > 0:
                logger.info(
                    "Queue item %s: preheat soak — holding for %ds (of %ds configured; chamber "
                    "has been above target for ~%ds already)",
                    item.id,
                    remaining,
                    soak_seconds,
                    soak_seconds - remaining,
                )
                if not await self._preheat_sleep(item.id, remaining):
                    logger.info(
                        "Queue item %s: preheat aborted — item cancelled or deleted during soak",
                        item.id,
                    )
                    return False
            else:
                logger.info(
                    "Queue item %s: preheat soak skipped — chamber has been above %d°C for ≥%ds",
                    item.id,
                    chamber_target,
                    soak_seconds,
                )

        logger.info("Queue item %s: preheat complete — proceeding to upload", item.id)
        return True

    async def _power_on_and_wait(self, plug: SmartPlug, printer_id: int, db: AsyncSession) -> bool:
        """Turn on smart plug and wait for printer to connect.

        Returns True if printer connected successfully within timeout.
        """
        # Get the appropriate service for the plug type (Tasmota or Home Assistant)
        service = await smart_plug_manager.get_service_for_plug(plug, db)

        # Check current plug state
        status = await service.get_status(plug)
        if not status.get("reachable"):
            logger.warning("Smart plug '%s' is not reachable", plug.name)
            return False

        # Turn on if not already on
        if status.get("state") != "ON":
            success = await service.turn_on(plug)
            if not success:
                logger.warning("Failed to turn on smart plug '%s'", plug.name)
                return False
            logger.info("Powered on smart plug '%s' for printer %s", plug.name, printer_id)

        # Get printer from database for connection
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printer = result.scalar_one_or_none()
        if not printer:
            logger.error("Printer %s not found in database", printer_id)
            return False

        # Wait for printer to boot (give it some time before trying to connect)
        logger.info("Waiting 30s for printer %s to boot...", printer_id)
        await asyncio.sleep(30)

        # Try to connect to the printer periodically
        elapsed = 30  # Already waited 30s
        while elapsed < self._power_on_wait_time:
            # Try to connect
            logger.info("Attempting to connect to printer %s...", printer_id)
            try:
                connected = await printer_manager.connect_printer(printer)
                if connected:
                    logger.info("Printer %s connected after %ss", printer_id, elapsed)
                    # Give it a moment to stabilize and get status
                    await asyncio.sleep(5)
                    return True
            except Exception as e:
                logger.debug("Connection attempt failed: %s", e)

            await asyncio.sleep(self._power_on_check_interval)
            elapsed += self._power_on_check_interval
            logger.debug("Waiting for printer %s to connect... (%ss)", printer_id, elapsed)

        logger.warning("Printer %s did not connect within %ss after power on", printer_id, self._power_on_wait_time)
        return False

    async def _check_previous_success(self, db: AsyncSession, item: PrintQueueItem) -> bool:
        """Check if the previous print on this printer succeeded.

        A user-cancelled predecessor is treated as neutral — `cancelled` is a
        deliberate action, not a failure, so subsequent items should still
        dispatch (#1667). `skipped` is excluded from the lookback entirely:
        a skip isn't an actual print attempt, so it must not gate downstream
        items — counting it as a failed predecessor was the cascade bug that
        let a single cancellation block 18 items over 3 days for the reporter.
        Only `failed` and `aborted` — real print-attempt failures — block.

        Failures with `gate_acknowledged=True` (set by the per-printer Resume
        action — #1818) are also excluded from the lookback so the user can
        clear the gate after fixing the physical issue without having to
        re-queue every downstream job.
        """
        result = await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.printer_id == item.printer_id)
            .where(PrintQueueItem.id != item.id)
            .where(PrintQueueItem.status.in_(["completed", "failed", "cancelled", "aborted"]))
            .where(PrintQueueItem.gate_acknowledged == False)  # noqa: E712
            .order_by(PrintQueueItem.completed_at.desc())
            .limit(1)
        )
        prev_item = result.scalar_one_or_none()

        # If no previous item, assume success (first in queue)
        if not prev_item:
            return True

        return prev_item.status in ("completed", "cancelled")

    async def _repoint_siblings_at_archive(
        self,
        db: AsyncSession,
        *,
        consumed_library_file_id: int,
        archive_id: int,
        dispatched_item_id: int,
    ) -> int:
        """Move the other queue items off a library row that is about to be deleted (#2819).

        ``cleanup_library_after_dispatch`` consumes the library row: the printer-card
        upload-and-print flow uploads a transient file, prints it, and deletes it.
        Queue creation happily puts that flag on every copy of a ``quantity > 1``
        request, and ``_clone_queue_item`` copies ``library_file_id`` onto batch
        clones, so the first dispatch could pull the file out from under rows that
        had not run yet. What those rows did next depended on the database, and
        neither answer was right: SQLite ships with ``PRAGMA foreign_keys`` off, so
        the ``ON DELETE CASCADE`` on ``print_queue.library_file_id`` never fired and
        they were left pointing at a row that no longer existed, failing with
        "Library file not found" whenever someone started them -- or sitting
        ``pending`` forever under ``manual_start``. PostgreSQL enforces the same
        constraint, so there the rows were deleted outright and the queued copies
        simply vanished, with no error and no history.

        The archive holds its own copy of the 3MF, so the remaining copies can print
        from it instead. Two things happen here, and both must happen *before* the
        delete -- afterwards there is nothing left to repair on PostgreSQL:

        * every row still naming the file has ``library_file_id`` cleared, which is
          what takes it out of the cascade's reach. That covers rows this cannot
          re-point as well -- a copy already printing from its own archive, and the
          finished ones, which are not spare parts but the record a batch order
          counts its progress from.
        * the rows that still need something to print are pointed at the archive.

        Returns how many items were re-pointed.
        """
        variant_item_ids = (
            (
                await db.execute(
                    select(PrintQueueVariant.queue_item_id).where(
                        PrintQueueVariant.library_file_id == consumed_library_file_id
                    )
                )
            )
            .scalars()
            .all()
        )
        # Candidate rows naming the consumed file have to go rather than be
        # cleared: `library_file_id` is NOT NULL there, so there is no way to keep
        # one out of the cascade. This is what PostgreSQL already does, and
        # _candidates_for skips such a variant on SQLite anyway, so no selection
        # outcome changes -- the two backends simply stop disagreeing about
        # whether the row is still there.
        #
        # It also has to happen before the re-point below: a cross-model item
        # (#671) picks a variant every pass and folds it onto the row, and
        # _resolve_variant clears archive_id as it does so, which would undo the
        # re-point on the very next lap.
        await db.execute(delete(PrintQueueVariant).where(PrintQueueVariant.library_file_id == consumed_library_file_id))

        # An item left with other candidates still has somewhere to go, and those
        # carry their own target model -- pointing it at this archive would print a
        # file the matcher never chose. It is excluded from the re-point and simply
        # re-resolves against what is left.
        surviving_variant_item_ids = set(
            (
                await db.execute(
                    select(PrintQueueVariant.queue_item_id).where(
                        PrintQueueVariant.queue_item_id.in_(variant_item_ids) if variant_item_ids else false()
                    )
                )
            )
            .scalars()
            .all()
        )
        repoint_ids = set(
            (
                await db.execute(
                    select(PrintQueueItem.id)
                    .where(PrintQueueItem.id != dispatched_item_id)
                    .where(PrintQueueItem.archive_id.is_(None))
                    # "skipped" belongs here with the two live states: it is not
                    # terminal. Clearing a printer's previous-success gate puts
                    # every item skipped by it back to "pending"
                    # (resume_after_failure), and one restored onto a deleted
                    # file is the same orphan by a slower route. "failed",
                    # "cancelled", "aborted" and "completed" never return.
                    .where(PrintQueueItem.status.in_(("pending", "printing", "skipped")))
                    .where(
                        PrintQueueItem.id.notin_(surviving_variant_item_ids) if surviving_variant_item_ids else true()
                    )
                    .where(
                        or_(
                            PrintQueueItem.library_file_id == consumed_library_file_id,
                            PrintQueueItem.id.in_(variant_item_ids) if variant_item_ids else false(),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        if repoint_ids:
            await db.execute(
                update(PrintQueueItem)
                .where(PrintQueueItem.id.in_(repoint_ids))
                .values(
                    archive_id=archive_id,
                    library_file_id=None,
                    # The file this flag named is already consumed. Leaving it set
                    # would arm every re-pointed copy to delete whatever library
                    # row it is next given.
                    cleanup_library_after_dispatch=False,
                )
            )
            logger.info(
                "Queue items %s: re-pointed at archive %s -- library file %s was consumed by item %s",
                sorted(repoint_ids),
                archive_id,
                consumed_library_file_id,
                dispatched_item_id,
            )

        # Everything else that still names the file: taken out of the cascade's
        # reach without touching what it prints. The file is gone either way; what
        # this preserves is the row.
        await db.execute(
            update(PrintQueueItem)
            .where(PrintQueueItem.id != dispatched_item_id)
            .where(PrintQueueItem.library_file_id == consumed_library_file_id)
            .values(library_file_id=None)
        )
        return len(repoint_ids)

    async def _power_off_if_needed(self, db: AsyncSession, item: PrintQueueItem):
        """Schedule power-off if the queue item enabled auto_off_after.

        Delegates to the smart-plug manager so the off honours each plug's
        configured strategy (time delay or temperature threshold), is cancelled
        if the printer starts printing again, and never cuts power on a loaded
        print (#1890). Previously this hardcoded a 50°C / 600s cooldown wait and
        powered off on the timeout regardless of print state.
        """
        if not item.auto_off_after:
            return
        try:
            await smart_plug_manager.schedule_off_after_queue_job(item.printer_id, db)
        except Exception as e:
            logger.warning("Auto-off: Failed to schedule power-off for printer %s: %s", item.printer_id, e)

    async def _get_job_name(self, db: AsyncSession, item: PrintQueueItem) -> str:
        """Get a human-readable name for a queue item."""
        if item.archive_id:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if archive:
                return archive.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        if item.library_file_id:
            result = await db.execute(LibraryFile.active().where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if library_file:
                return library_file.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        # A cross-model item (#671) holds no file of its own until a printer is
        # picked, so name it after its first candidate — otherwise every waiting
        # notification for one reads "Job #12". Queried rather than read off
        # item.variants because callers outside the selection loop have not
        # eager-loaded them, and a lazy load raises in async.
        first_variant_name = (
            await db.execute(
                select(LibraryFile.filename)
                .join(PrintQueueVariant, PrintQueueVariant.library_file_id == LibraryFile.id)
                .where(PrintQueueVariant.queue_item_id == item.id)
                .order_by(PrintQueueVariant.position, PrintQueueVariant.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if first_variant_name:
            return first_variant_name.replace(".gcode.3mf", "").replace(".3mf", "")
        return f"Job #{item.id}"

    async def _get_printer(self, db: AsyncSession, printer_id: int) -> Printer | None:
        """Get printer by ID."""
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        return result.scalar_one_or_none()

    async def _notify_dispatch_gave_up(
        self,
        queue_item_id: int,
        printer_id: int,
        created_by_id: int | None,
        reason: str = "Printer accepted the file but never started printing",
    ) -> None:
        """Tell the user the queue item was failed after exhausting its dispatch retries.

        Called from the watchdog, which is a background task with no session of
        its own — hence the fresh one here. Best-effort throughout: the row is
        already marked failed and that is the load-bearing part; a notification
        provider being down must not resurrect the retry loop we just stopped.

        ``reason`` defaults to the exhausted-retries wording. The command-rejected
        path passes its own, because "accepted the file but never started" is the
        opposite of what happened there — the printer refused it outright (#2732).
        """
        try:
            async with async_session() as db:
                item = await db.get(PrintQueueItem, queue_item_id)
                if not item:
                    return
                job_name = await self._get_job_name(db, item)
                printer = await self._get_printer(db, printer_id)
                await notification_service.on_queue_job_failed(
                    job_name=job_name,
                    printer_id=printer_id,
                    printer_name=printer.name if printer else "Unknown",
                    reason=reason,
                    db=db,
                )
        except Exception as e:
            logger.warning("Queue item %s: give-up notification failed: %s", queue_item_id, e)

        try:
            await ws_manager.send_queue_item_failed(
                user_id=created_by_id,
                queue_item_id=queue_item_id,
                printer_id=printer_id,
                reason="never_started",
            )
        except Exception:
            pass  # toast is best-effort

    async def _block_on_filament_deficit(
        self,
        db: AsyncSession,
        item: PrintQueueItem,
    ) -> bool:
        """Promote the item to manual_start when the assigned spool is short (#1496).

        Returns True when this dispatch attempt was blocked, False when the
        item is clear to start. A previously-flagged item whose spool has
        since been swapped to one with enough material clears the flag here
        so the next scheduler tick dispatches it.
        """
        # User has explicitly acknowledged the deficit ("Print Anyway") —
        # don't re-flag, don't even compute. Without this short-circuit the
        # scheduler bounces between "user said anyway" (route clears
        # manual_start) and "scheduler re-blocked" (this method re-flags it
        # on identical spool state) (#1698-followup).
        if item.skip_filament_check:
            # #1762 diagnostic: surface the short-circuit at INFO so a
            # future "Print Anyway didn't work" report (e.g. issue #1762
            # comment 3) has actionable evidence in the support bundle
            # without needing DEBUG enabled.
            logger.info(
                "Queue item %s honouring user's Print Anyway acknowledgement — skipping deficit check",
                item.id,
            )
            return False

        try:
            deficit = await compute_deficit_for_queue_item(db, item)
        except Exception as e:
            # Never let a flaky deficit check wedge the queue — log and let
            # dispatch proceed. The PrintModal-side check still runs on the
            # manual paths.
            logger.warning("Filament deficit check failed for item %s: %s", item.id, e)
            return False

        if deficit:
            item.filament_short = True
            item.manual_start = True
            await db.commit()
            job_name = await self._get_job_name(db, item)
            printer = await self._get_printer(db, item.printer_id) if item.printer_id else None
            logger.info(
                "Queue item %s blocked on filament deficit (%d slot(s)) — promoted to manual_start",
                item.id,
                len(deficit),
            )
            try:
                await notification_service.on_queue_job_waiting(
                    job_name=job_name,
                    target_model=(printer.model if printer else "") or "",
                    waiting_reason="filament_short",
                    db=db,
                )
            except Exception as e:
                logger.debug("filament_short notification failed for item %s: %s", item.id, e)
            return True

        # No deficit — clear any stale flag from a previous tick.
        if item.filament_short:
            item.filament_short = False
            await db.commit()
        return False

    async def _propagate_owner_to_printer_manager(self, db: AsyncSession, item: PrintQueueItem) -> None:
        """Hand the queue item's owner to printer_manager so the
        print-complete callback can credit the user in PrintLogEntry (#1670).

        No-ops when the item has no `created_by_id` or the referenced user
        row is missing (e.g. user deleted between queue-add and dispatch —
        in that case the print log row falls back to the existing un-credited
        behaviour rather than crashing the dispatch).
        """
        if not item.created_by_id:
            return
        from backend.app.models.user import User

        owner = await db.get(User, item.created_by_id)
        if owner:
            printer_manager.set_current_print_user(item.printer_id, owner.id, owner.username)

    async def _start_print(self, db: AsyncSession, item: PrintQueueItem):
        """Upload file and start print for a queue item.

        Supports two sources:
        - archive_id: Print from an existing archive
        - library_file_id: Print from a library file (file manager)
        """
        logger.info("Starting queue item %s", item.id)

        # Also covers a reservation left active by a process interruption
        # during an earlier attempt. `_dispatch_one` releases this marker on
        # every exit unless start_print() confirms that the command was sent.
        self._unconfirmed_budget_reservations.add(item.id)
        try:
            from backend.app.models.user import User

            queue_user = await db.get(User, item.created_by_id) if item.created_by_id is not None else None
            # Recompute at the final authorization boundary as well as enqueue
            # time. This covers rows created before the server-side estimate
            # migration and prevents any alternate write path from weakening
            # the budget reservation.
            archive = await db.get(PrintArchive, item.archive_id) if item.archive_id is not None else None
            library_file = await db.get(LibraryFile, item.library_file_id) if item.library_file_id is not None else None
            item.estimated_cost = await estimate_queue_source_cost(
                db,
                archive=archive,
                library_file=library_file,
                plate_id=item.plate_id,
                ams_mapping=item.ams_mapping,
                printer_id=item.printer_id,
            )
            await validate_print_budget(
                db,
                cost_center_id=item.cost_center_id,
                estimated_cost=item.estimated_cost,
                current_user=queue_user,
                exclude_queue_item_id=item.id,
                exclude_reservation_source_type="print_queue",
                exclude_reservation_source_id=item.id,
            )
            budget_reservation = await create_budget_reservation(
                db,
                cost_center_id=item.cost_center_id,
                estimated_cost=item.estimated_cost,
                current_user=queue_user,
                source_type="print_queue",
                source_id=item.id,
                print_archive_id=item.archive_id,
                exclude_queue_item_id=item.id,
            )
            await db.commit()
        except HTTPException as exc:
            item.status = "failed"
            item.error_message = str(exc.detail)
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: Budget check failed: %s", item.id, item.error_message)
            await self._power_off_if_needed(db, item)
            return

        # Get printer first (needed for both paths)
        result = await db.execute(select(Printer).where(Printer.id == item.printer_id))
        printer = result.scalar_one_or_none()
        if not printer:
            item.status = "failed"
            item.error_message = "Printer not found"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: Printer %s not found", item.id, item.printer_id)
            await self._power_off_if_needed(db, item)
            return

        # Check printer is connected
        if not printer_manager.is_connected(item.printer_id):
            item.status = "failed"
            item.error_message = "Printer not connected"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: Printer %s not connected", item.id, item.printer_id)
            await self._power_off_if_needed(db, item)
            return

        # Cancel-while-dispatching race (#1853): the scheduler's snapshot of
        # `items` was taken at the top of check_queue, but the user can /cancel
        # any pending row in the gap before we reach this point. Re-read the
        # row and bail out cleanly instead of starting an FTP upload for a row
        # that's already cancelled. The atomic CAS at the pending→printing
        # transition (below, before start_print) is the load-bearing guard;
        # this is the early-exit optimisation that avoids wasted FTP I/O.
        await db.refresh(item)
        if item.status != "pending":
            logger.info(
                "Queue item %s no longer pending (status=%s) — aborting dispatch",
                item.id,
                item.status,
            )
            return

        # Busy-printer guard (#2598). check_queue gates dispatch on
        # _is_printer_idle(), but that treats FINISH as idle and a printer can
        # keep reporting FINISH for tens of seconds *after* it accepted a
        # project_file (see the watchdog's phase-B note). A watchdog revert
        # (#2555) also releases the dispatch hold, so a re-selected item can
        # reach here while its printer has actually started printing. Uploading
        # and dispatching then collides with the live job — the firmware answers
        # 0500_4004 and, on an A1 mini, cancels the running print. Re-check the
        # live state right before the expensive FTP upload: if the printer is
        # busy, leave the item pending and let a later tick dispatch it once the
        # printer is genuinely idle. No wasted upload, no collision.
        pre_dispatch_state = getattr(printer_manager.get_status(item.printer_id), "state", None)
        if pre_dispatch_state in _ACTIVE_PRINT_STATES:
            logger.info(
                "Queue item %s: printer %s is busy (state=%s) — deferring dispatch, "
                "leaving item pending for a later tick (#2598)",
                item.id,
                item.printer_id,
                pre_dispatch_state,
            )
            return

        # Determine source: archive or library file
        archive = None
        library_file = None
        file_path = None
        filename = None
        cleanup_disk_paths: list[Path] = []

        if item.archive_id:
            # Print from archive
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if not archive:
                item.status = "failed"
                item.error_message = "Archive not found"
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.error("Queue item %s: Archive %s not found", item.id, item.archive_id)
                await self._power_off_if_needed(db, item)
                return

            # Persist the queue item's selected plate onto the archive so Print
            # History can show the actual plate after cancel/fail/complete (#2603).
            # Only when the archive doesn't already carry one, so a reprint of a
            # plate-specific archive isn't relabelled by a differently-plated
            # queue row.
            if archive.plate_id is None and item.plate_id is not None:
                archive.plate_id = item.plate_id

            file_path = settings.base_dir / archive.file_path
            filename = archive.filename

        elif item.library_file_id:
            # Print from library file (file manager)
            result = await db.execute(LibraryFile.active().where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if not library_file:
                # "Not found" covers two different situations and only one of
                # them is recoverable, so say which. A trashed file is still
                # there and restoring it makes a re-queued job work; a file that
                # is really gone needs a different one. Neither is knowable from
                # the queue, which is the whole complaint about this message.
                trashed = (
                    await db.execute(select(LibraryFile.filename).where(LibraryFile.id == item.library_file_id))
                ).scalar_one_or_none()
                item.status = "failed"
                item.error_message = (
                    f"'{trashed}' is in the library trash — restore it and queue the print again"
                    if trashed
                    else "Library file not found — it was deleted after this job was queued"
                )
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.error(
                    "Queue item %s: library file %s is %s",
                    item.id,
                    item.library_file_id,
                    "in the trash" if trashed else "gone",
                )
                await self._power_off_if_needed(db, item)
                return
            # Library files store absolute paths
            lib_path = Path(library_file.file_path)
            file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path
            filename = library_file.filename

            # Create archive from library file so usage tracking has access to the 3MF
            queue_item_id = item.id
            # Held separately: a cleanup dispatch clears item.library_file_id
            # below, and the log line at the end of this block reported that
            # cleared field -- so every consumed print logged "from library
            # file None", which is the one case worth being able to trace.
            source_library_file_id = item.library_file_id
            try:
                from backend.app.services.archive import ArchiveService

                archive_service = ArchiveService(db)
                archive = await archive_service.archive_print(
                    printer_id=item.printer_id,
                    source_file=file_path,
                    original_filename=filename,
                    created_by_id=item.created_by_id,
                    project_id=item.project_id,
                    cost_center_id=item.cost_center_id,
                    library_file_id=item.library_file_id,  # per-file project progress (#1897)
                    plate_id=item.plate_id,  # selected plate → Print History (#2603)
                )
                if archive:
                    item.archive_id = archive.id
                    if budget_reservation is not None:
                        budget_reservation.print_archive_id = archive.id
                    if item.cleanup_library_after_dispatch and not library_file.is_external:
                        consumed_library_file_id = library_file.id
                        item.library_file_id = None
                        cleanup_disk_paths.append(file_path)
                        if library_file.thumbnail_path:
                            thumb_path = Path(library_file.thumbnail_path)
                            if not thumb_path.is_absolute():
                                thumb_path = settings.base_dir / library_file.thumbnail_path
                            cleanup_disk_paths.append(thumb_path)
                        # Before the delete, not after: on PostgreSQL the FK
                        # cascade would already have taken these rows (#2819).
                        await self._repoint_siblings_at_archive(
                            db,
                            consumed_library_file_id=consumed_library_file_id,
                            archive_id=archive.id,
                            dispatched_item_id=item.id,
                        )
                        await db.delete(library_file)
                        file_path = settings.base_dir / archive.file_path
                        filename = archive.filename
                    # Commit, not flush — flush opens the SQLite write
                    # transaction (item.archive_id update + library_file
                    # delete) and would hold the WAL writer lock through the
                    # FTP upload below, causing "database is locked" cascades
                    # for sensor history + concurrent cancels (#1853).
                    await db.commit()
                    logger.info(
                        "Queue item %s: Created archive %s from library file %s",
                        item.id,
                        archive.id,
                        source_library_file_id,
                    )
            except Exception as e:
                logger.warning(
                    "Queue item %s: Failed to create archive from library file: %s",
                    queue_item_id,
                    e,
                    exc_info=True,
                )
                await db.rollback()
                item = await db.get(PrintQueueItem, queue_item_id)
                if item:
                    item.status = "failed"
                    item.error_message = "Failed to create archive from library file"
                    item.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    await self._power_off_if_needed(db, item)
                return

            if not archive:
                item.status = "failed"
                item.error_message = "Failed to create archive from library file"
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.error("Queue item %s: Archive creation from library file returned no archive", item.id)
                await self._power_off_if_needed(db, item)
                return

        else:
            # Neither archive nor library file specified
            item.status = "failed"
            item.error_message = "No source file specified"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: No archive_id or library_file_id specified", item.id)
            await self._power_off_if_needed(db, item)
            return

        # Check file exists on disk
        if not file_path.exists():
            item.status = "failed"
            item.error_message = "Source file not found on disk"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: File not found: %s", item.id, file_path)
            await self._power_off_if_needed(db, item)
            return

        # Nozzle-diameter mismatch guard (#1899). A file sliced for one nozzle
        # size dispatched to a printer with a different nozzle installed is
        # rejected by the firmware with a cryptic HMS ("Failed to get AMS mapping
        # table" 0700_8012, or "nozzle diameter … not consistent" 0500_4038) that
        # gives the user no idea what went wrong. Catch it here, before we spend
        # time preheating and uploading, and fail with an actionable message.
        # Fail-safe by construction: only a POSITIVE mismatch blocks — when the
        # slice carries no nozzle diameter (archive.nozzle_diameter is None) or
        # the printer hasn't reported its nozzles yet, we fall through and let the
        # print proceed exactly as before. On dual-nozzle printers (H2D) a match
        # against EITHER installed nozzle passes, so a 0.6 slice is fine as long
        # as one of the two hotends is a 0.6.
        #
        # On a tool-changer model (H2C) the nozzles parked in the rack count too
        # (#2885): the printer fetches one as part of starting the print, so the
        # set to test against is "reachable", not "currently mounted". This runs
        # well before the rack picker at the bottom of this method, so without
        # the rack in scope here that picker never got the chance to run.
        sliced_nozzle = archive.nozzle_diameter if archive else None
        if sliced_nozzle:
            nozzle_status = printer_manager.get_status(item.printer_id)
            installed = _installed_nozzle_diameters(nozzle_status)
            rack = _rack_nozzle_diameters(nozzle_status)
            mismatch_msg = _nozzle_mismatch_message(sliced_nozzle, installed, rack)
            if mismatch_msg:
                item.status = "failed"
                item.error_message = mismatch_msg
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.warning("Queue item %s: nozzle mismatch — %s", item.id, mismatch_msg)
                await notification_service.on_queue_job_failed(
                    job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                    printer_id=printer.id,
                    printer_name=printer.name,
                    reason=mismatch_msg,
                    db=db,
                )
                try:
                    await ws_manager.send_queue_item_failed(
                        user_id=item.created_by_id,
                        queue_item_id=item.id,
                        printer_id=item.printer_id,
                        reason="nozzle_mismatch",
                    )
                except Exception:
                    pass
                await self._power_off_if_needed(db, item)
                return

        # Preheat / heat-soak (#1468) — fires before upload so the printer's
        # bed (and chamber, if applicable) is at temperature when the firmware
        # starts the actual print routine. Best-effort: any failure logs and
        # falls through to the normal upload+start path rather than turning a
        # configuration issue into a failed queue item.
        # Returns False only when the item was cancelled or deleted while the
        # stage was holding at temperature. Uploading and starting it anyway
        # would print a job the user has already called off, so abandon the
        # dispatch here; `_dispatch_one`'s finally clause unwinds the heaters.
        if not await self._preheat_and_soak(db, item, printer, archive):
            logger.info("Queue item %s: dispatch abandoned — cancelled during preheat", item.id)
            return

        # G-code injection for auto-print systems (#422)
        injected_path = None
        # #2547: tracked separately from `injected_path`, which is also set when
        # only a START snippet was injected. Only an END snippet changes what the
        # camera sees at print completion.
        end_gcode_injected = False
        if item.gcode_injection:
            try:
                snippets_raw = await self._get_setting(db, "gcode_snippets")
                if snippets_raw:
                    snippets = json.loads(snippets_raw)
                    model_snippets = snippets.get(printer.model, {})
                    start_gc = (model_snippets.get("start_gcode") or "").strip()
                    end_gc = (model_snippets.get("end_gcode") or "").strip()
                    if start_gc or end_gc:
                        from backend.app.utils.threemf_tools import inject_gcode_into_3mf

                        injected_path = inject_gcode_into_3mf(
                            file_path, item.plate_id or 1, start_gc or None, end_gc or None
                        )
                        if injected_path:
                            file_path = injected_path
                            end_gcode_injected = bool(end_gc)
                            logger.info("Queue item %s: G-code injected for model %s", item.id, printer.model)
                        else:
                            logger.warning(
                                "Queue item %s: G-code injection returned no result, using original", item.id
                            )
            except Exception as e:
                logger.warning("Queue item %s: G-code injection failed, using original: %s", item.id, e)

        # #2547: the finish-photo path can't learn from telemetry that this print
        # ends with user End G-code — which means the plate may be gone by the
        # time FINISH arrives (#1867). Flag it here; `on_print_start` binds it to
        # the print once the printer confirms it running.
        if end_gcode_injected:
            print_dispatch_context.mark_pending(printer.id)

        # Upload to root directory (not /cache/) - the start_print command references
        # files by name only (ftp://{filename}), so they must be in the root
        remote_filename = derive_remote_filename(filename)
        remote_path = f"/{remote_filename}"

        # Get FTP retry settings
        ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

        logger.info(
            f"Queue item {item.id}: FTP upload starting - printer={printer.name} ({printer.model}), "
            f"ip={printer.ip_address}, file={remote_filename}, local_path={file_path}, "
            f"retry_enabled={ftp_retry_enabled}, retry_count={ftp_retry_count}, timeout={ftp_timeout}"
        )

        # Release the pooled DB connection before the FTP delete/upload (#2572).
        # Every read this method needs (printer, archive/library, preheat) is
        # done, and the library-file branch already committed its archive
        # creation. Without this the transaction opened by the first SELECT above
        # stays "idle in transaction" for the entire upload — multiple seconds
        # for a large 3MF — pinning one pooled connection per in-flight dispatch;
        # a farm dispatching many jobs at once then exhausts the pool. This was
        # correlated to an exact idle-in-transaction session on a 93-printer farm
        # (reporter @Jostxxl). expire_on_commit=False keeps item/printer/archive
        # readable; the status writes below (upload-failure path and the
        # pending->printing CAS) transparently open a fresh transaction.
        await db.commit()

        # Delete existing file if present (avoids 553 error on overwrite)
        try:
            logger.debug("Queue item %s: Deleting existing file %s if present...", item.id, remote_path)
            delete_result = await delete_file_async(
                printer.ip_address,
                printer.access_code,
                remote_path,
                socket_timeout=ftp_timeout,
                printer_model=printer.model,
                # This delete and the upload below are one bounded, user-initiated
                # unit -- at most nine connections -- so neither skips on the
                # handshake cool-off the opportunistic sweeps rely on. In #2898's
                # trace this delete took the TLS failure and armed the cool-off,
                # and the upload's four attempts were then spent against it
                # without a socket being opened.
                respect_handshake_cooloff=False,
            )
            logger.debug("Queue item %s: Delete result: %s", item.id, delete_result)
        except Exception as e:
            logger.debug("Queue item %s: Delete failed (may not exist): %s", item.id, e)

        # Dispatch toast — announce the upload start with the total byte
        # count so the frontend can render an honest progress bar.
        toast_uid = item.created_by_id
        toast_file_name = filename.replace(".gcode.3mf", "").replace(".3mf", "")
        try:
            total_bytes = file_path.stat().st_size
        except OSError:
            total_bytes = 0
        try:
            await ws_manager.send_queue_item_uploading(
                user_id=toast_uid,
                queue_item_id=item.id,
                printer_id=item.printer_id,
                printer_name=printer.name,
                file_name=toast_file_name,
                total_bytes=total_bytes,
            )
        except Exception:
            pass  # toast is best-effort

        progress_bridge = _UploadProgressBridge(toast_uid, item.id)

        # A deadline expiry gets its own message: "check your SD card" is the
        # wrong advice for a link that was simply too slow to finish (#2529).
        upload_error: str | None = None

        # Why the upload failed, straight from the client rather than inferred.
        # Owned here, so a background fetch for another print cannot overwrite
        # it between the failure and the sentence built from it (#2899).
        upload_failure = FtpFailureReport()

        try:
            if ftp_retry_enabled:
                uploaded = await with_ftp_retry(
                    upload_file_async,
                    printer.ip_address,
                    printer.access_code,
                    file_path,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                    progress_callback=progress_bridge,
                    respect_handshake_cooloff=False,
                    failure=upload_failure,
                    max_retries=ftp_retry_count,
                    retry_delay=ftp_retry_delay,
                    operation_name=f"Upload print to {printer.name}",
                )
            else:
                uploaded = await upload_file_async(
                    printer.ip_address,
                    printer.access_code,
                    file_path,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                    progress_callback=progress_bridge,
                    respect_handshake_cooloff=False,
                    failure=upload_failure,
                )
        except UploadCancelled as e:
            uploaded = False
            upload_error = (
                "Upload was too slow to finish and was cancelled. The printer's connection could not sustain "
                "the transfer — check its Wi-Fi signal, or move it closer to the access point."
            )
            logger.error("Queue item %s: upload deadline exceeded: %s", item.id, e)
        except Exception as e:
            uploaded = False
            logger.error("Queue item %s: FTP error: %s (type: %s)", item.id, e, type(e).__name__)

        # Clean up injected temp file after upload attempt
        if injected_path and injected_path.exists():
            injected_path.unlink(missing_ok=True)

        if not uploaded:
            # This used to be one string for every upload failure, telling
            # everyone to check the SD card. The client knows which of seven
            # things went wrong and logs each one differently; it just had no
            # way to say so here, so the card got named even for a TLS
            # handshake that never reached the printer's filesystem (#2899).
            error_msg = upload_error or describe_upload_failure(upload_failure.failure)
            item.status = "failed"
            item.error_message = error_msg
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error(
                f"Queue item {item.id}: FTP upload failed - printer={printer.name}, model={printer.model}, "
                f"ip={printer.ip_address}. Check logs above for storage diagnostics and specific error codes."
            )

            # Send failure notification
            await notification_service.on_queue_job_failed(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                # The same sentence the queue shows. A push notification saying
                # something different from the UI is its own small bug (#2899).
                reason=error_msg,
                db=db,
            )
            try:
                await ws_manager.send_queue_item_failed(
                    user_id=toast_uid,
                    queue_item_id=item.id,
                    printer_id=item.printer_id,
                    reason="upload_failed",
                )
            except Exception:
                pass
            await self._power_off_if_needed(db, item)
            return

        # Parse AMS mapping if stored
        ams_mapping = None
        if item.ams_mapping:
            try:
                ams_mapping = json.loads(item.ams_mapping)
            except json.JSONDecodeError:
                logger.warning("Queue item %s: Invalid AMS mapping JSON, ignoring", item.id)

        # Register as expected print so we don't create a duplicate archive
        # Only applicable for archive-based prints
        if archive:
            from backend.app.main import register_expected_print

            register_expected_print(
                item.printer_id,
                remote_filename,
                archive.id,
                ams_mapping=ams_mapping,
                created_by_id=item.created_by_id,
                cost_center_id=item.cost_center_id,
                plate_id=item.plate_id,
            )
            # Registration happens before the print command by necessity (the
            # printer can report the print before the send returns), so record
            # what to undo if we never get as far as sending. `_dispatch_one`
            # rolls back anything still pending here on every exit — exception,
            # early return, or cancel winning the CAS below.
            self._unconfirmed_expected_print[item.id] = (item.printer_id, remote_filename, archive.id)

        # Propagate the queue item's owner into printer_manager so the
        # print-complete callback can credit the user in the PrintLogEntry
        # (#1670). `created_by_id` is set either at queue-add time (UI-added
        # items) or when the user clicks the manual-start button.
        await self._propagate_owner_to_printer_manager(db, item)

        # IMPORTANT: Set status to "printing" BEFORE sending the print command.
        # This prevents phantom reprints if the backend crashes/restarts after the
        # print command is sent but before the status update is committed.
        # If we crash after this commit but before start_print(), the item will be
        # in "printing" status without actually printing - but that's safer than
        # accidentally reprinting the same file hours later.
        #
        # Atomic CAS (#1853): a user pressing /cancel mid-dispatch (between the
        # initial pending read at the top of check_queue and this point) flips
        # the row to "cancelled" in a separate session. Without the WHERE
        # status='pending' clause, the unconditional update here would silently
        # overwrite that cancellation and we'd ship the MQTT start_print below
        # — printer obeys, user sees "I pressed cancel and the print started".
        # rowcount==0 means the user won the race; bail out, best-effort delete
        # the file we just uploaded, do NOT send start_print.
        now_utc = datetime.now(timezone.utc)
        billing_run_id = str(uuid.uuid4())
        cas = await db.execute(
            update(PrintQueueItem)
            .where(PrintQueueItem.id == item.id)
            .where(PrintQueueItem.status == "pending")
            .values(status="printing", started_at=now_utc, billing_run_id=billing_run_id)
        )
        await db.commit()
        if cas.rowcount == 0:
            logger.info(
                "Queue item %s no longer pending at print-command time "
                "(cancelled or removed mid-dispatch) — aborting before MQTT send (#1853)",
                item.id,
            )
            try:
                await delete_file_async(
                    printer.ip_address,
                    printer.access_code,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                )
            except Exception as cleanup_err:
                logger.debug(
                    "Queue item %s: best-effort cleanup of uploaded file failed: %s",
                    item.id,
                    cleanup_err,
                )
            try:
                await ws_manager.send_queue_item_failed(
                    user_id=toast_uid,
                    queue_item_id=item.id,
                    printer_id=item.printer_id,
                    reason="cancelled_mid_dispatch",
                )
            except Exception:
                pass
            return
        # Sync the in-memory item so subsequent code that reads item.status /
        # item.started_at sees the values we just persisted.
        item.status = "printing"
        item.started_at = now_utc
        item.billing_run_id = billing_run_id
        if archive is not None:
            archive.billing_run_id = billing_run_id
            # Legacy transaction deletion used an archive-wide skip flag.
            # A newly dispatched run has its own UUID/tombstone, so it must be
            # billable independently of any older deleted run on this archive.
            archive.wallet_charge_skipped = False
            # Persist before MQTT send so completion and restart recovery can
            # always recover the internal billing identity.
            await db.commit()

        for cleanup_path in cleanup_disk_paths:
            try:
                if cleanup_path.exists():
                    cleanup_path.unlink()
            except OSError as cleanup_err:
                logger.warning(
                    "TRANSIENT_LIBRARY_FILE_ORPHAN %s",
                    json.dumps(
                        {
                            "queue_item_id": item.id,
                            "path": str(cleanup_path),
                            "error": str(cleanup_err),
                        },
                        sort_keys=True,
                    ),
                )

        # Clear the awaiting-plate-clear flag now that we're starting a new print
        printer_manager.set_awaiting_plate_clear(item.printer_id, False)
        logger.info("Queue item %s: Status set to 'printing', sending print command...", item.id)

        # Capture state before dispatch so the watchdog can detect whether the
        # printer actually transitioned (#967). Also capture subtask_id so the
        # watchdog can recognise "command landed but state hasn't flipped yet"
        # on slow H2D transitions (#1078).
        pre_status = printer_manager.get_status(item.printer_id)
        pre_state = getattr(pre_status, "state", None) if pre_status else None
        pre_subtask_id = getattr(pre_status, "subtask_id", None) if pre_status else None
        pre_gcode_file = getattr(pre_status, "gcode_file", None) if pre_status else None

        # #1721: respect the user's explicit timelapse choice. The #1397
        # force-on at dispatch was removed because it caused per-layer nozzle
        # parking on slicer profiles with Timelapse Type = Smooth. Finish-photo
        # capture is now driven by the stg_cur=22 transition in bambu_mqtt.py
        # ("Filament unloading", toolhead parked, bed not yet dropped) with a
        # FINISH-state fallback — no need to force a video.
        effective_timelapse = bool(item.timelapse)

        # Nozzle-rack fallback (#2800). A job that never passed through the
        # Virtual Printer carries no Bambu Studio nozzle pick, and an H2C then
        # dispatches with no nozzle field at all and chooses for itself — which
        # is how a print levelled on one hotend and then printed on another,
        # millimetres above the plate. Derive the per-slot extruder assignment
        # from the file being dispatched.
        #
        # Done here rather than at queue time because this is the first point
        # that knows both the actual printer and the actual file: an item can
        # be created without a printer (model-based assignment), reassigned
        # afterwards, or have its file swapped for a G-code-injected copy just
        # above. Every queue-creation path — the print dialog, a bulk library
        # add, the webhook, a pipeline run — is covered by the one call.
        # Skipped when the item already carries a Bambu Studio capture: that
        # one wins downstream anyway, so reading the 3MF again would be work
        # thrown away on every dispatch.
        # Rack position resolution (#1784), tried before the #2800 fallback
        # below because it can express what that one cannot: a plate wanting a
        # *different* hotend off the rack per filament group. The position per
        # group is the operator's pick — the 3MF states it nowhere, proven by
        # sending one plate twice with different picks and finding the two
        # files identical bar float noise — so it is resolved here against the
        # rack as it stands right now, after the upload, not at queue time.
        resolved_nozzle_mapping = None
        if not item.nozzle_mapping and file_path is not None and is_nozzle_rack_model(printer.model):
            rack_plan = extract_rack_plan_from_3mf(file_path, plate_id=item.plate_id or 1)
            if rack_plan is not None:
                try:
                    stored_choice = json.loads(item.nozzle_rack_choice) if item.nozzle_rack_choice else {}
                except (json.JSONDecodeError, TypeError):
                    stored_choice = {}
                    logger.warning(
                        "Queue item %s: unreadable nozzle_rack_choice %r, assigning rack positions instead",
                        item.id,
                        item.nozzle_rack_choice,
                    )
                # JSON object keys are strings; the groups are ints.
                choice: dict[int, int] = {}
                for key, value in (stored_choice or {}).items():
                    try:
                        choice[int(key)] = int(value)
                    except (TypeError, ValueError):
                        continue

                live_rack = getattr(printer_manager.get_status(item.printer_id), "nozzle_rack", None) or []
                resolved_nozzle_mapping, rack_error = resolve_rack_plan_mapping(
                    rack_plan.slot_groups, rack_plan.group_dicts(), choice, live_rack
                )
                if resolved_nozzle_mapping is None and choice:
                    # An explicit pick that no longer holds stops the print. The
                    # operator named a hotend; printing from a different one is
                    # how a plate gets levelled on one nozzle and drawn with
                    # another, millimetres above the bed. Nothing has been sent
                    # to the printer yet, so failing here costs only the upload.
                    item.status = "failed"
                    item.error_message = (
                        f"Nozzle rack pick no longer fits the printer: {rack_error}. "
                        "Edit the item to choose another position."
                    )
                    item.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    logger.warning(
                        "Queue item %s: refusing to dispatch to %s — %s (chose %s, rack %s)",
                        item.id,
                        printer.name,
                        rack_error,
                        choice,
                        [slot.get("id") for slot in live_rack],
                    )
                    await notification_service.on_queue_job_failed(
                        job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                        printer_id=printer.id,
                        printer_name=printer.name,
                        reason=item.error_message,
                        db=db,
                    )
                    try:
                        await ws_manager.send_queue_item_failed(
                            user_id=toast_uid,
                            queue_item_id=item.id,
                            printer_id=item.printer_id,
                            reason="nozzle_rack_pick_stale",
                        )
                    except Exception:
                        pass  # Best-effort — don't fail the error handler
                    # The file is already on the SD card by this point, and a
                    # 3MF left there is a phantom print waiting to be started
                    # from the touchscreen. Same cleanup the start_print
                    # failure path below does, for the same reason.
                    try:
                        await delete_file_async(
                            printer.ip_address,
                            printer.access_code,
                            remote_path,
                            printer_model=printer.model,
                        )
                    except Exception:
                        pass  # Best-effort — don't fail the error handler
                    return
                if resolved_nozzle_mapping is None:
                    # Nothing was picked and nothing could be assigned. Falls
                    # through to the #2800 path, which is what ran before this
                    # existed — strictly not worse than today.
                    logger.info(
                        "Queue item %s: no rack positions assignable (%s); falling back",
                        item.id,
                        rack_error,
                    )
                else:
                    logger.info(
                        "Queue item %s: rack mapping %s (groups %s, chosen %s)",
                        item.id,
                        resolved_nozzle_mapping,
                        rack_plan.slot_groups,
                        choice or "auto",
                    )

        nozzle_slot_extruders = None
        if (
            not item.nozzle_mapping
            and resolved_nozzle_mapping is None
            and file_path is not None
            and is_nozzle_rack_model(printer.model)
        ):
            slot_extruders = extract_slot_extruders_from_3mf(file_path, plate_id=item.plate_id or 1)
            if slot_extruders:
                nozzle_slot_extruders = json.dumps(slot_extruders)

        # Start the print with AMS mapping, plate_id and print options.
        # nozzle_mapping rides through verbatim — JSON string captured from
        # Bambu Studio's project_file on VP intake (#1780); the MQTT layer
        # parses + injects it only for dual-nozzle models so a null on every
        # other model is a transparent pass-through. The rack fallback is
        # resolved down there too, where the live rack position is known.
        started = printer_manager.start_print(
            item.printer_id,
            remote_filename,
            plate_id=item.plate_id or 1,
            ams_mapping=ams_mapping,
            bed_levelling=item.bed_levelling,
            flow_cali=item.flow_cali,
            vibration_cali=item.vibration_cali,
            layer_inspect=item.layer_inspect,
            timelapse=effective_timelapse,
            use_ams=item.use_ams,
            nozzle_offset_cali=item.nozzle_offset_cali,
            nozzle_mapping=item.nozzle_mapping
            or (json.dumps(resolved_nozzle_mapping) if resolved_nozzle_mapping else None),
            nozzle_slot_extruders=nozzle_slot_extruders,
        )

        if started:
            # The command is away, so the expectation is now legitimate and must
            # survive. Anything still in this dict when _dispatch_one exits gets
            # rolled back.
            self._unconfirmed_expected_print.pop(item.id, None)
            self._unconfirmed_budget_reservations.discard(item.id)
            # Handoff to the print's own gcode: keep whatever preheat set (bed
            # target, chamber target, airduct heating) — the gcode owns
            # heater/flap control from here. Clearing the pin prevents
            # `_dispatch_one`'s finally from unwinding a live print.
            self._preheat_pin.pop(item.printer_id, None)
            self._preheat_pin_bed.pop(item.printer_id, None)
            logger.info("Queue item %s: Print started successfully - %s", item.id, filename)
            # No dispatch-toast event here: the legacy bg-dispatch path kept
            # status='processing' from upload start until the printer acked
            # (or timed out). The frontend derives "Awaiting printer…" purely
            # from upload_progress_pct >= 99.9; an explicit 'dispatched' WS
            # event would push the status chip out of 'PROCESSING' prematurely
            # — which is exactly what the screenshot at #1625-followup
            # complained about.

            # Register the local 3MF in the cover-cache so /cover skips FTP
            # (#1166 follow-up). file_path was resolved earlier from either the
            # archive or the library file row.
            if file_path is not None:
                cache_3mf_download(item.printer_id, remote_filename, file_path)

            # Hold the printer against further dispatches until the watchdog
            # confirms the printer transitioned (or until the hard timeout).
            # Prevents multi-plate batches from triple-dispatching onto the
            # same H2D Pro while it digests the first project_file (#1157).
            self._mark_printer_dispatched(item.printer_id, pre_state, pre_subtask_id)

            # Watchdog: if the printer never transitions out of pre_state AND
            # never advances subtask_id, the MQTT publish was accepted locally but
            # didn't reach the printer (half-broken session — same shape as
            # #887/#936). Revert the queue item so the next dispatch can pick it
            # up instead of leaving it stuck in "printing" (#967). subtask_id
            # check avoids false reverts on slow H2D FINISH→PREPARE transitions
            # that would otherwise cause the item to re-dispatch as a reprint
            # of the just-finished job (#1078).
            if pre_state:
                spawn_background_task(
                    self._watchdog_print_start(
                        item.id,
                        item.printer_id,
                        pre_state,
                        pre_subtask_id,
                        pre_gcode_file,
                        created_by_id=toast_uid,
                    ),
                    name=f"watchdog-print-start-{item.id}",
                )

            # Get estimated time for notification.
            #
            # This used to fall back to `library_file.print_time_seconds`, a column
            # LibraryFile does not have — the print time it knows about lives in
            # `file_metadata`. So a library print whose archive carried no parseable
            # print time (a plain .gcode, or a 3MF the parser could not read) raised
            # AttributeError right here, *after* the printer had already been sent
            # the job: the started-notification never fired, and the exception
            # unwound the whole queue pass, so every other printer still waiting to
            # be dispatched on that tick silently missed its turn.
            #
            # The queue item caches the print time at creation ("Cached from
            # archive/library"), which is the value this was reaching for.
            estimated_time = None
            if archive and archive.print_time_seconds:
                estimated_time = archive.print_time_seconds
            elif item.print_time_seconds:
                estimated_time = item.print_time_seconds

            # Send job started notification
            await notification_service.on_queue_job_started(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                db=db,
                estimated_time=estimated_time,
            )

            # MQTT relay - publish queue job started
            try:
                from backend.app.services.mqtt_relay import mqtt_relay

                await mqtt_relay.on_queue_job_started(
                    job_id=item.id,
                    filename=filename,
                    printer_id=printer.id,
                    printer_name=printer.name,
                    printer_serial=printer.serial_number,
                )
            except Exception:
                pass  # Don't fail if MQTT fails
        else:
            # Clean up uploaded file from SD card to prevent phantom prints
            try:
                await delete_file_async(
                    printer.ip_address,
                    printer.access_code,
                    remote_path,
                    printer_model=printer.model,
                )
            except Exception:
                pass  # Best-effort — don't fail the error handler

            # Busy-refusal is a deferral, not a failure (#2598). The printer's
            # state can flip from idle to active in the window between the
            # pre-dispatch check above and this publish (the FTP upload takes
            # seconds); start_print() then refuses to send project_file to the
            # now-busy printer and returns False. Failing the item here would be
            # wrong — the printer is fine, it is simply busy — so revert to
            # pending and let a later tick dispatch it once the printer is idle,
            # exactly like the pre-dispatch guard. Only a start_print() False on
            # an idle/unknown printer is a genuine command failure.
            post_dispatch_state = getattr(printer_manager.get_status(item.printer_id), "state", None)
            if post_dispatch_state in _ACTIVE_PRINT_STATES:
                logger.info(
                    "Queue item %s: printer %s became busy (state=%s) before the start "
                    "command was sent — deferring, reverting item to pending (#2598)",
                    item.id,
                    item.printer_id,
                    post_dispatch_state,
                )
                item.status = "pending"
                item.started_at = None
                await db.commit()
                return

            # Print command failed - revert status
            item.status = "failed"
            item.error_message = "Failed to send print command to printer"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error(
                f"Queue item {item.id}: Failed to start print on {printer.name} ({printer.model}) - "
                f"printer_manager.start_print() returned False. "
                f"This may indicate: printer not connected, MQTT error, unsupported model configuration, or firmware issue. "
                f"Check printer status and backend logs for details."
            )

            # Send failure notification
            await notification_service.on_queue_job_failed(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                reason="Failed to send print command to printer - check printer connection and status",
                db=db,
            )
            try:
                await ws_manager.send_queue_item_failed(
                    user_id=toast_uid,
                    queue_item_id=item.id,
                    printer_id=item.printer_id,
                    reason="start_command_failed",
                )
            except Exception:
                pass

            await self._power_off_if_needed(db, item)

    @staticmethod
    async def _watchdog_print_start(
        queue_item_id: int,
        printer_id: int,
        pre_state: str,
        pre_subtask_id: str | None = None,
        pre_gcode_file: str | None = None,
        timeout: float = 90.0,
        phase_b_timeout: float = 180.0,
        poll_interval: float = 3.0,
        created_by_id: int | None = None,
    ) -> None:
        """Revert a queue item if the printer never acknowledges the start command.

        Bambuddy optimistically marks the queue item as "printing" right after the
        MQTT project_file publish succeeds locally. The watchdog runs in two phases:

        Phase A (up to ``timeout``): wait for either an active-state transition
        or a ``subtask_id`` advance past ``pre_subtask_id``. State alone is the
        primary signal; subtask_id advance handles the H2D case where state can
        sit at FINISH for ~50 s after the printer accepted ``project_file``
        before flipping to PREPARE (#1078). If neither happens, the MQTT publish
        was lost on a half-broken session (#887/#936) — revert and force
        reconnect (the #967 recovery path).

        Phase B (up to ``phase_b_timeout``, only if Phase A exited on subtask_id
        alone): keep watching for the active-state transition. subtask_id alone
        proves the file landed but not that the printer started — and a printer
        that accepts the command but stays at IDLE/FINISH indefinitely (e.g.
        cloud+LAN re-auth dance after a power cycle on old firmware, #1678)
        used to leave the queue item stuck in 'printing' forever because the
        old watchdog returned success as soon as subtask_id advanced. If Phase
        B times out, revert the queue item so the user can retry without
        restarting Bambuddy. Skip ``force_reconnect`` here: the file landed and
        a forced reconnect mid-parse triggers 0500_4003 (#1150).

        Phase A timeout raised from 45 s → 90 s as belt-and-braces for slow
        transitions that also don't emit an early subtask_id tick.

        Both phases also watch for ``HMS_MQTT_VERIFY_FAILED``. A printer that
        refuses to verify our commands will never start this job or any other,
        so waiting out the full 270 s and re-uploading the 3MF twice more only
        burns an upload slot the rest of the farm is queued behind — that path
        is for a printer that might still come good, which this one cannot
        (#2732). It fails the item on the spot with the actual reason instead.
        """
        last_status = None
        landed_on_subtask = False
        # Latched, not level-tested: state.hms_errors is rebuilt from scratch on
        # every push carrying an `hms` key, so the fault can come and go between
        # 3-second polls. Seeing it once inside the dispatch window is enough.
        command_rejected = False
        # Latched for the same reason as command_rejected: drying can finish, or
        # be stopped by the user, part-way through the dispatch window. Seeing it
        # once is what matters — it is the state the printer was in when it
        # declined to start (#2758).
        drying_ams_ids: list[int] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            status = printer_manager.get_status(printer_id)
            if not status:
                # Printer disconnected — don't mess with the DB. Drop the
                # in-memory dispatch hold too so a fresh dispatch can retry
                # once the printer comes back; the hard timeout would
                # otherwise hold the printer unnecessarily.
                scheduler._release_dispatch_hold(printer_id)
                return
            last_status = status
            if status.state in _ACTIVE_PRINT_STATES:
                # Printer is actively processing the job — release the
                # post-dispatch hold so the next pending item for this printer
                # can be evaluated normally. We do NOT accept arbitrary state
                # transitions: a printer going FINISH -> IDLE (user dismissed
                # the post-print prompt without accepting our project_file)
                # would otherwise look like "command landed" and leave the
                # queue item stuck in 'printing' forever (#1370).
                scheduler._release_dispatch_hold(printer_id)
                try:
                    await ws_manager.send_queue_item_acked(
                        user_id=created_by_id,
                        queue_item_id=queue_item_id,
                        printer_id=printer_id,
                    )
                except Exception:
                    pass
                return
            drying_ams_ids = drying_ams_ids or _drying_ams_ids(status)
            # Checked only after the active-state exit above: a stale HMS left
            # over from an earlier job must never abort a print that is visibly
            # running. An actually-refused command leaves the printer idle, so
            # this ordering costs the detection nothing.
            if _mqtt_commands_rejected(status):
                command_rejected = True
                break
            if pre_subtask_id is not None and status.subtask_id is not None and status.subtask_id != pre_subtask_id:
                # Phase A exit — printer accepted the file (subtask_id flipped
                # to our submission id). Don't return yet: the printer may
                # have accepted the command but never actually start (e.g.
                # cloud+LAN re-auth dance after a power cycle, #1678). Phase
                # B watches for the active-state transition.
                landed_on_subtask = True
                break

        if landed_on_subtask and not command_rejected:
            phase_b_deadline = time.monotonic() + phase_b_timeout
            while time.monotonic() < phase_b_deadline:
                await asyncio.sleep(poll_interval)
                status = printer_manager.get_status(printer_id)
                if not status:
                    scheduler._release_dispatch_hold(printer_id)
                    return
                last_status = status
                if status.state in _ACTIVE_PRINT_STATES:
                    scheduler._release_dispatch_hold(printer_id)
                    try:
                        await ws_manager.send_queue_item_acked(
                            user_id=created_by_id,
                            queue_item_id=queue_item_id,
                            printer_id=printer_id,
                        )
                    except Exception:
                        pass
                    return
                drying_ams_ids = drying_ams_ids or _drying_ams_ids(status)
                # Same ordering rule as Phase A: a running print wins over a
                # lingering HMS.
                if _mqtt_commands_rejected(status):
                    command_rejected = True
                    break

        # No active-state transition. Revert the item so the scheduler can retry.
        # Drop the in-memory hold so the retry isn't blocked by it.
        scheduler._release_dispatch_hold(printer_id)

        # Logged on every failed dispatch window, not just the last one, so a
        # support bundle shows the correlation from the first attempt rather than
        # only after the retry budget is spent (#2758).
        if drying_ams_ids:
            logger.info(
                "Queue item %s: printer %d never started while AMS %s drying — this may be why, see #2758",
                queue_item_id,
                printer_id,
                ", ".join(str(i) for i in drying_ams_ids),
            )

        # Four outcomes from the revert attempt, each routed differently:
        #   "reverted":          row flipped from printing -> pending, run recovery
        #   "gave_up":           same, but the retry budget is spent — row failed
        #                        rather than pending, so it stops going round again
        #   "already_moved_on":  item.status != 'printing' (completed/cancelled by
        #                        on_print_complete or user). Skip recovery entirely
        #                        — the print clearly landed somewhere even if the
        #                        watchdog didn't see the active-state transition.
        #   "revert_failed":     SQLite contention exhausted retries. Still run
        #                        recovery so the MQTT session gets a fresh client_id
        #                        on the half-broken-session path.
        #
        # The retry budget (#2555): reverting to 'pending' hands the item straight
        # back to the next queue pass, which re-uploads the whole 3MF and waits out
        # the watchdog again. For a printer that is genuinely wedged that loop never
        # ends — the reporter had one printer "since this morning still not launch"
        # — and each lap also consumes an upload slot that the other printers in the
        # farm are waiting on. Retrying is right; retrying forever is not.
        async def _do_revert(db):
            item = await db.get(PrintQueueItem, queue_item_id)
            if not item or item.status != "printing":
                return "already_moved_on"
            item.dispatch_attempts = (item.dispatch_attempts or 0) + 1
            item.started_at = None
            # Charge the attempt to the candidate that was actually dispatched, so
            # a cross-model item (#671) reaches for its other file next lap instead
            # of retrying the printer that just failed to start. Matched by file
            # because that is what the resolver copied onto the row.
            if item.library_file_id is not None:
                await db.execute(
                    update(PrintQueueVariant)
                    .where(PrintQueueVariant.queue_item_id == item.id)
                    .where(PrintQueueVariant.library_file_id == item.library_file_id)
                    .values(attempt_count=PrintQueueVariant.attempt_count + 1)
                )
            if command_rejected:
                # No retry budget for this one: the printer refused to verify the
                # command, and re-uploading the same 3MF to the same printer will
                # be refused the same way. Fail now with the fix rather than after
                # three laps of a message about SD cards (#2732).
                item.status = "failed"
                item.error_message = (
                    "The printer rejected the print command: MQTT command verification failed "
                    "(HMS 0500-0500-0001-0007). Enable Developer Mode on the printer, restart it, "
                    "then start the job again."
                )
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                return "command_rejected"
            if item.dispatch_attempts >= DISPATCH_MAX_ATTEMPTS:
                item.status = "failed"
                if drying_ams_ids:
                    # #2758: the generic message below sent the reporter looking
                    # at the SD card while the actual obstacle — AMS units in a
                    # drying cycle — was on screen the whole time. Name what we
                    # observed and let the user judge it; Bambuddy does not stop
                    # the cycle itself, because on this hardware drying can run
                    # alongside a print and stopping it may not be the fix.
                    units = ", ".join(f"AMS {i}" for i in drying_ams_ids)
                    item.error_message = (
                        f"The printer accepted the file but never started printing, after "
                        f"{item.dispatch_attempts} attempts. {units} "
                        f"{'was' if len(drying_ams_ids) == 1 else 'were'} drying throughout — "
                        f"some printers refuse to begin a print while an AMS is in a drying "
                        f"cycle, and an AMS drying without its external power supply can also "
                        f"leave too little power for the start-of-print calibration. Stop the "
                        f"drying, or connect the AMS power supply, and start the job again."
                    )
                else:
                    item.error_message = (
                        f"The printer accepted the file but never started printing, after "
                        f"{item.dispatch_attempts} attempts. Check the printer's screen for a "
                        f"prompt or error, confirm its SD card is readable, and start the job again."
                    )
                item.completed_at = datetime.now(timezone.utc)
                await release_budget_reservation(
                    db,
                    source_type="print_queue",
                    source_id=item.id,
                    status="released",
                )
                await db.commit()
                return "gave_up"
            item.status = "pending"
            await db.commit()
            return "reverted"

        try:
            revert_outcome = await run_with_retry(_do_revert, label=f"watchdog revert item={queue_item_id}")
        except Exception as e:
            logger.warning(
                "Queue item %s: failed to revert to 'pending' (printer %d): %s — "
                "scheduler may keep treating this item as in-flight",
                queue_item_id,
                printer_id,
                e,
            )
            revert_outcome = "revert_failed"

        if revert_outcome == "already_moved_on":
            # Preserves the pre-#1370 early-return: if on_print_complete (or any
            # other path) already moved the item past 'printing', don't run the
            # MQTT session-recovery logic below — a forced reconnect on a healthy
            # session breaks ongoing prints on the same printer.
            return

        total_timeout = timeout + (phase_b_timeout if landed_on_subtask else 0.0)
        if revert_outcome == "command_rejected":
            logger.error(
                "Queue item %s: printer %d reported HMS %s (MQTT command verification "
                "failed) — the print command was rejected, not lost. Failing the item "
                "without retrying; enable Developer Mode on the printer and restart it (#2732)",
                queue_item_id,
                printer_id,
                HMS_MQTT_VERIFY_FAILED,
            )
            await scheduler._notify_dispatch_gave_up(
                queue_item_id,
                printer_id,
                created_by_id,
                reason="Printer rejected the print command (MQTT command verification failed)",
            )
            # Same reasoning as the landed_on_subtask path below: the file is on
            # the printer and a forced reconnect would only add 0500_4003 to a
            # problem that has nothing to do with the MQTT session (#1150).
            return
        if revert_outcome == "gave_up":
            logger.error(
                "Queue item %s: printer %d never started the print after %d dispatch "
                "attempts (last one waited %.0fs) — marking the item failed instead of "
                "re-uploading it again (#2555)",
                queue_item_id,
                printer_id,
                DISPATCH_MAX_ATTEMPTS,
                total_timeout,
            )
            await scheduler._notify_dispatch_gave_up(queue_item_id, printer_id, created_by_id)
        elif revert_outcome == "reverted":
            if landed_on_subtask:
                logger.warning(
                    "Queue item %s: printer %d accepted project_file (subtask_id "
                    "advanced) but never transitioned to an active state within "
                    "%.0fs — printer wedged post-acceptance; reverted to 'pending' "
                    "for retry (#1678)",
                    queue_item_id,
                    printer_id,
                    total_timeout,
                )
            else:
                logger.warning(
                    "Queue item %s: printer %d did not respond to print command within "
                    "%.0fs (state still %s, subtask_id still %s) — reverted to 'pending' "
                    "for retry (#967)",
                    queue_item_id,
                    printer_id,
                    timeout,
                    pre_state,
                    pre_subtask_id,
                )

        # Phase B was entered iff subtask_id advanced, which means the
        # project_file landed on the printer. A forced reconnect at this point
        # would interrupt the printer's parse and trigger 0500_4003 (#1150) —
        # skip the recovery entirely.
        if landed_on_subtask:
            return

        # Phase A timeout path: if the printer's gcode_file changed since
        # pre-dispatch, the project_file command landed and the printer is
        # parsing — a forced reconnect mid-parse triggers 0500_4003 (#1150).
        # If gcode_file is unchanged, the publish was silently swallowed
        # (#887/#936) and force_reconnect recovery is what we want.
        client = printer_manager.get_client(printer_id)
        current_gcode_file = getattr(last_status, "gcode_file", None) if last_status else None
        publish_landed = current_gcode_file is not None and current_gcode_file != pre_gcode_file
        if publish_landed:
            logger.warning(
                "Queue item %s: gcode_file changed to %r (was %r) — printer "
                "received the command and is parsing slowly. Skipping forced "
                "MQTT reconnect to avoid 0500_4003 mid-parse (#1150).",
                queue_item_id,
                current_gcode_file,
                pre_gcode_file,
            )
        elif client and hasattr(client, "force_reconnect_stale_session"):
            client.force_reconnect_stale_session(
                f"queue print command unacknowledged after {timeout:.0f}s "
                f"(state still {pre_state}, gcode_file {current_gcode_file!r})"
            )


# Global scheduler instance
scheduler = PrintScheduler()

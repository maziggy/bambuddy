"""The queue's "To do" list: what each waiting job needs a person for (#3615).

Two sources, neither parsed from ``waiting_reason`` text: the printers' live
state (plate to clear, sensor interlock, failed power-on) and the jobs' own
state (staged for a manual start, plus the structured filament holds the
scheduler records next to the words it writes). A plate changer with no plate
left (#3575) is printer state too. This module holds the shared records and
the pure part of the list; the route gathers the inputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

KIND_PLATE_CLEAR = "plate_clear"
KIND_PLATE_CHANGER_EMPTY = "plate_changer_empty"
KIND_SENSOR_INTERLOCK = "sensor_interlock"
KIND_POWER_ON_FAILED = "power_on_failed"
KIND_LOAD_FILAMENT = "load_filament"
KIND_START_STAGED = "start_staged"

# Order of the entries inside one group: what keeps the printer from taking
# any job first, then the filament, then the jobs that only wait for a start.
_KIND_ORDER = (
    KIND_PLATE_CLEAR,
    KIND_PLATE_CHANGER_EMPTY,
    KIND_SENSOR_INTERLOCK,
    KIND_POWER_ON_FAILED,
    KIND_LOAD_FILAMENT,
    KIND_START_STAGED,
)


@dataclass(frozen=True)
class ShortTray:
    """A tray whose spool has too little filament for the job (#3137).

    ``ams_id`` and ``tray_id`` name the slot as the inventory stores it;
    ``remaining_grams`` is None when the amount is not known.
    """

    ams_id: int
    tray_id: int
    remaining_grams: float | None = None


@dataclass(frozen=True)
class TodoFilament:
    """One filament a job waits for. ``color`` is "#RRGGBB" or empty.

    ``nozzle`` is the extruder a dual-nozzle file wants it on, as the waiting
    text names it: 0 the right nozzle, 1 the left, None when the file names none.

    ``no_stock`` marks a filament no spool in the inventory has left (#3590).

    ``short`` marks a filament that is loaded but in too little amount (#3137):
    it is not missing, a spool holds less than the job needs. ``short_trays``
    name the tray of that spool and ``required_grams`` is what the job prints
    of the filament. None of the three takes part in equality, so a list of
    filaments compares as it did before they existed.
    """

    type: str
    color: str
    color_name: str = ""
    no_stock: bool = False
    nozzle: int | None = None
    short: bool = field(default=False, compare=False)
    short_trays: tuple[ShortTray, ...] = field(default=(), compare=False)
    required_grams: float | None = field(default=None, compare=False)


@dataclass(frozen=True)
class FilamentHold:
    """Why one job is held on filament, for one printer.

    ``printer_id`` None with ``target_model`` set is a model job whose printer
    set is not known (it is listed under "Any <model>").
    """

    item_id: int
    printer_id: int | None
    target_model: str | None
    filaments: tuple[TodoFilament, ...]


def todo_filament_from_requirement(req: Mapping) -> TodoFilament:
    """A TodoFilament from a requirement or override dict (type/color/color_name/nozzle_id)."""
    nozzle = req.get("nozzle_id")
    return TodoFilament(
        type=str(req.get("type") or "").upper(),
        color=normalize_hex(req.get("color")),
        color_name=str(req.get("color_name") or ""),
        nozzle=nozzle if type(nozzle) is int and nozzle in (0, 1) else None,
    )


def normalize_hex(value: object) -> str:
    """ "#RRGGBB" from "RRGGBB", "#rrggbbaa" and the like; empty when it is no colour."""
    if not isinstance(value, str):
        return ""
    raw = value.strip().lstrip("#")[:6]
    if len(raw) != 6 or any(c not in "0123456789abcdefABCDEF" for c in raw):
        return ""
    return f"#{raw.upper()}"


@dataclass
class TodoJob:
    """A pending job as the list needs it."""

    id: int
    name: str
    # Printers the job could go to: its pinned printer, or every printer of
    # its model (and location) that its creator may use and the caller may see.
    printer_ids: frozenset[int]
    # The pinned printer, None for a model job.
    pinned_id: int | None
    # Lower-case model of a model job, used to group holds that name no printer.
    model_key: str | None
    model_label: str | None
    manual_start: bool
    # Whether the caller may start it now (only read for staged jobs).
    can_start: bool = False


@dataclass
class _Group:
    printer_id: int | None
    printer_name: str
    target_model: str | None
    first_index: int
    entries: dict[tuple, dict] = field(default_factory=dict)


def _sorted(filaments: Iterable[TodoFilament]) -> tuple[TodoFilament, ...]:
    # Without a nozzle first, then right (0) before left (1).
    return tuple(sorted(filaments, key=lambda f: (f.type, f.color, f.color_name, -1 if f.nozzle is None else f.nozzle)))


def filament_identity(f: TodoFilament) -> tuple:
    """Everything that makes two filaments two things to do.

    ``TodoFilament`` equality leaves out the short fields; a short spool and a
    missing one of the same filament, or short spools in two trays, are still
    different entries.
    """
    return (f, f.short, tuple((t.ams_id, t.tray_id) for t in f.short_trays))


def _fil_key(filaments: Iterable[TodoFilament], trays: bool = True) -> tuple:
    # A short spool and a missing one of the same filament are two things to
    # do, and so are a filament to load and one to buy (#3590). Short spools in
    # different trays are too, except where the trays are dropped anyway (a
    # model job's printers, which ``_without_trays`` lists without a slot).
    return tuple(
        (
            f.type,
            f.color,
            f.nozzle,
            f.short,
            f.no_stock,
            tuple((t.ams_id, t.tray_id) for t in f.short_trays) if trays else (),
        )
        for f in _sorted(filaments)
    )


def _most_needed(base: tuple[TodoFilament, ...], more: tuple[TodoFilament, ...]) -> tuple[TodoFilament, ...]:
    """``base`` with the larger ``required_grams`` of ``more`` (the same filaments, in the same order)."""
    if len(base) != len(more):
        return base
    merged = []
    for a, b in zip(base, more, strict=True):
        if a.short and b.short and b.required_grams is not None and (a.required_grams or 0) < b.required_grams:
            a = replace(a, required_grams=b.required_grams)
        merged.append(a)
    return tuple(merged)


def _without_trays(filaments: Iterable[TodoFilament]) -> tuple[TodoFilament, ...]:
    """The filaments without the tray of a short spool, which belongs to one printer."""
    return tuple(replace(f, short_trays=()) if f.short_trays else f for f in filaments)


def build_todo(
    jobs: list[TodoJob],
    printers: Mapping[int, str],
    holds: Mapping[int, list[FilamentHold]],
    plate_clear_ids: Iterable[int],
    interlock: Mapping[int, str],
    power_failed_ids: Iterable[int],
    plate_changer_empty: Mapping[int, int | None] | None = None,
    printer_models: Mapping[int, str | None] | None = None,
) -> dict:
    """The response of GET /queue/todo.

    ``jobs`` are the visible pending jobs in queue order; ``printers`` maps the
    visible printer ids to their names. Printer entries need a waiting job
    that could go to that printer. ``plate_changer_empty`` maps the printers
    whose plate changer has no plate left to their capacity (#3575).
    ``printer_models`` maps printer ids to their model, which names the slot
    of a short spool as the printer card does (the external inputs of a rack
    printer).
    """
    groups: dict[tuple, _Group] = {}
    index_of = {job.id: i for i, job in enumerate(jobs)}

    def group_for(printer_id: int | None, model_key: str | None, model_label: str | None, index: int) -> _Group:
        key = ("p", printer_id) if printer_id is not None else ("m", model_key)
        group = groups.get(key)
        if group is None:
            name = printers.get(printer_id, "") if printer_id is not None else (model_label or "")
            group = groups[key] = _Group(printer_id, name, None if printer_id is not None else model_label, index)
        else:
            group.first_index = min(group.first_index, index)
        return group

    def add(group: _Group, kind: str, subkey: tuple, job: TodoJob, **extra) -> None:
        entry = group.entries.get((kind, subkey))
        if entry is None:
            entry = group.entries[(kind, subkey)] = {
                "kind": kind,
                "jobs": [],
                "detail": None,
                "printers": [],
                "plate_capacity": None,
                **extra,
            }
        elif "_filaments" in extra:
            # Another job waits for the same spool: the entry names the most any of them needs.
            entry["_filaments"] = _most_needed(entry["_filaments"], extra["_filaments"])
        if all(j["id"] != job.id for j in entry["jobs"]):
            entry["jobs"].append(
                {"id": job.id, "name": job.name, "can_start": kind == KIND_START_STAGED and job.can_start}
            )

    # Printer-state entries, one per printer, listing the jobs that could go there.
    printer_kinds: list[tuple[str, int, str | None]] = []
    printer_kinds += [(KIND_PLATE_CLEAR, pid, None) for pid in plate_clear_ids]
    printer_kinds += [(KIND_PLATE_CHANGER_EMPTY, pid, None) for pid in (plate_changer_empty or {})]
    printer_kinds += [(KIND_SENSOR_INTERLOCK, pid, reason) for pid, reason in interlock.items()]
    printer_kinds += [(KIND_POWER_ON_FAILED, pid, None) for pid in power_failed_ids]
    for kind, pid, detail in printer_kinds:
        if pid not in printers:
            continue
        waiting = [job for job in jobs if pid in job.printer_ids]
        if not waiting:
            continue
        group = group_for(pid, None, None, index_of[waiting[0].id])
        for job in waiting:
            add(group, kind, (), job)
        group.entries[(kind, ())]["detail"] = detail or None
        if kind == KIND_PLATE_CHANGER_EMPTY:
            group.entries[(kind, ())]["plate_capacity"] = (plate_changer_empty or {}).get(pid)

    for job in jobs:
        index = index_of[job.id]
        holds_of_job = holds.get(job.id, [])
        if job.pinned_id is not None:
            if job.pinned_id not in printers:
                continue
            group = group_for(job.pinned_id, None, None, index)
            for hold in holds_of_job:
                add(group, KIND_LOAD_FILAMENT, _fil_key(hold.filaments), job, _filaments=_sorted(hold.filaments))
        else:
            # A model job's per-printer holds become one entry: the printers
            # that miss the fewest filaments are where loading them lets it start.
            visible = [h for h in holds_of_job if h.printer_id in printers]
            if visible:
                fewest = min(len(h.filaments) for h in visible)
                by_set: dict[tuple, tuple[tuple[TodoFilament, ...], set[int]]] = {}
                for hold in visible:
                    if len(hold.filaments) == fewest:
                        sets = by_set.setdefault(
                            _fil_key(hold.filaments, trays=False), (_sorted(hold.filaments), set())
                        )
                        assert hold.printer_id is not None
                        sets[1].add(hold.printer_id)
                for fils, ids in by_set.values():
                    # The tray of a short spool is named for one printer only.
                    fils = fils if len(ids) == 1 else _without_trays(fils)
                    add(
                        group_for(None, job.model_key, job.model_label, index),
                        KIND_LOAD_FILAMENT,
                        (_fil_key(fils), tuple(sorted(ids))),
                        job,
                        _filaments=fils,
                        printers=[{"id": pid, "name": printers[pid]} for pid in sorted(ids)],
                    )
            else:
                for hold in holds_of_job:
                    if hold.printer_id is None:
                        add(
                            group_for(None, job.model_key, job.model_label, index),
                            KIND_LOAD_FILAMENT,
                            (_fil_key(hold.filaments), ()),
                            job,
                            _filaments=_sorted(hold.filaments),
                        )
        if job.manual_start:
            group = group_for(job.pinned_id, job.model_key, job.model_label, index)
            add(group, KIND_START_STAGED, (), job)

    out_groups = []
    count = 0
    for group in sorted(groups.values(), key=lambda g: g.first_index):
        entries = []
        for kind in _KIND_ORDER:
            for (k, _sub), entry in group.entries.items():
                if k != kind:
                    continue
                fils = entry.pop("_filaments", ())
                entry["filaments"] = [
                    {
                        "type": f.type,
                        "color": f.color,
                        "color_name": f.color_name,
                        "no_stock": f.no_stock,
                        "nozzle": f.nozzle,
                        "short": f.short,
                        "short_trays": [
                            {"ams_id": t.ams_id, "tray_id": t.tray_id, "remaining_grams": t.remaining_grams}
                            for t in f.short_trays
                        ],
                        "required_grams": f.required_grams,
                    }
                    for f in fils
                ]
                entries.append(entry)
        if not entries:
            continue
        count += len(entries)
        out_groups.append(
            {
                "printer_id": group.printer_id,
                "printer_name": group.printer_name,
                "target_model": group.target_model,
                "printer_model": (printer_models or {}).get(group.printer_id)
                if group.printer_id is not None
                else group.target_model,
                "entries": entries,
            }
        )
    return {"count": count, "groups": out_groups}

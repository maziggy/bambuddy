"""Eligibility matcher for Slicer Pipeline runs (#1425 PR B).

Given a pipeline + the user's pinned target printer, this returns a structured
report of issues the operator should resolve before running. The frontend
displays the report; the user can ``Run anyway`` to proceed (lenient policy —
the print may still fail at the printer, but Bambuddy isn't going to refuse
the click).

Issue kinds (pinned for tests + i18n keys):
  - printer_not_set         — pipeline has no target_printer_id
  - printer_not_found       — target_printer_id points at a deleted/missing row
  - printer_disabled        — Printer.is_active is False (#1476)
  - printer_offline         — MQTT not connected
  - filament_type_mismatch  — AMS slot loaded with wrong filament type
  - filament_color_mismatch — type matches, colour differs
  - ams_slot_missing        — pipeline expects N filament slots but AMS exposes fewer
  - filament_unverified     — pipeline filament preset is a non-local tier we
                              can't statically read (cloud / orca_cloud / standard);
                              the run will proceed, but the operator should
                              double-check

The matcher is a pure-ish function over (pipeline, printer row, live AMS state,
local-preset dict) so unit tests can drive it with fixtures without spinning up
MQTT. The route handler is the only place that talks to ``printer_manager``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.local_preset import LocalPreset
from backend.app.models.printer import Printer
from backend.app.models.slicer_pipeline import SlicerPipeline

IssueKind = Literal[
    "printer_not_set",
    "printer_not_found",
    "printer_disabled",
    "printer_offline",
    "filament_type_mismatch",
    "filament_color_mismatch",
    "ams_slot_missing",
    "filament_unverified",
    "no_class_matches",
    "class_not_set",
]


@dataclass(frozen=True)
class EligibilityIssue:
    kind: IssueKind
    slot_index: int | None = None
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class PerPrinterReport:
    """One row of the class-targeting eligibility breakdown."""

    printer_id: int
    printer_name: str
    ok: bool
    issues: tuple[EligibilityIssue, ...]


@dataclass(frozen=True)
class EligibilityReport:
    ok: bool
    target_kind: Literal["specific_printer", "printer_class"]
    target_printer_id: int | None
    target_printer_name: str | None
    target_model_class: str | None
    issues: tuple[EligibilityIssue, ...]
    printer_reports: tuple[PerPrinterReport, ...] = ()


# Same equivalence map as print_scheduler._canonical_filament_type but kept
# local so this module has no upward dependency on the scheduler. Mirrors the
# scheduler's behaviour: BBL-prefixed product names normalise to the base type
# (e.g. "PLA Basic" → "PLA"). When the scheduler's map gets a new alias, this
# one needs the same entry.
_FILAMENT_EQUIV_MAP = {
    "PLA": "PLA",
    "PLA BASIC": "PLA",
    "PLA MATTE": "PLA",
    "PLA SILK": "PLA",
    "PLA PRO": "PLA",
    "PLA TOUGH": "PLA",
    "PETG": "PETG",
    "PETG HF": "PETG",
    "PETG BASIC": "PETG",
    "PETG TRANSLUCENT": "PETG",
    "ABS": "ABS",
    "ASA": "ASA",
    "TPU": "TPU",
    "TPU 95A": "TPU",
    "PC": "PC",
    "PA": "PA",
    "PA-CF": "PA",
    "PVA": "PVA",
}


def _canonical(ftype: str) -> str:
    upper = (ftype or "").strip().upper()
    return _FILAMENT_EQUIV_MAP.get(upper, upper)


def _normalise_colour(colour: str | None) -> str:
    if not colour:
        return ""
    return colour.replace("#", "").lower()[:6]


def _ams_slots(raw_data: dict) -> list[tuple[str, str]]:
    """Flatten AMS + external spool into ``[(type, colour_hex6), ...]`` in slot
    order. Uses the same field shape as print_scheduler._check_required_filaments.
    """
    out: list[tuple[str, str]] = []
    for ams_unit in raw_data.get("ams") or []:
        for tray in ams_unit.get("tray") or []:
            tray_type = tray.get("tray_type") or ""
            tray_colour = tray.get("tray_color") or ""
            out.append((_canonical(tray_type), _normalise_colour(tray_colour)))
    for vt in raw_data.get("vt_tray") or []:
        vt_type = vt.get("tray_type") or ""
        vt_colour = vt.get("tray_color") or ""
        out.append((_canonical(vt_type), _normalise_colour(vt_colour)))
    return out


async def _expected_filament(
    db: AsyncSession,
    source: str,
    preset_id: str,
) -> tuple[str | None, str | None]:
    """Return ``(canonical_type, normalised_colour)`` for a pipeline filament
    slot's PresetRef, or ``(None, None)`` when the preset can't be resolved
    statically (cloud / orca_cloud / standard — read at slice time, not here).
    """
    if source != "local":
        # Cloud / orca_cloud / standard: surface as ``filament_unverified``
        # in the report, the matcher decides.
        return (None, None)
    try:
        local_id = int(preset_id)
    except (TypeError, ValueError):
        return (None, None)
    row = (await db.execute(select(LocalPreset).where(LocalPreset.id == local_id))).scalar_one_or_none()
    if row is None:
        return (None, None)
    return (_canonical(row.filament_type or ""), _normalise_colour(row.default_filament_colour))


async def _check_one_printer(
    db: AsyncSession,
    pipeline: SlicerPipeline,
    printer: Printer,
    printer_raw_status: dict | None,
) -> tuple[bool, tuple[EligibilityIssue, ...]]:
    """Run the per-printer eligibility checks. Returns ``(ok, issues)`` so the
    caller can flatten them into either a single-printer or class-targeting
    report. Pulled out of the original entry function so PR C's class branch
    can reuse it for each candidate printer."""
    issues: list[EligibilityIssue] = []

    if not printer.is_active:
        issues.append(EligibilityIssue(kind="printer_disabled"))

    if not printer_raw_status or not printer_raw_status.get("connected"):
        issues.append(EligibilityIssue(kind="printer_offline"))
        return (not issues, tuple(issues))

    try:
        filament_refs = json.loads(pipeline.filament_presets_json or "[]")
    except (json.JSONDecodeError, TypeError):
        filament_refs = []

    ams_slots = _ams_slots(printer_raw_status.get("raw_data") or {})

    for slot_index, ref in enumerate(filament_refs):
        if not isinstance(ref, dict):
            continue
        source = ref.get("source", "")
        preset_id = ref.get("id", "")
        expected_type, expected_colour = await _expected_filament(db, source, str(preset_id))

        if expected_type is None:
            issues.append(
                EligibilityIssue(
                    kind="filament_unverified",
                    slot_index=slot_index,
                    expected=f"{source}:{preset_id}",
                )
            )
            continue

        if slot_index >= len(ams_slots):
            issues.append(
                EligibilityIssue(
                    kind="ams_slot_missing",
                    slot_index=slot_index,
                    expected=expected_type,
                )
            )
            continue

        actual_type, actual_colour = ams_slots[slot_index]
        if expected_type and actual_type and expected_type != actual_type:
            issues.append(
                EligibilityIssue(
                    kind="filament_type_mismatch",
                    slot_index=slot_index,
                    expected=expected_type,
                    actual=actual_type or "(empty)",
                )
            )
            continue
        if expected_colour and actual_colour and expected_colour != actual_colour:
            issues.append(
                EligibilityIssue(
                    kind="filament_color_mismatch",
                    slot_index=slot_index,
                    expected=expected_colour,
                    actual=actual_colour,
                )
            )

    # ``filament_unverified`` is informational — doesn't flip ok=False.
    blocking_issues = [i for i in issues if i.kind != "filament_unverified"]
    return (not blocking_issues, tuple(issues))


async def check_pipeline_eligibility(
    db: AsyncSession,
    pipeline: SlicerPipeline,
    printer_raw_status: dict | None = None,
    *,
    status_lookup: object = None,
) -> EligibilityReport:
    """Build the eligibility report.

    Two calling shapes, chosen by ``pipeline.target_kind``:
      - ``specific_printer``: ``printer_raw_status`` carries the live
        ``PrinterState`` dict (``connected`` + ``raw_data``) for the pinned
        target_printer_id. PR B signature, preserved.
      - ``printer_class``: ``status_lookup`` is a callable
        ``(printer_id) -> dict | None`` that the matcher calls for each
        printer whose model matches ``pipeline.target_model_class``.
    """
    # PR A pipelines default target_kind to 'printer_class' but PR B and
    # earlier UI only let users pin a specific_printer; treat
    # ``target_printer_id is not None`` as the source of truth for the
    # specific-printer path until the editor exposes target_kind explicitly.
    if pipeline.target_printer_id is not None or pipeline.target_kind == "specific_printer":
        # Specific-printer branch (PR B parity).
        if pipeline.target_printer_id is None:
            return EligibilityReport(
                ok=False,
                target_kind="specific_printer",
                target_printer_id=None,
                target_printer_name=None,
                target_model_class=None,
                issues=(EligibilityIssue(kind="printer_not_set"),),
            )

        printer = (
            await db.execute(select(Printer).where(Printer.id == pipeline.target_printer_id))
        ).scalar_one_or_none()
        if printer is None:
            return EligibilityReport(
                ok=False,
                target_kind="specific_printer",
                target_printer_id=pipeline.target_printer_id,
                target_printer_name=None,
                target_model_class=None,
                issues=(EligibilityIssue(kind="printer_not_found"),),
            )

        ok, issues = await _check_one_printer(db, pipeline, printer, printer_raw_status)
        return EligibilityReport(
            ok=ok,
            target_kind="specific_printer",
            target_printer_id=printer.id,
            target_printer_name=printer.name,
            target_model_class=None,
            issues=issues,
        )

    # Class-targeting branch (PR C).
    if not pipeline.target_model_class:
        return EligibilityReport(
            ok=False,
            target_kind="printer_class",
            target_printer_id=None,
            target_printer_name=None,
            target_model_class=None,
            issues=(EligibilityIssue(kind="class_not_set"),),
        )

    candidates = (await db.execute(select(Printer).where(Printer.model == pipeline.target_model_class))).scalars().all()

    if not candidates:
        return EligibilityReport(
            ok=False,
            target_kind="printer_class",
            target_printer_id=None,
            target_printer_name=None,
            target_model_class=pipeline.target_model_class,
            issues=(
                EligibilityIssue(
                    kind="no_class_matches",
                    expected=pipeline.target_model_class,
                ),
            ),
        )

    reports: list[PerPrinterReport] = []
    if status_lookup is None:
        # Treat all printers as offline when no lookup was provided — keeps
        # the matcher pure-ish for unit tests.
        for printer in candidates:
            ok, issues = await _check_one_printer(db, pipeline, printer, None)
            reports.append(
                PerPrinterReport(
                    printer_id=printer.id,
                    printer_name=printer.name,
                    ok=ok,
                    issues=issues,
                )
            )
    else:
        for printer in candidates:
            raw = status_lookup(printer.id)
            ok, issues = await _check_one_printer(db, pipeline, printer, raw)
            reports.append(
                PerPrinterReport(
                    printer_id=printer.id,
                    printer_name=printer.name,
                    ok=ok,
                    issues=issues,
                )
            )

    any_ok = any(r.ok for r in reports)
    return EligibilityReport(
        ok=any_ok,
        target_kind="printer_class",
        target_printer_id=None,
        target_printer_name=None,
        target_model_class=pipeline.target_model_class,
        issues=(),
        printer_reports=tuple(reports),
    )

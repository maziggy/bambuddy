"""The nozzle flow a slice is cut for: Standard or High Flow (#3136).

Sliced with profiles, the slicer CLI sets every extruder to Standard unless
``--nozzle-volume-type`` says otherwise. A printer with a High Flow nozzle
fitted then gets a file for a Standard nozzle, slices it with the Standard
speeds, and warns at the start of the print that "the current nozzle setting
does not match the slicing file" (HMS 0500-803C). Measured on the sidecar:
the flag sets ``nozzle_volume_type`` in the G-code and in ``slice_info.config``,
which is what the printer compares, and picks the High Flow process variant.

The flow is read from the printers: H2-series and P2S report the fitted
nozzle's type as a flow code, ``HH01`` (High Flow) or ``HS01`` (Standard).
X1 / P1 report the nozzle material instead, which says nothing about flow, so
they never decide it. The first printer of the model, by name, whose nozzles
all report a flow and match the profile's nozzle size decides, the same
printer the Slice dialog takes its default from.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.printer import Printer
from backend.app.services.filament_map import DEFAULT_PHYSICAL_EXTRUDER_MAP
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

STANDARD = "Standard"
HIGH_FLOW = "High Flow"


@dataclass(frozen=True, slots=True)
class FittedNozzle:
    """One nozzle as the printer reports it. ``extruder_id`` is the MQTT id."""

    extruder_id: int
    diameter: str | None
    flow: str | None  # STANDARD, HIGH_FLOW, or None when the printer does not say


def flow_of(nozzle_type: object) -> str | None:
    """The flow a reported nozzle type names, or None for a material or nothing."""
    code = str(nozzle_type or "").strip().upper()[:2]
    if code == "HH":
        return HIGH_FLOW
    if code == "HS":
        return STANDARD
    return None


def fitted_nozzles(status: object, nozzle_count: int) -> list[FittedNozzle]:
    """The nozzles a printer has fitted, from its live status.

    ``status.nozzles`` is indexed by MQTT extruder id and always has two
    entries, so a single-nozzle printer's second one is ignored.
    """
    nozzles = getattr(status, "nozzles", None) or []
    out: list[FittedNozzle] = []
    for extruder_id, nozzle in enumerate(nozzles[: max(1, nozzle_count)]):
        diameter = str(getattr(nozzle, "nozzle_diameter", "") or "").strip() or None
        out.append(FittedNozzle(extruder_id, diameter, flow_of(getattr(nozzle, "nozzle_type", None))))
    return out


def _same_size(a: object, b: object) -> bool:
    try:
        return abs(float(str(a)) - float(str(b))) < 1e-6
    except (TypeError, ValueError):
        return False


def plan_nozzle_volume_type(printer_json: str, nozzles: Sequence[FittedNozzle]) -> list[str] | None:
    """One flow per slicer extruder for this profile, read from ``nozzles``.

    ``None`` when the profile cannot be read, or when any extruder's nozzle
    does not report a flow or is not the size the profile is for: a 0.4
    High Flow nozzle says nothing about a job sliced for 0.6.
    """
    try:
        profile = json.loads(printer_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(profile, dict):
        return None
    sizes = profile.get("nozzle_diameter")
    if not isinstance(sizes, list) or not sizes:
        return None
    if len(sizes) == 1:
        physical: list[object] = ["0"]
    else:
        physical = profile.get("physical_extruder_map") or list(DEFAULT_PHYSICAL_EXTRUDER_MAP)
        if not isinstance(physical, list) or len(physical) != len(sizes):
            return None

    by_id = {n.extruder_id: n for n in nozzles}
    flows: list[str] = []
    for size, mqtt_id in zip(sizes, physical, strict=True):
        try:
            nozzle = by_id.get(int(str(mqtt_id)))
        except ValueError:
            return None
        if nozzle is None or nozzle.flow is None or not _same_size(nozzle.diameter, size):
            return None
        flows.append(nozzle.flow)
    return flows


async def auto_nozzle_volume_type(
    db: AsyncSession,
    *,
    target_model: str | None,
    printer_json: str,
    allows: Callable[[int], bool],
) -> list[str] | None:
    """The flows of the first connected printer of the model that states them.

    ``allows`` is the caller's printer scope, narrowed to the target printer
    when the caller names one (#1727). Never raises: anything that stops this
    leaves the slice as it was, Standard.
    """
    from backend.app.utils.printer_models import (
        is_nozzle_rack_model,
        normalize_printer_model,
        normalize_printer_model_id,
        supports_nozzle_flow_type,
    )

    if target_model is None or not supports_nozzle_flow_type(target_model):
        return None
    # A rack machine (H2C) changes hotends during the print, so the nozzle on
    # an extruder now is not the one each part prints with.
    if is_nozzle_rack_model(target_model):
        return None

    try:
        model = normalize_printer_model(target_model) or normalize_printer_model_id(target_model) or target_model
        query = (
            select(Printer)
            .where(func.lower(Printer.model) == model.lower())
            .where(Printer.is_active == True)  # noqa: E712
            .order_by(Printer.name)
        )
        for printer in (await db.execute(query)).scalars().all():
            if not allows(printer.id):
                continue
            status = printer_manager.get_status(printer.id)
            if status is None or not status.connected:
                continue
            flows = plan_nozzle_volume_type(printer_json, fitted_nozzles(status, printer.nozzle_count))
            if flows:
                logger.info("Nozzle flow %s from printer %s", ",".join(flows), printer.id)
                return flows
    except Exception as exc:
        logger.warning("Nozzle flow not read from the printers, slicing as Standard: %s", exc)
    return None

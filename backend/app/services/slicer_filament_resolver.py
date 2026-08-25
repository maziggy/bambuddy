"""Shared spool ``slicer_filament`` → ``(tray_info_idx, setting_id)`` resolver.

The internal-inventory and Spoolman-inventory routes both need to translate
a spool's stored slicer-preset reference (cloud preset ID / local preset ID /
GF-prefix Bambu filament ID / free-text material name) into the two MQTT
fields ``ams_filament_setting`` consumes: the printer-side ``tray_info_idx``
(filament_id) and the slicer-side ``setting_id``. The two routes were drifting
in lockstep before #1713 — internal mode resolved everything, Spoolman mode
silently dropped slicer_filament on the floor and only the generic-material
fallback fired. This module is the single chokepoint so the two flows can't
diverge again.

Resolver outcomes:

- Returns ``("", "", None)`` when ``slicer_filament`` is empty, unresolvable,
  or sanitised away as a slicer-rejected value (literal material name,
  PFUS / PFCN cloud setting_id). The caller is responsible for the
  generic-material fallback when this happens.
- Returns ``(tray_info_idx, setting_id, sub_brand_override)`` otherwise.
  The third element is non-empty when a cloud-detail lookup or a local-
  preset name provides a more specific brand label than the spool's own
  ``"<brand> <material> <subtype>"`` concatenation — the caller should
  prefer it over its computed default.

The resolver is async because the GFS / PFUS / PFCN branches need cloud
authentication and the local-preset branch reads ``LocalPreset`` from the
DB. Pass ``current_user=None`` to skip cloud auth (the on_ams_change
replay path uses this); cloud-prefix presets then fall back to a static
``normalize_slicer_filament`` parse, which is correct when the slot was
already configured by an earlier authenticated assign and the printer's
calibration table preserves the real filament_id.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.user import User
from backend.app.utils.filament_ids import (
    GENERIC_FILAMENT_IDS,
    filament_id_to_setting_id,
    normalize_slicer_filament,
)
from backend.app.utils.filament_types import is_material_name

logger = logging.getLogger(__name__)


def _preset_filament_type(raw: object) -> str | None:
    """Read a slicer preset's ``filament_type`` field.

    Bambu Studio and OrcaSlicer both store it as a one-element array
    (``["PLA"]``); some hand-written and older profiles store a bare string.
    ``orca_profiles._extract_filament_fields`` accepts both and this has to
    agree with it, since that is what fills ``LocalPreset.filament_type``.

    The value is still run through ``printer_filament_type`` by the caller.
    That is a no-op for every type the app knows -- ``TestTheMaterialsBambuddyOffers``
    pins exactly that -- and a pass-through for a type it does not, so nothing
    the slicer says is discarded. It only bites on a hand-edited profile whose
    ``filament_type`` is a product line, which is the case this whole module
    exists to keep out of an AMS slot.
    """
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


async def resolve_slicer_filament(
    *,
    db: AsyncSession,
    current_user: User | None,
    slicer_filament: str | None,
    slicer_filament_name: str | None,
    material: str | None,
) -> tuple[str, str, str | None, str | None]:
    """Resolve a spool's slicer-preset reference to printer-side ids.

    ``slicer_filament``: the spool's stored reference (e.g. ``"GFA01"``,
    ``"PFUS990b6e19965353"``, ``"38"`` for a numeric LocalPreset id, or
    free-text). May be empty or None — returns the empty tuple in that case.

    ``slicer_filament_name``: optional builtin-name realignment hint. When
    set and the resolved tray_info_idx maps to a different builtin name,
    the resolver swaps to the builtin whose name matches (e.g. user picked
    "Bambu PLA Matte" but the cloud lookup landed on "Bambu PLA Basic").

    ``material``: spool material string for the local-preset fallback
    branch when the LocalPreset's setting JSON doesn't carry a filament_id.

    Returns ``(tray_info_idx, setting_id, sub_brand_override, type_override)``
    — all empty when nothing resolved. ``sub_brand_override`` is non-None when
    a more specific brand label is available (cloud detail name or local preset
    name); ``None`` means the caller should use its own default.

    ``type_override`` is the preset's own ``filament_type`` when the preset
    carries one — the slicer's answer to what the material is, rather than one
    parsed out of the spool's material column. It is what the caller should
    write into ``tray_type``. ``None`` means no preset said, and the caller
    falls back to reducing the spool's material (``printer_filament_type``).
    Raised in the #2902 thread by @doncaruana: a preset has to be chosen from
    a list the slicer defines, so its type needs no interpreting. It cannot be
    the only source, though — ``slicer_filament`` is nullable on a spool while
    ``material`` is required, and the spool this issue was reported for had no
    preset at all.
    """
    sf = (slicer_filament or "").strip()
    if not sf:
        return ("", "", None, None)

    tray_info_idx = ""
    setting_id = ""
    sub_brand_override: str | None = None
    type_override: str | None = None

    base_sf = sf.split("_")[0] if "_" in sf else sf

    # Cloud-side preset IDs in three known shapes:
    #   GFS…   — Bambu official cloud preset
    #   PFUS…  — cloud user-created preset
    #   PFCN…  — cloud shared / partner preset (e.g. Polymaker's "(Custom)"
    #            Bambu Lab H2D variant, #1648)
    # All three need a cloud-detail lookup to extract the underlying
    # filament_id; without it the raw cloud id ends up in tray_info_idx
    # and the printer's calibration table can't resolve it.
    if base_sf.startswith("GFS") or base_sf.startswith("PFUS") or base_sf.startswith("PFCN"):
        setting_id = base_sf
        try:
            from backend.app.api.routes.cloud import build_authenticated_cloud

            cloud = await build_authenticated_cloud(db, current_user)
            if cloud is not None and cloud.is_authenticated:
                try:
                    detail = await cloud.get_setting_detail(base_sf)
                    # The preset's own type, straight from the slicer's own
                    # profile -- no parsing of a product name (#2902). The
                    # preset JSON is nested under ``setting``; some responses
                    # carry it at the top level instead, the same shape spread
                    # ``preset_resolver`` documents.
                    cloud_setting = detail.get("setting")
                    type_override = _preset_filament_type(
                        (cloud_setting if isinstance(cloud_setting, dict) else detail).get("filament_type")
                    )
                    if detail.get("filament_id"):
                        tray_info_idx = detail["filament_id"]
                        cloud_name = detail.get("name", "")
                        if cloud_name:
                            sub_brand_override = cloud_name.replace(r"@.*$", "").split("@")[0].strip()
                    elif detail.get("base_id"):
                        bid = detail["base_id"].split("_")[0]
                        if bid.startswith("GFS") and len(bid) >= 5:
                            tray_info_idx = f"GF{bid[3:]}"
                        else:
                            tray_info_idx = bid
                finally:
                    await cloud.close()
            elif cloud is not None:
                await cloud.close()
        except Exception as e:
            logger.warning("Slicer-filament resolve: cloud lookup failed for %r: %s", sf, e)

        if not tray_info_idx:
            tray_info_idx, setting_id = normalize_slicer_filament(sf)
    elif base_sf.startswith("GF"):
        tray_info_idx, setting_id = normalize_slicer_filament(sf)
    else:
        try:
            local_id = int(sf)
            from backend.app.models.local_preset import LocalPreset as LP

            lp_result = await db.execute(select(LP).where(LP.id == local_id, LP.preset_type == "filament"))
            lp = lp_result.scalar_one_or_none()
            if lp:
                # The slicer's own answer, extracted from the profile at import
                # time by ``orca_profiles``. Preferred over anything parsed out
                # of the spool's material column (#2902).
                type_override = _preset_filament_type(lp.filament_type)
                # Local preset's setting JSON carries the printer-recognized
                # filament_id (e.g. "P4d64437") — use that directly so the
                # slicer can resolve the specific preset. Falls through to
                # generic material id only when the JSON doesn't carry one.
                lp_filament_id = ""
                if lp.setting:
                    try:
                        setting_data = json.loads(lp.setting)
                        raw_fid = setting_data.get("filament_id")
                        if isinstance(raw_fid, str) and raw_fid:
                            lp_filament_id = raw_fid
                    except (json.JSONDecodeError, AttributeError):
                        pass
                if lp_filament_id:
                    tray_info_idx = lp_filament_id
                    setting_id = filament_id_to_setting_id(lp_filament_id)
                else:
                    # Deliberately not widened to cover product-line materials
                    # ("PLA+", "HTPLA") the way the callers' own fallbacks were
                    # (#2902). Returning an id here rather than "" would skip
                    # the caller's whole no-id block, and with it the slot-reuse
                    # branch that keeps a printer's calibrated preset -- so the
                    # widening belongs there, after reuse has had its turn.
                    mat = (material or lp.filament_type or "").upper().strip()
                    tray_info_idx = (
                        GENERIC_FILAMENT_IDS.get(mat) or GENERIC_FILAMENT_IDS.get(mat.split("-")[0].split(" ")[0]) or ""
                    )
                if lp.name:
                    sub_brand_override = lp.name.split("@")[0].strip()
        except (ValueError, TypeError):
            tray_info_idx, setting_id = normalize_slicer_filament(sf)

    # Realign tray_info_idx to a builtin whose name matches slicer_filament_name
    # when the current resolution lands on a builtin with a different name
    # (e.g. cloud detail returned PLA Basic but the spool was labelled PLA Matte).
    if tray_info_idx and slicer_filament_name:
        from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES

        expected_name = _BUILTIN_FILAMENT_NAMES.get(tray_info_idx, "")
        if expected_name and expected_name != slicer_filament_name:
            for fid, fname in _BUILTIN_FILAMENT_NAMES.items():
                if fname == slicer_filament_name:
                    tray_info_idx = fid
                    setting_id = filament_id_to_setting_id(fid)
                    break

    # Defend against tray_info_idx values the slicer cannot resolve. Three
    # shapes leak through and must be discarded so the caller's generic-
    # material fallback can rescue the slot:
    #   1. Literal material names ("PLA", "PETG-CF") that pass through
    #      normalize_slicer_filament unchanged when the spool's slicer_filament
    #      is free-text rather than a real preset ID. Product lines ("PLA+",
    #      "HTPLA") count as material names too -- see is_material_name, which
    #      is shared with the slot-reuse check that must agree with this.
    #   2. PFUS-prefix cloud setting_ids — valid as setting_id but rejected
    #      by the slicer as tray_info_idx (the printer's calibration table
    #      indexes by filament_id, and a PFUS isn't one). This normally gets
    #      realigned to a P-prefix local id via the caller's printer_kp
    #      lookup, but on the replay path in main.py.on_ams_change
    #      current_user=None skips cloud auth and leaves the raw PFUS in
    #      tray_info_idx — overwriting the correctly-configured slot from
    #      the original assign.
    #   3. PFCN-prefix cloud shared / partner presets (e.g. Polymaker's
    #      "(Custom)" H2D variants, #1648) — same shape problem as PFUS.
    # Valid tray_info_idx values: "GF" + letter + digits (Bambu official) or
    # "P" followed by hex (user/local presets, NOT "PFUS" or "PFCN").
    if tray_info_idx and (
        is_material_name(tray_info_idx) or tray_info_idx.startswith("PFUS") or tray_info_idx.startswith("PFCN")
    ):
        tray_info_idx = ""
        # Preserve setting_id when it's still a valid slicer reference
        # (PFUS / PFCN cloud user/shared preset, or GFS Bambu official
        # preset). The slicer accepts these as setting_id even though
        # they're rejected as tray_info_idx; without preservation the
        # slicer falls back to whatever generic filament the caller's
        # tray_info_idx fallback produces and shows "Generic <Material>"
        # instead of the user's actual custom preset (#1815). Material-name
        # leaks (e.g. setting_id="PETG") are still cleared — those are
        # never valid slicer references.
        if not (
            setting_id
            and (setting_id.startswith("PFUS") or setting_id.startswith("PFCN") or setting_id.startswith("GFS"))
        ):
            setting_id = ""

    return (tray_info_idx, setting_id, sub_brand_override, type_override)

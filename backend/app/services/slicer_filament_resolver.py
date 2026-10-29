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
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.utils.filament_ids import (
    GENERIC_FILAMENT_IDS,
    filament_id_to_setting_id,
    normalize_slicer_filament,
    orca_to_bambu_filament_id,
    setting_id_to_filament_id,
)
from backend.app.utils.filament_types import is_material_name
from backend.app.utils.slicer_strings import first_slicer_string

logger = logging.getLogger(__name__)

# Orca Cloud profile ids are UUIDs, the one preset reference in this codebase
# with no letter prefix to key off. A spool stores the bare id (the spool form
# persists ``preset.setting_id`` verbatim), so shape is all there is to go on.
_ORCA_PROFILE_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@dataclass(frozen=True)
class OrcaFilamentLookup:
    """Outcome of resolving an Orca Cloud profile to a printer filament id.

    ``filament_id`` is empty when nothing usable was found; ``reason`` then says
    why, so a caller in front of a user can say so instead of quietly sending
    a generic id (#3216). ``source`` is ``"own"`` for the profile's own id,
    ``"inherited"`` for one taken from a parent.
    """

    filament_id: str = ""
    name: str | None = None
    filament_type: str | None = None
    source: str = ""
    reason: str = ""


# How many ``inherits`` hops to follow before giving up. Orca presets are one
# or two deep in practice; the cap only guards against a cycle in hand-edited
# profiles.
_MAX_INHERITS_DEPTH = 5


def _content_filament_id(content: dict) -> str:
    """A profile's filament_id as the printer knows it.

    One copied from an OrcaSlicer system preset is Orca's own id ("OFMUWNkp"),
    which the printer cannot resolve; it is translated with Orca's own table,
    and one the table does not know reads as none, so the lookup goes on to
    the parents (#3387)."""
    raw = content.get("filament_id")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return orca_to_bambu_filament_id(raw.strip()) if isinstance(raw, str) else ""


def _catalog_filament_id(preset_name: str) -> str:
    """Bambu catalog id for a Bambu system preset name, or ``""``.

    An Orca profile that inherits a system preset ("Bambu PLA Basic @BBL H2D")
    carries no id of its own. Its parent's id is the one the printer and the
    slicer both know -- OrcaSlicer since 2.5 translates the Bambu catalog ids
    at the printer boundary -- so it beats a generic for the material.
    """
    from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES

    base = preset_name.split("@")[0].strip().lstrip("#").strip().lower()
    if not base:
        return ""
    for fid, fname in _BUILTIN_FILAMENT_NAMES.items():
        if fname.lower() == base:
            return fid
    return ""


async def lookup_orca_filament_id(
    db: AsyncSession,
    current_user: User | None,
    profile_id: str,
) -> OrcaFilamentLookup:
    """Look up the filament id an Orca Cloud profile puts in an AMS slot.

    OrcaSlicer resolves a slot to a preset by ``filament_id`` alone, so this is
    the value that decides whether "Sync filaments" finds the user's profile or
    falls back to Generic (#3216). Tried in order: the profile's own id; the
    nearest parent among the user's Orca profiles that has one; the Bambu
    catalog id of a Bambu system parent.

    Best-effort by construction: this runs inside spool assignment and slot
    configuration, so a missing pairing, a revoked token or a lapsed permission
    must degrade to the caller's fallback rather than fail the request. That is
    also why ``clear_on_auth_failure=False`` -- Orca reports every refresh
    rejection with one composite reason, so this caller cannot tell a real
    revocation from a lost rotation race and must not wipe a working pairing on
    it. The Orca Cloud routes hit the same failure in front of a user and clear
    there.
    """
    if current_user is not None and not current_user.has_permission(Permission.ORCA_CLOUD_AUTH.value):
        logger.info("Orca filament lookup skipped for %r: caller lacks orca_cloud:auth", profile_id)
        return OrcaFilamentLookup(reason="no_permission")

    svc = None
    try:
        from backend.app.api.routes.orca_cloud import _build_authenticated_service

        svc = await _build_authenticated_service(db, current_user, clear_on_auth_failure=False)
        profile = await svc.get_profile(profile_id)
        content = profile.get("content") if isinstance(profile, dict) else None
        if not isinstance(content, dict):
            logger.info("Orca filament lookup for %r: profile has no content", profile_id)
            return OrcaFilamentLookup(reason="no_filament_id")
        raw_name = profile.get("name")
        name = raw_name if isinstance(raw_name, str) and raw_name else None
        filament_type = _preset_filament_type(content.get("filament_type"))

        own = _content_filament_id(content)
        if own:
            logger.info("Orca filament lookup for %r: own filament_id %r", profile_id, own)
            return OrcaFilamentLookup(own, name, filament_type, source="own")

        parent = content.get("inherits")
        parent = parent.strip() if isinstance(parent, str) else ""
        by_name: dict[str, dict] | None = None
        for _ in range(_MAX_INHERITS_DEPTH):
            if not parent:
                break
            if by_name is None:
                # One more pull, only for a profile that needs its parent. A
                # failure here keeps the name and type already read, as the
                # lookup returned them before it followed parents.
                by_name = {}
                try:
                    entries = await svc.list_profiles()
                except Exception as e:
                    logger.info("Orca filament lookup for %r: could not read its parents: %s", profile_id, e)
                    return OrcaFilamentLookup(name=name, filament_type=filament_type, reason="lookup_failed")
                for entry in entries:
                    entry_content = entry.get("content") if isinstance(entry, dict) else None
                    if not isinstance(entry_content, dict):
                        continue
                    for key in (entry.get("name"), entry_content.get("name")):
                        if isinstance(key, str) and key:
                            by_name.setdefault(key, entry_content)
            parent_content = by_name.get(parent)
            if parent_content is None:
                catalog = _catalog_filament_id(parent)
                if catalog:
                    logger.info(
                        "Orca filament lookup for %r: catalog filament_id %r of system parent %r",
                        profile_id,
                        catalog,
                        parent,
                    )
                    return OrcaFilamentLookup(catalog, name, filament_type, source="inherited")
                break
            inherited = _content_filament_id(parent_content)
            if inherited:
                logger.info(
                    "Orca filament lookup for %r: filament_id %r inherited from %r", profile_id, inherited, parent
                )
                return OrcaFilamentLookup(inherited, name, filament_type, source="inherited")
            next_parent = parent_content.get("inherits")
            parent = next_parent.strip() if isinstance(next_parent, str) else ""

        logger.info("Orca filament lookup for %r: no filament_id on the profile or its parents", profile_id)
        return OrcaFilamentLookup(name=name, filament_type=filament_type, reason="no_filament_id")
    except Exception as e:
        # INFO, not DEBUG: this is the line that explains a slot coming out as
        # Generic in a support bundle (#3216).
        logger.info("Orca filament lookup failed for %r: %s", profile_id, e)
        return OrcaFilamentLookup(reason="lookup_failed")
    finally:
        # A raise in `finally` escapes the `except` above, so guard it: closing
        # an httpx client must never be what fails a spool assignment.
        if svc is not None:
            try:
                await svc.close()
            except Exception as e:  # noqa: BLE001 - close() is best-effort
                logger.debug("Orca client close failed after lookup of %r: %s", profile_id, e)


async def _orca_filament_id(
    db: AsyncSession,
    current_user: User | None,
    profile_id: str,
) -> tuple[str, str | None, str | None]:
    """``(filament_id, name, filament_type)`` for the spool resolver below --
    all empty/None when nothing was found, which leaves it on its generic
    fallback. See ``lookup_orca_filament_id``."""
    found = await lookup_orca_filament_id(db, current_user, profile_id)
    return (found.filament_id, found.name, found.filament_type)


def _preset_filament_type(raw: object) -> str | None:
    """Read a slicer preset's ``filament_type`` field.

    Bambu Studio and OrcaSlicer both store it as a one-element array
    (``["PLA"]``); some hand-written and older profiles store a bare string,
    and Orca Cloud can return it INI-quoted (``'"PLA"'``, #3380).
    ``orca_profiles._extract_filament_fields`` accepts both and this has to
    agree with it, since that is what fills ``LocalPreset.filament_type``.

    The value is still run through ``printer_filament_type`` by the caller.
    That is a no-op for every type the app knows -- ``TestTheMaterialsBambuddyOffers``
    pins exactly that -- and a pass-through for a type it does not, so nothing
    the slicer says is discarded. It only bites on a hand-edited profile whose
    ``filament_type`` is a product line, which is the case this whole module
    exists to keep out of an AMS slot.
    """
    return first_slicer_string(raw)


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
    # Source order is Orca Cloud, Bambu Cloud, local import, generic fallback.
    # Orca goes first because its ids are the only ones identified by shape
    # rather than prefix -- and because, before #3003, a UUID fell through every
    # branch below into ``normalize_slicer_filament``, which passes anything it
    # does not recognise straight through. A 36-character UUID then went into
    # tray_info_idx, an 8-character field, and the slot ended up pointing at the
    # first 8 characters of a UUID: the same failure the PFUS guard at the
    # bottom of this function exists for.
    if _ORCA_PROFILE_ID.fullmatch(base_sf):
        tray_info_idx, orca_name, orca_type = await _orca_filament_id(db, current_user, base_sf)
        if orca_type:
            type_override = orca_type
        if orca_name:
            sub_brand_override = orca_name.split("@")[0].strip()
        # setting_id is left empty here: the UUID is what the slicer cannot
        # resolve, and unlike a PFUS there is no cloud id form it accepts
        # instead. All three callers then derive one from the filament_id
        # (`filament_id_to_setting_id`), which is what keeps the slot from
        # going out half configured -- the same path a local import takes.
    elif base_sf.startswith("GFS") or base_sf.startswith("PFUS") or base_sf.startswith("PFCN"):
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
                    # A custom preset's OWN filament_id is the only thing that
                    # gets it into an AMS slot as itself: the printer stores
                    # that id, the slicer matches its presets against it, and
                    # the 8-character field fits it exactly ("P" + 7 hex).
                    #
                    # Bambu Cloud normally returns it on the envelope, which is
                    # what the captures in #1053 show for a Studio-created
                    # preset (filament_id: "Pbd31b30"). The `setting` fallback
                    # here is belt-and-braces for a response that carries it in
                    # the preset JSON instead, the same spread `filament_type`
                    # above has to handle -- no captured response has needed it
                    # yet, and it costs a dict lookup to be ready for one.
                    #
                    # An Orca-created preset has no filament_id anywhere: the
                    # envelope says null and `setting` is a delta from the base
                    # (#1053 again). Those legitimately fall to base_id below
                    # and reach the slicer as the profile they inherit from --
                    # an OrcaSlicer preset-format gap, filed upstream as
                    # OrcaSlicer PR #13315, not something resolvable here.
                    own_filament_id = detail.get("filament_id") or (
                        cloud_setting.get("filament_id") if isinstance(cloud_setting, dict) else None
                    )
                    # An official preset's id comes from its full setting_id
                    # (#3387). The detail read above is the suffix-free base's,
                    # and for some bases that is another filament: "GFSA05" is
                    # Bambu PLA Matte @BBL X1, while "GFSA05_07" -- and the
                    # bare id Bambuddy builds from GFA05 -- is Bambu PLA Silk.
                    # Its type and name only count when it is the same filament.
                    # A spool named after that very preset is the exception: it
                    # was picked as Bambu's legacy bare preset ("Bambu TPU 95A
                    # @BBL X1C", "GFSU00" -> GFU01), not built by Bambuddy, and
                    # Bambu's own id is then the right one.
                    official = setting_id_to_filament_id(sf) if base_sf.startswith("GFS") else ""
                    detail_name = detail.get("name")
                    if (
                        official
                        and own_filament_id
                        and isinstance(detail_name, str)
                        and detail_name.strip()
                        and detail_name.strip() == (slicer_filament_name or "").strip()
                    ):
                        official = own_filament_id
                    if official:
                        tray_info_idx = official
                        if own_filament_id and own_filament_id != official:
                            type_override = None
                        else:
                            cloud_name = detail.get("name", "")
                            if cloud_name:
                                sub_brand_override = cloud_name.split("@")[0].strip()
                    elif own_filament_id:
                        tray_info_idx = own_filament_id
                        cloud_name = detail.get("name", "")
                        if cloud_name:
                            sub_brand_override = cloud_name.replace(r"@.*$", "").split("@")[0].strip()
                    elif detail.get("base_id"):
                        tray_info_idx = setting_id_to_filament_id(detail["base_id"])
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
    #   4. Orca Cloud profile UUIDs, when the branch above could not reach the
    #      profile to trade one for its filament_id (#3003). Worst of the four
    #      at 36 characters against an 8-character field.
    # Valid tray_info_idx values: "GF" + letter + digits (Bambu official) or
    # "P" followed by hex (user/local presets, NOT "PFUS" or "PFCN").
    if tray_info_idx and (
        is_material_name(tray_info_idx)
        or tray_info_idx.startswith("PFUS")
        or tray_info_idx.startswith("PFCN")
        or _ORCA_PROFILE_ID.fullmatch(tray_info_idx)
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

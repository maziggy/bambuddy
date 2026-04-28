"""Pydantic schemas for the unified slicer-presets endpoint.

The SliceModal pulls printer/process/filament options from three sources, in
priority order: cloud (the user's Bambu Cloud account), local (DB-backed
imported profiles), and standard (slicer-bundled stock profiles). The endpoint
returns all three lists with name-based dedup applied so each preset appears
exactly once across the response.
"""

from typing import Literal

from pydantic import BaseModel

CloudStatus = Literal["ok", "not_authenticated", "expired", "unreachable"]


class UnifiedPreset(BaseModel):
    """A single printer/process/filament preset with its source.

    The ``id`` shape varies by source:
      - cloud  → Bambu Cloud setting_id (e.g. ``"PFUS9ac902733670a9"``)
      - local  → stringified DB row id from ``local_presets``
      - standard → preset name as written in the bundled JSON (the slicer
                   resolves bundled profiles by name during inheritance walk)

    The frontend treats ``id`` as opaque; the slice dispatch path uses
    ``(source, id)`` to fetch / pass the preset content to the sidecar.
    """

    id: str
    name: str
    source: Literal["cloud", "local", "standard"]


class UnifiedPresetsBySlot(BaseModel):
    """Three slots in the order Bambu Studio / OrcaSlicer use."""

    printer: list[UnifiedPreset] = []
    process: list[UnifiedPreset] = []
    filament: list[UnifiedPreset] = []


class UnifiedPresetsResponse(BaseModel):
    """Each tier carries only the names that didn't appear in a higher tier.

    Cloud is the highest priority (user's personal customisations win), then
    the local imports the user explicitly curated, then the slicer's stock
    fallback. A name that appears in cloud is filtered out of local and
    standard; a name that appears in local is filtered out of standard.

    ``cloud_status`` lets the frontend show a banner explaining why the cloud
    tier is empty when the user expected to see it (signed out / token
    expired / network down).
    """

    cloud: UnifiedPresetsBySlot = UnifiedPresetsBySlot()
    local: UnifiedPresetsBySlot = UnifiedPresetsBySlot()
    standard: UnifiedPresetsBySlot = UnifiedPresetsBySlot()
    cloud_status: CloudStatus = "ok"

"""Tests for ``resolve_slicer_filament`` (#1815).

The defensive filter at the end of the resolver clears ``tray_info_idx``
when its value isn't slicer-acceptable (literal material names + PFUS /
PFCN cloud-preset prefixes that the printer's calibration table can't
key on). Pre-#1815 it cleared ``setting_id`` alongside, which dropped
the slicer's only handle on the user's actual custom preset and forced
the caller into the generic-material fallback — Bambu Studio then
displayed "Generic <Material>" for spools whose Bambu Cloud detail
lookup didn't resolve a ``filament_id`` (cloud unauth on the on_ams_change
replay path, transient cloud failure, or custom presets whose detail
JSON omits ``filament_id``).

Post-#1815 the filter preserves a setting_id that's still a valid
slicer reference (PFUS / PFCN cloud user/shared preset, or GFS Bambu
official preset) even when ``tray_info_idx`` is cleared.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.slicer_filament_resolver import resolve_slicer_filament


@pytest.mark.asyncio
async def test_pfus_cloud_unavailable_preserves_setting_id():
    """Reporter scenario: PFUS cloud user preset, cloud lookup fails to
    return a filament_id. setting_id must survive so the slicer can
    still find the user's actual custom preset."""
    db = MagicMock()
    with patch(
        "backend.app.api.routes.cloud.build_authenticated_cloud",
        AsyncMock(return_value=None),
    ):
        tray_info_idx, setting_id, sub_brand = await resolve_slicer_filament(
            db=db,
            current_user=None,
            slicer_filament="PFUS990b6e19965353",
            slicer_filament_name="Jayo PETG HF",
            material="PETG",
        )
    assert tray_info_idx == ""
    assert setting_id == "PFUS990b6e19965353"
    assert sub_brand is None


@pytest.mark.asyncio
async def test_pfcn_cloud_unavailable_preserves_setting_id():
    """PFCN partner/shared cloud preset (e.g. Polymaker H2D variants,
    #1648) shares the same shape problem as PFUS."""
    db = MagicMock()
    with patch(
        "backend.app.api.routes.cloud.build_authenticated_cloud",
        AsyncMock(return_value=None),
    ):
        tray_info_idx, setting_id, sub_brand = await resolve_slicer_filament(
            db=db,
            current_user=None,
            slicer_filament="PFCN1234567890",
            slicer_filament_name="Polymaker PolyTerra PLA",
            material="PLA",
        )
    assert tray_info_idx == ""
    assert setting_id == "PFCN1234567890"
    assert sub_brand is None


@pytest.mark.asyncio
async def test_pfus_cloud_resolves_filament_id_regression_guard():
    """When cloud auth works and returns a filament_id, the resolver
    keeps its existing behaviour: tray_info_idx = real filament_id,
    setting_id = original PFUS reference."""
    db = MagicMock()
    cloud_mock = MagicMock()
    cloud_mock.is_authenticated = True
    cloud_mock.get_setting_detail = AsyncMock(return_value={"filament_id": "P285e239", "name": "Jayo PETG HF @P1S"})
    cloud_mock.close = AsyncMock()
    with patch(
        "backend.app.api.routes.cloud.build_authenticated_cloud",
        AsyncMock(return_value=cloud_mock),
    ):
        tray_info_idx, setting_id, sub_brand = await resolve_slicer_filament(
            db=db,
            current_user=MagicMock(),
            slicer_filament="PFUS990b6e19965353",
            slicer_filament_name="Jayo PETG HF",
            material="PETG",
        )
    assert tray_info_idx == "P285e239"
    assert setting_id == "PFUS990b6e19965353"
    assert sub_brand == "Jayo PETG HF"


@pytest.mark.asyncio
async def test_gfs_cloud_unavailable_resolves_via_normalize():
    """GFS Bambu official preset + cloud unavailable: normalize strips
    the 'S' to give a real filament_id ('GFG02'), so tray_info_idx is
    valid and the defensive filter doesn't trigger. setting_id stays as
    the original GFS reference. Regression guard for the cloud-down
    Bambu-official path."""
    db = MagicMock()
    with patch(
        "backend.app.api.routes.cloud.build_authenticated_cloud",
        AsyncMock(return_value=None),
    ):
        tray_info_idx, setting_id, sub_brand = await resolve_slicer_filament(
            db=db,
            current_user=None,
            slicer_filament="GFSG02",
            slicer_filament_name=None,
            material="PETG",
        )
    assert tray_info_idx == "GFG02"
    assert setting_id == "GFSG02"
    assert sub_brand is None


@pytest.mark.asyncio
async def test_literal_material_name_clears_both():
    """slicer_filament='PETG' (free-text material leak from legacy
    spools): both tray_info_idx and setting_id must be cleared so the
    caller's generic-material fallback rescues the slot. Regression
    guard that the PFUS preservation doesn't accidentally preserve
    literal material names."""
    db = MagicMock()
    with patch(
        "backend.app.api.routes.cloud.build_authenticated_cloud",
        AsyncMock(return_value=None),
    ):
        tray_info_idx, setting_id, sub_brand = await resolve_slicer_filament(
            db=db,
            current_user=None,
            slicer_filament="PETG",
            slicer_filament_name=None,
            material="PETG",
        )
    assert tray_info_idx == ""
    assert setting_id == ""
    assert sub_brand is None

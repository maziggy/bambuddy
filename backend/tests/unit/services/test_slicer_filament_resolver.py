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
        tray_info_idx, setting_id, sub_brand, _type_override = await resolve_slicer_filament(
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
        tray_info_idx, setting_id, sub_brand, _type_override = await resolve_slicer_filament(
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
        tray_info_idx, setting_id, sub_brand, _type_override = await resolve_slicer_filament(
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
        tray_info_idx, setting_id, sub_brand, _type_override = await resolve_slicer_filament(
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
        tray_info_idx, setting_id, sub_brand, _type_override = await resolve_slicer_filament(
            db=db,
            current_user=None,
            slicer_filament="PETG",
            slicer_filament_name=None,
            material="PETG",
        )
    assert tray_info_idx == ""
    assert setting_id == ""
    assert sub_brand is None


class TestThePresetsOwnType:
    """#2902: a preset is chosen from a list the slicer defines, so its
    ``filament_type`` is the slicer's own answer to what the material is --
    no reading of a product name required. Raised by @doncaruana on the issue
    after the first fix reduced "PLA Aero" to "PLA".

    The resolver hands that answer back as the fourth element; the two assign
    routes write it into ``tray_type`` in preference to reducing the spool's
    material column. ``None`` means no preset said, and the reduction stands.
    """

    @pytest.mark.asyncio
    async def test_a_local_presets_type_is_returned(self):
        db = MagicMock()
        lp = MagicMock()
        lp.filament_type = "PLA-AERO"
        lp.setting = None
        lp.name = "Bambu PLA Aero @BBL X1C"
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=lp)
        db.execute = AsyncMock(return_value=result)

        _idx, _sid, _brand, type_override = await resolve_slicer_filament(
            db=db,
            current_user=None,
            slicer_filament="38",
            slicer_filament_name=None,
            material="PLA",
        )
        assert type_override == "PLA-AERO"

    @pytest.mark.asyncio
    async def test_a_cloud_presets_type_is_read_out_of_its_profile(self):
        """Both slicers store it as a one-element array, and the preset JSON
        sits under ``setting`` in the cloud envelope."""
        db = MagicMock()
        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.get_setting_detail = AsyncMock(
            return_value={
                "filament_id": "GFA11",
                "name": "Bambu PLA Aero @BBL X1C",
                "setting": {"filament_type": ["PLA-AERO"]},
            }
        )
        cloud.close = AsyncMock()
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            AsyncMock(return_value=cloud),
        ):
            idx, _sid, _brand, type_override = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament="GFSA11",
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == "GFA11"
        assert type_override == "PLA-AERO"

    @pytest.mark.asyncio
    async def test_a_bare_string_filament_type_is_accepted_too(self):
        """Hand-written and older profiles store it unwrapped. ``orca_profiles``
        accepts both forms, so this has to as well."""
        db = MagicMock()
        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.get_setting_detail = AsyncMock(
            return_value={"filament_id": "GFG02", "setting": {"filament_type": "PETG"}}
        )
        cloud.close = AsyncMock()
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            AsyncMock(return_value=cloud),
        ):
            _idx, _sid, _brand, type_override = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament="GFSG02",
                slicer_filament_name=None,
                material="PETG",
            )
        assert type_override == "PETG"

    @pytest.mark.asyncio
    async def test_no_preset_means_no_answer(self):
        """A spool with no slicer_filament -- the case this issue was reported
        for. ``material`` is required on a spool and ``slicer_filament`` is
        not, so the reduction has to stay as the fallback."""
        db = MagicMock()
        _idx, _sid, _brand, type_override = await resolve_slicer_filament(
            db=db,
            current_user=None,
            slicer_filament=None,
            slicer_filament_name=None,
            material="PLA+",
        )
        assert type_override is None

    @pytest.mark.asyncio
    async def test_a_preset_that_does_not_say_gets_no_opinion(self):
        db = MagicMock()
        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.get_setting_detail = AsyncMock(return_value={"filament_id": "GFG02", "setting": {}})
        cloud.close = AsyncMock()
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            AsyncMock(return_value=cloud),
        ):
            _idx, _sid, _brand, type_override = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament="GFSG02",
                slicer_filament_name=None,
                material="PETG",
            )
        assert type_override is None


class TestACustomPresetsOwnFilamentId:
    """Where the id that carries a custom preset into an AMS slot comes from.

    The slot holds one filament reference and the printer truncates it to 8
    characters, so a custom preset reaches the slicer as itself only when its
    own filament_id ("P" + 7 hex) goes into ``tray_info_idx``. 92 trays across
    eight models in the support archive do exactly that, so the mechanism
    works -- what #3003 found is that we only ever read one of the two places
    Bambu Cloud returns that id from.
    """

    @pytest.mark.asyncio
    async def test_filament_id_is_read_from_inside_the_preset_json(self):
        """The envelope has none, the preset JSON does -- and it wins over base_id.

        Before #3003 this fell through to the base_id branch and the slot came
        out as the Bambu profile the custom preset inherits from.
        """
        db = MagicMock()
        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.get_setting_detail = AsyncMock(
            return_value={
                "name": "SUNLU PLA Transparent @BBL A1",
                "base_id": "GFSNLS03",
                "setting": {"filament_id": "P4d64437", "filament_type": ["PLA"]},
            }
        )
        cloud.close = AsyncMock()
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            AsyncMock(return_value=cloud),
        ):
            idx, sid, brand, _type = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament="PFUSfb87cd50b76616",
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == "P4d64437"
        assert sid == "PFUSfb87cd50b76616"
        assert brand == "SUNLU PLA Transparent"

    @pytest.mark.asyncio
    async def test_the_envelope_still_wins_when_it_has_one(self):
        """Unchanged behaviour for the presets that already resolved."""
        db = MagicMock()
        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.get_setting_detail = AsyncMock(
            return_value={
                "filament_id": "P285e239",
                "name": "Jayo PETG HF @P1S",
                "base_id": "GFSG02",
                "setting": {"filament_id": "P999aaaa"},
            }
        )
        cloud.close = AsyncMock()
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            AsyncMock(return_value=cloud),
        ):
            idx, _sid, _brand, _type = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament="PFUS992454068158eb",
                slicer_filament_name=None,
                material="PETG",
            )
        assert idx == "P285e239"

    @pytest.mark.asyncio
    async def test_base_id_is_still_the_fallback_when_neither_place_has_one(self):
        """A preset with no filament_id of its own genuinely is its base, and
        the base id is storable, so it is the right answer -- just not one to
        reach for while the preset's own id is sitting under ``setting``."""
        db = MagicMock()
        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.get_setting_detail = AsyncMock(
            return_value={"name": "My PLA @BBL A1", "base_id": "GFSNLS03", "setting": {}}
        )
        cloud.close = AsyncMock()
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            AsyncMock(return_value=cloud),
        ):
            idx, sid, _brand, _type = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament="PFUSfb87cd50b76616",
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == "GFNLS03"
        assert sid == "PFUSfb87cd50b76616"


class TestOrcaCloudIsTheFirstSource:
    """Source order is Orca Cloud, Bambu Cloud, local import, generic.

    Orca was absent from the resolver entirely: a spool referencing an Orca
    profile stores the bare UUID, which matched no branch and fell through
    ``normalize_slicer_filament`` -- a function that passes anything it does
    not recognise straight through. The UUID reached tray_info_idx, a field
    the printer truncates to 8 characters (#3003).
    """

    ORCA_ID = "3f2a9c1e-4b7d-4a02-9f61-8c5e2d1a7b30"

    @staticmethod
    def _svc(profile):
        svc = MagicMock()
        svc.get_profile = AsyncMock(return_value=profile)
        svc.close = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_the_profiles_own_filament_id_is_used(self):
        db = MagicMock()
        svc = self._svc(
            {
                "id": self.ORCA_ID,
                "name": "Overture Matte PLA @Orca",
                "content": {"filament_id": "P56e1be0", "filament_type": ["PLA"]},
            }
        )
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            AsyncMock(return_value=svc),
        ):
            idx, sid, brand, type_override = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament=self.ORCA_ID,
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == "P56e1be0"
        # The UUID is foreign to the slicer in either field, so nothing carries it.
        assert sid == ""
        assert brand == "Overture Matte PLA"
        assert type_override == "PLA"
        svc.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_profile_with_no_filament_id_leaves_the_caller_its_fallback(self):
        db = MagicMock()
        svc = self._svc({"id": self.ORCA_ID, "name": "My PLA", "content": {"filament_type": ["PLA"]}})
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            AsyncMock(return_value=svc),
        ):
            idx, sid, _brand, _type = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament=self.ORCA_ID,
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == ""
        assert sid == ""

    @pytest.mark.asyncio
    async def test_an_unreachable_orca_never_leaks_the_uuid(self):
        """No pairing, dead token, Orca down -- all the same answer. The UUID
        must not reach tray_info_idx, which is what happened before the branch
        existed at all."""
        db = MagicMock()
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            AsyncMock(side_effect=RuntimeError("Orca Cloud is not connected")),
        ):
            idx, sid, _brand, _type = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament=self.ORCA_ID,
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == ""
        assert sid == ""

    @pytest.mark.asyncio
    async def test_a_caller_without_the_permission_skips_the_lookup(self):
        db = MagicMock()
        user = MagicMock()
        user.has_permission = MagicMock(return_value=False)
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            AsyncMock(side_effect=AssertionError("must not be called")),
        ):
            idx, _sid, _brand, _type = await resolve_slicer_filament(
                db=db,
                current_user=user,
                slicer_filament=self.ORCA_ID,
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == ""

    @pytest.mark.asyncio
    async def test_a_uuid_is_refused_as_tray_info_idx_by_the_closing_guard(self):
        """Belt and braces: a profile whose content names itself by UUID still
        does not put one in the field."""
        db = MagicMock()
        svc = self._svc({"id": self.ORCA_ID, "name": "Odd", "content": {"filament_id": self.ORCA_ID}})
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            AsyncMock(return_value=svc),
        ):
            idx, sid, _brand, _type = await resolve_slicer_filament(
                db=db,
                current_user=None,
                slicer_filament=self.ORCA_ID,
                slicer_filament_name=None,
                material="PLA",
            )
        assert idx == ""
        assert sid == ""

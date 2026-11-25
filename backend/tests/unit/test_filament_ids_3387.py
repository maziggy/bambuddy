"""Filament ids that the "drop the S after GF" rule gets wrong (#3387).

Bambu's public preset list carries each preset's setting_id next to the
filament_id Bambu Studio sends to the printer. SUNLU's do not follow the rule
("GFSNLS03_04" is "GFSNL03"), and a few P1/X1 presets carry another
filament's id altogether, decided by the full setting_id.
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES, get_slicer_settings
from backend.app.utils.filament_id_tables import ORCA_FILAMENT_IDS, SETTING_ID_FILAMENT_IDS
from backend.app.utils.filament_ids import (
    derived_filament_id,
    filament_id_to_setting_id,
    normalize_slicer_filament,
    orca_to_bambu_filament_id,
    setting_id_to_filament_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestSettingIdToFilamentId:
    @pytest.mark.parametrize(
        ("setting_id", "filament_id"),
        [
            ("GFSNLS03_04", "GFSNL03"),  # SUNLU PLA+ @BBL A1, the reported one
            ("GFSNLS08", "GFSNL08"),  # SUNLU PETG @BBL X1C, no suffix
            ("GFSA00_04", "GFA00"),  # Bambu PLA Basic @BBL A1, the plain rule
            ("GFSA05_10", "GFA01"),  # Bambu PLA Matte @BBL P1P
            ("GFSA05_02", "GFA05"),  # Bambu PLA Silk @BBL X1: same base, own id
            ("GFSR99_10", "GFU99"),  # Generic TPU @BBL P1P
            ("GFSR99_00", "GFR99"),  # Generic EVA @BBL X1C
            ("GFSS02_10", "GFS00"),  # Bambu Support W @BBL P1P
            ("GFSL23", "GFL00"),  # PolyLite PLA @BBL X1; "GFL23" is no filament
        ],
    )
    def test_bambus_own_id(self, setting_id, filament_id):
        assert setting_id_to_filament_id(setting_id) == filament_id

    @pytest.mark.parametrize("filament_id", ["GFSNL03", "GFS99", "GFL05", "PFUS990b6e19965353", "P8a85d5a", ""])
    def test_a_filament_id_or_user_preset_is_left_alone(self, filament_id):
        assert setting_id_to_filament_id(filament_id) == filament_id

    def test_a_bare_id_bambuddy_builds_keeps_the_rule(self):
        """Bambuddy turns a filament id into a bare setting_id itself
        ("GFA05" -> "GFSA05"). Bambu also has a legacy preset of that bare id
        (Bambu PLA Matte @BBL X1, "GFA01"); following it would turn every such
        Bambu PLA Silk spool into Matte."""
        assert setting_id_to_filament_id(filament_id_to_setting_id("GFA05")) == "GFA05"
        assert setting_id_to_filament_id("GFSR99") == "GFR99"


class TestFilamentIdToSettingId:
    @pytest.mark.parametrize(
        ("filament_id", "setting_id"),
        [("GFSNL03", "GFSNLS03"), ("GFSNLS03", "GFSNLS03"), ("GFA00", "GFSA00"), ("GFS99", "GFSS99")],
    )
    def test_converts(self, filament_id, setting_id):
        assert filament_id_to_setting_id(filament_id) == setting_id

    def test_normalize_reads_the_filament_id_from_the_full_id(self):
        assert normalize_slicer_filament("GFSA05_10") == ("GFA01", "GFSA05")
        assert normalize_slicer_filament("GFSNLS03_04") == ("GFSNL03", "GFSNLS03")
        assert normalize_slicer_filament("GFSL05_07") == ("GFL05", "GFSL05")

    def test_normalize_still_strips_the_suffix_of_anything_else(self):
        """Free text that starts with "P" must keep reducing to its material,
        which the resolver's guard then refuses, rather than going to the
        printer as a 9-character id."""
        assert normalize_slicer_filament("PLA_Basic") == ("PLA", "PLA")
        assert normalize_slicer_filament("PFUSabc_01") == ("PFUSabc", "PFUSabc")


class TestOrcaIds:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("OFMUWNkp", "GFSNL03"), ("OFzzzzzz", ""), ("GFL99", "GFL99"), ("Pfc74047", "Pfc74047"), ("", "")],
    )
    def test_translates_orca_ids_only(self, value, expected):
        assert orca_to_bambu_filament_id(value) == expected

    def test_every_filament_orca_knows_has_a_builtin_name(self):
        """The Orca lookup finds a system parent's id by name in the builtin
        table; SUNLU missing from it sent those profiles out as Generic PLA."""
        assert set(ORCA_FILAMENT_IDS.values()) <= set(_BUILTIN_FILAMENT_NAMES)


class TestTheGeneratedTables:
    def test_only_exceptions_are_listed(self):
        for setting_id, filament_id in SETTING_ID_FILAMENT_IDS.items():
            assert derived_filament_id(setting_id.split("_")[0]) != filament_id, setting_id

    def test_no_bare_entry_shadows_a_real_filament(self):
        """See test_a_bare_id_bambuddy_builds_keeps_the_rule."""
        for setting_id in SETTING_ID_FILAMENT_IDS:
            if "_" not in setting_id:
                assert derived_filament_id(setting_id) not in _BUILTIN_FILAMENT_NAMES, setting_id

    def test_the_frontend_copy_matches(self):
        ts = (REPO_ROOT / "frontend" / "src" / "utils" / "filamentIdTables.ts").read_text()
        assert dict(re.findall(r"^\s+(\w+): '(\w+)',$", ts, re.M)) == SETTING_ID_FILAMENT_IDS


class TestTheCloudListCarriesTheId:
    @pytest.mark.asyncio
    async def test_filament_id_is_passed_through(self):
        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.close = AsyncMock()
        cloud.get_slicer_settings = AsyncMock(
            return_value={
                "filament": {
                    "public": [
                        {"setting_id": "GFSNLS03_04", "name": "SUNLU PLA+ @BBL A1", "filament_id": "GFSNL03"},
                        {"setting_id": "GFSA00_04", "name": "Bambu PLA Basic @BBL A1", "filament_id": 7},
                    ],
                    "private": [{"setting_id": "PFUSfb87cd50b76616", "name": "Mine"}],
                }
            }
        )
        with patch("backend.app.api.routes.cloud.build_authenticated_cloud", AsyncMock(return_value=cloud)):
            result = await get_slicer_settings(version="2.0.0.0", db=MagicMock(), current_user=None)

        assert {s.setting_id: s.filament_id for s in result.filament} == {
            "PFUSfb87cd50b76616": None,
            "GFSNLS03_04": "GFSNL03",
            "GFSA00_04": None,  # not a string: dropped, not a failed list
        }


class TestOrcaProfiles:
    ORCA_ID = "34d8f588-860b-5be1-bcbe-c0d46d96324b"

    async def _lookup(self, profile, others=()):
        from backend.app.services.slicer_filament_resolver import lookup_orca_filament_id

        svc = MagicMock()
        svc.get_profile = AsyncMock(return_value=profile)
        svc.list_profiles = AsyncMock(return_value=[profile, *others])
        svc.close = AsyncMock()
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            AsyncMock(return_value=svc),
        ):
            return await lookup_orca_filament_id(MagicMock(), None, self.ORCA_ID)

    @pytest.mark.asyncio
    async def test_inheriting_a_sunlu_system_preset(self):
        child = {"id": self.ORCA_ID, "name": "My SUNLU", "content": {"inherits": "SUNLU PLA+ @BBL A1"}}
        found = await self._lookup(child)
        assert (found.filament_id, found.source) == ("GFSNL03", "inherited")
        assert filament_id_to_setting_id(found.filament_id) == "GFSNLS03"

    @pytest.mark.asyncio
    async def test_an_orca_id_of_its_own_is_translated(self):
        profile = {"id": self.ORCA_ID, "name": "Mine", "content": {"filament_id": "OFMUWNkp"}}
        found = await self._lookup(profile)
        assert (found.filament_id, found.source) == ("GFSNL03", "own")

    @pytest.mark.asyncio
    async def test_an_unknown_orca_id_never_reaches_the_printer(self):
        profile = {"id": self.ORCA_ID, "name": "Mine", "content": {"filament_id": "OFzzzzzz"}}
        found = await self._lookup(profile)
        assert (found.filament_id, found.reason) == ("", "no_filament_id")

    @pytest.mark.asyncio
    async def test_an_unknown_orca_id_still_lets_a_parent_answer(self):
        child = {"id": self.ORCA_ID, "name": "C", "content": {"filament_id": "OFzzzzzz", "inherits": "P"}}
        parent = {"id": "p", "name": "P", "content": {"filament_id": "OF2GCW1l"}}
        found = await self._lookup(child, [parent])
        assert (found.filament_id, found.source) == ("GFSNL08", "inherited")


class TestSpoolAssignmentSignedIn:
    """With Bambu Cloud signed in, the resolver reads the detail of the
    suffix-free base, which for "GFSA05" is Bambu PLA Matte @BBL X1. The
    official id comes from the spool's full setting_id instead (#3387)."""

    MATTE_X1 = {"name": "Bambu PLA Matte @BBL X1", "filament_id": "GFA01", "setting": {"filament_type": ["PLA"]}}

    async def _resolve(self, slicer_filament, detail, name=None):
        from backend.app.services.slicer_filament_resolver import resolve_slicer_filament

        cloud = MagicMock()
        cloud.is_authenticated = True
        cloud.get_setting_detail = AsyncMock(return_value=detail)
        cloud.close = AsyncMock()
        with patch("backend.app.api.routes.cloud.build_authenticated_cloud", AsyncMock(return_value=cloud)):
            return await resolve_slicer_filament(
                db=MagicMock(),
                current_user=None,
                slicer_filament=slicer_filament,
                slicer_filament_name=name,
                material="PLA",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slicer_filament", ["GFSA05_07", "GFSA05"])
    async def test_a_silk_spool_stays_silk(self, slicer_filament):
        idx, sid, brand, ftype = await self._resolve(slicer_filament, self.MATTE_X1)
        assert (idx, sid) == ("GFA05", "GFSA05")
        # Matte's name and type are another filament's.
        assert (brand, ftype) == (None, None)

    @pytest.mark.asyncio
    async def test_the_p1p_matte_spool_gets_matte(self):
        idx, _sid, brand, ftype = await self._resolve("GFSA05_10", self.MATTE_X1)
        assert (idx, brand, ftype) == ("GFA01", "Bambu PLA Matte", "PLA")

    @pytest.mark.asyncio
    async def test_a_spool_picked_as_the_legacy_bare_preset_gets_bambus_id(self):
        """Picked in the spool form, the spool carries the preset's own name;
        the bare id is then Bambu's preset, not one Bambuddy built."""
        detail = {"name": "Bambu TPU 95A @BBL X1C", "filament_id": "GFU01", "setting": {"filament_type": ["TPU"]}}
        idx, _sid, brand, ftype = await self._resolve("GFSU00", detail, name="Bambu TPU 95A @BBL X1C")
        assert (idx, brand, ftype) == ("GFU01", "Bambu TPU 95A", "TPU")

    @pytest.mark.asyncio
    async def test_a_silk_spool_named_silk_stays_silk(self):
        idx, *_ = await self._resolve("GFSA05", self.MATTE_X1, name="Bambu PLA Silk")
        assert idx == "GFA05"

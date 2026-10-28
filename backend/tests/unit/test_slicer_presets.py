"""Tests for the unified slicer-presets endpoint helpers.

The endpoint stitches together four preset sources (local / orca_cloud /
cloud / standard). It does NOT dedup across tiers — every tier surfaces
its full list so the user can pick any source. Bambu Cloud filament
metadata is enriched from same-named entries in the other tiers so it
can still score in the SliceModal's auto-pick. These tests pin the
enrich behaviour, the cloud-status mapping, and the per-user / sidecar
caches at the helper level — full HTTP integration is covered by the
routes test.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import slicer_presets as sp
from backend.app.schemas.slicer_presets import UnifiedPreset


def _slot(items: list[tuple[str, str, str]]) -> dict[str, list[UnifiedPreset]]:
    """Helper: build a single-slot dict from (id, name, source) tuples placed
    on the printer slot. Process / filament default to empty so each test
    only exercises the slot it cares about."""
    return {
        "printer": [UnifiedPreset(id=i, name=n, source=s) for i, n, s in items],
        "process": [],
        "filament": [],
    }


class TestEnrichCloudMetadata:
    """No cross-tier dedup — every tier's full list comes back; Bambu Cloud
    filament metadata is enriched from same-named entries in other tiers."""

    def test_same_name_in_all_tiers_appears_in_every_tier(self):
        """Critical regression guard for #1712: a user who has imported a
        local profile AND signed in to Orca AND has Bambu Cloud with the
        same name should see it under EACH source, not just the highest-
        priority tier. The order is used for auto-pick + group rendering;
        it is NOT used to hide profiles."""
        orca = _slot([("oid1", "Bambu PLA Basic", "orca_cloud")])
        cloud = _slot([("cid1", "Bambu PLA Basic", "cloud")])
        local = _slot([("lid1", "Bambu PLA Basic", "local")])
        standard = _slot([("Bambu PLA Basic", "Bambu PLA Basic", "standard")])

        oc, c, l_, s = sp._enrich_cloud_metadata(orca, cloud, local, standard)

        assert [p.source for p in l_["printer"]] == ["local"]
        assert [p.source for p in oc["printer"]] == ["orca_cloud"]
        assert [p.source for p in c["printer"]] == ["cloud"]
        assert [p.source for p in s["printer"]] == ["standard"]

    def test_preserves_order_within_tier(self):
        """A tier's input order must be preserved — nothing in the enrich
        pass should sort, reverse, or otherwise reorder entries."""
        cloud = _slot(
            [
                ("c1", "Z-First", "cloud"),
                ("c2", "A-Second", "cloud"),
                ("c3", "M-Third", "cloud"),
            ]
        )
        _oc, c, _l, _s = sp._enrich_cloud_metadata(_slot([]), cloud, _slot([]), _slot([]))
        assert [p.name for p in c["printer"]] == ["Z-First", "A-Second", "M-Third"]

    def test_bambu_cloud_filament_metadata_backfilled_from_local(self):
        """Bambu Cloud's list response omits filament_type/colour for
        rate-limit reasons. A same-named local entry's metadata fills in
        so the cloud entry can still score in pickFilamentForSlot."""
        local = {
            "printer": [],
            "process": [],
            "filament": [
                UnifiedPreset(
                    id="lp1",
                    name="Bambu PLA Basic",
                    source="local",
                    filament_type="PLA",
                    filament_colour="#FF0000",
                )
            ],
        }
        cloud = {
            "printer": [],
            "process": [],
            "filament": [UnifiedPreset(id="cp1", name="Bambu PLA Basic", source="cloud")],
        }
        _oc, c, _l, _s = sp._enrich_cloud_metadata(_slot([]), cloud, local, _slot([]))
        # Cloud entry now carries the local entry's metadata.
        assert c["filament"][0].filament_type == "PLA"
        assert c["filament"][0].filament_colour == "#FF0000"
        # Local entry is untouched.
        assert local["filament"][0].filament_type == "PLA"

    def test_bambu_cloud_metadata_falls_back_through_orca_and_standard(self):
        """When local doesn't carry the name, orca_cloud / standard fill in."""
        orca = {
            "printer": [],
            "process": [],
            "filament": [
                UnifiedPreset(
                    id="o1",
                    name="Bambu PLA Basic",
                    source="orca_cloud",
                    filament_type="PLA",
                    filament_colour="#00FF00",
                )
            ],
        }
        cloud = {
            "printer": [],
            "process": [],
            "filament": [UnifiedPreset(id="cp1", name="Bambu PLA Basic", source="cloud")],
        }
        _oc, c, _l, _s = sp._enrich_cloud_metadata(orca, cloud, _slot([]), _slot([]))
        assert c["filament"][0].filament_type == "PLA"
        assert c["filament"][0].filament_colour == "#00FF00"

    def test_bambu_cloud_keeps_its_own_metadata_when_present(self):
        """If Bambu Cloud already has filament_type / filament_colour the
        enrich pass must not overwrite them with a different same-named
        entry's values."""
        local = {
            "printer": [],
            "process": [],
            "filament": [
                UnifiedPreset(
                    id="lp1",
                    name="Bambu PLA Basic",
                    source="local",
                    filament_type="PETG",
                    filament_colour="#000000",
                )
            ],
        }
        cloud = {
            "printer": [],
            "process": [],
            "filament": [
                UnifiedPreset(
                    id="cp1",
                    name="Bambu PLA Basic",
                    source="cloud",
                    filament_type="PLA",
                    filament_colour="#FFFFFF",
                )
            ],
        }
        _oc, c, _l, _s = sp._enrich_cloud_metadata(_slot([]), cloud, local, _slot([]))
        assert c["filament"][0].filament_type == "PLA"
        assert c["filament"][0].filament_colour == "#FFFFFF"


def _filaments(source: str, *items: tuple[str, str | None]) -> dict[str, list[UnifiedPreset]]:
    """A tier with only filament presets, from (name, filament_type) pairs."""
    return {
        "printer": [],
        "process": [],
        "filament": [UnifiedPreset(id=n, name=n, source=source, filament_type=t) for n, t in items],
    }


class TestEnrichMaterialWithoutTwin:
    """#3403: with OrcaSlicer preferred, the bundle has no H2C profiles, so a
    Bambu Cloud "@BBL H2C" filament has no same-named twin and used to stay
    untyped -- a PETG-CF slot then auto-picked Generic PETG."""

    def _bundled(self) -> dict[str, list[UnifiedPreset]]:
        # The OrcaSlicer 2.4.2 bundle as it lists these: every printer's
        # profile but the H2C, and Orca's own "@System" generics.
        return _filaments(
            "standard",
            ("Generic PETG-CF @BBL X1C", "PETG-CF"),
            ("Generic PETG-CF @BBL H2D", "PETG-CF"),
            ("Generic PETG-CF @System", "PETG"),
            ("Generic PETG @BBL X1C", "PETG"),
            ("Generic PE-CF @BBL X1C", "PE-CF"),
            ("Generic PE-CF @System", "PE"),
        )

    def test_bambu_cloud_stock_profile_takes_the_material_of_its_other_printers(self):
        cloud = _filaments(
            "cloud",
            ("Generic PETG @BBL H2C", None),
            ("Generic PETG-CF @BBL H2C", None),
            ("Generic PETG-CF @BBL H2C 0.4 nozzle", None),
            ("Generic PE-CF @BBL H2C", None),
        )

        _oc, c, _l, _s = sp._enrich_cloud_metadata(
            _filaments("orca_cloud"), cloud, _filaments("local"), self._bundled()
        )

        assert [p.filament_type for p in c["filament"]] == ["PETG", "PETG-CF", "PETG-CF", "PE-CF"]
        # Material only: the colour of another printer's profile isn't this one's.
        assert all(p.filament_colour is None for p in c["filament"])

    def test_disagreeing_bundled_materials_leave_it_unknown(self):
        bundled = _filaments("standard", ("Odd PLA @BBL X1C", "PLA"), ("Odd PLA @BBL A1", "PLA-CF"))
        cloud = _filaments("cloud", ("Odd PLA @BBL H2C", None))

        _oc, c, _l, _s = sp._enrich_cloud_metadata(_filaments("orca_cloud"), cloud, _filaments("local"), bundled)

        assert c["filament"][0].filament_type is None

    def test_only_bundled_bbl_names_count(self):
        """A name without "@BBL", or a user's own "@BBL"-named profile, is no
        source of another profile's material."""
        local = _filaments("local", ("Generic PLA @BBL X1C", "PETG"))
        cloud = _filaments("cloud", ("Generic PETG-CF", None), ("Generic PLA @BBL H2C", None))

        _oc, c, _l, _s = sp._enrich_cloud_metadata(_filaments("orca_cloud"), cloud, local, self._bundled())

        assert [p.filament_type for p in c["filament"]] == [None, None]

    def test_a_users_copy_of_a_stock_profile_is_left_unknown(self):
        """Its material may be the one the user changed it to."""
        cloud = _filaments("cloud", ("Generic PETG @BBL H2C - Copy", None), ("Generic PETG @BBL H2C 0.4 nozzle", None))

        _oc, c, _l, _s = sp._enrich_cloud_metadata(
            _filaments("orca_cloud"), cloud, _filaments("local"), self._bundled()
        )

        assert [p.filament_type for p in c["filament"]] == [None, "PETG"]

    def test_a_stated_material_is_kept(self):
        cloud = _filaments("cloud", ("Generic PETG-CF @BBL H2C", "PETG"))

        _oc, c, _l, _s = sp._enrich_cloud_metadata(
            _filaments("orca_cloud"), cloud, _filaments("local"), self._bundled()
        )

        assert c["filament"][0].filament_type == "PETG"

    def test_orca_cloud_profile_takes_the_material_of_its_parent(self):
        orca = _filaments(
            "orca_cloud",
            ("My PETG-CF", None),
            ("My PETG-CF fast", None),
            ("My H2C PETG-CF", None),
            ("Loop A", None),
            ("Loop B", None),
            ("Typed", "PLA"),
            ("From System", None),
        )
        parents = {
            "My PETG-CF": "Generic PETG-CF @BBL X1C",  # bundled, same name
            "My PETG-CF fast": "My PETG-CF",  # through another user profile
            "My H2C PETG-CF": "Generic PETG-CF @BBL H2C",  # not bundled: by filament
            "Loop A": "Loop B",
            "Loop B": "Loop A",
            "Typed": "Generic PETG @BBL X1C",
            # Orca's own generic states the coarser PETG: no source.
            "From System": "Generic PETG-CF @System",
        }
        for p in orca["filament"]:
            p.inherits = parents[p.name]

        oc, _c, _l, _s = sp._enrich_cloud_metadata(orca, _filaments("cloud"), _filaments("local"), self._bundled())

        assert [p.filament_type for p in oc["filament"]] == ["PETG-CF", "PETG-CF", "PETG-CF", None, None, "PLA", None]


class TestEnrichCompatiblePrinters:
    """#2628: the same name bridge carries ``compatible_printers`` onto the
    tiers that don't ship one. Bambu Cloud never does — so a profile whose
    name carries no printer model reads as "compatibility unknown", which the
    SliceModal treats as usable and auto-picks for the wrong printer."""

    COMPAT = ["Bambu Lab X1 Carbon 0.2 nozzle", "Bambu Lab P1S 0.2 nozzle"]

    def _tier(self, source: str, slot: str, compat: list[str] | None) -> dict[str, list[UnifiedPreset]]:
        empty: dict[str, list[UnifiedPreset]] = {"printer": [], "process": [], "filament": []}
        empty[slot] = [
            UnifiedPreset(id=f"{source}1", name="Overture PLA Matte @0.2", source=source, compatible_printers=compat)
        ]
        return empty

    def test_bambu_cloud_borrows_the_list_from_a_same_named_local_import(self):
        local = self._tier("local", "filament", self.COMPAT)
        cloud = self._tier("cloud", "filament", None)

        _oc, c, _l, _s = sp._enrich_cloud_metadata(_slot([]), cloud, local, _slot([]))

        assert c["filament"][0].compatible_printers == self.COMPAT

    def test_bambu_cloud_borrows_from_orca_cloud_too(self):
        orca = self._tier("orca_cloud", "filament", self.COMPAT)
        cloud = self._tier("cloud", "filament", None)

        _oc, c, _l, _s = sp._enrich_cloud_metadata(orca, cloud, _slot([]), _slot([]))

        assert c["filament"][0].compatible_printers == self.COMPAT

    def test_orca_cloud_borrows_when_its_own_content_had_no_list(self):
        orca = self._tier("orca_cloud", "filament", None)
        local = self._tier("local", "filament", self.COMPAT)

        oc, _c, _l, _s = sp._enrich_cloud_metadata(orca, _slot([]), local, _slot([]))

        assert oc["filament"][0].compatible_printers == self.COMPAT

    def test_process_slot_is_bridged_as_well(self):
        local = self._tier("local", "process", self.COMPAT)
        cloud = self._tier("cloud", "process", None)

        _oc, c, _l, _s = sp._enrich_cloud_metadata(_slot([]), cloud, local, _slot([]))

        assert c["process"][0].compatible_printers == self.COMPAT

    def test_never_overwrites_a_list_the_entry_already_has(self):
        own = ["Bambu Lab P2S 0.4 nozzle"]
        local = self._tier("local", "filament", self.COMPAT)
        cloud = self._tier("cloud", "filament", own)

        _oc, c, _l, _s = sp._enrich_cloud_metadata(_slot([]), cloud, local, _slot([]))

        assert c["filament"][0].compatible_printers == own

    def test_no_donor_leaves_the_entry_unclassified(self):
        """Absent evidence the entry must stay None — the SliceModal then
        falls back to the name matcher instead of hiding the profile."""
        cloud = self._tier("cloud", "filament", None)

        _oc, c, _l, _s = sp._enrich_cloud_metadata(_slot([]), cloud, _slot([]), _slot([]))

        assert c["filament"][0].compatible_printers is None

    def test_borrowed_list_is_copied_not_shared(self):
        """A later mutation of one tier's list must not reach through to the
        other — these objects are cached per user between requests."""
        local = self._tier("local", "filament", list(self.COMPAT))
        cloud = self._tier("cloud", "filament", None)

        _oc, c, l_, _s = sp._enrich_cloud_metadata(_slot([]), cloud, local, _slot([]))
        l_["filament"][0].compatible_printers.append("Bambu Lab H2D 0.4 nozzle")

        assert c["filament"][0].compatible_printers == self.COMPAT


def _user_with_cloud_auth(user_id: int = 1) -> MagicMock:
    """Construct a mock User that passes the CLOUD_AUTH permission check.

    `MagicMock` defaults `.has_permission(...)` to a truthy MagicMock object,
    which would coincidentally pass the gate — but explicit is better than
    accidental. Setting `.return_value = True` documents the intent."""
    user = MagicMock(id=user_id)
    user.has_permission = MagicMock(return_value=True)
    return user


class TestFetchOrcaCloudPresets:
    """``_fetch_orca_cloud_presets`` mirrors the Bambu Cloud fetcher's status
    vocabulary (``ok`` / ``not_authenticated`` / ``expired`` / ``unreachable``)
    and the same permission-shortcut + caching behaviour. Tests pin the
    contract so a future bug in either fetcher doesn't silently desync them."""

    def _orca_creds(self, token: str | None = "tok") -> MagicMock:
        creds = MagicMock()
        creds.token = token
        return creds

    @pytest.mark.asyncio
    async def test_no_token_returns_not_authenticated(self):
        sp._orca_cloud_cache.clear()
        with patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds(None))):
            user = MagicMock(id=1)
            user.has_permission = MagicMock(return_value=True)
            slots, status = await sp._fetch_orca_cloud_presets(MagicMock(), user)
        assert status == "not_authenticated"
        assert slots == {"printer": [], "process": [], "filament": []}

    @pytest.mark.asyncio
    async def test_user_without_orca_cloud_auth_returns_not_authenticated(self):
        """Defence-in-depth — a user lacking ORCA_CLOUD_AUTH must not see Orca
        presets even if their User row carries a stale token. Credentials
        lookup must short-circuit ahead of the token read."""
        sp._orca_cloud_cache.clear()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=False)
        with patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))) as load:
            slots, status = await sp._fetch_orca_cloud_presets(MagicMock(), user)
        assert status == "not_authenticated"
        assert slots["printer"] == []
        load.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_error_returns_expired(self):
        sp._orca_cloud_cache.clear()
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(side_effect=sp.OrcaCloudAuthError("expired"))
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        ):
            _slots, status = await sp._fetch_orca_cloud_presets(MagicMock(), user)
        assert status == "expired"
        svc_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_orca_error_returns_unreachable(self):
        sp._orca_cloud_cache.clear()
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(side_effect=sp.OrcaCloudError("net down"))
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        ):
            _slots, status = await sp._fetch_orca_cloud_presets(MagicMock(), user)
        assert status == "unreachable"

    @pytest.mark.asyncio
    async def test_happy_path_shapes_grouped_by_type(self):
        """Orca content.type values map onto Bambu Cloud's preset type vocab
        (``printer`` / ``print`` → ``process`` / ``filament``). Verify the
        full mapping by feeding one of each shape."""
        sp._orca_cloud_cache.clear()
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(
            return_value=[
                {"id": "m1", "name": "Orca X1C", "content": {"type": "printer"}},
                {"id": "p1", "name": "Orca 0.20mm", "content": {"type": "print"}},
                {
                    "id": "f1",
                    "name": "Orca PLA",
                    "content": {
                        "type": "filament",
                        "filament_type": ["PLA"],
                        "default_filament_colour": ["#000000"],
                    },
                },
            ]
        )
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        ):
            slots, status = await sp._fetch_orca_cloud_presets(MagicMock(), user)
        assert status == "ok"
        assert [p.name for p in slots["printer"]] == ["Orca X1C"]
        assert [p.name for p in slots["process"]] == ["Orca 0.20mm"]
        filament = slots["filament"]
        assert [p.name for p in filament] == ["Orca PLA"]
        # Inline metadata extracted from the content blob (Orca's sync_pull
        # returns full content, so unlike Bambu Cloud we don't need a second
        # per-preset fetch to enrich filament_type / filament_colour).
        assert filament[0].filament_type == "PLA"
        assert filament[0].filament_colour == "#000000"

    @pytest.mark.asyncio
    async def test_extracts_compatible_printers_from_content(self):
        """#2628: Orca's sync_pull already carries the profile's own
        compatible-printer list. Surfacing it lets the SliceModal reject a
        profile built for another printer instead of falling back to reading
        the model out of the NAME — which fails outright for names that carry
        no model ("Overture PLA Matte @0.2")."""
        sp._orca_cloud_cache.clear()
        compat = ["Bambu Lab X1 Carbon 0.2 nozzle", "Bambu Lab P1S 0.2 nozzle"]
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(
            return_value=[
                {
                    "id": "f1",
                    "name": "Overture PLA Matte @0.2",
                    "content": {
                        "type": "filament",
                        "filament_type": ["PLA"],
                        "compatible_printers": compat,
                    },
                },
                {
                    "id": "p1",
                    "name": "Orca 0.20mm",
                    "content": {"type": "print", "compatible_printers": "Bambu Lab P2S 0.4 nozzle"},
                },
                {"id": "m1", "name": "Orca X1C", "content": {"type": "printer"}},
            ]
        )
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        ):
            slots, status = await sp._fetch_orca_cloud_presets(MagicMock(), user)

        assert status == "ok"
        assert slots["filament"][0].compatible_printers == compat
        # A single-printer profile may store a bare string — normalised to a list.
        assert slots["process"][0].compatible_printers == ["Bambu Lab P2S 0.4 nozzle"]
        # Printer presets have nothing to be compatible with.
        assert slots["printer"][0].compatible_printers is None

    @pytest.mark.asyncio
    async def test_printer_preset_carries_the_preset_it_was_saved_from(self):
        """#3250: a printer preset saved under a name of its own is in no
        process's compatible_printers; the SliceModal needs its parent."""
        sp._orca_cloud_cache.clear()
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(
            return_value=[
                {
                    "id": "m1",
                    "name": "Bambu Lab H2D 0.4 nozzle - Skew",
                    "content": {"type": "printer", "inherits": " Bambu Lab H2D 0.4 nozzle "},
                },
                {"id": "m2", "name": "Orca X1C", "content": {"type": "printer", "inherits": ""}},
                {"id": "m3", "name": "Odd", "content": {"type": "printer", "inherits": ["x"]}},
            ]
        )
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        ):
            slots, _ = await sp._fetch_orca_cloud_presets(MagicMock(), user)

        assert [p.inherits for p in slots["printer"]] == ["Bambu Lab H2D 0.4 nozzle", None, None]

    @pytest.mark.asyncio
    async def test_filament_preset_carries_the_preset_it_was_saved_from(self):
        """#3403: a filament saved with only the keys the user changed states no
        material of its own; the enrich pass reads it off this parent."""
        sp._orca_cloud_cache.clear()
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(
            return_value=[
                {
                    "id": "f1",
                    "name": "My PETG-CF",
                    "content": {"type": "filament", "inherits": "Generic PETG-CF @BBL X1C"},
                },
                {
                    "id": "p1",
                    "name": "Orca 0.20mm",
                    "content": {"type": "print", "inherits": "0.20mm Standard @BBL X1C"},
                },
            ]
        )
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        ):
            slots, _ = await sp._fetch_orca_cloud_presets(MagicMock(), user)

        assert slots["filament"][0].inherits == "Generic PETG-CF @BBL X1C"
        assert slots["filament"][0].filament_type is None
        assert slots["process"][0].inherits is None

    @pytest.mark.asyncio
    async def test_missing_or_malformed_compatible_printers_stays_none(self):
        """No data must read as "unknown", never as "compatible with nothing" —
        the SliceModal falls back to the name matcher for those."""
        sp._orca_cloud_cache.clear()
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(
            return_value=[
                {"id": "f1", "name": "No list", "content": {"type": "filament"}},
                {"id": "f2", "name": "Empty list", "content": {"type": "filament", "compatible_printers": []}},
                {"id": "f3", "name": "Blanks", "content": {"type": "filament", "compatible_printers": ["", "  "]}},
                {"id": "f4", "name": "Wrong type", "content": {"type": "filament", "compatible_printers": {"a": 1}}},
            ]
        )
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        ):
            slots, _status = await sp._fetch_orca_cloud_presets(MagicMock(), user)

        assert [p.compatible_printers for p in slots["filament"]] == [None, None, None, None]

    @pytest.mark.asyncio
    async def test_cache_hit_skips_orca_call(self):
        """A second call within TTL must reuse the cached slots and NOT
        hit the Orca service again — same TTL as Bambu Cloud (5 min)."""
        sp._orca_cloud_cache.clear()
        svc_mock = MagicMock()
        svc_mock.list_profiles = AsyncMock(return_value=[])
        svc_mock.close = AsyncMock()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=True)
        with (
            patch.object(sp, "_load_orca_credentials", AsyncMock(return_value=self._orca_creds("tok"))),
            patch.object(sp, "_build_orca_service", AsyncMock(return_value=svc_mock)) as build,
        ):
            await sp._fetch_orca_cloud_presets(MagicMock(), user)
            await sp._fetch_orca_cloud_presets(MagicMock(), user)
        # Build is the cache miss signal — second call reused the cache.
        build.assert_awaited_once()


class TestFetchCloudPresets:
    """`_fetch_cloud_presets` translates token state and cloud errors into
    the four ``cloud_status`` values the SliceModal banner consumes."""

    @pytest.mark.asyncio
    async def test_no_token_returns_not_authenticated(self):
        sp._cloud_cache.clear()
        with patch.object(sp, "get_stored_token", AsyncMock(return_value=(None, None, None))):
            slots, status = await sp._fetch_cloud_presets(MagicMock(), _user_with_cloud_auth())
        assert status == "not_authenticated"
        assert slots == {"printer": [], "process": [], "filament": []}

    @pytest.mark.asyncio
    async def test_user_without_cloud_auth_returns_not_authenticated(self):
        """Defence-in-depth: a user lacking CLOUD_AUTH must NOT see cloud
        presets even if their User row carries a stale cloud_token from a
        previous permission state. Token lookup is skipped entirely."""
        sp._cloud_cache.clear()
        user = MagicMock(id=1)
        user.has_permission = MagicMock(return_value=False)
        with patch.object(sp, "get_stored_token", AsyncMock(return_value=("leftover-token", None, None))) as get_tok:
            slots, status = await sp._fetch_cloud_presets(MagicMock(), user)
        assert status == "not_authenticated"
        assert slots["printer"] == []
        # Token was never read — the perm check short-circuits ahead of it.
        get_tok.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_error_returns_expired(self):
        sp._cloud_cache.clear()
        cloud_mock = MagicMock()
        cloud_mock.set_token = MagicMock()
        cloud_mock.get_slicer_settings = AsyncMock(side_effect=sp.BambuCloudAuthError("expired"))
        cloud_mock.close = AsyncMock()
        with (
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", "e@x", None))),
            patch.object(sp, "BambuCloudService", return_value=cloud_mock),
        ):
            slots, status = await sp._fetch_cloud_presets(MagicMock(), _user_with_cloud_auth())
        assert status == "expired"
        assert slots["printer"] == []
        cloud_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cloud_error_returns_unreachable(self):
        sp._cloud_cache.clear()
        cloud_mock = MagicMock()
        cloud_mock.set_token = MagicMock()
        cloud_mock.get_slicer_settings = AsyncMock(side_effect=sp.BambuCloudError("net down"))
        cloud_mock.close = AsyncMock()
        with (
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", None, None))),
            patch.object(sp, "BambuCloudService", return_value=cloud_mock),
        ):
            _slots, status = await sp._fetch_cloud_presets(MagicMock(), _user_with_cloud_auth())
        assert status == "unreachable"

    @pytest.mark.asyncio
    async def test_happy_path_shapes_private_then_public(self):
        """Cloud presets split into private (user-custom) + public (Bambu's
        stock cloud presets). Private should sort before public so a user's
        own customisations sit at the top of the dropdown."""
        sp._cloud_cache.clear()
        cloud_mock = MagicMock()
        cloud_mock.set_token = MagicMock()
        cloud_mock.get_slicer_settings = AsyncMock(
            return_value={
                "printer": {
                    "private": [{"setting_id": "PFUprivate1", "name": "My X1C"}],
                    "public": [{"setting_id": "PFUpublic1", "name": "Bambu X1C Stock"}],
                },
                "print": {"private": [], "public": []},
                "filament": {"private": [], "public": []},
            }
        )
        cloud_mock.close = AsyncMock()
        with (
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", None, None))),
            patch.object(sp, "BambuCloudService", return_value=cloud_mock),
        ):
            slots, status = await sp._fetch_cloud_presets(MagicMock(), _user_with_cloud_auth())
        assert status == "ok"
        names = [p.name for p in slots["printer"]]
        assert names == ["My X1C", "Bambu X1C Stock"]

    @pytest.mark.asyncio
    async def test_cache_hit_skips_cloud_call(self):
        """A second call within TTL must reuse the cached slots and NOT
        hit Bambu Cloud again."""
        sp._cloud_cache.clear()
        cloud_mock = MagicMock()
        cloud_mock.set_token = MagicMock()
        cloud_mock.get_slicer_settings = AsyncMock(
            return_value={
                "printer": {"private": [{"setting_id": "id1", "name": "X1C"}], "public": []},
                "print": {"private": [], "public": []},
                "filament": {"private": [], "public": []},
            }
        )
        cloud_mock.close = AsyncMock()
        user = _user_with_cloud_auth(user_id=42)
        with (
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", None, None))),
            patch.object(sp, "BambuCloudService", return_value=cloud_mock),
        ):
            await sp._fetch_cloud_presets(MagicMock(), user)
            await sp._fetch_cloud_presets(MagicMock(), user)
        cloud_mock.get_slicer_settings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_is_per_user(self):
        """User A's cached cloud presets must not surface for user B."""
        sp._cloud_cache.clear()

        def make_mock(name: str):
            m = MagicMock()
            m.set_token = MagicMock()
            m.get_slicer_settings = AsyncMock(
                return_value={
                    "printer": {"private": [{"setting_id": f"id-{name}", "name": name}], "public": []},
                    "print": {"private": [], "public": []},
                    "filament": {"private": [], "public": []},
                }
            )
            m.close = AsyncMock()
            return m

        sequence = [make_mock("AliceX1C"), make_mock("BobX1C")]
        with (
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", None, None))),
            patch.object(sp, "BambuCloudService", side_effect=sequence),
        ):
            alice_slots, _ = await sp._fetch_cloud_presets(MagicMock(), _user_with_cloud_auth(1))
            bob_slots, _ = await sp._fetch_cloud_presets(MagicMock(), _user_with_cloud_auth(2))

        assert alice_slots["printer"][0].name == "AliceX1C"
        assert bob_slots["printer"][0].name == "BobX1C"

    @pytest.mark.asyncio
    async def test_cache_invalidates_on_token_change(self):
        """A token change (logout + login, admin reset, region switch) must
        bypass the cache for that user — pinning a real-world auth bug
        where user re-login + cache-stuck-on-old-cloud-account would
        silently serve a different account's preset list for ~5 minutes."""
        sp._cloud_cache.clear()

        def make_mock(name: str):
            m = MagicMock()
            m.set_token = MagicMock()
            m.get_slicer_settings = AsyncMock(
                return_value={
                    "printer": {"private": [{"setting_id": f"id-{name}", "name": name}], "public": []},
                    "print": {"private": [], "public": []},
                    "filament": {"private": [], "public": []},
                }
            )
            m.close = AsyncMock()
            return m

        # Same user_id, different token between calls — the second call must
        # NOT serve the first call's cached slots.
        services = [make_mock("OldAccountX1C"), make_mock("NewAccountX1C")]
        token_sequence = [("tok-old", None, None), ("tok-new", None, None)]
        user = _user_with_cloud_auth(user_id=7)

        with (
            patch.object(sp, "get_stored_token", AsyncMock(side_effect=token_sequence)),
            patch.object(sp, "BambuCloudService", side_effect=services),
        ):
            first, _ = await sp._fetch_cloud_presets(MagicMock(), user)
            second, _ = await sp._fetch_cloud_presets(MagicMock(), user)

        assert first["printer"][0].name == "OldAccountX1C"
        assert second["printer"][0].name == "NewAccountX1C"

    @pytest.mark.asyncio
    async def test_refresh_bypasses_cloud_cache(self):
        """``refresh=True`` must skip an otherwise-warm cache entry and hit
        Bambu Cloud again — wiring for the SliceModal's Refresh button so a
        user who deletes a cloud preset in Bambu Studio / Handy doesn't have
        to wait for the 5-minute TTL to expire (#1581)."""
        sp._cloud_cache.clear()
        cloud_mock = MagicMock()
        cloud_mock.set_token = MagicMock()
        cloud_mock.get_slicer_settings = AsyncMock(
            return_value={
                "printer": {"private": [{"setting_id": "id1", "name": "X1C"}], "public": []},
                "print": {"private": [], "public": []},
                "filament": {"private": [], "public": []},
            }
        )
        cloud_mock.close = AsyncMock()
        user = _user_with_cloud_auth(user_id=99)
        with (
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", None, None))),
            patch.object(sp, "BambuCloudService", return_value=cloud_mock),
        ):
            await sp._fetch_cloud_presets(MagicMock(), user)
            # Without refresh, the second call hits cache (covered by
            # test_cache_hit_skips_cloud_call). With refresh=True it MUST
            # re-fetch.
            await sp._fetch_cloud_presets(MagicMock(), user, refresh=True)
        assert cloud_mock.get_slicer_settings.await_count == 2

    @pytest.mark.asyncio
    async def test_refresh_writes_back_to_cache(self):
        """A refresh call must still update the cache so a subsequent normal
        call doesn't re-hit the cloud immediately afterwards."""
        sp._cloud_cache.clear()
        cloud_mock = MagicMock()
        cloud_mock.set_token = MagicMock()
        cloud_mock.get_slicer_settings = AsyncMock(
            return_value={
                "printer": {"private": [{"setting_id": "id1", "name": "X1C"}], "public": []},
                "print": {"private": [], "public": []},
                "filament": {"private": [], "public": []},
            }
        )
        cloud_mock.close = AsyncMock()
        user = _user_with_cloud_auth(user_id=101)
        with (
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", None, None))),
            patch.object(sp, "BambuCloudService", return_value=cloud_mock),
        ):
            await sp._fetch_cloud_presets(MagicMock(), user, refresh=True)
            await sp._fetch_cloud_presets(MagicMock(), user)
        # Two calls — first refresh, second a normal cache hit.
        assert cloud_mock.get_slicer_settings.await_count == 1


class TestFetchBundledPresets:
    """Standard tier reaches out to the slicer-api sidecar; tolerate the
    sidecar being absent / unreachable so the modal still works."""

    @pytest.mark.asyncio
    async def test_no_sidecar_url_returns_empty(self):
        sp._bundled_cache = None
        with patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value=None)):
            slots = await sp._fetch_bundled_presets(MagicMock())
        assert slots == {"printer": [], "process": [], "filament": []}
        # No URL means no useful cache result either — second call should
        # try again (so users who configure a URL mid-session see results).
        assert sp._bundled_cache is None

    @pytest.mark.asyncio
    async def test_sidecar_error_returns_empty(self):
        sp._bundled_cache = None
        svc_mock = MagicMock()
        svc_mock.list_bundled_profiles = AsyncMock(side_effect=sp.SlicerApiError("boom"))
        svc_mock.__aenter__ = AsyncMock(return_value=svc_mock)
        svc_mock.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://nope")),
            patch.object(sp, "SlicerApiService", return_value=svc_mock),
        ):
            slots = await sp._fetch_bundled_presets(MagicMock())
        assert slots == {"printer": [], "process": [], "filament": []}

    @pytest.mark.asyncio
    async def test_happy_path_shapes_response(self):
        sp._bundled_cache = None
        svc_mock = MagicMock()
        svc_mock.list_bundled_profiles = AsyncMock(
            return_value={
                "printer": [{"name": "Bambu X1C 0.4", "base_id": None}],
                "process": [{"name": "0.20mm Standard", "base_id": "fdm_process_common"}],
                "filament": [{"name": "Bambu PLA Basic", "base_id": "fdm_filament_pla"}],
            }
        )
        svc_mock.__aenter__ = AsyncMock(return_value=svc_mock)
        svc_mock.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc_mock),
        ):
            slots = await sp._fetch_bundled_presets(MagicMock())
        assert slots["printer"][0].name == "Bambu X1C 0.4"
        assert slots["printer"][0].source == "standard"
        # Bundled presets are addressed by name (the slicer's inheritance
        # walker resolves them by name), so id == name.
        assert slots["printer"][0].id == "Bambu X1C 0.4"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_sidecar(self):
        """A second call within TTL must serve from the cached entry and not
        re-hit the sidecar HTTP."""
        sp._bundled_cache = (
            time.monotonic(),
            {
                "printer": [UnifiedPreset(id="Cached", name="Cached", source="standard")],
                "process": [],
                "filament": [],
            },
        )
        # If `SlicerApiService` is constructed at all we've missed the cache.
        with patch.object(sp, "SlicerApiService", side_effect=AssertionError("cache miss!")):
            slots = await sp._fetch_bundled_presets(MagicMock())
        assert slots["printer"][0].name == "Cached"

    @pytest.mark.asyncio
    async def test_refresh_bypasses_bundled_cache(self):
        """``refresh=True`` must re-hit the sidecar even when the in-process
        cache is warm — paired with the cloud-cache refresh, this is what
        powers the SliceModal's Refresh button (#1581)."""
        sp._bundled_cache = (
            time.monotonic(),
            {
                "printer": [UnifiedPreset(id="Stale", name="Stale", source="standard")],
                "process": [],
                "filament": [],
            },
        )
        svc_mock = MagicMock()
        svc_mock.list_bundled_profiles = AsyncMock(
            return_value={
                "printer": [{"name": "Fresh", "base_id": None}],
                "process": [],
                "filament": [],
            }
        )
        svc_mock.__aenter__ = AsyncMock(return_value=svc_mock)
        svc_mock.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc_mock),
        ):
            slots = await sp._fetch_bundled_presets(MagicMock(), refresh=True)
        svc_mock.list_bundled_profiles.assert_awaited_once()
        assert [p.name for p in slots["printer"]] == ["Fresh"]
        # The fresh result must also be written back to the cache so a
        # subsequent normal (non-refresh) call doesn't re-hit the sidecar.
        assert sp._bundled_cache is not None
        assert [p.name for p in sp._bundled_cache[1]["printer"]] == ["Fresh"]


class TestResolveSlicerApiUrl:
    """`_resolve_slicer_api_url` must respect the user's `preferred_slicer`
    setting just like the slice route does. The bundled-listing fetch
    used to be hardcoded to OrcaSlicer's URL, which left the Standard
    tier permanently empty for BambuStudio installs."""

    @pytest.mark.asyncio
    async def test_bambu_studio_preference_uses_bambu_url(self):
        """When the user prefers Bambu Studio, the listing fetch must hit
        the bambu-studio-api sidecar (port 3001 by default), not orca's
        port 3003."""

        async def fake_get_setting(_db, key):
            return {
                "preferred_slicer": "bambu_studio",
                "bambu_studio_api_url": "http://bambu-studio-api:3000",
            }.get(key)

        with patch(
            "backend.app.api.routes.settings.get_setting",
            new=fake_get_setting,
        ):
            url = await sp._resolve_slicer_api_url(MagicMock())
        assert url == "http://bambu-studio-api:3000"

    @pytest.mark.asyncio
    async def test_orcaslicer_preference_uses_orca_url(self):
        async def fake_get_setting(_db, key):
            return {
                "preferred_slicer": "orcaslicer",
                "orcaslicer_api_url": "http://orca-slicer-api:3000",
            }.get(key)

        with patch(
            "backend.app.api.routes.settings.get_setting",
            new=fake_get_setting,
        ):
            url = await sp._resolve_slicer_api_url(MagicMock())
        assert url == "http://orca-slicer-api:3000"

    @pytest.mark.asyncio
    async def test_default_preference_is_bambu_studio(self):
        """Empty preferred_slicer → bambu_studio (matches the slice route's
        default at library.py:_run_slicer_with_fallback)."""

        async def fake_get_setting(_db, key):
            return {
                # preferred_slicer not set
                "bambu_studio_api_url": "http://bambu-default:3000",
            }.get(key)

        with patch(
            "backend.app.api.routes.settings.get_setting",
            new=fake_get_setting,
        ):
            url = await sp._resolve_slicer_api_url(MagicMock())
        assert url == "http://bambu-default:3000"

    @pytest.mark.asyncio
    async def test_unknown_preference_returns_none(self):
        """An unrecognised preferred_slicer value (e.g. set out-of-band by
        a stale migration) returns None so the modal degrades to "no
        Standard tier" rather than crashing — the slice route raises 400
        in this case but the listing is informational, so be lenient."""

        async def fake_get_setting(_db, key):
            return {"preferred_slicer": "prusaslicer"}.get(key)

        with patch(
            "backend.app.api.routes.settings.get_setting",
            new=fake_get_setting,
        ):
            url = await sp._resolve_slicer_api_url(MagicMock())
        assert url is None


class TestParseCompatiblePrinters:
    """``compatible_printers`` exposed for local process / filament presets so
    the SliceModal can filter the dropdowns by the selected printer (#1325)."""

    def test_parses_json_array(self):
        raw = '["Bambu Lab X1 Carbon 0.4 nozzle", "Bambu Lab X1 0.4 nozzle"]'
        assert sp._parse_compatible_printers(raw) == [
            "Bambu Lab X1 Carbon 0.4 nozzle",
            "Bambu Lab X1 0.4 nozzle",
        ]

    def test_none_and_empty_return_none(self):
        assert sp._parse_compatible_printers(None) is None
        assert sp._parse_compatible_printers("") is None
        assert sp._parse_compatible_printers("[]") is None

    def test_malformed_json_returns_none(self):
        assert sp._parse_compatible_printers("not json") is None
        # A JSON value that isn't an array is treated as absent, not an error.
        assert sp._parse_compatible_printers('"a string"') is None

    def test_drops_non_string_and_blank_entries(self):
        assert sp._parse_compatible_printers('["X1C", 5, "", "  ", "A1"]') == [
            "X1C",
            "A1",
        ]


class TestListPrinterModels:
    """``GET /slicer/printer-models`` exposes ``PRINTER_MODEL_MAP`` so the
    frontend doesn't duplicate the Bambu model registry (#1325 follow-up)."""

    def test_returns_canonical_printer_model_map(self):
        from backend.app.utils.printer_models import PRINTER_MODEL_MAP

        result = sp.list_printer_models()
        # Same shape - mapping from "Bambu Lab <model>" to short code.
        assert result == PRINTER_MODEL_MAP
        # Spot-check a few entries: the SliceModal name-fallback (#1325)
        # specifically depends on these resolving.
        assert result["Bambu Lab X1 Carbon"] == "X1C"
        assert result["Bambu Lab P2S"] == "P2S"
        assert result["Bambu Lab A1 mini"] == "A1 Mini"
        assert result["Bambu Lab H2D Pro"] == "H2D Pro"

    def test_returns_a_copy_not_the_module_dict(self):
        # A response handler must never hand out the live module-level dict —
        # accidental mutation by middleware / serialisers would silently
        # corrupt the registry for every subsequent request.
        from backend.app.utils.printer_models import PRINTER_MODEL_MAP

        result = sp.list_printer_models()
        assert result is not PRINTER_MODEL_MAP


class TestNoUserWithAuthOn:
    """The sign-in stored without a user is the auth-off install's, and may be
    left over after auth was turned on. A caller with no user while auth is
    on (an API key without Allow Cloud Access) must not list its presets."""

    @pytest.mark.asyncio
    async def test_bambu_cloud(self):
        sp._cloud_cache.clear()
        with (
            patch.object(sp, "is_auth_enabled", AsyncMock(return_value=True)),
            patch.object(sp, "get_stored_token", AsyncMock(return_value=("global-tok", None, None))) as get_tok,
        ):
            slots, status = await sp._fetch_cloud_presets(MagicMock(), None)
        assert status == "not_authenticated"
        assert slots == {"printer": [], "process": [], "filament": []}
        get_tok.assert_not_called()

    @pytest.mark.asyncio
    async def test_orca_cloud(self):
        sp._orca_cloud_cache.clear()
        with (
            patch.object(sp, "is_auth_enabled", AsyncMock(return_value=True)),
            patch.object(sp, "_load_orca_credentials", AsyncMock()) as load,
        ):
            slots, status = await sp._fetch_orca_cloud_presets(MagicMock(), None)
        assert status == "not_authenticated"
        assert slots == {"printer": [], "process": [], "filament": []}
        load.assert_not_called()


class TestLocalPrinterInherits:
    """#3250: local printer presets report the preset they were saved from."""

    @pytest.mark.asyncio
    async def test_local_printer_preset_carries_inherits(self, db_session):
        from backend.app.models.local_preset import LocalPreset

        db_session.add_all(
            [
                LocalPreset(
                    name="Bambu Lab H2D 0.4 nozzle - Skew",
                    preset_type="printer",
                    source="orcaslicer",
                    setting="{}",
                    inherits="Bambu Lab H2D 0.4 nozzle",
                ),
                LocalPreset(name="Plain", preset_type="printer", source="orcaslicer", setting="{}", inherits="  "),
                LocalPreset(
                    name="0.20mm Mine",
                    preset_type="process",
                    source="orcaslicer",
                    setting="{}",
                    inherits="0.20mm Standard @BBL H2D",
                ),
            ]
        )
        await db_session.commit()

        slots = await sp._fetch_local_presets(db_session)

        by_name = {p.name: p for p in slots["printer"]}
        assert by_name["Bambu Lab H2D 0.4 nozzle - Skew"].inherits == "Bambu Lab H2D 0.4 nozzle"
        assert by_name["Plain"].inherits is None
        # Only printer presets need it; a process is matched by its own list.
        assert slots["process"][0].inherits is None

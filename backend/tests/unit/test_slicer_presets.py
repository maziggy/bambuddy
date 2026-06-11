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


class TestBundleRoutes:
    """Route-level coverage for the bundle proxy endpoints. Each route
    resolves the sidecar URL via _resolve_slicer_api_url, then proxies the
    operation through SlicerApiService. We mock both pieces so we can pin
    the HTTP-status mapping (sidecar input error → 400, BundleNotFoundError
    → 404, unreachable → 503) without spinning up a sidecar.
    """

    SAMPLE_SUMMARY = sp.BundleSummary(
        id="abc123def456abcd",
        printer_preset_name="# Bambu Lab H2D 0.4 nozzle",
        printer=["# Bambu Lab H2D 0.4 nozzle"],
        process=["# 0.20mm Standard @BBL H2D"],
        filament=["# Bambu PLA Basic @BBL H2D"],
        version="02.06.00.50",
    )

    def _patched_service(self, **methods) -> MagicMock:
        """Build a SlicerApiService mock that supports `async with` and
        exposes the bundle methods via AsyncMock per the override dict."""
        svc = MagicMock()
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)
        for name, mock in methods.items():
            setattr(svc, name, mock)
        return svc

    @pytest.mark.asyncio
    async def test_import_bundle_happy_path(self):
        from io import BytesIO

        from fastapi import UploadFile

        svc = self._patched_service(
            import_bundle=AsyncMock(return_value=self.SAMPLE_SUMMARY),
        )
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
        ):
            file = UploadFile(filename="H2D.bbscfg", file=BytesIO(b"PK\x03\x04"))
            result = await sp.import_slicer_bundle(file=file, db=MagicMock(), _=None)
        assert result["id"] == "abc123def456abcd"
        assert result["printer"] == ["# Bambu Lab H2D 0.4 nozzle"]
        svc.import_bundle.assert_awaited_once()
        kwargs = svc.import_bundle.await_args.kwargs
        assert kwargs["filename"] == "H2D.bbscfg"

    @pytest.mark.asyncio
    async def test_import_bundle_no_sidecar_returns_503(self):
        from io import BytesIO

        from fastapi import HTTPException, UploadFile

        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value=None)),
            pytest.raises(HTTPException) as exc,
        ):
            await sp.import_slicer_bundle(
                file=UploadFile(filename="x.bbscfg", file=BytesIO(b"x")),
                db=MagicMock(),
                _=None,
            )
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_import_bundle_empty_file_returns_400(self):
        from io import BytesIO

        from fastapi import HTTPException, UploadFile

        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            pytest.raises(HTTPException) as exc,
        ):
            await sp.import_slicer_bundle(
                file=UploadFile(filename="x.bbscfg", file=BytesIO(b"")),
                db=MagicMock(),
                _=None,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_import_bundle_sidecar_400_passes_through(self, caplog):
        from io import BytesIO

        from fastapi import HTTPException, UploadFile

        svc = self._patched_service(
            import_bundle=AsyncMock(side_effect=sp.SlicerInputError("bad zip")),
        )
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
            caplog.at_level("WARNING", logger="backend.app.api.routes.slicer_presets"),
            pytest.raises(HTTPException) as exc,
        ):
            await sp.import_slicer_bundle(
                file=UploadFile(filename="x.bbscfg", file=BytesIO(b"x")),
                db=MagicMock(),
                _=None,
            )
        assert exc.value.status_code == 400
        # #1312: the sidecar's reject reason MUST land in the log so it
        # ends up in support bundles without us having to ask reporters
        # to copy the FE toast.
        assert any("bad zip" in r.message for r in caplog.records)
        assert any("x.bbscfg" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_import_bundle_sidecar_unreachable_returns_503(self):
        from io import BytesIO

        from fastapi import HTTPException, UploadFile

        svc = self._patched_service(
            import_bundle=AsyncMock(side_effect=sp.SlicerApiUnavailableError("offline")),
        )
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
            pytest.raises(HTTPException) as exc,
        ):
            await sp.import_slicer_bundle(
                file=UploadFile(filename="x.bbscfg", file=BytesIO(b"x")),
                db=MagicMock(),
                _=None,
            )
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_list_bundles_happy_path(self):
        svc = self._patched_service(
            list_bundles=AsyncMock(return_value=[self.SAMPLE_SUMMARY]),
        )
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
        ):
            result = await sp.list_slicer_bundles(db=MagicMock(), _=None)
        assert len(result) == 1
        assert result[0]["id"] == "abc123def456abcd"

    @pytest.mark.asyncio
    async def test_list_bundles_no_sidecar_returns_empty(self):
        # Differs from import: list returns [] instead of 503 so the
        # SliceModal still renders cleanly when no sidecar is configured
        # (matches bundled-tier behaviour above).
        with patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value=None)):
            result = await sp.list_slicer_bundles(db=MagicMock(), _=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_list_bundles_sidecar_unreachable_returns_503(self):
        from fastapi import HTTPException

        svc = self._patched_service(
            list_bundles=AsyncMock(side_effect=sp.SlicerApiUnavailableError("offline")),
        )
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
            pytest.raises(HTTPException) as exc,
        ):
            await sp.list_slicer_bundles(db=MagicMock(), _=None)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_get_bundle_404(self):
        from fastapi import HTTPException

        svc = self._patched_service(
            get_bundle=AsyncMock(side_effect=sp.BundleNotFoundError("not found")),
        )
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
            pytest.raises(HTTPException) as exc,
        ):
            await sp.get_slicer_bundle("missing", db=MagicMock(), _=None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_bundle_204(self):
        # delete returns None on success; FastAPI sends 204 because the route
        # declares status_code=204.
        svc = self._patched_service(delete_bundle=AsyncMock(return_value=None))
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
        ):
            result = await sp.delete_slicer_bundle("abc", db=MagicMock(), _=None)
        assert result is None
        svc.delete_bundle.assert_awaited_once_with("abc")

    @pytest.mark.asyncio
    async def test_delete_bundle_404(self):
        from fastapi import HTTPException

        svc = self._patched_service(
            delete_bundle=AsyncMock(side_effect=sp.BundleNotFoundError("not found")),
        )
        with (
            patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
            patch.object(sp, "SlicerApiService", return_value=svc),
            pytest.raises(HTTPException) as exc,
        ):
            await sp.delete_slicer_bundle("missing", db=MagicMock(), _=None)
        assert exc.value.status_code == 404


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

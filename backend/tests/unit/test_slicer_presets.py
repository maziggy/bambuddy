"""Tests for the unified slicer-presets endpoint helpers.

The endpoint stitches together three preset sources (cloud / local /
standard) with name-based dedup. These tests pin the dedup logic, the
cloud-status mapping, and the per-user / sidecar caches at the
helper level — full HTTP integration is covered by the routes test.
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


class TestDedupeByName:
    """Cloud > local > standard, by ``name``, order preserved within tier."""

    def test_cloud_wins_over_local_and_standard(self):
        cloud = _slot([("cid1", "Bambu PLA Basic", "cloud")])
        local = _slot([("lid1", "Bambu PLA Basic", "local")])
        standard = _slot([("Bambu PLA Basic", "Bambu PLA Basic", "standard")])

        c, l_, s = sp._dedupe_by_name(cloud, local, standard)

        assert [p.source for p in c["printer"]] == ["cloud"]
        assert l_["printer"] == []
        assert s["printer"] == []

    def test_local_filtered_only_when_present_in_cloud(self):
        cloud = _slot([("cid1", "Custom PLA", "cloud")])
        local = _slot(
            [
                ("lid1", "Custom PLA", "local"),  # filtered (in cloud)
                ("lid2", "My Workhorse PLA", "local"),  # kept
            ]
        )
        standard = _slot([])

        _c, l_, _s = sp._dedupe_by_name(cloud, local, standard)
        assert [p.name for p in l_["printer"]] == ["My Workhorse PLA"]

    def test_standard_filtered_against_both_higher_tiers(self):
        cloud = _slot([("c1", "A", "cloud")])
        local = _slot([("l1", "B", "local")])
        standard = _slot(
            [
                ("A", "A", "standard"),  # filtered (in cloud)
                ("B", "B", "standard"),  # filtered (in local)
                ("C", "C", "standard"),  # kept
            ]
        )

        _c, _l, s = sp._dedupe_by_name(cloud, local, standard)
        assert [p.name for p in s["printer"]] == ["C"]

    def test_preserves_order_within_tier(self):
        """A tier's input order must be preserved in its output — nothing in
        the dedupe pass should sort, reverse, or otherwise reorder entries."""
        cloud = _slot(
            [
                ("c1", "Z-First", "cloud"),
                ("c2", "A-Second", "cloud"),
                ("c3", "M-Third", "cloud"),
            ]
        )
        c, _l, _s = sp._dedupe_by_name(cloud, _slot([]), _slot([]))
        assert [p.name for p in c["printer"]] == ["Z-First", "A-Second", "M-Third"]

    def test_dedupe_is_per_slot(self):
        """A name colliding across DIFFERENT slots must NOT cross-filter —
        a "Custom" filament shouldn't hide a "Custom" printer."""
        cloud = {
            "printer": [],
            "process": [],
            "filament": [UnifiedPreset(id="cf1", name="Custom", source="cloud")],
        }
        local = {
            "printer": [UnifiedPreset(id="lp1", name="Custom", source="local")],
            "process": [],
            "filament": [],
        }
        _c, l_, _s = sp._dedupe_by_name(cloud, local, _slot([]))
        # The filament-tier collision must NOT remove the printer-tier "Custom".
        assert [p.name for p in l_["printer"]] == ["Custom"]


def _user_with_cloud_auth(user_id: int = 1) -> MagicMock:
    """Construct a mock User that passes the CLOUD_AUTH permission check.

    `MagicMock` defaults `.has_permission(...)` to a truthy MagicMock object,
    which would coincidentally pass the gate — but explicit is better than
    accidental. Setting `.return_value = True` documents the intent."""
    user = MagicMock(id=user_id)
    user.has_permission = MagicMock(return_value=True)
    return user


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
    async def test_import_bundle_sidecar_400_passes_through(self):
        from io import BytesIO

        from fastapi import HTTPException, UploadFile

        svc = self._patched_service(
            import_bundle=AsyncMock(side_effect=sp.SlicerInputError("bad zip")),
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
        assert exc.value.status_code == 400

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

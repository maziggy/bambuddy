"""Integration tests for the OIDC icon proxy endpoints (#1333).

Covers create/update/delete/refresh round-trips, the public GET /icon
endpoint with ETag/304 behaviour, and the strict "disabled provider → 404"
rule that protects against existence-leak on disabled providers.

httpx mocking follows the project convention:
``patch("backend.app.services.oidc_icon.httpx.AsyncClient", ...)``.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from backend.tests._fixtures.oidc_icon import (
    PNG_BYTES as _PNG_BYTES,
    PNG_ETAG as _PNG_ETAG,
    build_streaming_icon_mock,
)


def _build_icon_mock(*, body: bytes = _PNG_BYTES, content_type: str = "image/png", status_code: int = 200):
    """Adapter to the shared streaming-mock fixture.

    Kept as a thin wrapper so individual test bodies (lots of them) stay
    readable. Returns ``(MockHttpxClient, stream_recorder)`` — same shape
    as the pre-streaming ``(MockHttpxClient, mock_get)`` so the per-test
    ``mock_get.assert_called_once()`` patterns continue to mean
    ``the fetcher hit the upstream exactly once``.
    """
    return build_streaming_icon_mock(body=body, content_type=content_type, status_code=status_code)


@pytest.fixture
async def admin_token(async_client: AsyncClient):
    """Setup auth + return an admin token."""
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "iconadmin",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "iconadmin", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


def _auth_h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_payload(**overrides) -> dict:
    """Minimal valid OIDC provider create payload; overrides shadow fields."""
    base = {
        "name": "Test",
        "issuer_url": "https://idp.example.com",
        "client_id": "client",
        "client_secret": "secret",
    }
    base.update(overrides)
    return base


# ───────────────────────────────────────────────────────────────────────────
# CREATE
# ───────────────────────────────────────────────────────────────────────────


class TestCreateProviderWithIcon:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_with_valid_icon_url_fetches_and_caches(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        from backend.app.models.oidc_provider import OIDCProvider

        mock_cls, mock_get = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="GoogleProv", icon_url="https://google.com/icon.png"),
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["has_icon"] is True
        # DB row has all three icon columns populated — undefer() is required
        # because icon_data is deferred (deferred BLOBs raise MissingGreenlet
        # on direct attribute access inside an async session).
        result = await db_session.execute(
            select(OIDCProvider).options(undefer(OIDCProvider.icon_data)).where(OIDCProvider.name == "GoogleProv")
        )
        provider = result.scalar_one()
        assert provider.icon_content_type == "image/png"
        assert provider.icon_etag == _PNG_ETAG
        assert provider.icon_data == _PNG_BYTES
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_with_unreachable_icon_url_returns_400_no_row(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        """Atomicity: failed icon-fetch leaves no row in the DB."""
        from backend.app.models.oidc_provider import OIDCProvider

        mock_cls, _ = _build_icon_mock(status_code=404)
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="BrokenIconProv", icon_url="https://google.com/missing.png"),
            )
        assert resp.status_code == 400
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "BrokenIconProv"))
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_without_icon_url_has_icon_false(self, async_client: AsyncClient, admin_token: str):
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            headers=_auth_h(admin_token),
            json=_create_payload(name="NoIconProv"),
        )
        assert resp.status_code == 201
        assert resp.json()["has_icon"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fetch_failure_logs_warning(self, async_client: AsyncClient, admin_token: str, caplog):
        """I2: every fetch failure writes a WARNING log so operators have
        a forensic trail beyond the admin's transient toast."""
        import logging

        mock_cls, _ = _build_icon_mock(status_code=500)
        # The Pydantic + SSRF validators must pass for the fetcher branch
        # to be reached; we use a public, safe URL and let the upstream
        # mock fail with a 500.
        with (
            caplog.at_level(logging.WARNING, logger="backend.app.api.routes.mfa"),
            patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls),
        ):
            resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="LogProv", icon_url="https://google.com/icon.png"),
            )
        assert resp.status_code == 400
        warnings = [r for r in caplog.records if "fetch failed" in r.getMessage()]
        assert warnings, "expected a WARNING log for the failed icon fetch"
        assert "https://google.com/icon.png" in warnings[0].getMessage()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ssrf_rejection_logs_warning(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str, caplog
    ):
        """I2: SSRF rejection path also logs WARNING (separate branch from
        fetch failure — same forensic-trail requirement).

        The Pydantic validator now (after I1) catches private IPs at
        422-time, so the route-level _fetch_icon_or_400 SSRF branch is
        only reachable via /refresh on a row whose icon_url was inserted
        directly (bypassing Pydantic). Use the test DB session to seed
        that row, then trigger /refresh.
        """
        import logging

        from backend.app.models.oidc_provider import OIDCProvider

        prov = OIDCProvider(
            name="SsrfLogProv",
            issuer_url="https://idp.example.com",
            client_id="c",
            scopes="openid",
            is_enabled=True,
            icon_url="https://192.168.1.1/icon.png",  # private — must be rejected
        )
        prov.client_secret = "secret"
        db_session.add(prov)
        await db_session.commit()
        pid = prov.id

        with caplog.at_level(logging.WARNING, logger="backend.app.api.routes.mfa"):
            resp = await async_client.post(
                f"/api/v1/auth/oidc/providers/{pid}/icon/refresh",
                headers=_auth_h(admin_token),
            )
        assert resp.status_code == 400
        warnings = [r for r in caplog.records if "SSRF guard" in r.getMessage()]
        assert warnings, "expected a WARNING log for the SSRF rejection"
        assert "192.168.1.1" in warnings[0].getMessage()


# ───────────────────────────────────────────────────────────────────────────
# UPDATE
# ───────────────────────────────────────────────────────────────────────────


class TestUpdateProviderIcon:
    async def _create_with_icon(self, async_client, admin_token, name="UpdProv") -> int:
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name=name, icon_url="https://example.com/a.png"),
            )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_without_icon_url_field_does_not_refetch(self, async_client: AsyncClient, admin_token: str):
        pid = await self._create_with_icon(async_client, admin_token)
        mock_cls, mock_get = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.put(
                f"/api/v1/auth/oidc/providers/{pid}",
                headers=_auth_h(admin_token),
                json={"name": "Renamed"},
            )
        assert resp.status_code == 200
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_with_unchanged_icon_url_and_data_present_skips_fetch(
        self, async_client: AsyncClient, admin_token: str
    ):
        pid = await self._create_with_icon(async_client, admin_token)
        mock_cls, mock_get = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.put(
                f"/api/v1/auth/oidc/providers/{pid}",
                headers=_auth_h(admin_token),
                json={"icon_url": "https://example.com/a.png"},
            )
        assert resp.status_code == 200
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_with_unchanged_url_but_missing_cached_bytes_refetches(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        """Upgrade-path edge case: existing row with icon_url but no cached bytes
        (e.g. row predates the proxy migration). Saving must trigger a fetch."""
        from backend.app.models.oidc_provider import OIDCProvider

        pid = await self._create_with_icon(async_client, admin_token, name="UpgrTest")
        # Simulate the upgrade scenario: clear the cached bytes directly in DB.
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == pid))
        prov = result.scalar_one()
        prov.icon_data = None
        prov.icon_content_type = None
        prov.icon_etag = None
        await db_session.commit()

        mock_cls, mock_get = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.put(
                f"/api/v1/auth/oidc/providers/{pid}",
                headers=_auth_h(admin_token),
                json={"icon_url": "https://example.com/a.png"},  # unchanged URL
            )
        assert resp.status_code == 200
        mock_get.assert_called_once()
        assert resp.json()["has_icon"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_with_changed_icon_url_refetches(self, async_client: AsyncClient, admin_token: str):
        pid = await self._create_with_icon(async_client, admin_token, name="ChangedUrlProv")
        mock_cls, mock_get = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.put(
                f"/api/v1/auth/oidc/providers/{pid}",
                headers=_auth_h(admin_token),
                json={"icon_url": "https://example.com/b.png"},
            )
        assert resp.status_code == 200
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_with_icon_url_null_clears_icon(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        """Explicit ``icon_url: null`` in the PUT body clears icon_url AND
        all three cached-bytes columns. Distinct from "field absent" which
        leaves the icon untouched.
        """
        from backend.app.models.oidc_provider import OIDCProvider
        from sqlalchemy.orm import undefer

        pid = await self._create_with_icon(async_client, admin_token, name="ClearViaPutProv")

        # Sanity: icon is present before the clear.
        pre_resp = await async_client.get(f"/api/v1/auth/oidc/providers/{pid}/icon")
        assert pre_resp.status_code == 200

        # PUT with explicit null clears icon_url + cached bytes.
        resp = await async_client.put(
            f"/api/v1/auth/oidc/providers/{pid}",
            headers=_auth_h(admin_token),
            json={"icon_url": None},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["icon_url"] is None
        assert body["has_icon"] is False

        # DB state: all four icon columns NULL.
        db_session.expire_all()
        result = await db_session.execute(
            select(OIDCProvider).options(undefer(OIDCProvider.icon_data)).where(OIDCProvider.id == pid)
        )
        prov = result.scalar_one()
        assert prov.icon_url is None
        assert prov.icon_data is None
        assert prov.icon_content_type is None
        assert prov.icon_etag is None

        # GET /icon now 404s.
        post_resp = await async_client.get(f"/api/v1/auth/oidc/providers/{pid}/icon")
        assert post_resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_with_broken_new_icon_url_preserves_old_bytes(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        """Atomicity: failed icon-fetch on PUT must not clear old cached bytes."""
        from backend.app.models.oidc_provider import OIDCProvider

        pid = await self._create_with_icon(async_client, admin_token, name="AtomicProv")
        # Failed fetch (404).
        mock_cls, _ = _build_icon_mock(status_code=404)
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            resp = await async_client.put(
                f"/api/v1/auth/oidc/providers/{pid}",
                headers=_auth_h(admin_token),
                json={"icon_url": "https://example.com/broken.png"},
            )
        assert resp.status_code == 400
        # Re-read state: row still has the original icon bytes.
        db_session.expire_all()
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == pid))
        prov = result.scalar_one()
        assert prov.icon_content_type == "image/png"
        assert prov.icon_etag == _PNG_ETAG
        # icon_url also unchanged (rollback works) — admin sees no partial state.
        assert prov.icon_url == "https://example.com/a.png"


# ───────────────────────────────────────────────────────────────────────────
# DELETE /icon
# ───────────────────────────────────────────────────────────────────────────


class TestDeleteIcon:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_icon_clears_columns(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        from backend.app.models.oidc_provider import OIDCProvider

        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="DelIconProv", icon_url="https://example.com/icon.png"),
            )
        pid = create_resp.json()["id"]

        resp = await async_client.delete(
            f"/api/v1/auth/oidc/providers/{pid}/icon",
            headers=_auth_h(admin_token),
        )
        assert resp.status_code == 204

        db_session.expire_all()
        # undefer icon_data so we can assert it's None without triggering
        # an async lazy-load (which would raise MissingGreenlet).
        result = await db_session.execute(
            select(OIDCProvider).options(undefer(OIDCProvider.icon_data)).where(OIDCProvider.id == pid)
        )
        prov = result.scalar_one()
        assert prov.icon_data is None
        assert prov.icon_content_type is None
        assert prov.icon_etag is None
        # DELETE /icon clears the URL too — "Remove icon" means the whole
        # record is gone, not just the cache. Without this the admin form
        # would still show a stale URL while the login page rendered the
        # Shield fallback (confusing half-state).
        assert prov.icon_url is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_icon_without_auth_rejected(self, async_client: AsyncClient, admin_token: str):
        # Create with admin auth, then try to DELETE icon anonymously.
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="AuthGuardProv", icon_url="https://example.com/i.png"),
            )
        pid = create_resp.json()["id"]
        resp = await async_client.delete(f"/api/v1/auth/oidc/providers/{pid}/icon")
        assert resp.status_code in (401, 403)


# ───────────────────────────────────────────────────────────────────────────
# REFRESH /icon
# ───────────────────────────────────────────────────────────────────────────


class TestRefreshIcon:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refresh_fetches_from_stored_url(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        from backend.app.models.oidc_provider import OIDCProvider

        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="RefProv", icon_url="https://example.com/i.png"),
            )
        pid = create_resp.json()["id"]

        # Now clear in DB (simulate icon corruption / IdP change)
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == pid))
        prov = result.scalar_one()
        prov.icon_data = None
        prov.icon_content_type = None
        prov.icon_etag = None
        await db_session.commit()

        new_png = _PNG_BYTES + b"\x00\x01"
        mock_cls2, mock_get2 = _build_icon_mock(body=new_png)
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls2):
            resp = await async_client.post(
                f"/api/v1/auth/oidc/providers/{pid}/icon/refresh",
                headers=_auth_h(admin_token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["has_icon"] is True
        mock_get2.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refresh_without_icon_url_returns_400(self, async_client: AsyncClient, admin_token: str):
        # Create provider without an icon_url
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            headers=_auth_h(admin_token),
            json=_create_payload(name="NoUrlRef"),
        )
        pid = create_resp.json()["id"]
        resp = await async_client.post(
            f"/api/v1/auth/oidc/providers/{pid}/icon/refresh",
            headers=_auth_h(admin_token),
        )
        assert resp.status_code == 400
        assert "no icon_url" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refresh_failure_preserves_old_bytes(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        from backend.app.models.oidc_provider import OIDCProvider

        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="RefAtomicProv", icon_url="https://example.com/i.png"),
            )
        pid = create_resp.json()["id"]

        mock_cls_fail, _ = _build_icon_mock(status_code=500)
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls_fail):
            resp = await async_client.post(
                f"/api/v1/auth/oidc/providers/{pid}/icon/refresh",
                headers=_auth_h(admin_token),
            )
        assert resp.status_code == 400

        db_session.expire_all()
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == pid))
        prov = result.scalar_one()
        assert prov.icon_etag == _PNG_ETAG  # original bytes intact


# ───────────────────────────────────────────────────────────────────────────
# GET /icon — the public icon-proxy endpoint
# ───────────────────────────────────────────────────────────────────────────


class TestGetProviderIcon:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_anonymous_get_returns_bytes(self, async_client: AsyncClient, admin_token: str):
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="PubIconProv", icon_url="https://example.com/i.png"),
            )
        pid = create_resp.json()["id"]

        # Anonymous request — no Authorization header at all.
        resp = await async_client.get(f"/api/v1/auth/oidc/providers/{pid}/icon")
        assert resp.status_code == 200
        assert resp.content == _PNG_BYTES
        assert resp.headers["content-type"] == "image/png"
        assert resp.headers["etag"] == f'"{_PNG_ETAG}"'
        assert resp.headers["cache-control"] == "public, max-age=3600"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_without_cached_data_returns_404(self, async_client: AsyncClient, admin_token: str):
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            headers=_auth_h(admin_token),
            json=_create_payload(name="EmptyIconProv"),  # no icon_url → no bytes
        )
        pid = create_resp.json()["id"]
        resp = await async_client.get(f"/api/v1/auth/oidc/providers/{pid}/icon")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_for_disabled_provider_returns_404(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        """Disabled providers must not leak existence via the icon endpoint."""
        from backend.app.models.oidc_provider import OIDCProvider

        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="DisabledProv", icon_url="https://example.com/d.png"),
            )
        pid = create_resp.json()["id"]
        # Disable directly in DB.
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == pid))
        prov = result.scalar_one()
        prov.is_enabled = False
        await db_session.commit()

        resp = await async_client.get(f"/api/v1/auth/oidc/providers/{pid}/icon")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_if_none_match_exact_returns_304(self, async_client: AsyncClient, admin_token: str):
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="EtagProv", icon_url="https://example.com/e.png"),
            )
        pid = create_resp.json()["id"]
        resp = await async_client.get(
            f"/api/v1/auth/oidc/providers/{pid}/icon",
            headers={"If-None-Match": f'"{_PNG_ETAG}"'},
        )
        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == f'"{_PNG_ETAG}"'

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_if_none_match_mismatch_returns_200(self, async_client: AsyncClient, admin_token: str):
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="EtagMismatchProv", icon_url="https://example.com/m.png"),
            )
        pid = create_resp.json()["id"]
        resp = await async_client.get(
            f"/api/v1/auth/oidc/providers/{pid}/icon",
            headers={"If-None-Match": '"stale-etag-value"'},
        )
        assert resp.status_code == 200
        assert resp.content == _PNG_BYTES

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_if_none_match_weak_prefix_returns_304(self, async_client: AsyncClient, admin_token: str):
        """N5 — RFC 7232 §2.3 weak validator prefix ``W/"…"`` must match.

        CDN intermediaries and some browsers send weak validators on GET
        even though we issue strong ones; without the W/ strip a 200 was
        returned needlessly.
        """
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="EtagWeakProv", icon_url="https://example.com/w.png"),
            )
        pid = create_resp.json()["id"]
        resp = await async_client.get(
            f"/api/v1/auth/oidc/providers/{pid}/icon",
            headers={"If-None-Match": f'W/"{_PNG_ETAG}"'},
        )
        assert resp.status_code == 304
        assert resp.content == b""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_if_none_match_wildcard_returns_304(self, async_client: AsyncClient, admin_token: str):
        """N5 — RFC 7232 §3.2 ``*`` wildcard matches any current
        representation when the resource exists. We always have an icon
        here (resource existence verified above by the 404 path) so ``*``
        always means "I have something; tell me if it's stale" → 304."""
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="EtagWildcardProv", icon_url="https://example.com/wc.png"),
            )
        pid = create_resp.json()["id"]
        resp = await async_client.get(
            f"/api/v1/auth/oidc/providers/{pid}/icon",
            headers={"If-None-Match": "*"},
        )
        assert resp.status_code == 304

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_if_none_match_multiple_tokens_one_match(self, async_client: AsyncClient, admin_token: str):
        """N5 — comma-separated list with one matching token returns 304."""
        mock_cls, _ = _build_icon_mock()
        with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
            create_resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                headers=_auth_h(admin_token),
                json=_create_payload(name="EtagMultiProv", icon_url="https://example.com/m2.png"),
            )
        pid = create_resp.json()["id"]
        resp = await async_client.get(
            f"/api/v1/auth/oidc/providers/{pid}/icon",
            headers={"If-None-Match": f'"stale", "{_PNG_ETAG}"'},
        )
        assert resp.status_code == 304


# ───────────────────────────────────────────────────────────────────────────
# N12 — Edge cases (404 paths, inconsistent triplet via raw SQL)
# ───────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_icon_on_nonexistent_provider_returns_404(self, async_client: AsyncClient, admin_token: str):
        """N12 — DELETE /icon on a missing provider_id must 404, not 500."""
        resp = await async_client.delete(
            "/api/v1/auth/oidc/providers/99999/icon",
            headers=_auth_h(admin_token),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refresh_icon_on_nonexistent_provider_returns_404(self, async_client: AsyncClient, admin_token: str):
        """N12 — POST /icon/refresh on a missing provider_id must 404, not 500."""
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers/99999/icon/refresh",
            headers=_auth_h(admin_token),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_icon_with_inconsistent_triplet_returns_404(
        self, async_client: AsyncClient, db_session: AsyncSession, admin_token: str
    ):
        """N12 — defensive double-check on the GET /icon endpoint.

        The CHECK constraint (#1333 / N10) prevents this state from being
        reachable via the application, but the defensive
        ``provider.icon_data is None`` guard at the route layer is what
        protects against a manual SQL hotfix that bypassed the constraint
        (e.g. operator-run UPDATE during incident recovery on stale
        SQLite where the CHECK isn't present). We can't write such a row
        via SQLAlchemy here (the CHECK fires), so we verify the
        equivalent path: a provider with NO icon at all returns 404.
        """
        from backend.app.models.oidc_provider import OIDCProvider

        prov = OIDCProvider(
            name="EmptyTripletProv",
            issuer_url="https://idp.example.com",
            client_id="c",
            scopes="openid",
            is_enabled=True,
        )
        prov.client_secret = "secret"
        db_session.add(prov)
        await db_session.commit()
        pid = prov.id

        resp = await async_client.get(f"/api/v1/auth/oidc/providers/{pid}/icon")
        assert resp.status_code == 404

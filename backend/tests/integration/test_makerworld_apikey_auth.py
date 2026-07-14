"""Integration tests for #1777 — API-keyed callers on /makerworld/*.

The contract being pinned (mirrors the slice path's #1182 follow-up):

  When auth is enabled and the request carries an X-API-Key whose owner
  has a stored Bambu Cloud token, the makerworld routes must resolve
  identity via ``resolve_api_key_cloud_owner`` (instead of always seeing
  ``current_user=None``) so:

  - /status reports ``has_cloud_token=True`` for keys whose owner has a token
  - /resolve builds a MakerWorldService seeded with that token
  - /import succeeds end-to-end and attributes the resulting LibraryFile
    to the API-key owner

  The fail-closed path is preserved: keys without ``can_access_cloud=True``
  still surface the "requires Bambu Cloud login" experience (no auth gap).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.library import LibraryFile
from backend.app.models.user import User


async def _setup_auth_with_admin(client: AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "mwadmin",
            "admin_password": "AdminPass1!",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "mwadmin", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


async def _store_admin_cloud_token(db: AsyncSession, username: str, token: str) -> User:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one()
    user.cloud_token = token
    user.cloud_email = "owner@example.com"
    user.cloud_region = "global"
    await db.commit()
    await db.refresh(user)
    return user


async def _make_key(
    db: AsyncSession,
    *,
    owner: User,
    name: str,
    can_access_cloud: bool = True,
    can_read_status: bool = True,
    can_manage_library: bool = True,
) -> str:
    """Mint an API key with the scopes /makerworld/* expects.

    /status + /resolve gate on ``Permission.MAKERWORLD_VIEW`` which maps
    to the ``can_read_status`` scope (see ``_APIKEY_SCOPE_BY_PERMISSION``
    in core/auth.py). /import gates on ``Permission.MAKERWORLD_IMPORT``
    which maps to ``can_manage_library``. ``can_access_cloud`` is what
    ``resolve_api_key_cloud_owner`` checks before returning the owner —
    the separate field this PR's fix actually depends on.
    """
    full_key, key_hash, key_prefix = generate_api_key()
    row = APIKey(
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        user_id=owner.id,
        can_access_cloud=can_access_cloud,
        can_read_status=can_read_status,
        can_manage_library=can_manage_library,
    )
    db.add(row)
    await db.commit()
    return full_key


def _fake_service(**stubs):
    """Mirror of the fixture in test_makerworld_routes.py — AsyncMock with
    method stubs that return the supplied payloads."""
    svc = AsyncMock()
    svc.close = AsyncMock()
    for name, value in stubs.items():
        if callable(value) and not isinstance(value, AsyncMock):
            setattr(svc, name, AsyncMock(side_effect=value))
        else:
            setattr(svc, name, AsyncMock(return_value=value))
    return svc


class TestStatusEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_owner_with_token_sees_has_cloud_token_true(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        await _setup_auth_with_admin(async_client)
        admin = await _store_admin_cloud_token(db_session, "mwadmin", token="fake-bambu-token")
        key = await _make_key(db_session, owner=admin, name="status-cloud")

        resp = await async_client.get(
            "/api/v1/makerworld/status",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"has_cloud_token": True, "can_download": True, "sign_in_expired": False}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_without_cloud_scope_reports_no_token(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Key has the per-route scope (can_read_status) but NOT can_access_cloud.

        Before this PR, both these conditions reported has_cloud_token=False.
        After the PR the per-route scope alone still doesn't grant cloud
        access — the resolver fences on can_access_cloud — so the response
        is unchanged for this case. Pinning so a future change can't
        accidentally widen the gate.
        """
        await _setup_auth_with_admin(async_client)
        admin = await _store_admin_cloud_token(db_session, "mwadmin", token="fake-bambu-token")
        key = await _make_key(db_session, owner=admin, name="status-no-cloud", can_access_cloud=False)

        resp = await async_client.get(
            "/api/v1/makerworld/status",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200
        assert resp.json() == {"has_cloud_token": False, "can_download": False, "sign_in_expired": False}


class TestResolveEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_owner_with_token_builds_authed_service(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """The route must reach ``_build_service`` with the API-key owner's
        User, which is what ultimately seeds MakerWorldService.auth_token.
        We assert on the resolved user argument the route passes through —
        the upstream MakerWorld API call is mocked so the test stays offline.
        """
        await _setup_auth_with_admin(async_client)
        admin = await _store_admin_cloud_token(db_session, "mwadmin", token="fake-bambu-token")
        key = await _make_key(db_session, owner=admin, name="resolve-cloud")

        design = {"id": 1400373, "modelId": "US2bb73b106683e5", "title": "Cube", "instances": []}
        instances = {"total": 0, "hits": []}
        svc = _fake_service(get_design=design, get_design_instances=instances)
        build = AsyncMock(return_value=svc)

        with patch("backend.app.api.routes.makerworld._build_service", build):
            resp = await async_client.post(
                "/api/v1/makerworld/resolve",
                json={"url": "https://makerworld.com/en/models/1400373"},
                headers={"X-API-Key": key},
            )
        assert resp.status_code == 200, resp.text
        # _build_service receives (db, user); the user arg must be the owning admin.
        # Without the fix it'd be None (the API-key dep value).
        assert build.await_count == 1
        passed_user = (
            build.await_args.args[1] if len(build.await_args.args) > 1 else build.await_args.kwargs.get("user")
        )
        assert passed_user is not None, "resolve_url must pass the API-key owner, not None"
        assert passed_user.id == admin.id


class TestImportEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_owner_import_succeeds_and_stamps_owner_id(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """End-to-end: /import via X-API-Key downloads the 3MF and saves it
        with the API-key owner's id on the LibraryFile row, not NULL."""
        await _setup_auth_with_admin(async_client)
        admin = await _store_admin_cloud_token(db_session, "mwadmin", token="fake-bambu-token")
        key = await _make_key(db_session, owner=admin, name="import-cloud")

        design = {
            "id": 1400373,
            "modelId": "US2bb73b106683e5",
            "title": "Cube",
            "instances": [{"profileId": 298919107, "title": "default"}],
        }
        manifest = {
            "name": "cube.3mf",
            "url": "https://makerworld.bblmw.com/makerworld/model/X/Y/cube.3mf?exp=1&key=k",
        }
        # 3MF download returns (bytes, filename). The bytes don't have to be a
        # valid zip — save_3mf_bytes_to_library stores them as-is and the
        # downstream thumbnail extractor swallows errors.
        svc = _fake_service(
            get_design=design,
            get_profile_download=manifest,
            download_3mf=(b"PK\x03\x04fake-3mf-bytes", "cube.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373},
                headers={"X-API-Key": key},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["was_existing"] is False

        # The library row was attributed to the API-key owner.
        # save_3mf_bytes_to_library translates owner_id → created_by_id on the
        # LibraryFile column (see library.py:534).
        result = await db_session.execute(select(LibraryFile).where(LibraryFile.id == body["library_file_id"]))
        saved = result.scalar_one()
        assert saved.created_by_id == admin.id, "Import via API key must attribute the row to the key's owner, not NULL"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_without_cloud_scope_still_imports_but_owner_is_none(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Fail-closed parity: a key with can_manage_library but NOT
        can_access_cloud reaches the route (permission gate passes) but
        the cloud-token resolver returns None, so the service is built
        without a token. The MakerWorldService itself would 401 on
        get_profile_download in production — here we just confirm the
        route doesn't suddenly grant cloud identity from a non-cloud key,
        and that the library row's owner_id stays NULL when there's no
        resolved cloud-scoped owner.
        """
        await _setup_auth_with_admin(async_client)
        admin = await _store_admin_cloud_token(db_session, "mwadmin", token="fake-bambu-token")
        key = await _make_key(db_session, owner=admin, name="import-no-cloud", can_access_cloud=False)

        design = {
            "id": 1400373,
            "modelId": "US2bb73b106683e5",
            "instances": [{"profileId": 298919107}],
        }
        manifest = {"name": "cube.3mf", "url": "https://makerworld.bblmw.com/x.3mf"}
        svc = _fake_service(
            get_design=design,
            get_profile_download=manifest,
            download_3mf=(b"PK\x03\x04fake", "cube.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)) as build:
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373},
                headers={"X-API-Key": key},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # _build_service got None — same as before the PR for non-cloud keys.
        passed_user = (
            build.await_args.args[1] if len(build.await_args.args) > 1 else build.await_args.kwargs.get("user")
        )
        assert passed_user is None

        # And owner_id is NULL because the cloud-scope fence said no.
        result = await db_session.execute(select(LibraryFile).where(LibraryFile.id == body["library_file_id"]))
        saved = result.scalar_one()
        assert saved.created_by_id is None


class TestJwtPathUnchanged:
    """Parity check — the existing JWT-authed flow must keep behaving as
    it did. The added Depends(resolve_api_key_cloud_owner) returns None
    for JWT callers so current_user from RequirePermissionIfAuthEnabled
    wins the ``or`` and nothing about the JWT path changes."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_with_jwt_admin_token(self, async_client: AsyncClient, db_session: AsyncSession):
        admin_token = await _setup_auth_with_admin(async_client)
        await _store_admin_cloud_token(db_session, "mwadmin", token="fake-bambu-token")

        resp = await async_client.get(
            "/api/v1/makerworld/status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"has_cloud_token": True, "can_download": True, "sign_in_expired": False}

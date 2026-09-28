"""The /makerworld/* permission gate with auth enabled.

The gate moved out of the route signature and into the handler: the provider
that a request actually uses comes from the body (``source_type`` on import,
the pasted URL on resolve), and FastAPI resolves dependencies before the body
exists, so a dependency could only ever name one provider's permission. What
must not change is the enforcement itself, so these pin the outcomes rather
than the wiring: anonymous callers are still refused before the body is read,
and a signed-in user without the permission still gets a 403.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.model_providers.base import (
    ProviderDownload,
    ProviderDownloadInfo,
    ProviderResolvedModel,
    ProviderResourceRef,
)


async def _setup_auth_with_admin(client: AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "mwadmin", "admin_password": "AdminPass1!"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "mwadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _make_user(client: AsyncClient, admin_jwt: str, *, username: str, permissions: list[str]) -> str:
    """Create a user in a fresh group holding exactly *permissions*."""
    group = await client.post(
        "/api/v1/groups/",
        headers={"Authorization": f"Bearer {admin_jwt}"},
        json={"name": f"grp_{username}", "permissions": permissions},
    )
    assert group.status_code in (200, 201), group.text
    created = await client.post(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {admin_jwt}"},
        json={"username": username, "password": "UserPass1!", "group_ids": [group.json()["id"]]},
    )
    assert created.status_code in (200, 201), created.text
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _fake_service(**stubs):
    svc = AsyncMock()
    svc.close = AsyncMock()
    for name, value in stubs.items():
        setattr(svc, name, AsyncMock(return_value=value))
    return svc


def _import_service():
    return _fake_service(
        get_download=ProviderDownloadInfo(
            ref=ProviderResourceRef(source_type="makerworld", external_id="1400373", sub_id="298919107"),
            url="https://makerworld.bblmw.com/makerworld/model/X/Y/cube.3mf?exp=1&key=k",
            suggested_filename="cube.3mf",
        ),
        download=ProviderDownload(file_bytes=b"PK\x03\x04fake-3mf-bytes", filename="cube.3mf"),
    )


class TestAnonymousIsRefusedFirst:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_anonymous_import_is_401(self, async_client: AsyncClient):
        await _setup_auth_with_admin(async_client)
        resp = await async_client.post("/api/v1/makerworld/import", json={"model_id": 1400373})
        assert resp.status_code == 401, resp.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_anonymous_resolve_is_401(self, async_client: AsyncClient):
        await _setup_auth_with_admin(async_client)
        resp = await async_client.post(
            "/api/v1/makerworld/resolve",
            json={"url": "https://makerworld.com/en/models/1400373"},
        )
        assert resp.status_code == 401, resp.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_anonymous_with_a_malformed_body_is_still_401_not_422(self, async_client: AsyncClient):
        """The permission moved into the handler, but authentication stayed a
        route dependency precisely so an unauthenticated caller cannot probe
        the request schema through validation errors."""
        await _setup_auth_with_admin(async_client)
        resp = await async_client.post("/api/v1/makerworld/import", json={"nonsense": True})
        assert resp.status_code == 401, resp.text


class TestPermissionStillBites:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_view_only_user_cannot_import(self, async_client: AsyncClient):
        admin = await _setup_auth_with_admin(async_client)
        jwt = await _make_user(async_client, admin, username="mwviewer", permissions=["makerworld:view"])

        with patch(
            "backend.app.api.routes.makerworld._build_service",
            AsyncMock(return_value=_import_service()),
        ):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373},
                headers={"Authorization": f"Bearer {jwt}"},
            )
        assert resp.status_code == 403, resp.text
        assert "makerworld:import" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_user_without_view_cannot_resolve(self, async_client: AsyncClient):
        admin = await _setup_auth_with_admin(async_client)
        jwt = await _make_user(async_client, admin, username="mwnoview", permissions=["printers:read"])

        resp = await async_client.post(
            "/api/v1/makerworld/resolve",
            json={"url": "https://makerworld.com/en/models/1400373"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 403, resp.text
        assert "makerworld:view" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_user_holding_the_permission_gets_through(self, async_client: AsyncClient):
        admin = await _setup_auth_with_admin(async_client)
        jwt = await _make_user(
            async_client,
            admin,
            username="mwimporter",
            permissions=["makerworld:view", "makerworld:import"],
        )

        with patch(
            "backend.app.api.routes.makerworld._build_service",
            AsyncMock(return_value=_import_service()),
        ):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373},
                headers={"Authorization": f"Bearer {jwt}"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["was_existing"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resolve_passes_for_a_viewer(self, async_client: AsyncClient):
        admin = await _setup_auth_with_admin(async_client)
        jwt = await _make_user(async_client, admin, username="mwviewer2", permissions=["makerworld:view"])

        svc = _fake_service(
            resolve=ProviderResolvedModel(
                ref=ProviderResourceRef(source_type="makerworld", external_id="1400373"),
                design={"id": 1400373},
                instances=[],
            )
        )
        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/resolve",
                json={"url": "https://makerworld.com/en/models/1400373"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["model_id"] == 1400373

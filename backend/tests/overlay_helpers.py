"""Shared authentication helpers for overlay integration tests."""

from httpx import AsyncClient


async def setup_admin(async_client: AsyncClient, *, suffix: str) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": f"overlayadmin{suffix}",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": f"overlayadmin{suffix}", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


async def mint_token(async_client: AsyncClient, jwt: str, *, scope: str, name: str = "obs") -> str:
    response = await async_client.post(
        "/api/v1/auth/tokens",
        headers={"Authorization": f"Bearer {jwt}"},
        json={"name": name, "expires_in_days": 30, "scope": scope},
    )
    assert response.status_code == 201, response.text
    assert response.json()["scope"] == scope
    return response.json()["token"]

"""Configuration that decides who can sign in is admin-only.

A group can be granted the settings permissions. Those reached sign-in
itself: a directory or identity provider that hands out the Administrators
group, the mail server the reset links go through, the address the links
point at, the session length, a whole-database restore, a reset that also
cleared the authentication switch, and a backup holding every password hash.
Like the user and group routes, these are admin-only; the permissions keep
everything else.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

# pragma: allowlist secret — test fixtures only, not real credentials
ADMIN_PW = "AdminConf1!"  # noqa: S105
MANAGER_PW = "Manager1pass!"  # noqa: S105

MANAGER_PERMISSIONS = [
    "settings:read",
    "settings:update",
    "settings:backup",
    "settings:restore",
    "github:restore",
]


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin(async_client: AsyncClient) -> dict[str, str]:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "confadmin", "admin_password": ADMIN_PW},
    )
    return await _login(async_client, "confadmin", ADMIN_PW)


@pytest.fixture
async def manager(async_client: AsyncClient, admin) -> dict[str, str]:
    """A non-admin holding every settings permission."""
    group = await async_client.post(
        "/api/v1/groups", json={"name": "Settings managers", "permissions": MANAGER_PERMISSIONS}, headers=admin
    )
    assert group.status_code == 201, group.text
    user = await async_client.post(
        "/api/v1/users",
        json={"username": "confmanager", "password": MANAGER_PW, "group_ids": [group.json()["id"]]},
        headers=admin,
    )
    assert user.status_code == 201, user.text
    return await _login(async_client, "confmanager", MANAGER_PW)


ADMIN_ONLY_ROUTES = [
    (
        "POST",
        "/api/v1/auth/oidc/providers",
        {"name": "x", "issuer_url": "https://idp.example", "client_id": "c", "client_secret": "s"},
    ),
    ("PUT", "/api/v1/auth/oidc/providers/1", {"name": "x"}),
    ("DELETE", "/api/v1/auth/oidc/providers/1", None),
    ("DELETE", "/api/v1/auth/oidc/providers/1/icon", None),
    ("POST", "/api/v1/auth/oidc/providers/1/icon/refresh", None),
    ("POST", "/api/v1/auth/smtp", {"smtp_host": "mail.example", "smtp_port": 25}),
    ("POST", "/api/v1/auth/smtp/test", {"test_recipient": "someone@example.com"}),
    ("POST", "/api/v1/auth/ldap/test", None),
    ("POST", "/api/v1/settings/reset", None),
    ("POST", "/api/v1/local-backup/backups/bambuddy-backup.zip/restore", None),
    ("GET", "/api/v1/settings/backup", None),
    ("GET", "/api/v1/local-backup/backups/bambuddy-backup.zip/download", None),
    # A connected app's redirect address decides where an admin's sign-in goes.
    ("POST", "/api/v1/connect/apps", {"name": "x", "redirect_uri": "https://attacker.example/cb"}),
    ("PATCH", "/api/v1/connect/apps/1", {"redirect_uri": "https://attacker.example/cb"}),
    ("POST", "/api/v1/connect/apps/1/rotate-secret", None),
    ("DELETE", "/api/v1/connect/apps/1", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY_ROUTES)
async def test_a_settings_manager_is_refused(async_client: AsyncClient, manager, method, path, body):
    resp = await async_client.request(method, path, json=body, headers=manager)
    assert resp.status_code == 403, resp.text


async def test_a_settings_manager_cannot_restore_a_database(async_client: AsyncClient, manager):
    resp = await async_client.post(
        "/api/v1/settings/restore",
        files={"file": ("backup.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")},
        headers=manager,
    )
    assert resp.status_code == 403, resp.text


async def test_a_reset_by_a_settings_manager_leaves_authentication_on(async_client: AsyncClient, manager):
    await async_client.post("/api/v1/settings/reset", headers=manager)

    status = await async_client.get("/api/v1/auth/status")
    assert status.json()["auth_enabled"] is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ldap_server_url", "ldap://attacker.example"),
        ("ldap_default_group", "Administrators"),
        ("ldap_auto_provision", True),
        ("ldap_enabled", True),
        ("local_login_enabled", False),
        ("session_max_hours", 720),
        ("external_url", "https://attacker.example"),
        ("prometheus_enabled", True),
        ("prometheus_token", "x"),
        # A full backup written into a folder the web server hands out.
        ("local_backup_path", "/opt/bambuddy/static/img"),
        ("external_url", None),
    ],
)
async def test_a_settings_manager_cannot_change_sign_in_settings(async_client: AsyncClient, manager, key, value):
    for method in ("PUT", "PATCH"):
        resp = await async_client.request(method, "/api/v1/settings/", json={key: value}, headers=manager)
        assert resp.status_code == 403, (method, resp.text)


async def test_a_settings_manager_still_saves_the_settings_page(async_client: AsyncClient, admin, manager):
    """The page sends every field on each save; unchanged sign-in fields pass."""
    current = (await async_client.get("/api/v1/settings/", headers=manager)).json()
    payload = {k: current[k] for k in ("local_login_enabled", "session_max_hours", "external_url", "ldap_enabled")}
    payload["date_format"] = "eu" if current.get("date_format") != "eu" else "us"

    resp = await async_client.put("/api/v1/settings/", json=payload, headers=manager)

    assert resp.status_code == 200, resp.text
    assert resp.json()["date_format"] == payload["date_format"]


async def test_a_refused_save_changes_nothing(async_client: AsyncClient, admin, manager):
    before = (await async_client.get("/api/v1/settings/", headers=admin)).json()
    flipped = "eu" if before.get("date_format") != "eu" else "us"

    resp = await async_client.put(
        "/api/v1/settings/",
        json={"date_format": flipped, "external_url": "https://attacker.example"},
        headers=manager,
    )

    assert resp.status_code == 403
    after = (await async_client.get("/api/v1/settings/", headers=admin)).json()
    assert after["date_format"] == before["date_format"]
    assert after["external_url"] == before["external_url"]


async def test_an_admin_still_changes_sign_in_settings(async_client: AsyncClient, admin):
    resp = await async_client.put(
        "/api/v1/settings/", json={"external_url": "https://bambuddy.example", "session_max_hours": 48}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_url"] == "https://bambuddy.example"


async def test_an_admin_still_reaches_the_admin_only_routes(async_client: AsyncClient, admin):
    resp = await async_client.post("/api/v1/auth/ldap/test", headers=admin)
    assert resp.status_code != 403
    resp = await async_client.get("/api/v1/settings/backup", headers=admin)
    assert resp.status_code != 403


async def _git_config(async_client: AsyncClient, admin) -> None:
    from unittest.mock import AsyncMock, patch

    with patch(
        "backend.app.services.github_backup.github_backup_service.test_connection",
        new=AsyncMock(
            return_value={"success": True, "message": "ok", "permissions": {"push": True}, "is_private": True}
        ),
    ):
        resp = await async_client.post(
            "/api/v1/github-backup/config",
            headers=admin,
            json={
                "repository_url": "https://github.com/test/repo",
                "access_token": "ghp_testtoken123",  # pragma: allowlist secret — test fixture
                "branch": "main",
                "backup_settings": True,
                "enabled": True,
            },
        )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(("who", "as_admin"), [("manager", False), ("admin", True)])
async def test_a_git_restore_keeps_sign_in_settings_unless_an_admin_runs_it(
    async_client: AsyncClient, admin, manager, who, as_admin
):
    from unittest.mock import AsyncMock, patch

    await _git_config(async_client, admin)
    headers = manager if who == "manager" else admin
    run = AsyncMock(return_value={"success": True, "message": "ok", "results": {}})
    preview = AsyncMock(return_value={"success": True, "message": "ok", "ref": "abc", "categories": []})
    with (
        patch("backend.app.services.github_restore.github_restore_service.run_restore", new=run),
        patch("backend.app.services.github_restore.github_restore_service.preview", new=preview),
    ):
        restored = await async_client.post(
            "/api/v1/github-backup/restore", json={"categories": ["settings"]}, headers=headers
        )
        previewed = await async_client.get("/api/v1/github-backup/restore/preview", headers=headers)

    assert restored.status_code == 200, restored.text
    assert previewed.status_code == 200, previewed.text
    assert run.await_args.kwargs["as_admin"] is as_admin
    assert preview.await_args.kwargs["as_admin"] is as_admin


@pytest.mark.parametrize(("new", "old"), [(True, "true"), (False, "false"), (24, "24"), (None, None)])
def test_a_value_is_the_same_once_stored(new, old):
    from backend.app.api.routes.settings import _same_setting_value

    assert _same_setting_value(new, old)


@pytest.mark.parametrize(
    ("new", "old"),
    [
        (True, "false"),
        (48, "24"),
        ("https://a.example", "https://b.example"),
        # Stored as written: the text "None" and the spaces would reach the links.
        (None, ""),
        ("https://b.example ", "https://b.example"),
    ],
)
def test_a_different_value_is_a_change(new, old):
    from backend.app.api.routes.settings import _same_setting_value

    assert not _same_setting_value(new, old)

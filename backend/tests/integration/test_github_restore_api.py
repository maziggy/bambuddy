"""Integration tests for the Git backup restore API endpoints (#2656)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.tests.integration.test_ownership_permissions import TestOwnershipPermissionsSetup


@pytest.fixture(autouse=True)
def _mock_private_repo_check():
    """POST /config refuses to save unless the repo is confirmed private."""
    with patch(
        "backend.app.services.github_backup.github_backup_service.test_connection",
        new=AsyncMock(
            return_value={
                "success": True,
                "message": "Connection successful",
                "repo_name": "test/repo",
                "permissions": {"push": True},
                "is_private": True,
            }
        ),
    ) as m:
        yield m


async def _create_config(async_client: AsyncClient, token: str | None = None) -> dict:
    response = await async_client.post(
        "/api/v1/github-backup/config",
        headers={"Authorization": f"Bearer {token}"} if token else {},
        json={
            "repository_url": "https://github.com/test/repo",
            "access_token": "ghp_testtoken123",
            "branch": "main",
            "backup_kprofiles": True,
            "backup_spools": True,
            "backup_archives": True,
            "backup_settings": True,
            "enabled": True,
        },
    )
    assert response.status_code == 200
    return response.json()


class TestCommitsEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_404_when_not_configured(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/github-backup/commits")
        assert response.status_code == 404
        assert "Configure backup first" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_commits_from_the_provider(self, async_client: AsyncClient):
        await _create_config(async_client)
        commits = [
            {"sha": "aaa1111", "message": "Bambuddy backup", "author": "Bambuddy", "date": "2026-07-02T10:00:00Z"}
        ]
        with patch(
            "backend.app.services.git_providers.github.GitHubBackend.list_commits",
            new=AsyncMock(return_value={"success": True, "message": "OK", "commits": commits}),
        ):
            response = await async_client.get("/api/v1/github-backup/commits")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["branch"] == "main"
        assert body["commits"][0]["sha"] == "aaa1111"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_provider_failure_is_reported_not_raised(self, async_client: AsyncClient):
        await _create_config(async_client)
        with patch(
            "backend.app.services.git_providers.github.GitHubBackend.list_commits",
            new=AsyncMock(return_value={"success": False, "message": "Invalid access token", "commits": []}),
        ):
            response = await async_client.get("/api/v1/github-backup/commits")

        assert response.status_code == 200
        assert response.json()["success"] is False
        assert response.json()["commits"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_limit_is_bounded(self, async_client: AsyncClient):
        await _create_config(async_client)
        assert (await async_client.get("/api/v1/github-backup/commits?limit=0")).status_code == 422
        assert (await async_client.get("/api/v1/github-backup/commits?limit=101")).status_code == 422


class TestPreviewEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_404_when_not_configured(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/github-backup/restore/preview")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reports_available_and_missing_categories(self, async_client: AsyncClient):
        await _create_config(async_client)
        preview = {
            "success": True,
            "message": "OK",
            "ref": "aaa1111",
            "commit": None,
            "metadata_version": "1.0",
            "categories": [
                {"category": "kprofiles", "available": False, "item_count": 0, "detail": "Not present"},
                {"category": "settings", "available": True, "item_count": 12, "detail": None},
                {"category": "spools", "available": True, "item_count": 4, "detail": "plus 9 usage records"},
                {"category": "archives", "available": True, "item_count": 30, "detail": "Metadata only"},
            ],
        }
        with patch(
            "backend.app.services.github_restore.github_restore_service.preview",
            new=AsyncMock(return_value=preview),
        ):
            response = await async_client.get("/api/v1/github-backup/restore/preview?ref=aaa1111")

        assert response.status_code == 200
        body = response.json()
        assert body["metadata_version"] == "1.0"
        by_name = {c["category"]: c for c in body["categories"]}
        assert by_name["kprofiles"]["available"] is False
        assert by_name["spools"]["item_count"] == 4

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("ref", ["main", "abc", "../../etc/passwd", "zzzzzzz"])
    async def test_rejects_refs_that_are_not_object_names(self, async_client: AsyncClient, ref):
        await _create_config(async_client)
        response = await async_client.get(f"/api/v1/github-backup/restore/preview?ref={ref}")
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_defaults_to_head(self, async_client: AsyncClient):
        await _create_config(async_client)
        mock = AsyncMock(return_value={"success": True, "message": "OK", "ref": "aaa1111", "categories": []})
        with patch("backend.app.services.github_restore.github_restore_service.preview", new=mock):
            response = await async_client.get("/api/v1/github-backup/restore/preview")

        assert response.status_code == 200
        assert mock.await_args.kwargs["ref"] == "HEAD"


class TestRestoreEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_404_when_not_configured(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/github-backup/restore", json={"categories": ["spools"]})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_applies_selected_categories(self, async_client: AsyncClient):
        await _create_config(async_client)
        outcome = {
            "success": True,
            "message": "Restored 5 item(s) from aaa1111",
            "log_id": 3,
            "ref": "aaa1111",
            "results": {
                "spools": {"restored": 4, "skipped": 1, "failed": 0, "notes": []},
                "settings": {
                    "restored": 1,
                    "skipped": 2,
                    "failed": 0,
                    "notes": [
                        {
                            "code": "settingsCredentialsSkipped",
                            "params": {"count": 1},
                            "message": "1 credential-like key(s) skipped",
                        }
                    ],
                },
            },
        }
        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(return_value=outcome),
        ) as mock:
            response = await async_client.post(
                "/api/v1/github-backup/restore",
                json={"ref": "aaa1111", "categories": ["spools", "settings"], "overwrite_existing": True},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["results"]["spools"]["restored"] == 4
        # Notes cross the wire as code + params + English fallback, so a
        # non-English client can translate them (#2656).
        assert body["results"]["settings"]["notes"] == [
            {
                "code": "settingsCredentialsSkipped",
                "params": {"count": 1},
                "message": "1 credential-like key(s) skipped",
            }
        ]
        assert mock.await_args.kwargs["overwrite_existing"] is True
        assert mock.await_args.kwargs["ref"] == "aaa1111"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_empty_category_list(self, async_client: AsyncClient):
        await _create_config(async_client)
        response = await async_client.post("/api/v1/github-backup/restore", json={"categories": []})
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_unknown_category(self, async_client: AsyncClient):
        await _create_config(async_client)
        response = await async_client.post("/api/v1/github-backup/restore", json={"categories": ["cloud_profiles"]})
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_malformed_ref(self, async_client: AsyncClient):
        await _create_config(async_client)
        response = await async_client.post(
            "/api/v1/github-backup/restore", json={"ref": "main", "categories": ["spools"]}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_defaults_overwrite_to_false(self, async_client: AsyncClient):
        """The safe default: a restore only inserts what's missing."""
        await _create_config(async_client)
        mock = AsyncMock(return_value={"success": True, "message": "ok", "results": {}})
        with patch("backend.app.services.github_restore.github_restore_service.run_restore", new=mock):
            response = await async_client.post("/api/v1/github-backup/restore", json={"categories": ["spools"]})

        assert response.status_code == 200
        assert mock.await_args.kwargs["overwrite_existing"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_service_failure_is_reported_in_body(self, async_client: AsyncClient):
        await _create_config(async_client)
        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(
                return_value={
                    "success": False,
                    "message": "A backup is currently running. Wait for it to finish before restoring.",
                    "results": {},
                }
            ),
        ):
            response = await async_client.post("/api/v1/github-backup/restore", json={"categories": ["spools"]})

        assert response.status_code == 200
        assert response.json()["success"] is False
        assert "backup is currently running" in response.json()["message"]


class TestStatusExposesRestoreState:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_running_is_false_when_idle(self, async_client: AsyncClient):
        await _create_config(async_client)
        response = await async_client.get("/api/v1/github-backup/status")
        assert response.status_code == 200
        assert response.json()["restore_running"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_running_is_reported(self, async_client: AsyncClient):
        """The UI disables both action buttons off this flag."""
        await _create_config(async_client)
        from backend.app.services.github_restore import github_restore_service

        github_restore_service._running_restore = True
        github_restore_service._progress = "Restoring spool inventory..."
        try:
            response = await async_client.get("/api/v1/github-backup/status")
        finally:
            github_restore_service._running_restore = False
            github_restore_service._progress = None

        assert response.json()["restore_running"] is True
        assert response.json()["progress"] == "Restoring spool inventory..."

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unconfigured_status_still_has_the_field(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/github-backup/status")
        assert response.status_code == 200
        assert response.json()["restore_running"] is False


class TestRestoredArchivesAreVisibleToTheirOwner(TestOwnershipPermissionsSetup):
    """The archive-ownership blocker, proved through the route that enforces it.

    ``_ensure_archive_visible`` fails closed on a NULL ``created_by_id`` — 404 for
    any caller without ``archives:read_all`` — so before the collector and the
    restore carried the column across, a multi-user instance got archives the
    tally called restored and their owner could not open.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_owning_non_admin_can_open_a_restored_archive(
        self, async_client: AsyncClient, auth_setup, db_session
    ):
        from backend.app.models.archive import PrintArchive
        from backend.app.services.github_restore import _CategoryTally, github_restore_service

        owner_id = auth_setup["operator_user"]["id"]
        payload = {
            "archives": [
                {
                    "id": 77,
                    "filename": "benchy.3mf",
                    "file_size": 2048,
                    "content_hash": "abc123",
                    "print_name": "Benchy",
                    "started_at": "2026-03-01 10:00:00",
                    "created_at": "2026-03-01 10:00:00",
                    "created_by_id": owner_id,
                }
            ]
        }
        await github_restore_service._restore_archives(db_session, payload, False, _CategoryTally(), {})
        await db_session.commit()

        restored = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert restored.id != 77, "the backup's primary key must not be reused"

        response = await async_client.get(
            f"/api/v1/archives/{restored.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 200, "the owner cannot see their own restored archive"
        assert response.json()["print_name"] == "Benchy"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_different_operator_still_cannot(self, async_client: AsyncClient, auth_setup, db_session):
        """Control: carrying the owner across must not widen who can read it."""
        from backend.app.models.archive import PrintArchive
        from backend.app.services.github_restore import _CategoryTally, github_restore_service

        payload = {
            "archives": [
                {
                    "id": 77,
                    "filename": "benchy.3mf",
                    "file_size": 2048,
                    "content_hash": "abc123",
                    "started_at": "2026-03-01 10:00:00",
                    "created_by_id": auth_setup["operator_user"]["id"],
                }
            ]
        }
        await github_restore_service._restore_archives(db_session, payload, False, _CategoryTally(), {})
        await db_session.commit()

        restored = (await db_session.execute(select(PrintArchive))).scalar_one()
        response = await async_client.get(
            f"/api/v1/archives/{restored.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator2_token']}"},
        )

        assert response.status_code == 404


class TestRestoreDoesNotOpenTheMetricsEndpoint:
    """The companion-credential rule, proved against the endpoint it protects.

    ``/api/v1/metrics`` is on ``PUBLIC_API_ROUTES`` and its only gate is
    ``if token:``, so writing ``prometheus_enabled`` onto an instance with no
    ``prometheus_token`` row hands the entire metrics body to anyone who can
    reach the port. The restore refuses that token as credential-shaped, so
    before this change the pair came apart and the endpoint opened — with
    overwrite *off*, since the local row is missing rather than present.

    Driven through the real service and the real endpoint against one database:
    the unit tests can show the toggle is not written, only this can show what
    that means.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restoring_prometheus_enabled_leaves_the_endpoint_shut(self, async_client: AsyncClient, db_session):
        from backend.app.services.github_restore import _CategoryTally, github_restore_service

        # An instance that never enabled Prometheus: no toggle row, no token row.
        assert (await async_client.get("/api/v1/metrics")).status_code == 404

        tally = _CategoryTally()
        await github_restore_service._restore_settings(
            db_session,
            {"settings": {"prometheus_enabled": "true", "prometheus_token": "s3cret", "currency": "EUR"}},
            overwrite=False,
            tally=tally,
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/metrics")
        assert response.status_code == 404, "a settings restore opened the metrics endpoint"
        assert "bambuddy_build_info" not in response.text
        assert any(note["code"] == "settingsCompanionSkipped" for note in tally.notes)

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "payload",
        [
            {"prometheus_enabled": "true", "currency": "EUR"},
            {"prometheus_enabled": "true", "prometheus_token": "", "currency": "EUR"},
        ],
        ids=["token-key-absent", "token-blank"],
    )
    async def test_a_token_less_backup_leaves_the_endpoint_shut_too(
        self, async_client: AsyncClient, db_session, payload
    ):
        """The route the test above does not cover, and the likelier one.

        ``prometheus_token`` is optional, so an instance can enable Prometheus
        without ever setting it. Such a backup carries the toggle and no usable
        token — and because the companion rule's second condition asks whether
        the *backup* had a credential, that payload used to sail straight past
        the refusal and open the endpoint the case above proves shut.
        """
        from backend.app.services.github_restore import _CategoryTally, github_restore_service

        assert (await async_client.get("/api/v1/metrics")).status_code == 404

        tally = _CategoryTally()
        await github_restore_service._restore_settings(db_session, {"settings": payload}, overwrite=False, tally=tally)
        await db_session.commit()

        response = await async_client.get("/api/v1/metrics")
        assert response.status_code == 404, "a token-less Prometheus backup opened the metrics endpoint"
        assert "bambuddy_build_info" not in response.text
        assert any(note["code"] == "settingsCompanionSkipped" for note in tally.notes)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_instance_with_its_own_token_still_gets_the_toggle_back(
        self, async_client: AsyncClient, db_session
    ):
        """Control. The rule must not break a legitimate Prometheus restore."""
        from backend.app.services.github_restore import _CategoryTally, github_restore_service

        await async_client.put(
            "/api/v1/settings/", json={"prometheus_enabled": False, "prometheus_token": "local-token"}
        )

        await github_restore_service._restore_settings(
            db_session,
            {"settings": {"prometheus_enabled": "true", "prometheus_token": "s3cret"}},
            overwrite=True,
            tally=_CategoryTally(),
        )
        await db_session.commit()

        assert (await async_client.get("/api/v1/metrics")).status_code == 401
        authorised = await async_client.get("/api/v1/metrics", headers={"Authorization": "Bearer local-token"})
        assert authorised.status_code == 200
        assert "bambuddy_build_info" in authorised.text


class TestSettingsRestoreNeedsSettingsUpdate(TestOwnershipPermissionsSetup):
    """A Backup-only role must not reach around the gate that owns the rows (#2656).

    Each category rewrites rows some other endpoint already owns —
    ``PUT /api/v1/settings/`` gates on ``settings:update``, the inventory writes
    on ``inventory:update``, an archive that is not yours on
    ``archives:update_all``, and the K-profile batch on ``kprofiles:update``.
    Backup is its own permission group, so gating the restore endpoint on
    ``github:restore`` alone let a role holding only Backup write, through a
    restore, what it could not write through the endpoint that owns them. This
    module already makes that argument — it is why the four protected auth keys
    are refused outright — so the gap was an inconsistency in ours.

    Settings was gated first; the other three followed on review, because gating
    one and not the rest is the only state that is not defensible.
    """

    async def _token_for(self, async_client: AsyncClient, admin_token: str, name: str, permissions: list[str]) -> str:
        headers = {"Authorization": f"Bearer {admin_token}"}
        group = await async_client.post(
            "/api/v1/groups/",
            headers=headers,
            json={"name": name, "permissions": permissions},
        )
        assert group.status_code == 201, group.text
        created = await async_client.post(
            "/api/v1/users/",
            headers=headers,
            json={"username": name, "password": "Restorepass1!", "group_ids": [group.json()["id"]]},
        )
        assert created.status_code in (200, 201), created.text
        login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": name, "password": "Restorepass1!"},
        )
        assert login.status_code == 200, login.text
        return login.json()["access_token"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_only_role_cannot_restore_settings(self, async_client: AsyncClient, auth_setup):
        token = await self._token_for(
            async_client, auth_setup["admin_token"], "backuponly", ["github:backup", "github:restore"]
        )
        await _create_config(async_client, auth_setup["admin_token"])

        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(return_value={"success": True, "message": "", "log_id": 1, "ref": "aaa1111", "results": {}}),
        ) as mock:
            response = await async_client.post(
                "/api/v1/github-backup/restore",
                headers={"Authorization": f"Bearer {token}"},
                json={"categories": ["settings"]},
            )

        assert response.status_code == 403
        assert "settings:update" in response.json()["detail"]
        mock.assert_not_awaited(), "the refusal has to happen before anything is written"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("category", "permission"),
        [
            ("spools", "inventory:update"),
            ("archives", "archives:update_all"),
            ("kprofiles", "kprofiles:update"),
        ],
    )
    async def test_backup_only_role_cannot_restore_the_other_categories(
        self, async_client: AsyncClient, auth_setup, category, permission
    ):
        """Same argument as settings: these rows have an owning permission too."""
        token = await self._token_for(
            async_client, auth_setup["admin_token"], f"backuponly-{category}", ["github:backup", "github:restore"]
        )
        await _create_config(async_client, auth_setup["admin_token"])

        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(return_value={"success": True, "message": "", "log_id": 1, "ref": "aaa1111", "results": {}}),
        ) as mock:
            response = await async_client.post(
                "/api/v1/github-backup/restore",
                headers={"Authorization": f"Bearer {token}"},
                json={"categories": [category]},
            )

        assert response.status_code == 403
        assert permission in response.json()["detail"]
        mock.assert_not_awaited(), "the refusal has to happen before anything is written"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_every_missing_permission_is_named_at_once(self, async_client: AsyncClient, auth_setup):
        """One round trip tells the caller everything to fix, not just the first.

        A restore is a multi-select, so reporting one category at a time turns
        picking four into four refusals.
        """
        token = await self._token_for(
            async_client, auth_setup["admin_token"], "backuponly-all", ["github:backup", "github:restore"]
        )
        await _create_config(async_client, auth_setup["admin_token"])

        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(return_value={"success": True, "message": "", "log_id": 1, "ref": "aaa1111", "results": {}}),
        ):
            response = await async_client.post(
                "/api/v1/github-backup/restore",
                headers={"Authorization": f"Bearer {token}"},
                json={"categories": ["settings", "spools", "archives", "kprofiles"]},
            )

        assert response.status_code == 403
        detail = response.json()["detail"]
        for permission in ("settings:update", "inventory:update", "archives:update_all", "kprofiles:update"):
            assert permission in detail

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_gate_is_per_category_not_a_blanket_demotion(self, async_client: AsyncClient, auth_setup):
        """Control: holding one category's permission is enough to restore that one."""
        token = await self._token_for(
            async_client,
            auth_setup["admin_token"],
            "backupandinventory",
            ["github:backup", "github:restore", "inventory:read", "inventory:update"],
        )
        await _create_config(async_client, auth_setup["admin_token"])

        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(return_value={"success": True, "message": "", "log_id": 1, "ref": "aaa1111", "results": {}}),
        ):
            response = await async_client.post(
                "/api/v1/github-backup/restore",
                headers={"Authorization": f"Bearer {token}"},
                json={"categories": ["spools"]},
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_every_restorable_category_has_an_owning_permission(self):
        """Guards the map against a category added without a gate.

        A new ``RestoreCategory`` that is missing here is not a failing test
        anywhere else — it simply restores under ``github:restore`` alone, which
        is the hole this whole class exists to close.
        """
        from backend.app.api.routes.github_backup import _CATEGORY_WRITE_PERMISSION
        from backend.app.schemas.github_backup import RestoreCategory

        assert set(_CATEGORY_WRITE_PERMISSION) == set(RestoreCategory)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_role_holding_both_can_restore_settings(self, async_client: AsyncClient, auth_setup):
        """Control: the gate must not lock out a role that legitimately holds both."""
        token = await self._token_for(
            async_client,
            auth_setup["admin_token"],
            "backupandsettings",
            ["github:backup", "github:restore", "settings:read", "settings:update"],
        )
        await _create_config(async_client, auth_setup["admin_token"])

        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(return_value={"success": True, "message": "", "log_id": 1, "ref": "aaa1111", "results": {}}),
        ):
            response = await async_client.post(
                "/api/v1/github-backup/restore",
                headers={"Authorization": f"Bearer {token}"},
                json={"categories": ["settings"]},
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auth_disabled_is_unaffected(self, async_client: AsyncClient):
        """Control: with auth off there is no user to check, and the dep returns None."""
        await _create_config(async_client)

        with patch(
            "backend.app.services.github_restore.github_restore_service.run_restore",
            new=AsyncMock(return_value={"success": True, "message": "", "log_id": 1, "ref": "aaa1111", "results": {}}),
        ):
            response = await async_client.post(
                "/api/v1/github-backup/restore",
                json={"categories": ["settings"]},
            )

        assert response.status_code == 200

"""Integration tests for ownership-based permission system.

Tests the ownership permission model where users can have:
- *_all permissions: can modify any item
- *_own permissions: can only modify items they created
- Ownerless items (created_by_id = null) require *_all permission
"""

import pytest
from httpx import AsyncClient


class TestOwnershipPermissionsSetup:
    """Helper fixture class for ownership permission tests."""

    @pytest.fixture
    async def auth_setup(self, async_client: AsyncClient):
        """Setup auth with admin, create test users with different permission levels."""
        # Enable auth with admin user
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "ownershipadmin",
                "admin_password": "AdminPass1!",
            },
        )

        # Login as admin
        admin_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "ownershipadmin", "password": "AdminPass1!"},
        )
        admin_token = admin_login.json()["access_token"]
        admin_user = admin_login.json()["user"]

        # Get group IDs
        groups_response = await async_client.get(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        groups = groups_response.json()
        operators_group = next(g for g in groups if g["name"] == "Operators")
        viewers_group = next(g for g in groups if g["name"] == "Viewers")

        # Create operator user (has *_own permissions)
        operator_response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "operator1",
                "password": "Operatorpass1!",
                "group_ids": [operators_group["id"]],
            },
        )
        operator_user = operator_response.json()

        # Login as operator
        operator_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "operator1", "password": "Operatorpass1!"},
        )
        operator_token = operator_login.json()["access_token"]

        # Create second operator (for cross-user tests)
        operator2_response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "operator2",
                "password": "Operatorpass1!",
                "group_ids": [operators_group["id"]],
            },
        )
        operator2_user = operator2_response.json()

        operator2_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "operator2", "password": "Operatorpass1!"},
        )
        operator2_token = operator2_login.json()["access_token"]

        # Create viewer user (has no update/delete permissions)
        await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "viewer1",
                "password": "Viewerpass1!",
                "group_ids": [viewers_group["id"]],
            },
        )

        viewer_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "viewer1", "password": "Viewerpass1!"},
        )
        viewer_token = viewer_login.json()["access_token"]

        return {
            "admin_token": admin_token,
            "admin_user": admin_user,
            "operator_token": operator_token,
            "operator_user": operator_user,
            "operator2_token": operator2_token,
            "operator2_user": operator2_user,
            "viewer_token": viewer_token,
        }


class TestArchiveOwnershipPermissions(TestOwnershipPermissionsSetup):
    """Tests for archive ownership-based permissions."""

    # ========================================================================
    # DELETE permissions
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_can_delete_any_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Admin with *_all permissions can delete any archive."""
        printer = await printer_factory()
        # Create archive owned by operator
        archive = await archive_factory(
            printer.id,
            print_name="Operator Archive",
            created_by_id=auth_setup["operator_user"]["id"],
        )

        # Admin deletes it
        response = await async_client.delete(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_delete_own_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Operator with *_own permissions can delete their own archive."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="My Archive",
            created_by_id=auth_setup["operator_user"]["id"],
        )

        response = await async_client.delete(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_delete_others_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Operator with *_own permissions cannot delete another user's archive."""
        printer = await printer_factory()
        # Archive created by operator2
        archive = await archive_factory(
            printer.id,
            print_name="Other's Archive",
            created_by_id=auth_setup["operator2_user"]["id"],
        )

        # operator1 tries to delete it
        response = await async_client.delete(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 403
        assert "your own" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_delete_ownerless_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Operator with *_own permissions cannot delete ownerless archive."""
        printer = await printer_factory()
        # Archive with no owner (legacy data)
        archive = await archive_factory(
            printer.id,
            print_name="Ownerless Archive",
            created_by_id=None,
        )

        response = await async_client.delete(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_viewer_cannot_delete_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Viewer with no delete permissions cannot delete any archive."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="Any Archive")

        response = await async_client.delete(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['viewer_token']}"},
        )

        assert response.status_code == 403

    # ========================================================================
    # UPDATE permissions
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_can_update_any_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Admin can update any archive."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Original Name",
            created_by_id=auth_setup["operator_user"]["id"],
        )

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
            json={"print_name": "Admin Updated"},
        )

        assert response.status_code == 200
        assert response.json()["print_name"] == "Admin Updated"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_update_own_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Operator can update their own archive."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Original Name",
            created_by_id=auth_setup["operator_user"]["id"],
        )

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"print_name": "Operator Updated"},
        )

        assert response.status_code == 200
        assert response.json()["print_name"] == "Operator Updated"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_update_others_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Operator cannot update another user's archive."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Other's Archive",
            created_by_id=auth_setup["operator2_user"]["id"],
        )

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"print_name": "Attempted Update"},
        )

        assert response.status_code == 403

    # ========================================================================
    # REPRINT permissions
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_reprint_others_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Operator cannot reprint another user's archive."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            created_by_id=auth_setup["operator2_user"]["id"],
        )

        response = await async_client.post(
            f"/api/v1/archives/{archive.id}/reprint?printer_id={printer.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 403


class TestQueueOwnershipPermissions(TestOwnershipPermissionsSetup):
    """Tests for print queue ownership-based permissions."""

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items."""

        async def _create_item(**kwargs):
            from backend.app.models.print_queue import PrintQueueItem

            printer = await printer_factory()
            # Create an archive to link to the queue item
            archive = await archive_factory(printer.id)

            defaults = {
                "printer_id": printer.id,
                "archive_id": archive.id,
                "status": "pending",
                "position": 0,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_can_delete_any_queue_item(self, async_client: AsyncClient, auth_setup, queue_item_factory):
        """Admin can delete any queue item."""
        item = await queue_item_factory(created_by_id=auth_setup["operator_user"]["id"])

        response = await async_client.delete(
            f"/api/v1/queue/{item.id}",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_delete_own_queue_item(self, async_client: AsyncClient, auth_setup, queue_item_factory):
        """Operator can delete their own queue item."""
        item = await queue_item_factory(created_by_id=auth_setup["operator_user"]["id"])

        response = await async_client.delete(
            f"/api/v1/queue/{item.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_delete_others_queue_item(
        self, async_client: AsyncClient, auth_setup, queue_item_factory
    ):
        """Operator cannot delete another user's queue item."""
        item = await queue_item_factory(created_by_id=auth_setup["operator2_user"]["id"])

        response = await async_client.delete(
            f"/api/v1/queue/{item.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_update_own_queue_item(self, async_client: AsyncClient, auth_setup, queue_item_factory):
        """Operator can update their own queue item."""
        item = await queue_item_factory(created_by_id=auth_setup["operator_user"]["id"])

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"position": 10},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_update_others_queue_item(
        self, async_client: AsyncClient, auth_setup, queue_item_factory
    ):
        """Operator cannot update another user's queue item."""
        item = await queue_item_factory(created_by_id=auth_setup["operator2_user"]["id"])

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"position": 10},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_cancel_others_queue_item(
        self, async_client: AsyncClient, auth_setup, queue_item_factory
    ):
        """Operator cannot cancel another user's queue item."""
        item = await queue_item_factory(created_by_id=auth_setup["operator2_user"]["id"])

        response = await async_client.post(
            f"/api/v1/queue/{item.id}/cancel",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_skips_non_owned_items(self, async_client: AsyncClient, auth_setup, queue_item_factory):
        """Bulk update only updates items the user owns."""
        # Create items owned by different users
        own_item = await queue_item_factory(
            created_by_id=auth_setup["operator_user"]["id"],
        )
        other_item = await queue_item_factory(
            created_by_id=auth_setup["operator2_user"]["id"],
        )

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={
                "item_ids": [own_item.id, other_item.id],
                "manual_start": True,
            },
        )

        assert response.status_code == 200
        result = response.json()
        # Should only update the owned item
        assert result["updated_count"] == 1
        assert result["skipped_count"] == 1


class TestLibraryOwnershipPermissions(TestOwnershipPermissionsSetup):
    """Tests for library file ownership-based permissions."""

    @pytest.fixture
    async def library_file_factory(self, db_session):
        """Factory to create test library files."""
        _counter = [0]

        async def _create_file(**kwargs):
            from backend.app.models.library import LibraryFile

            _counter[0] += 1
            defaults = {
                "filename": f"test_{_counter[0]}.3mf",
                "file_path": f"library/test_{_counter[0]}.3mf",
                "file_type": "3mf",
                "file_size": 1024,
            }
            defaults.update(kwargs)

            file = LibraryFile(**defaults)
            db_session.add(file)
            await db_session.commit()
            await db_session.refresh(file)
            return file

        return _create_file

    @pytest.fixture
    async def library_folder_factory(self, db_session):
        """Factory to create test library folders."""
        _counter = [0]

        async def _create_folder(**kwargs):
            from backend.app.models.library import LibraryFolder

            _counter[0] += 1
            defaults = {
                "name": f"TestFolder_{_counter[0]}",
            }
            defaults.update(kwargs)

            folder = LibraryFolder(**defaults)
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)
            return folder

        return _create_folder

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_can_delete_any_library_file(self, async_client: AsyncClient, auth_setup, library_file_factory):
        """Admin can delete any library file."""
        file = await library_file_factory(created_by_id=auth_setup["operator_user"]["id"])

        response = await async_client.delete(
            f"/api/v1/library/files/{file.id}",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_delete_own_library_file(
        self, async_client: AsyncClient, auth_setup, library_file_factory
    ):
        """Operator can delete their own library file."""
        file = await library_file_factory(created_by_id=auth_setup["operator_user"]["id"])

        response = await async_client.delete(
            f"/api/v1/library/files/{file.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_delete_others_library_file(
        self, async_client: AsyncClient, auth_setup, library_file_factory
    ):
        """Operator cannot delete another user's library file."""
        file = await library_file_factory(created_by_id=auth_setup["operator2_user"]["id"])

        response = await async_client.delete(
            f"/api/v1/library/files/{file.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_update_own_library_file(
        self, async_client: AsyncClient, auth_setup, library_file_factory
    ):
        """Operator can update their own library file."""
        file = await library_file_factory(created_by_id=auth_setup["operator_user"]["id"])

        response = await async_client.put(
            f"/api/v1/library/files/{file.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"filename": "renamed.3mf"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_update_others_library_file(
        self, async_client: AsyncClient, auth_setup, library_file_factory
    ):
        """Operator cannot update another user's library file."""
        file = await library_file_factory(created_by_id=auth_setup["operator2_user"]["id"])

        response = await async_client.put(
            f"/api/v1/library/files/{file.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"filename": "renamed.3mf"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_folders_require_all_permission(self, async_client: AsyncClient, auth_setup, library_folder_factory):
        """Folders require *_all permission (no ownership tracking on folders)."""
        folder = await library_folder_factory(name="TestFolder")

        # Operator cannot delete folder (needs *_all)
        response = await async_client.delete(
            f"/api/v1/library/folders/{folder.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_delete_skips_non_owned_files(self, async_client: AsyncClient, auth_setup, library_file_factory):
        """Bulk delete only deletes files the user owns."""
        own_file = await library_file_factory(
            filename="own.3mf",
            created_by_id=auth_setup["operator_user"]["id"],
        )
        other_file = await library_file_factory(
            filename="other.3mf",
            created_by_id=auth_setup["operator2_user"]["id"],
        )

        response = await async_client.post(
            "/api/v1/library/bulk-delete",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"file_ids": [own_file.id, other_file.id], "folder_ids": []},
        )

        assert response.status_code == 200
        result = response.json()
        # Should only delete the owned file; other_file is skipped (but skipped count not in response)
        assert result["deleted_files"] == 1


class TestAuthDisabledPermissions:
    """Tests that verify all operations are allowed when auth is disabled."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_archive_without_auth(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """When auth is disabled, anyone can delete archives."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.delete(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_without_auth(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """When auth is disabled, anyone can update archives."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            json={"print_name": "Updated Name"},
        )

        assert response.status_code == 200


class TestUserItemsCountAndDeletion(TestOwnershipPermissionsSetup):
    """Tests for user items count endpoint and deletion with items."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_user_items_count(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Verify items count endpoint returns correct counts."""
        printer = await printer_factory()
        user_id = auth_setup["operator_user"]["id"]

        # Create some items for the operator
        await archive_factory(printer.id, created_by_id=user_id)
        await archive_factory(printer.id, created_by_id=user_id)

        response = await async_client.get(
            f"/api/v1/users/{user_id}/items-count",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )

        assert response.status_code == 200
        counts = response.json()
        assert counts["archives"] >= 2
        assert "queue_items" in counts
        assert "library_files" in counts

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_user_keeps_items(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Verify deleting user without delete_items keeps items (ownerless)."""
        printer = await printer_factory()
        user_id = auth_setup["operator2_user"]["id"]

        # Create archive for operator2
        archive = await archive_factory(printer.id, created_by_id=user_id)
        archive_id = archive.id

        # Delete user without deleting items
        response = await async_client.delete(
            f"/api/v1/users/{user_id}?delete_items=false",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )

        assert response.status_code == 204

        # Verify archive still exists but is now ownerless
        archive_response = await async_client.get(
            f"/api/v1/archives/{archive_id}",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )
        assert archive_response.status_code == 200
        assert archive_response.json()["created_by_id"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_user_with_items(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Verify deleting user with delete_items=true removes their items."""
        printer = await printer_factory()

        # Create a new user with items
        create_response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
            json={
                "username": "deletewithitems",
                "password": "Password123!",
            },
        )
        user_id = create_response.json()["id"]

        # Create archive for this user
        archive = await archive_factory(printer.id, created_by_id=user_id)
        archive_id = archive.id

        # Delete user WITH deleting items
        response = await async_client.delete(
            f"/api/v1/users/{user_id}?delete_items=true",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )

        assert response.status_code == 204

        # Verify archive was deleted
        archive_response = await async_client.get(
            f"/api/v1/archives/{archive_id}",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )
        assert archive_response.status_code == 404


class TestReadIDORClosure(TestOwnershipPermissionsSetup):
    """Regression tests pinning maziggy/bambuddy-security #2 — IDOR on
    archives / library / queue read paths.

    Before the fix, ARCHIVES_READ / LIBRARY_READ / QUEUE_READ were flat
    "see everything" permissions even though the write side was split into
    OWN/ALL. An operator with only ARCHIVES_READ could read, download, and
    queue any user's archive via direct id reference. These tests pin the
    bambuddy_archive_idor.py and bambuddy_archive_viewer_idor.py PoC paths
    so the IDOR can't regress silently.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_get_others_archive_returns_404_not_200(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """PoC #2 read path. operator1 GET /archives/{id} where id is admin's
        archive must NOT leak the row. 404 (not 403) so the operator can't
        enumerate which ids exist — same shape as a nonexistent id."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Admin Archive",
            created_by_id=auth_setup["admin_user"]["id"],
        )
        response = await async_client.get(
            f"/api/v1/archives/{archive.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_download_others_archive_returns_404(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Viewer-IDOR PoC path: GET /archives/{id}/download on admin's archive.
        Before the fix this streamed the 3MF body straight to a viewer-tier
        token."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Admin Archive 2",
            created_by_id=auth_setup["admin_user"]["id"],
        )
        response = await async_client.get(
            f"/api/v1/archives/{archive.id}/download",
            headers={"Authorization": f"Bearer {auth_setup['viewer_token']}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_list_archives_excludes_others(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """GET /archives/ must filter to own archives only for OWN-level callers."""
        printer = await printer_factory()
        own = await archive_factory(
            printer.id, print_name="Operator's Own", created_by_id=auth_setup["operator_user"]["id"]
        )
        others = await archive_factory(printer.id, print_name="Admin's", created_by_id=auth_setup["admin_user"]["id"])
        response = await async_client.get(
            "/api/v1/archives/",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )
        assert response.status_code == 200
        returned_ids = {a["id"] for a in response.json()}
        assert own.id in returned_ids
        assert others.id not in returned_ids

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_list_archives_includes_all(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """ARCHIVES_READ_ALL → admin sees own + every user's archives."""
        printer = await printer_factory()
        admin_archive = await archive_factory(
            printer.id, print_name="Admin's", created_by_id=auth_setup["admin_user"]["id"]
        )
        operator_archive = await archive_factory(
            printer.id, print_name="Operator's", created_by_id=auth_setup["operator_user"]["id"]
        )
        response = await async_client.get(
            "/api/v1/archives/",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
        )
        assert response.status_code == 200
        returned_ids = {a["id"] for a in response.json()}
        assert admin_archive.id in returned_ids
        assert operator_archive.id in returned_ids

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_queue_others_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """PoC #2 queue path. POST /queue/ with admin's archive_id as
        operator1 must return 404, not create a queue item. Before the fix
        this returned 201 and queued the admin archive (Landon's CONFIRMED
        line in the PoC)."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Admin Archive (queue-target)",
            created_by_id=auth_setup["admin_user"]["id"],
        )
        response = await async_client.post(
            "/api/v1/queue/",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"archive_id": archive.id, "printer_id": printer.id, "quantity": 1},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_can_queue_others_archive(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Belt-and-suspenders for the ALL path: admin (ARCHIVES_READ_ALL) can
        queue a user's archive on their behalf — common workshop pattern."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Operator's archive (queue by admin)",
            created_by_id=auth_setup["operator_user"]["id"],
        )
        response = await async_client.post(
            "/api/v1/queue/",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
            json={"archive_id": archive.id, "printer_id": printer.id, "quantity": 1},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_get_others_library_file_returns_404(
        self, async_client: AsyncClient, auth_setup, db_session
    ):
        """Library IDOR closure (same shape as archives — closed in the same PR
        per maziggy/bambuddy-security #2)."""
        from backend.app.models.library import LibraryFile

        admin_file = LibraryFile(
            filename="admin_secret.3mf",
            file_path="library/admin_secret.3mf",
            file_type="3mf",
            file_size=2048,
            created_by_id=auth_setup["admin_user"]["id"],
        )
        db_session.add(admin_file)
        await db_session.commit()
        await db_session.refresh(admin_file)

        response = await async_client.get(
            f"/api/v1/library/files/{admin_file.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_list_library_files_excludes_others(self, async_client: AsyncClient, auth_setup, db_session):
        from backend.app.models.library import LibraryFile

        own = LibraryFile(
            filename="my_file.3mf",
            file_path="library/my_file.3mf",
            file_type="3mf",
            file_size=1024,
            created_by_id=auth_setup["operator_user"]["id"],
        )
        others = LibraryFile(
            filename="admin_file.3mf",
            file_path="library/admin_file.3mf",
            file_type="3mf",
            file_size=1024,
            created_by_id=auth_setup["admin_user"]["id"],
        )
        db_session.add_all([own, others])
        await db_session.commit()
        await db_session.refresh(own)
        await db_session.refresh(others)

        response = await async_client.get(
            "/api/v1/library/files",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )
        assert response.status_code == 200
        returned_ids = {f["id"] for f in response.json()}
        assert own.id in returned_ids
        assert others.id not in returned_ids

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_queue_list_excludes_others_items(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """GET /queue/ must filter to own queue items only for OWN callers —
        same shape as the archive list."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="A", created_by_id=auth_setup["operator_user"]["id"])
        own_item = PrintQueueItem(
            archive_id=archive.id,
            printer_id=printer.id,
            status="pending",
            position=1,
            created_by_id=auth_setup["operator_user"]["id"],
        )
        admin_item = PrintQueueItem(
            archive_id=archive.id,
            printer_id=printer.id,
            status="pending",
            position=2,
            created_by_id=auth_setup["admin_user"]["id"],
        )
        db_session.add_all([own_item, admin_item])
        await db_session.commit()
        await db_session.refresh(own_item)
        await db_session.refresh(admin_item)

        response = await async_client.get(
            "/api/v1/queue/",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )
        assert response.status_code == 200
        returned_ids = {q["id"] for q in response.json()}
        assert own_item.id in returned_ids
        assert admin_item.id not in returned_ids

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_get_others_queue_item_returns_404(
        self, async_client: AsyncClient, auth_setup, archive_factory, printer_factory, db_session
    ):
        """Direct-id queue item access — same enumeration risk as archive get."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="A", created_by_id=auth_setup["admin_user"]["id"])
        admin_item = PrintQueueItem(
            archive_id=archive.id,
            printer_id=printer.id,
            status="pending",
            position=1,
            created_by_id=auth_setup["admin_user"]["id"],
        )
        db_session.add(admin_item)
        await db_session.commit()
        await db_session.refresh(admin_item)

        response = await async_client.get(
            f"/api/v1/queue/{admin_item.id}",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auth_disabled_preserves_single_tenant_read_all(
        self, async_client: AsyncClient, archive_factory, printer_factory
    ):
        """With auth disabled, ARCHIVES_READ resolves to read-all (can_modify_all=True
        in require_ownership_permission's auth-disabled branch). Existing
        single-user installs see no behavior change."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="Anonymous", created_by_id=None)
        # No Authorization header — auth-disabled mode.
        response = await async_client.get(f"/api/v1/archives/{archive.id}")
        # Either 200 (auth disabled in this test session) or 401 (auth enabled
        # from a prior test) — both are acceptable; the IDOR closure does not
        # change auth-enable/disable behavior. Pin not-404 to avoid masking a
        # regression where auth-disabled callers would lose access.
        assert response.status_code in (200, 401)

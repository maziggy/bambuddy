"""Library folder ownership and sharing (#3201).

A library:read_own user sees their own folders, shared ones, folders holding
their files and the parents leading there; they add only to their own and
shared folders. Counts and activity times only reflect their own files.
"""

import io
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from backend.tests.integration.test_ownership_permissions import TestOwnershipPermissionsSetup


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ids(tree: list[dict]) -> set[int]:
    out: set[int] = set()
    for node in tree:
        out.add(node["id"])
        out |= _ids(node["children"])
    return out


def _find(tree: list[dict], folder_id: int) -> dict | None:
    for node in tree:
        if node["id"] == folder_id:
            return node
        found = _find(node["children"], folder_id)
        if found:
            return found
    return None


class TestFolderOwnership(TestOwnershipPermissionsSetup):
    @pytest.fixture
    async def folder(self, db_session):
        async def _create(**kwargs):
            from backend.app.models.library import LibraryFolder

            folder = LibraryFolder(**{"name": "F", **kwargs})
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)
            return folder

        return _create

    @pytest.fixture
    async def file(self, db_session):
        counter = [0]

        async def _create(**kwargs):
            from backend.app.models.library import LibraryFile

            counter[0] += 1
            row = LibraryFile(
                **{
                    "filename": f"f{counter[0]}.3mf",
                    "file_path": f"library/f{counter[0]}.3mf",
                    "file_type": "3mf",
                    "file_size": 1,
                    **kwargs,
                }
            )
            db_session.add(row)
            await db_session.commit()
            await db_session.refresh(row)
            return row

        return _create

    # ---- visibility ---------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tree_shows_only_what_the_user_may_see(self, async_client: AsyncClient, auth_setup, folder, file):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        mine = await folder(name="Mine", created_by_id=me)
        theirs = await folder(name="Theirs", created_by_id=other)
        shared = await folder(name="Class", created_by_id=other, shared=True)
        # Their private folder holding one of my files: visible, and its
        # parent too, for navigation; a sibling of it is not.
        outer = await folder(name="Outer", created_by_id=other)
        inner = await folder(name="Inner", created_by_id=other, parent_id=outer.id)
        sibling = await folder(name="Sibling", created_by_id=other, parent_id=outer.id)
        await file(folder_id=inner.id, created_by_id=me)
        unowned_private = await folder(name="Unshared")

        tree = (await async_client.get("/api/v1/library/folders", headers=_h(auth_setup["operator_token"]))).json()
        assert _ids(tree) == {mine.id, shared.id, outer.id, inner.id}
        assert theirs.id not in _ids(tree) and sibling.id not in _ids(tree)
        assert unowned_private.id not in _ids(tree)

        node = _find(tree, mine.id)
        assert node["can_write"] and node["can_rename"] and node["can_delete"] and not node["shared"]
        assert _find(tree, shared.id)["can_write"] and not _find(tree, shared.id)["can_rename"]
        # Reachable, not writable.
        assert not _find(tree, outer.id)["can_write"] and not _find(tree, inner.id)["can_write"]

        admin_tree = (await async_client.get("/api/v1/library/folders", headers=_h(auth_setup["admin_token"]))).json()
        assert {theirs.id, sibling.id, unowned_private.id} <= _ids(admin_tree)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_counts_and_activity_reflect_own_files_only(
        self, async_client: AsyncClient, auth_setup, folder, file
    ):
        """lonix's point on #3201: counts and times leaked other users' files."""
        from datetime import datetime

        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        shared = await folder(name="Class", shared=True, created_by_id=other)
        await file(folder_id=shared.id, created_by_id=me)
        recent = datetime(2030, 1, 1)
        await file(folder_id=shared.id, created_by_id=other, updated_at=recent)
        await file(folder_id=shared.id, created_by_id=other)

        tree = (await async_client.get("/api/v1/library/folders", headers=_h(auth_setup["operator_token"]))).json()
        node = _find(tree, shared.id)
        assert node["file_count"] == 1
        assert not node["latest_activity_at"].startswith("2030")

        single = await async_client.get(
            f"/api/v1/library/folders/{shared.id}", headers=_h(auth_setup["operator_token"])
        )
        assert single.json()["file_count"] == 1

        admin = (await async_client.get("/api/v1/library/folders", headers=_h(auth_setup["admin_token"]))).json()
        assert _find(admin, shared.id)["file_count"] == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hidden_folder_answers_404(self, async_client: AsyncClient, auth_setup, folder):
        theirs = await folder(name="Theirs", created_by_id=auth_setup["operator2_user"]["id"])
        token = auth_setup["operator_token"]
        assert (await async_client.get(f"/api/v1/library/folders/{theirs.id}", headers=_h(token))).status_code == 404
        readme = await async_client.get(f"/api/v1/library/folders/{theirs.id}/readme", headers=_h(token))
        assert readme.status_code == 404 and readme.json()["detail"] == "Folder not found"
        assert (
            await async_client.get(f"/api/v1/library/folders/{theirs.id}", headers=_h(auth_setup["operator2_token"]))
        ).status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stats_count_visible_folders(self, async_client: AsyncClient, auth_setup, folder):
        await folder(name="Mine", created_by_id=auth_setup["operator_user"]["id"])
        await folder(name="Theirs", created_by_id=auth_setup["operator2_user"]["id"])
        await folder(name="Class", shared=True)
        stats = await async_client.get("/api/v1/library/stats", headers=_h(auth_setup["operator_token"]))
        assert stats.json()["total_folders"] == 2
        admin = await async_client.get("/api/v1/library/stats", headers=_h(auth_setup["admin_token"]))
        assert admin.json()["total_folders"] == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_of_a_hidden_mount_answers_404(self, async_client: AsyncClient, auth_setup, folder):
        hidden = await folder(
            name="Mount", is_external=True, external_path="/nonexistent", created_by_id=auth_setup["admin_user"]["id"]
        )
        response = await async_client.post(
            f"/api/v1/library/folders/{hidden.id}/scan", headers=_h(auth_setup["operator_token"])
        )
        assert response.status_code == 404

    # ---- creating and adding ------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_new_folder_belongs_to_its_creator(self, async_client: AsyncClient, auth_setup):
        created = await async_client.post(
            "/api/v1/library/folders", json={"name": "Assignment 1"}, headers=_h(auth_setup["operator_token"])
        )
        assert created.status_code == 200
        body = created.json()
        assert body["created_by_id"] == auth_setup["operator_user"]["id"]
        assert body["shared"] is False and body["can_write"] is True

        other = (await async_client.get("/api/v1/library/folders", headers=_h(auth_setup["operator2_token"]))).json()
        assert body["id"] not in _ids(other)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_folder_made_without_a_user_is_shared(self, async_client: AsyncClient):
        """Auth off: nobody to own it, so it stays everyone's, as before."""
        body = (await async_client.post("/api/v1/library/folders", json={"name": "Open"})).json()
        assert body["created_by_id"] is None and body["shared"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_subfolder_only_where_the_user_may_write(self, async_client: AsyncClient, auth_setup, folder, file):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        theirs = await folder(name="Theirs", created_by_id=other)
        shared = await folder(name="Class", created_by_id=other, shared=True)
        passthrough = await folder(name="Outer", created_by_id=other)
        await file(folder_id=passthrough.id, created_by_id=me)
        token = auth_setup["operator_token"]

        async def create(parent_id):
            return await async_client.post(
                "/api/v1/library/folders", json={"name": "Sub", "parent_id": parent_id}, headers=_h(token)
            )

        assert (await create(theirs.id)).status_code == 404
        assert (await create(passthrough.id)).status_code == 403
        assert (await create(shared.id)).status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_files_only_into_writable_folders(self, async_client: AsyncClient, auth_setup, folder, file):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        theirs = await folder(name="Theirs", created_by_id=other)
        shared = await folder(name="Class", shared=True)
        mine = await file(created_by_id=me)
        token = auth_setup["operator_token"]

        moved = await async_client.post(
            "/api/v1/library/files/move", json={"file_ids": [mine.id], "folder_id": theirs.id}, headers=_h(token)
        )
        assert moved.status_code == 404
        update = await async_client.put(
            f"/api/v1/library/files/{mine.id}", json={"folder_id": theirs.id}, headers=_h(token)
        )
        assert update.status_code == 404
        moved = await async_client.post(
            "/api/v1/library/files/move", json={"file_ids": [mine.id], "folder_id": shared.id}, headers=_h(token)
        )
        assert moved.status_code == 200 and moved.json()["moved"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_zip_never_reuses_another_users_folder(self, async_client: AsyncClient, auth_setup, folder):
        """Extracting "Alice.zip" must not land in Alice's own "Alice" folder."""
        alice_folder = await folder(name="Alice", created_by_id=auth_setup["operator2_user"]["id"])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Alice/model.txt", "x")
        response = await async_client.post(
            "/api/v1/library/files/extract-zip",
            files={"file": ("Alice.zip", buf.getvalue(), "application/zip")},
            params={"preserve_structure": "true", "create_folder_from_zip": "true"},
            headers=_h(auth_setup["operator_token"]),
        )
        assert response.status_code == 200
        landed = response.json()["files"][0]["folder_id"]
        assert landed != alice_folder.id
        tree = (await async_client.get("/api/v1/library/folders", headers=_h(auth_setup["operator_token"]))).json()
        assert alice_folder.id not in _ids(tree) and landed in _ids(tree)

    # ---- renaming, sharing, deleting ---------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_owner_renames_admin_shares(self, async_client: AsyncClient, auth_setup, folder):
        me = auth_setup["operator_user"]["id"]
        mine = await folder(name="Mine", created_by_id=me)
        class_folder = await folder(name="Class", shared=True, created_by_id=auth_setup["admin_user"]["id"])
        token = auth_setup["operator_token"]

        renamed = await async_client.put(
            f"/api/v1/library/folders/{mine.id}", json={"name": "Mine2"}, headers=_h(token)
        )
        assert renamed.status_code == 200 and renamed.json()["name"] == "Mine2"
        assert (
            await async_client.put(f"/api/v1/library/folders/{class_folder.id}", json={"name": "X"}, headers=_h(token))
        ).status_code == 403
        assert (
            await async_client.put(f"/api/v1/library/folders/{mine.id}", json={"shared": True}, headers=_h(token))
        ).status_code == 403

        # The admin shares my folder: operator2 now sees it and may add to it.
        shared = await async_client.put(
            f"/api/v1/library/folders/{mine.id}", json={"shared": True}, headers=_h(auth_setup["admin_token"])
        )
        assert shared.status_code == 200 and shared.json()["shared"] is True
        other = (await async_client.get("/api/v1/library/folders", headers=_h(auth_setup["operator2_token"]))).json()
        assert _find(other, mine.id)["can_write"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_owner_moves_folder_only_into_writable_parent(self, async_client: AsyncClient, auth_setup, folder):
        me = auth_setup["operator_user"]["id"]
        mine = await folder(name="Mine", created_by_id=me)
        theirs = await folder(name="Theirs", created_by_id=auth_setup["operator2_user"]["id"])
        token = auth_setup["operator_token"]
        assert (
            await async_client.put(
                f"/api/v1/library/folders/{mine.id}", json={"parent_id": theirs.id}, headers=_h(token)
            )
        ).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_owner_deletes_folder_only_when_everything_is_theirs(
        self, async_client: AsyncClient, auth_setup, folder, file
    ):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        token = auth_setup["operator_token"]

        own_full = await folder(name="Full", created_by_id=me)
        await folder(name="Sub", created_by_id=me, parent_id=own_full.id)
        await file(folder_id=own_full.id, created_by_id=me)
        assert (
            await async_client.delete(f"/api/v1/library/folders/{own_full.id}", headers=_h(token))
        ).status_code == 200

        mixed = await folder(name="Mixed", created_by_id=me, shared=True)
        await file(folder_id=mixed.id, created_by_id=other)
        assert (await async_client.delete(f"/api/v1/library/folders/{mixed.id}", headers=_h(token))).status_code == 403

        their_shared = await folder(name="Class", created_by_id=other, shared=True)
        assert (
            await async_client.delete(f"/api/v1/library/folders/{their_shared.id}", headers=_h(token))
        ).status_code == 403

        theirs = await folder(name="Theirs", created_by_id=other)
        assert (await async_client.delete(f"/api/v1/library/folders/{theirs.id}", headers=_h(token))).status_code == 404

        bulk = await async_client.post(
            "/api/v1/library/bulk-delete",
            json={"file_ids": [], "folder_ids": [theirs.id, their_shared.id]},
            headers=_h(token),
        )
        assert bulk.json()["deleted_folders"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_all_permissions_reach_folders_they_cannot_see(
        self, async_client: AsyncClient, auth_setup, folder, file
    ):
        """A group with update_all/delete_all but only read_own kept full reach before #3201."""
        admin = _h(auth_setup["admin_token"])
        groups = (await async_client.get("/api/v1/groups/", headers=admin)).json()
        operators = next(g for g in groups if g["name"] == "Operators")
        perms = [p for p in operators["permissions"] if p not in ("library:update_own", "library:delete_own")]
        perms += ["library:update_all", "library:delete_all"]
        group = await async_client.post(
            "/api/v1/groups/", json={"name": "Editors", "permissions": perms}, headers=admin
        )
        assert group.status_code in (200, 201), group.text
        user = await async_client.post(
            "/api/v1/users/",
            json={"username": "editor1", "password": "Editorpass1!", "group_ids": [group.json()["id"]]},
            headers=admin,
        )
        assert user.status_code in (200, 201), user.text
        login = await async_client.post("/api/v1/auth/login", json={"username": "editor1", "password": "Editorpass1!"})
        token = login.json()["access_token"]
        other = auth_setup["operator2_user"]["id"]
        theirs = await folder(name="Theirs", created_by_id=other)
        doomed = await folder(name="Doomed", created_by_id=other)
        mine = await file(created_by_id=user.json()["id"])

        # Still hidden from view: read_own decides what is listed.
        tree = (await async_client.get("/api/v1/library/folders", headers=_h(token))).json()
        assert theirs.id not in _ids(tree)
        # But the *_all permissions act on it as before.
        renamed = await async_client.put(f"/api/v1/library/folders/{theirs.id}", json={"name": "R"}, headers=_h(token))
        assert renamed.status_code == 200
        moved = await async_client.post(
            "/api/v1/library/files/move", json={"file_ids": [mine.id], "folder_id": theirs.id}, headers=_h(token)
        )
        assert moved.status_code == 200 and moved.json()["moved"] == 1
        assert (await async_client.delete(f"/api/v1/library/folders/{doomed.id}", headers=_h(token))).status_code == 200

    # ---- imports ------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_default_import_folder(self, db_session, auth_setup):
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from backend.app.models.user import User
        from backend.app.services.library_folder_access import default_import_folder

        users = {
            u.id: u for u in (await db_session.execute(select(User).options(selectinload(User.groups)))).scalars().all()
        }
        op1 = users[auth_setup["operator_user"]["id"]]
        op2 = users[auth_setup["operator2_user"]["id"]]

        first = await default_import_folder(db_session, "MakerWorld", op1)
        assert first.shared is True
        assert (await default_import_folder(db_session, "MakerWorld", op2)).id == first.id

        # An admin made it private: op2 gets one of their own, not a refusal.
        first.shared = False
        await db_session.flush()
        own = await default_import_folder(db_session, "MakerWorld", op2)
        assert own.id != first.id and own.created_by_id == op2.id and own.shared is False


class TestFolderOwnerBackfill:
    @pytest.mark.asyncio
    async def test_infers_owners_once(self, test_engine, db_session):
        from backend.app.core.database import _backfill_library_folder_owners
        from backend.app.models.library import LibraryFile, LibraryFolder
        from backend.app.models.project import Project
        from backend.app.models.user import User

        alice = User(username="alice", password_hash="x", role="user")
        bob = User(username="bob", password_hash="x", role="user")
        project = Project(name="P")
        db_session.add_all([alice, bob, project])
        await db_session.flush()

        def folder(name, parent=None, **kw):
            f = LibraryFolder(name=name, parent_id=parent.id if parent else None, **kw)
            db_session.add(f)
            return f

        students = folder("Students")
        await db_session.flush()
        a = folder("Alice", students)
        b = folder("Bob", students)
        empty = folder("Testing")
        maker = folder("MakerWorld")
        linked = folder("Linked")
        await db_session.flush()
        a_old = folder("Old", a)  # empty, inside Alice's folder
        linked.project_id = project.id
        await db_session.flush()

        from datetime import datetime, timezone

        db_session.add_all(
            [
                LibraryFile(
                    folder_id=a.id, created_by_id=alice.id, filename="1", file_path="1", file_type="3mf", file_size=1
                ),
                LibraryFile(
                    folder_id=a.id,
                    created_by_id=alice.id,
                    filename="2",
                    file_path="2",
                    file_type="3mf",
                    file_size=1,
                    deleted_at=datetime.now(timezone.utc),
                ),
                LibraryFile(
                    folder_id=b.id, created_by_id=bob.id, filename="3", file_path="3", file_type="3mf", file_size=1
                ),
                LibraryFile(
                    folder_id=maker.id,
                    created_by_id=alice.id,
                    filename="4",
                    file_path="4",
                    file_type="3mf",
                    file_size=1,
                ),
                LibraryFile(
                    folder_id=linked.id, created_by_id=bob.id, filename="5", file_path="5", file_type="3mf", file_size=1
                ),
            ]
        )
        await db_session.commit()
        ids = {
            n: f.id
            for n, f in {
                "students": students,
                "a": a,
                "b": b,
                "empty": empty,
                "maker": maker,
                "linked": linked,
                "a_old": a_old,
            }.items()
        }
        alice_id, bob_id = alice.id, bob.id

        async with test_engine.begin() as conn:
            await _backfill_library_folder_owners(conn)

        async with test_engine.begin() as conn:
            rows = {
                r[0]: (r[1], bool(r[2]))
                for r in (await conn.execute(text("SELECT id, created_by_id, shared FROM library_folders"))).all()
            }
        assert rows[ids["a"]] == (alice_id, False)
        assert rows[ids["a_old"]] == (alice_id, False)
        assert rows[ids["b"]] == (bob_id, False)
        assert rows[ids["students"]] == (None, True)  # two owners below it
        assert rows[ids["empty"]] == (None, True)
        assert rows[ids["maker"]] == (None, True)  # import destination
        assert rows[ids["linked"]] == (None, True)

        # Gated: a second boot leaves an admin's later choice alone.
        async with test_engine.begin() as conn:
            await conn.execute(text("UPDATE library_folders SET shared = FALSE WHERE id = :i"), {"i": ids["empty"]})
            await _backfill_library_folder_owners(conn)
            again = (
                await conn.execute(text("SELECT shared FROM library_folders WHERE id = :i"), {"i": ids["empty"]})
            ).scalar_one()
        assert not again

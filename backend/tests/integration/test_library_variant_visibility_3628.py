"""Variant groups only show the members the caller may read (#3628).

A library:read_own user must not learn the names, positions or number of other
users' files through a group they share a file with.
"""

from datetime import datetime, timezone

import pytest

from backend.tests.integration.test_ownership_permissions import TestOwnershipPermissionsSetup


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _GroupFixtures(TestOwnershipPermissionsSetup):
    @pytest.fixture
    async def make_group(self, db_session):
        """Create a group whose members are (owner_id, model, deleted) tuples."""

        async def _create(members: list[tuple[int, str, bool]], created_by: int | None = None, name: str = "bracket"):
            from backend.app.models.library import FileVariantGroup, LibraryFile

            group = FileVariantGroup(name=name, created_by_id=created_by)
            db_session.add(group)
            await db_session.flush()
            files = []
            for position, (owner_id, model, deleted) in enumerate(members):
                f = LibraryFile(
                    filename=f"{model}_{position}.gcode.3mf",
                    file_path=f"library/{model}_{position}.gcode.3mf",
                    file_type="gcode.3mf",
                    file_size=1,
                    file_metadata={"sliced_for_model": model},
                    created_by_id=owner_id,
                    variant_group_id=group.id,
                    variant_position=position,
                    deleted_at=datetime.now(timezone.utc) if deleted else None,
                )
                db_session.add(f)
                files.append(f)
            await db_session.commit()
            for f in files:
                await db_session.refresh(f)
            await db_session.refresh(group)
            return group, files

        return _create


class TestVariantGroupVisibility(_GroupFixtures):
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_read_own_sees_only_own_members(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (other, "H2C", False), (me, "P1S", False)])
        token = auth_setup["operator_token"]

        r = await async_client.get(f"/api/v1/library/variant-groups/{group.id}", headers=_h(token))
        assert r.status_code == 200
        assert [m["library_file_id"] for m in r.json()["members"]] == [files[0].id, files[2].id]

        r = await async_client.get(f"/api/v1/library/variant-groups/by-file/{files[0].id}", headers=_h(token))
        assert r.status_code == 200
        assert [m["library_file_id"] for m in r.json()["members"]] == [files[0].id, files[2].id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_read_own_gets_404_when_no_member_is_theirs(self, async_client, auth_setup, make_group):
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(other, "H2S", False), (other, "H2C", False)])
        token = auth_setup["operator_token"]

        r = await async_client.get(f"/api/v1/library/variant-groups/{group.id}", headers=_h(token))
        assert r.status_code == 404
        r = await async_client.get(f"/api/v1/library/variant-groups/by-file/{files[0].id}", headers=_h(token))
        assert r.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_read_all_sees_every_member(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (other, "H2C", False)])

        r = await async_client.get(f"/api/v1/library/variant-groups/{group.id}", headers=_h(auth_setup["admin_token"]))
        assert r.status_code == 200
        assert [m["library_file_id"] for m in r.json()["members"]] == [f.id for f in files]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleted_members_are_excluded(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (me, "H2C", False), (me, "P1S", True)])

        r = await async_client.get(f"/api/v1/library/variant-groups/{group.id}", headers=_h(auth_setup["admin_token"]))
        assert [m["library_file_id"] for m in r.json()["members"]] == [files[0].id, files[1].id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_variant_count_in_the_file_list_counts_visible_members(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        _, files = await make_group([(me, "H2S", False), (other, "H2C", False), (me, "P1S", False), (me, "X1C", True)])

        own = await async_client.get("/api/v1/library/files", headers=_h(auth_setup["operator_token"]))
        assert {f["id"]: f["variant_count"] for f in own.json()} == {files[0].id: 2, files[2].id: 2}

        everyone = await async_client.get("/api/v1/library/files", headers=_h(auth_setup["admin_token"]))
        assert {f["variant_count"] for f in everyone.json()} == {3}


class TestListWithVariantMembers(_GroupFixtures):
    @pytest.fixture
    async def folders(self, db_session):
        from backend.app.models.library import LibraryFolder

        rows = [LibraryFolder(name="Here"), LibraryFolder(name="There"), LibraryFolder(name="NAS", is_external=True)]
        db_session.add_all(rows)
        await db_session.commit()
        for r in rows:
            await db_session.refresh(r)
        return rows

    async def _place(self, db_session, files, folder_ids):
        for f, folder_id in zip(files, folder_ids, strict=True):
            f.folder_id = folder_id
        await db_session.commit()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_default_response_is_unchanged(self, async_client, auth_setup, make_group, folders, db_session):
        here, there, _ = folders
        me = auth_setup["operator_user"]["id"]
        _, files = await make_group([(me, "H2S", False), (me, "H2C", False)])
        await self._place(db_session, files, [here.id, there.id])

        r = await async_client.get(
            f"/api/v1/library/files?folder_id={here.id}", headers=_h(auth_setup["operator_token"])
        )
        rows = r.json()
        assert [f["id"] for f in rows] == [files[0].id]
        assert rows[0]["variant_group_name"] is None and rows[0]["variant_position"] is None
        assert rows[0]["variant_elsewhere"] is False and rows[0]["folder_name"] is None
        assert rows[0]["variant_count"] == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_members_from_other_folders_are_appended_and_flagged(
        self, async_client, auth_setup, make_group, folders, db_session
    ):
        here, there, nas = folders
        me = auth_setup["operator_user"]["id"]
        _, files = await make_group([(me, "H2S", False), (me, "H2C", False), (me, "P1S", False)], created_by=me)
        await self._place(db_session, files, [here.id, there.id, nas.id])
        files[2].is_external = True
        await db_session.commit()

        r = await async_client.get(
            f"/api/v1/library/files?folder_id={here.id}&with_variant_members=true",
            headers=_h(auth_setup["operator_token"]),
        )
        rows = {f["id"]: f for f in r.json()}
        assert set(rows) == {f.id for f in files}
        assert rows[files[0].id]["variant_elsewhere"] is False
        assert rows[files[0].id]["folder_name"] is None
        assert rows[files[1].id]["variant_elsewhere"] is True
        assert rows[files[1].id]["folder_id"] == there.id and rows[files[1].id]["folder_name"] == "There"
        assert rows[files[2].id]["folder_name"] == "NAS" and rows[files[2].id]["is_external"] is True
        assert {f["variant_group_name"] for f in rows.values()} == {"bracket"}
        assert [rows[f.id]["variant_position"] for f in files] == [0, 1, 2]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_members_already_listed_are_not_duplicated(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        _, files = await make_group([(me, "H2S", False), (me, "H2C", False)])

        r = await async_client.get(
            "/api/v1/library/files?with_variant_members=true", headers=_h(auth_setup["operator_token"])
        )
        rows = r.json()
        assert sorted(f["id"] for f in rows) == sorted(f.id for f in files)
        assert not any(f["variant_elsewhere"] for f in rows)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ownership_and_deleted_members_are_respected(
        self, async_client, auth_setup, make_group, folders, db_session
    ):
        here, there, _ = folders
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        _, files = await make_group([(me, "H2S", False), (other, "H2C", False), (me, "P1S", False), (me, "X1C", True)])
        await self._place(db_session, files, [here.id, there.id, there.id, there.id])
        url = f"/api/v1/library/files?folder_id={here.id}&with_variant_members=true"

        own = await async_client.get(url, headers=_h(auth_setup["operator_token"]))
        assert {f["id"] for f in own.json()} == {files[0].id, files[2].id}
        assert {f["variant_count"] for f in own.json()} == {2}

        everyone = await async_client.get(url, headers=_h(auth_setup["admin_token"]))
        assert {f["id"] for f in everyone.json()} == {files[0].id, files[1].id, files[2].id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ungrouped_files_get_no_group_fields(self, async_client, auth_setup, make_group, db_session):
        from backend.app.models.library import LibraryFile

        me = auth_setup["operator_user"]["id"]
        db_session.add(
            LibraryFile(filename="a.3mf", file_path="library/a.3mf", file_type="3mf", file_size=1, created_by_id=me)
        )
        await db_session.commit()

        r = await async_client.get(
            "/api/v1/library/files?with_variant_members=true", headers=_h(auth_setup["operator_token"])
        )
        row = r.json()[0]
        assert row["variant_group_name"] is None and row["variant_position"] is None
        assert row["variant_elsewhere"] is False


class TestVariantGroupChangesCheckOwnership(_GroupFixtures):
    """Changing a group needs ownership of every live member (#3628)."""

    @pytest.fixture
    async def make_loose(self, db_session):
        """An ungrouped sliced file (owner_id, model)."""

        async def _create(owner_id: int, model: str):
            from backend.app.models.library import LibraryFile

            f = LibraryFile(
                filename=f"loose_{model}.gcode.3mf",
                file_path=f"library/loose_{model}.gcode.3mf",
                file_type="gcode.3mf",
                file_size=1,
                file_metadata={"sliced_for_model": model},
                created_by_id=owner_id,
            )
            db_session.add(f)
            await db_session.commit()
            await db_session.refresh(f)
            return f

        return _create

    @pytest.fixture
    async def reader(self, async_client, auth_setup):
        """A read_all + update_own user."""
        admin = _h(auth_setup["admin_token"])
        group = await async_client.post(
            "/api/v1/groups/",
            headers=admin,
            json={"name": "ReadAllUpdateOwn", "permissions": ["library:read_all", "library:update_own"]},
        )
        assert group.status_code in (200, 201)
        await async_client.post(
            "/api/v1/users/",
            headers=admin,
            json={"username": "reader1", "password": "Readerpass1!", "group_ids": [group.json()["id"]]},
        )
        login = await async_client.post("/api/v1/auth/login", json={"username": "reader1", "password": "Readerpass1!"})
        return login.json()["access_token"]

    @staticmethod
    def _url(group, suffix=""):
        return f"/api/v1/library/variant-groups/{group.id}{suffix}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_foreign_group_is_404_for_every_change(self, async_client, auth_setup, make_group, make_loose):
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(other, "H2S", False), (other, "H2C", False)])
        extra = await make_loose(other, "P1S")
        h = _h(auth_setup["operator_token"])

        responses = [
            await async_client.patch(self._url(group), json={"name": "mine"}, headers=h),
            await async_client.patch(self._url(group), json={"member_file_ids": [files[1].id, files[0].id]}, headers=h),
            await async_client.patch(self._url(group), json={"member_file_ids": [999999]}, headers=h),
            await async_client.post(self._url(group, "/members"), json={"library_file_id": extra.id}, headers=h),
            await async_client.delete(self._url(group, f"/members/{files[0].id}"), headers=h),
            await async_client.delete(self._url(group, "/members/999999"), headers=h),
            await async_client.delete(self._url(group), headers=h),
        ]
        assert {r.status_code for r in responses} == {404}
        assert {r.json()["detail"] for r in responses} == {"Variant group not found"}
        # A group that does not exist answers identically.
        missing = await async_client.delete("/api/v1/library/variant-groups/999999", headers=h)
        assert (missing.status_code, missing.json()["detail"]) == (404, "Variant group not found")

        r = await async_client.get(self._url(group), headers=_h(auth_setup["admin_token"]))
        assert r.json()["name"] == "bracket" and len(r.json()["members"]) == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mixed_group_is_403_for_every_change(self, async_client, auth_setup, make_group, make_loose):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (other, "H2C", False)])
        mine = await make_loose(me, "P1S")
        h = _h(auth_setup["operator_token"])

        responses = [
            await async_client.patch(self._url(group), json={"name": "mine"}, headers=h),
            await async_client.patch(self._url(group), json={"member_file_ids": [files[1].id, files[0].id]}, headers=h),
            await async_client.post(self._url(group, "/members"), json={"library_file_id": mine.id}, headers=h),
            await async_client.delete(self._url(group, f"/members/{files[0].id}"), headers=h),
            await async_client.delete(self._url(group), headers=h),
        ]
        assert {r.status_code for r in responses} == {403}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trashed_foreign_member_does_not_block_the_owner(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (me, "H2C", False), (other, "P1S", True)], created_by=me)
        h = _h(auth_setup["operator_token"])

        # Reorder lists the live members only; the trashed one is ignored.
        r = await async_client.patch(self._url(group), json={"member_file_ids": [files[1].id, files[0].id]}, headers=h)
        assert r.status_code == 200, r.text
        assert [m["library_file_id"] for m in r.json()["members"]] == [files[1].id, files[0].id]

        r = await async_client.patch(self._url(group), json={"name": "renamed"}, headers=h)
        assert r.status_code == 200 and r.json()["name"] == "renamed"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_can_reorder_a_group_with_a_trashed_member(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (me, "H2C", False), (me, "P1S", True)])

        r = await async_client.patch(
            self._url(group),
            json={"member_file_ids": [files[1].id, files[0].id]},
            headers=_h(auth_setup["admin_token"]),
        )
        assert r.status_code == 200, r.text
        bad = await async_client.patch(
            self._url(group),
            json={"member_file_ids": [files[1].id, files[0].id, files[2].id]},
            headers=_h(auth_setup["admin_token"]),
        )
        assert bad.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_owner_and_admin_still_succeed(self, async_client, auth_setup, make_group, make_loose):
        me = auth_setup["operator_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (me, "H2C", False)])
        extra = await make_loose(me, "P1S")
        h = _h(auth_setup["operator_token"])

        r = await async_client.post(self._url(group, "/members"), json={"library_file_id": extra.id}, headers=h)
        assert r.status_code == 200 and len(r.json()["members"]) == 3
        r = await async_client.delete(self._url(group, f"/members/{extra.id}"), headers=h)
        assert r.status_code == 204
        r = await async_client.delete(self._url(group), headers=h)
        assert r.status_code == 204

        mixed, mfiles = await make_group([(me, "H2S", False), (auth_setup["operator2_user"]["id"], "H2C", False)])
        r = await async_client.patch(self._url(mixed), json={"name": "admin"}, headers=_h(auth_setup["admin_token"]))
        assert r.status_code == 200 and r.json()["name"] == "admin"
        r = await async_client.delete(self._url(mixed), headers=_h(auth_setup["admin_token"]))
        assert r.status_code == 204

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_removing_down_to_one_live_member_dissolves_despite_a_trashed_one(
        self, async_client, auth_setup, make_group, db_session
    ):
        from backend.app.models.library import LibraryFile

        me = auth_setup["operator_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (me, "H2C", False), (me, "P1S", True)])
        kept_id = files[0].id

        r = await async_client.delete(
            self._url(group, f"/members/{files[1].id}"), headers=_h(auth_setup["operator_token"])
        )
        assert r.status_code == 204
        gone = await async_client.get(self._url(group), headers=_h(auth_setup["admin_token"]))
        assert gone.status_code == 404
        db_session.expire_all()
        assert (await db_session.get(LibraryFile, kept_id)).variant_group_id is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_collision_message_names_only_readable_files(self, async_client, auth_setup, make_group, make_loose):
        me = auth_setup["operator_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (me, "H2C", False)])
        dup = await make_loose(me, "H2S")

        r = await async_client.post(
            self._url(group, "/members"),
            json={"library_file_id": dup.id},
            headers=_h(auth_setup["operator_token"]),
        )
        assert r.status_code == 400 and files[0].filename in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mutation_response_uses_the_read_permission(self, async_client, auth_setup, make_group, reader):
        from backend.app.models.user import User  # noqa: F401

        # read_all + update_own: the reader owns every live member of its own group
        # but may also read other users' files, so a group with a trashed foreign
        # member is theirs to change and the response lists what they may read.
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        # operator1 (read_own) renames: response lists their own members only.
        group, files = await make_group([(me, "H2S", False), (me, "H2C", False), (other, "P1S", True)])
        r = await async_client.patch(self._url(group), json={"name": "x"}, headers=_h(auth_setup["operator_token"]))
        assert [m["library_file_id"] for m in r.json()["members"]] == [files[0].id, files[1].id]

        # reader is neither owner nor admin: 403 on a group of others' files, 404 never
        # (it sees the members), and nothing changes.
        r = await async_client.patch(self._url(group), json={"name": "y"}, headers=_h(reader))
        assert r.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_read_all_update_own_user_sees_all_members_in_the_response(
        self, async_client, auth_setup, make_group, reader, db_session
    ):
        from sqlalchemy import select

        from backend.app.models.user import User

        reader_id = await db_session.scalar(select(User.id).where(User.username == "reader1"))
        other = auth_setup["operator2_user"]["id"]
        # Own live members plus a trashed foreign one: changeable, and the response
        # is filtered by the READ permission (all live members), not by update.
        group, files = await make_group([(reader_id, "H2S", False), (reader_id, "H2C", False), (other, "P1S", True)])
        r = await async_client.patch(self._url(group), json={"name": "z"}, headers=_h(reader))
        assert r.status_code == 200, r.text
        assert [m["library_file_id"] for m in r.json()["members"]] == [files[0].id, files[1].id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_group_name_equal_to_a_hidden_filename_is_not_shown(
        self, async_client, auth_setup, make_group, db_session
    ):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(other, "H2S", False), (me, "H2C", False)])
        group.name = files[0].filename  # named after another user's file
        await db_session.commit()
        h = _h(auth_setup["operator_token"])

        r = await async_client.get(self._url(group), headers=h)
        assert r.status_code == 200
        assert files[0].filename not in r.text
        assert r.json()["name"] == files[1].filename

        listing = await async_client.get("/api/v1/library/files?with_variant_members=true", headers=h)
        assert files[0].filename not in listing.text
        assert {f["variant_group_name"] for f in listing.json()} == {None}

        everyone = await async_client.get(self._url(group), headers=_h(auth_setup["admin_token"]))
        assert everyone.json()["name"] == files[0].filename

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_own_scoped_api_key_behaves_like_its_owner(self, async_client, auth_setup, make_group, db_session):
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(
                name="own",
                key_hash=key_hash,
                key_prefix=key_prefix,
                user_id=me,
                can_queue=True,
                can_read_status=True,
                can_manage_archives=True,
                can_manage_library=True,
            )
        )
        await db_session.commit()
        key = {"X-API-Key": full_key}
        foreign, _ = await make_group([(other, "H2S", False), (other, "H2C", False)])
        mixed, _ = await make_group([(me, "H2S", False), (other, "H2C", False)])
        mine, _ = await make_group([(me, "H2S", False), (me, "H2C", False)])

        assert (await async_client.delete(self._url(foreign), headers=key)).status_code == 404
        assert (await async_client.delete(self._url(mixed), headers=key)).status_code == 403
        assert (await async_client.patch(self._url(mine), json={"name": "k"}, headers=key)).status_code == 200
        assert (await async_client.delete(self._url(mine), headers=key)).status_code == 204


class TestWithVariantMembersFolderKind(_GroupFixtures):
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_internal_and_external_only_restrict_appended_members(
        self, async_client, auth_setup, make_group, db_session
    ):
        me = auth_setup["operator_user"]["id"]
        _, files = await make_group([(me, "H2S", False), (me, "H2C", False), (me, "P1S", False)])
        files[2].is_external = True
        await db_session.commit()
        h = _h(auth_setup["operator_token"])
        base = "/api/v1/library/files?with_variant_members=true"

        internal = await async_client.get(f"{base}&internal_only=true", headers=h)
        assert {f["id"] for f in internal.json()} == {files[0].id, files[1].id}
        external = await async_client.get(f"{base}&external_only=true", headers=h)
        assert {f["id"] for f in external.json()} == {files[2].id}


class TestGroupNameNeverLeaksAFilename(_GroupFixtures):
    """A stored group name is shown only where it cannot be someone else's filename (#3628)."""

    async def _names(self, async_client, group, token):
        h = _h(token)
        single = await async_client.get(f"/api/v1/library/variant-groups/{group.id}", headers=h)
        listing = await async_client.get("/api/v1/library/files?with_variant_members=true", headers=h)
        return single, listing

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_name_stays_hidden_after_the_other_users_file_is_trashed(
        self, async_client, auth_setup, make_group, db_session
    ):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group(
            [(other, "H2S", False), (me, "H2C", False), (me, "P1S", False)], created_by=other
        )
        leaked = files[0].filename
        group.name = leaked  # named after the other user's file, as create does
        files[0].deleted_at = datetime.now(timezone.utc)
        await db_session.commit()

        single, listing = await self._names(async_client, group, auth_setup["operator_token"])
        assert single.status_code == 200
        assert leaked not in single.text and leaked not in listing.text
        assert single.json()["name"] == files[1].filename
        assert {f["variant_group_name"] for f in listing.json()} == {None}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_name_stays_hidden_after_the_other_users_file_is_renamed(
        self, async_client, auth_setup, make_group, db_session
    ):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(other, "H2S", False), (me, "H2C", False)], created_by=other)
        leaked = files[0].filename
        group.name = leaked
        files[0].filename = "renamed_since.gcode.3mf"
        await db_session.commit()

        single, listing = await self._names(async_client, group, auth_setup["operator_token"])
        assert leaked not in single.text and leaked not in listing.text
        assert single.json()["name"] == files[1].filename

        # Whoever may read everything still sees the stored name.
        everyone = await async_client.get(
            f"/api/v1/library/variant-groups/{group.id}", headers=_h(auth_setup["admin_token"])
        )
        assert everyone.json()["name"] == leaked

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_creator_and_a_name_of_an_own_file_are_shown(self, async_client, auth_setup, make_group, db_session):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        # The caller created the group: its name is theirs to see, whatever it is.
        typed, _ = await make_group([(me, "H2S", False), (me, "H2C", False)], created_by=me, name="My bracket")
        # Someone else created it, but it is named after the caller's own file.
        own_named, files = await make_group([(me, "H2S", False), (me, "H2C", False)], created_by=other)
        own_named.name = files[0].filename
        # Someone else created and named it: not shown, falls back to the caller's first file.
        foreign, foreign_files = await make_group(
            [(me, "H2S", False), (me, "H2C", False)], created_by=other, name="Somebody's name"
        )
        await db_session.commit()
        h = _h(auth_setup["operator_token"])

        async def name_of(group):
            return (await async_client.get(f"/api/v1/library/variant-groups/{group.id}", headers=h)).json()["name"]

        assert await name_of(typed) == "My bracket"
        assert await name_of(own_named) == files[0].filename
        assert await name_of(foreign) == foreign_files[0].filename

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_created_group_is_named_after_the_first_file_and_shown_to_its_creator(
        self, async_client, auth_setup, db_session
    ):
        from backend.app.models.library import LibraryFile

        me = auth_setup["operator_user"]["id"]
        files = []
        for model in ("H2S", "H2C"):
            f = LibraryFile(
                filename=f"new_{model}.gcode.3mf",
                file_path=f"library/new_{model}.gcode.3mf",
                file_type="gcode.3mf",
                file_size=1,
                file_metadata={"sliced_for_model": model},
                created_by_id=me,
            )
            db_session.add(f)
            files.append(f)
        await db_session.commit()
        for f in files:
            await db_session.refresh(f)
        h = _h(auth_setup["operator_token"])

        r = await async_client.post(
            "/api/v1/library/variant-groups",
            json={"members": [{"library_file_id": f.id} for f in files]},
            headers=h,
        )
        assert r.status_code == 201, r.text
        assert r.json()["name"] == files[0].filename
        listing = await async_client.get("/api/v1/library/files?with_variant_members=true", headers=h)
        assert {f["variant_group_name"] for f in listing.json()} == {files[0].filename}


class TestListSaysWhetherAGroupMayBeChanged(_GroupFixtures):
    """`variant_group_editable` mirrors what the change routes would answer (#3628)."""

    async def _flags(self, async_client, token, query="?with_variant_members=true"):
        r = await async_client.get(f"/api/v1/library/files{query}", headers=_h(token))
        assert r.status_code == 200, r.text
        return {f["id"]: f["variant_group_editable"] for f in r.json()}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_read_own_update_own_user_may_change_only_groups_that_are_all_theirs(
        self, async_client, auth_setup, make_group
    ):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        _, mine = await make_group([(me, "H2S", False), (me, "H2C", False)])
        # The default Operators case: sees two own versions, a third is hidden.
        mixed_group, mixed = await make_group([(me, "H2S", False), (me, "H2C", False), (other, "P1S", False)])
        # A trashed foreign member blocks nothing, as on the change routes.
        _, trashed = await make_group([(me, "H2S", False), (me, "H2C", False), (other, "P1S", True)])

        flags = await self._flags(async_client, auth_setup["operator_token"])
        assert flags == {
            mine[0].id: True,
            mine[1].id: True,
            mixed[0].id: False,
            mixed[1].id: False,
            trashed[0].id: True,
            trashed[1].id: True,
        }

        # The flag and the server agree, and the refusal names no cause.
        r = await async_client.delete(
            f"/api/v1/library/variant-groups/{mixed_group.id}", headers=_h(auth_setup["operator_token"])
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "You may not change this variant group"
        assert "other" not in r.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_all_may_change_every_group_and_no_update_permission_none(
        self, async_client, auth_setup, make_group
    ):
        me = auth_setup["operator_user"]["id"]
        other = auth_setup["operator2_user"]["id"]
        await make_group([(me, "H2S", False), (other, "H2C", False)])

        assert set((await self._flags(async_client, auth_setup["admin_token"])).values()) == {True}

        admin = _h(auth_setup["admin_token"])
        group = await async_client.post(
            "/api/v1/groups/", headers=admin, json={"name": "ReadAllOnly", "permissions": ["library:read_all"]}
        )
        assert group.status_code in (200, 201)
        await async_client.post(
            "/api/v1/users/",
            headers=admin,
            json={"username": "looker1", "password": "Lookerpass1!", "group_ids": [group.json()["id"]]},
        )
        login = await async_client.post("/api/v1/auth/login", json={"username": "looker1", "password": "Lookerpass1!"})
        assert set((await self._flags(async_client, login.json()["access_token"])).values()) == {False}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_flag_is_only_sent_with_variant_members(self, async_client, auth_setup, make_group):
        me = auth_setup["operator_user"]["id"]
        await make_group([(me, "H2S", False), (me, "H2C", False)])
        assert set((await self._flags(async_client, auth_setup["operator_token"], "")).values()) == {None}


class TestUpdateAllWithReadOwn(_GroupFixtures):
    """What a caller may change and what it may read are separate questions (#3628).

    update_all with only read_own: it may change any group, and still must not
    be shown, in a response or in an error, a file its read permission hides.
    """

    @pytest.fixture
    async def changer(self, async_client, auth_setup, db_session):
        """A read_own + update_all user: (token, user id, API key header for a key of theirs)."""
        from sqlalchemy import select

        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey
        from backend.app.models.user import User

        admin = _h(auth_setup["admin_token"])
        group = await async_client.post(
            "/api/v1/groups/",
            headers=admin,
            json={"name": "ReadOwnUpdateAll", "permissions": ["library:read_own", "library:update_all"]},
        )
        assert group.status_code in (200, 201)
        await async_client.post(
            "/api/v1/users/",
            headers=admin,
            json={"username": "changer1", "password": "Changerpass1!", "group_ids": [group.json()["id"]]},
        )
        login = await async_client.post(
            "/api/v1/auth/login", json={"username": "changer1", "password": "Changerpass1!"}
        )
        user_id = await db_session.scalar(select(User.id).where(User.username == "changer1"))
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(
                name="changer",
                key_hash=key_hash,
                key_prefix=key_prefix,
                user_id=user_id,
                can_queue=True,
                can_read_status=True,
                can_manage_archives=True,
                can_manage_library=True,
            )
        )
        await db_session.commit()
        return login.json()["access_token"], user_id, {"X-API-Key": full_key}

    @pytest.fixture
    async def make_loose(self, db_session):
        async def _create(owner_id: int, model: str):
            from backend.app.models.library import LibraryFile

            f = LibraryFile(
                filename=f"loose_{model}.gcode.3mf",
                file_path=f"library/loose_{model}.gcode.3mf",
                file_type="gcode.3mf",
                file_size=1,
                file_metadata={"sliced_for_model": model},
                created_by_id=owner_id,
            )
            db_session.add(f)
            await db_session.commit()
            await db_session.refresh(f)
            return f

        return _create

    @staticmethod
    def _url(group, suffix=""):
        return f"/api/v1/library/variant-groups/{group.id}{suffix}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("via", ["session", "api_key"])
    async def test_change_response_lists_only_what_the_caller_may_read(
        self, async_client, auth_setup, make_group, changer, via
    ):
        token, me, key = changer
        headers = _h(token) if via == "session" else key
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (other, "H2C", False), (me, "P1S", False)])

        r = await async_client.patch(self._url(group), json={"name": "renamed"}, headers=headers)
        assert r.status_code == 200, r.text
        assert [m["library_file_id"] for m in r.json()["members"]] == [files[0].id, files[2].id]
        assert files[1].filename not in r.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("via", ["session", "api_key"])
    async def test_collision_message_does_not_name_a_member_the_caller_cannot_read(
        self, async_client, auth_setup, make_group, make_loose, changer, via
    ):
        token, me, key = changer
        headers = _h(token) if via == "session" else key
        other = auth_setup["operator2_user"]["id"]
        group, files = await make_group([(me, "H2S", False), (other, "H2C", False)])
        mine = await make_loose(me, "H2C")  # collides with the other user's member

        r = await async_client.post(self._url(group, "/members"), json={"library_file_id": mine.id}, headers=headers)
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "both sliced for H2C" in detail
        assert mine.filename in detail
        assert files[1].filename not in detail
        assert "another file" in detail

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("via", ["session", "api_key"])
    async def test_messages_about_a_file_the_caller_may_change_but_not_read_do_not_name_it(
        self, async_client, auth_setup, make_group, make_loose, changer, via
    ):
        token, me, key = changer
        headers = _h(token) if via == "session" else key
        other = auth_setup["operator2_user"]["id"]
        group, _ = await make_group([(me, "H2S", False), (me, "H2C", False)])
        _, taken = await make_group([(other, "X1C", False), (other, "P1S", False)])
        theirs = await make_loose(other, "H2S")

        # Already in another group.
        r = await async_client.post(
            self._url(group, "/members"), json={"library_file_id": taken[0].id}, headers=headers
        )
        assert r.status_code == 409 and taken[0].filename not in r.text

        # Collides with the caller's own member: theirs is named, the hidden one is not.
        r = await async_client.post(self._url(group, "/members"), json={"library_file_id": theirs.id}, headers=headers)
        assert r.status_code == 400 and theirs.filename not in r.text

        # Creating a group from files it cannot read: no filename in the answer or the name.
        other_h2c = await make_loose(other, "H2C")
        r = await async_client.post(
            "/api/v1/library/variant-groups",
            json={"members": [{"library_file_id": theirs.id}, {"library_file_id": other_h2c.id}]},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert theirs.filename not in r.text and other_h2c.filename not in r.text
        assert r.json()["members"] == []

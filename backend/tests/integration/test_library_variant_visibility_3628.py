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

        async def _create(members: list[tuple[int, str, bool]]):
            from backend.app.models.library import FileVariantGroup, LibraryFile

            group = FileVariantGroup(name="bracket")
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
        _, files = await make_group([(me, "H2S", False), (me, "H2C", False), (me, "P1S", False)])
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

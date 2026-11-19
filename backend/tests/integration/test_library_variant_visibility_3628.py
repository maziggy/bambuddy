"""Variant groups only show the members the caller may read (#3628).

A library:read_own user must not learn the names, positions or number of other
users' files through a group they share a file with.
"""

from datetime import datetime, timezone

import pytest

from backend.tests.integration.test_ownership_permissions import TestOwnershipPermissionsSetup


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestVariantGroupVisibility(TestOwnershipPermissionsSetup):
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

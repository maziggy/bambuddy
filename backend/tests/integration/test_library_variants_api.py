"""Integration tests for variant groups (#671 / #2570).

A variant group is the user declaring that several sliced files are the same
job for different printers. The endpoints exist to enforce what that statement
has to mean before the scheduler acts on it without a human in the loop.
"""

import pytest
from httpx import AsyncClient


@pytest.fixture
async def sliced_file_factory(db_session):
    """Create a sliced library file declaring the model it was sliced for."""
    _counter = [0]

    async def _create(model: str | None = "H2S", **kwargs):
        from backend.app.models.library import LibraryFile

        _counter[0] += 1
        defaults = {
            "filename": f"job_{_counter[0]}.gcode.3mf",
            "file_path": f"/test/job_{_counter[0]}.gcode.3mf",
            "file_size": 100,
            "file_type": "gcode.3mf",
            "file_metadata": {"sliced_for_model": model} if model else {},
        }
        defaults.update(kwargs)
        f = LibraryFile(**defaults)
        db_session.add(f)
        await db_session.commit()
        await db_session.refresh(f)
        return f

    return _create


async def _create_group(client: AsyncClient, *file_ids: int, name: str | None = None):
    payload = {"members": [{"library_file_id": fid} for fid in file_ids]}
    if name:
        payload["name"] = name
    return await client.post("/api/v1/library/variant-groups", json=payload)


class TestCreateVariantGroup:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_groups_two_slices_in_priority_order(self, async_client, sliced_file_factory):
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")

        r = await _create_group(async_client, h2s.id, h2c.id, name="bracket")
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "bracket"
        assert [m["target_model"] for m in body["members"]] == ["H2S", "H2C"]
        assert [m["position"] for m in body["members"]] == [0, 1]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_model_is_read_from_the_file_not_the_caller(self, async_client, sliced_file_factory):
        """The group never carries its own model data, so it cannot disagree with
        the 3MFs. "Bambu Lab H2S" normalizes to the same H2S the scheduler matches."""
        a = await sliced_file_factory("Bambu Lab H2S")
        b = await sliced_file_factory("O1C")  # internal code for H2C

        body = (await _create_group(async_client, a.id, b.id)).json()
        assert [m["target_model"] for m in body["members"]] == ["H2S", "H2C"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_two_slices_for_the_same_printer_are_rejected(self, async_client, sliced_file_factory):
        """Not alternatives — the resolver would have no basis to prefer one, and
        the arbitrary pick would look like a bug the first time it chose wrong."""
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2S")

        r = await _create_group(async_client, a.id, b.id)
        assert r.status_code == 400
        assert "different printers" in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_normalization_catches_the_same_printer_spelled_differently(self, async_client, sliced_file_factory):
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("Bambu Lab H2S")

        r = await _create_group(async_client, a.id, b.id)
        assert r.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unsliced_file_cannot_be_a_variant(self, async_client, sliced_file_factory):
        """A source .3mf has no G-code — it can never be dispatched to anything."""
        sliced = await sliced_file_factory("H2S")
        source = await sliced_file_factory(None, filename="model.3mf", file_type="3mf")

        r = await _create_group(async_client, sliced.id, source.id)
        assert r.status_code == 400
        assert "not a sliced file" in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_file_without_a_model_must_name_one(self, async_client, sliced_file_factory):
        """Legacy 3MFs declare no model. Rather than guess, make the user say."""
        known = await sliced_file_factory("H2S")
        legacy = await sliced_file_factory(None)

        r = await _create_group(async_client, known.id, legacy.id)
        assert r.status_code == 400
        assert "does not say which printer" in r.json()["detail"]

        r = await async_client.post(
            "/api/v1/library/variant-groups",
            json={
                "members": [
                    {"library_file_id": known.id},
                    {"library_file_id": legacy.id, "target_model": "H2C"},
                ]
            },
        )
        assert r.status_code == 201
        assert [m["target_model"] for m in r.json()["members"]] == ["H2S", "H2C"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_file_belongs_to_one_group_only(self, async_client, sliced_file_factory):
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        c = await sliced_file_factory("H2D")
        assert (await _create_group(async_client, a.id, b.id)).status_code == 201

        r = await _create_group(async_client, a.id, c.id)
        assert r.status_code == 409
        assert "already belongs" in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_single_member_is_rejected_by_the_schema(self, async_client, sliced_file_factory):
        only = await sliced_file_factory("H2S")
        r = await _create_group(async_client, only.id)
        assert r.status_code == 422, "a group of one expresses no choice"


class TestVariantGroupMembership:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_version_to_an_existing_group(self, async_client, sliced_file_factory):
        """The common real case: the H2S version was queued last week, the H2C
        version was sliced today."""
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        c = await sliced_file_factory("H2D")
        gid = (await _create_group(async_client, a.id, b.id)).json()["id"]

        r = await async_client.post(f"/api/v1/library/variant-groups/{gid}/members", json={"library_file_id": c.id})
        assert r.status_code == 200
        assert [m["target_model"] for m in r.json()["members"]] == ["H2S", "H2C", "H2D"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_added_member_cannot_duplicate_a_model(self, async_client, sliced_file_factory):
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        dupe = await sliced_file_factory("H2C")
        gid = (await _create_group(async_client, a.id, b.id)).json()["id"]

        r = await async_client.post(f"/api/v1/library/variant-groups/{gid}/members", json={"library_file_id": dupe.id})
        assert r.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_removing_down_to_one_dissolves_the_group(self, async_client, sliced_file_factory):
        """A leftover one-member group would look like a choice and behave like an
        ordinary job — worse than no group at all."""
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        gid = (await _create_group(async_client, a.id, b.id)).json()["id"]

        r = await async_client.delete(f"/api/v1/library/variant-groups/{gid}/members/{b.id}")
        assert r.status_code == 204
        assert (await async_client.get(f"/api/v1/library/variant-groups/{gid}")).status_code == 404
        # ...and the survivor is still a perfectly good file.
        assert (await async_client.get(f"/api/v1/library/variant-groups/by-file/{a.id}")).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_removing_from_a_three_member_group_keeps_it(self, async_client, sliced_file_factory):
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        c = await sliced_file_factory("H2D")
        gid = (await _create_group(async_client, a.id, b.id, c.id)).json()["id"]

        assert (await async_client.delete(f"/api/v1/library/variant-groups/{gid}/members/{c.id}")).status_code == 204
        body = (await async_client.get(f"/api/v1/library/variant-groups/{gid}")).json()
        assert [m["library_file_id"] for m in body["members"]] == [a.id, b.id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_a_group_keeps_the_files(self, async_client, sliced_file_factory):
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        gid = (await _create_group(async_client, a.id, b.id)).json()["id"]

        assert (await async_client.delete(f"/api/v1/library/variant-groups/{gid}")).status_code == 204
        for f in (a, b):
            assert (await async_client.get(f"/api/v1/library/files/{f.id}")).status_code == 200


class TestVariantGroupOrdering:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reorder_changes_priority(self, async_client, sliced_file_factory):
        """Order is the user saying which printer they would rather have when both
        are free, so it has to be editable."""
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        gid = (await _create_group(async_client, a.id, b.id)).json()["id"]

        r = await async_client.patch(f"/api/v1/library/variant-groups/{gid}", json={"member_file_ids": [b.id, a.id]})
        assert r.status_code == 200
        assert [m["target_model"] for m in r.json()["members"]] == ["H2C", "H2S"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_partial_reorder_is_rejected(self, async_client, sliced_file_factory):
        """Listing a subset would leave the rest in an order nobody chose."""
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        gid = (await _create_group(async_client, a.id, b.id)).json()["id"]

        r = await async_client.patch(f"/api/v1/library/variant-groups/{gid}", json={"member_file_ids": [a.id]})
        assert r.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_lookup_by_file(self, async_client, sliced_file_factory):
        """Both consumers start from a file: the print modal knows what was
        clicked, the queue flow knows what was selected."""
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2C")
        gid = (await _create_group(async_client, a.id, b.id)).json()["id"]

        r = await async_client.get(f"/api/v1/library/variant-groups/by-file/{b.id}")
        assert r.status_code == 200
        assert r.json()["id"] == gid

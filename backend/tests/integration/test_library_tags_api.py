"""Integration tests for the library tag catalog + bulk-assign (#1268)."""

import pytest
from httpx import AsyncClient


@pytest.fixture
async def folder_factory(db_session):
    """Minimal folder factory shared across the tests in this module."""
    _counter = [0]

    async def _create_folder(**kwargs):
        from backend.app.models.library import LibraryFolder

        _counter[0] += 1
        defaults = {"name": f"Folder {_counter[0]}"}
        defaults.update(kwargs)
        f = LibraryFolder(**defaults)
        db_session.add(f)
        await db_session.commit()
        await db_session.refresh(f)
        return f

    return _create_folder


@pytest.fixture
async def file_factory(db_session):
    """Minimal file factory shared across the tests in this module."""
    _counter = [0]

    async def _create_file(**kwargs):
        from backend.app.models.library import LibraryFile

        _counter[0] += 1
        defaults = {
            "filename": f"file_{_counter[0]}.3mf",
            "file_path": f"/test/file_{_counter[0]}.3mf",
            "file_size": 100,
            "file_type": "3mf",
        }
        defaults.update(kwargs)
        f = LibraryFile(**defaults)
        db_session.add(f)
        await db_session.commit()
        await db_session.refresh(f)
        return f

    return _create_file


class TestLibraryTagCRUD:
    """Catalog CRUD: create / list / rename / delete."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_tag_and_list(self, async_client: AsyncClient):
        r = await async_client.post("/api/v1/library/tags", json={"name": "toy"})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "toy"
        assert body["file_count"] == 0

        r = await async_client.get("/api/v1/library/tags")
        assert r.status_code == 200
        names = [t["name"] for t in r.json()]
        assert "toy" in names

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_tag_strips_whitespace(self, async_client: AsyncClient):
        r = await async_client.post("/api/v1/library/tags", json={"name": "  kid-safe  "})
        assert r.status_code == 201
        assert r.json()["name"] == "kid-safe"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_duplicate_case_insensitive_409(self, async_client: AsyncClient):
        """'Toys' / 'toys' / 'TOYS  ' all collide on name_key."""
        r1 = await async_client.post("/api/v1/library/tags", json={"name": "Toys"})
        assert r1.status_code == 201
        for dup in ("toys", "TOYS", "  ToYs  "):
            r = await async_client.post("/api/v1/library/tags", json={"name": dup})
            assert r.status_code == 409, dup

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rename_tag(self, async_client: AsyncClient):
        r = await async_client.post("/api/v1/library/tags", json={"name": "kidsafe"})
        tag_id = r.json()["id"]
        r = await async_client.patch(f"/api/v1/library/tags/{tag_id}", json={"name": "kid-safe"})
        assert r.status_code == 200
        assert r.json()["name"] == "kid-safe"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rename_collision_409(self, async_client: AsyncClient):
        a = (await async_client.post("/api/v1/library/tags", json={"name": "a"})).json()
        b = (await async_client.post("/api/v1/library/tags", json={"name": "b"})).json()
        # Renaming b → A (case-insensitive collision with a) must fail.
        r = await async_client.patch(f"/api/v1/library/tags/{b['id']}", json={"name": "A"})
        assert r.status_code == 409
        # Renaming a row to its own current name (round-trip with the same key)
        # must NOT 409 — the pre-check excludes the tag itself.
        r = await async_client.patch(f"/api/v1/library/tags/{a['id']}", json={"name": "a"})
        assert r.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_tag_cascades_associations_keeps_file(self, async_client: AsyncClient, file_factory):
        f = await file_factory()
        tag = (await async_client.post("/api/v1/library/tags", json={"name": "x"})).json()
        await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id], "tag_ids": [tag["id"]], "action": "add"},
        )
        r = await async_client.delete(f"/api/v1/library/tags/{tag['id']}")
        assert r.status_code == 204

        # Tag list no longer contains it.
        names = [t["name"] for t in (await async_client.get("/api/v1/library/tags")).json()]
        assert "x" not in names
        # File still listed (CASCADE only dropped the association row).
        r = await async_client.get(
            f"/api/v1/library/files?folder_id={f.folder_id}" if f.folder_id else "/api/v1/library/files"
        )
        assert any(item["id"] == f.id for item in r.json())

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_unknown_tag_404(self, async_client: AsyncClient):
        r = await async_client.delete("/api/v1/library/tags/999999")
        assert r.status_code == 404


class TestLibraryTagBulkAssign:
    """Bulk-assign: add / remove / replace + per-action assertions."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_is_idempotent(self, async_client: AsyncClient, file_factory):
        f = await file_factory()
        t = (await async_client.post("/api/v1/library/tags", json={"name": "t"})).json()
        payload = {"file_ids": [f.id], "tag_ids": [t["id"]], "action": "add"}
        r1 = await async_client.post("/api/v1/library/tags/bulk-assign", json=payload)
        assert r1.status_code == 200
        assert r1.json()["associations_added"] == 1
        r2 = await async_client.post("/api/v1/library/tags/bulk-assign", json=payload)
        # Second call adds 0 — pair already exists; route remains 200 not 409.
        assert r2.status_code == 200
        assert r2.json()["associations_added"] == 0
        # And the file_count for the tag is still exactly 1.
        tags = (await async_client.get("/api/v1/library/tags")).json()
        assert next(x["file_count"] for x in tags if x["id"] == t["id"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_remove_drops_only_listed_tags(self, async_client: AsyncClient, file_factory):
        f = await file_factory()
        a = (await async_client.post("/api/v1/library/tags", json={"name": "a"})).json()
        b = (await async_client.post("/api/v1/library/tags", json={"name": "b"})).json()
        await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id], "tag_ids": [a["id"], b["id"]], "action": "add"},
        )
        # Remove only `a`. `b` should still be on the file.
        r = await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id], "tag_ids": [a["id"]], "action": "remove"},
        )
        assert r.status_code == 200
        assert r.json()["associations_removed"] == 1
        # Tag-filter listing by `b` still returns the file.
        r = await async_client.get(f"/api/v1/library/files?tag_ids={b['id']}")
        assert {x["id"] for x in r.json()} == {f.id}
        r = await async_client.get(f"/api/v1/library/files?tag_ids={a['id']}")
        assert {x["id"] for x in r.json()} == set()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_replace_with_empty_tag_set_clears(self, async_client: AsyncClient, file_factory):
        f = await file_factory()
        a = (await async_client.post("/api/v1/library/tags", json={"name": "a"})).json()
        await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id], "tag_ids": [a["id"]], "action": "add"},
        )
        # Replace with [] → file ends up with no tags.
        r = await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id], "tag_ids": [], "action": "replace"},
        )
        assert r.status_code == 200
        assert r.json()["associations_removed"] == 1
        # File listing shows empty tags array.
        r = await async_client.get("/api/v1/library/files?include_root=false")
        item = next(x for x in r.json() if x["id"] == f.id)
        assert item["tags"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_file_ids_silently_skipped(self, async_client: AsyncClient, file_factory):
        """Unknown / inaccessible file ids must not 404 the whole call — the
        caller may be racing a delete or have a stale selection. Counts reflect
        what actually happened."""
        f = await file_factory()
        t = (await async_client.post("/api/v1/library/tags", json={"name": "t"})).json()
        r = await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id, 999999], "tag_ids": [t["id"]], "action": "add"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["files_updated"] == 1
        assert body["associations_added"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_action_422(self, async_client: AsyncClient, file_factory):
        f = await file_factory()
        r = await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id], "tag_ids": [], "action": "nuke"},
        )
        assert r.status_code == 422


class TestLibraryTagFilter:
    """list_files?tag_ids=… — AND semantics + folder bypass."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_and_semantics(self, async_client: AsyncClient, file_factory):
        a_only = await file_factory(filename="a_only.3mf")
        b_only = await file_factory(filename="b_only.3mf")
        ab = await file_factory(filename="ab.3mf")
        a = (await async_client.post("/api/v1/library/tags", json={"name": "A"})).json()
        b = (await async_client.post("/api/v1/library/tags", json={"name": "B"})).json()
        await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [a_only.id, ab.id], "tag_ids": [a["id"]], "action": "add"},
        )
        await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [b_only.id, ab.id], "tag_ids": [b["id"]], "action": "add"},
        )
        # Filter by A alone → a_only + ab
        r = await async_client.get(f"/api/v1/library/files?tag_ids={a['id']}")
        assert {x["id"] for x in r.json()} == {a_only.id, ab.id}
        # Filter by A AND B → only ab
        r = await async_client.get(f"/api/v1/library/files?tag_ids={a['id']}&tag_ids={b['id']}")
        assert {x["id"] for x in r.json()} == {ab.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_filter_ignores_folder_id(self, async_client: AsyncClient, folder_factory, file_factory):
        """Tag filter is cross-cutting — passing folder_id must NOT narrow the
        result. Confirms decision #2 from the design discussion."""
        folder_a = await folder_factory(name="A")
        folder_b = await folder_factory(name="B")
        in_a = await file_factory(folder_id=folder_a.id, filename="in_a.3mf")
        in_b = await file_factory(folder_id=folder_b.id, filename="in_b.3mf")
        tag = (await async_client.post("/api/v1/library/tags", json={"name": "x"})).json()
        await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={
                "file_ids": [in_a.id, in_b.id],
                "tag_ids": [tag["id"]],
                "action": "add",
            },
        )
        # Pass folder_id=folder_a alongside tag_ids — file from folder_b must
        # STILL appear because the tag filter overrides folder scoping.
        r = await async_client.get(f"/api/v1/library/files?folder_id={folder_a.id}&tag_ids={tag['id']}")
        assert {x["id"] for x in r.json()} == {in_a.id, in_b.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_file_listing_includes_tags_array(self, async_client: AsyncClient, file_factory):
        f = await file_factory()
        tag = (await async_client.post("/api/v1/library/tags", json={"name": "petg"})).json()
        await async_client.post(
            "/api/v1/library/tags/bulk-assign",
            json={"file_ids": [f.id], "tag_ids": [tag["id"]], "action": "add"},
        )
        r = await async_client.get("/api/v1/library/files?include_root=false")
        item = next(x for x in r.json() if x["id"] == f.id)
        assert item["tags"] == [{"id": tag["id"], "name": "petg"}]

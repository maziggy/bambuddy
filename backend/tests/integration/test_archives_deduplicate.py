"""
Integration tests for POST /archives/deduplicate (Airtho fork addition).

Verifies that:
  - Calling the endpoint when there are no duplicates returns deleted=0
  - Duplicate archives (same content_hash) are removed, keeping the earliest (lowest id)
  - Archives without a content_hash are never touched
  - The response shape matches the frontend api.deduplicateArchives() type:
    { deleted: number, errors: { id: number, error: string }[] }
"""

import pytest
from httpx import AsyncClient


class TestDeduplicateArchives:
    """Integration tests for POST /api/v1/archives/deduplicate."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_duplicates_returns_zero_deleted(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """With no duplicate hashes the endpoint is a no-op."""
        printer = await printer_factory()
        await archive_factory(printer.id, content_hash="aaa111", print_name="Unique A")
        await archive_factory(printer.id, content_hash="bbb222", print_name="Unique B")

        response = await async_client.post("/api/v1/archives/deduplicate")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 0
        assert data["errors"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicates_deleted_keeps_earliest(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Two archives with the same hash — only the newer one is deleted."""
        printer = await printer_factory()
        first = await archive_factory(printer.id, content_hash="deadbeef", print_name="First")
        second = await archive_factory(printer.id, content_hash="deadbeef", print_name="Second")
        # first has the lower id and must be kept

        response = await async_client.post("/api/v1/archives/deduplicate")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 1
        assert data["errors"] == []

        # first must still exist
        get_first = await async_client.get(f"/api/v1/archives/{first.id}")
        assert get_first.status_code == 200

        # second must be gone
        get_second = await async_client.get(f"/api/v1/archives/{second.id}")
        assert get_second.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_three_duplicates_keeps_only_earliest(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Three archives with the same hash — two are deleted, the first survives."""
        printer = await printer_factory()
        first = await archive_factory(printer.id, content_hash="cafebabe", print_name="A")
        second = await archive_factory(printer.id, content_hash="cafebabe", print_name="B")
        third = await archive_factory(printer.id, content_hash="cafebabe", print_name="C")

        response = await async_client.post("/api/v1/archives/deduplicate")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 2

        assert (await async_client.get(f"/api/v1/archives/{first.id}")).status_code == 200
        assert (await async_client.get(f"/api/v1/archives/{second.id}")).status_code == 404
        assert (await async_client.get(f"/api/v1/archives/{third.id}")).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_null_content_hash_archives_untouched(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Archives with content_hash=None are never considered duplicates."""
        printer = await printer_factory()
        no_hash_a = await archive_factory(printer.id, content_hash=None, print_name="No Hash A")
        no_hash_b = await archive_factory(printer.id, content_hash=None, print_name="No Hash B")

        response = await async_client.post("/api/v1/archives/deduplicate")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 0

        # Both archives still present
        assert (await async_client.get(f"/api/v1/archives/{no_hash_a.id}")).status_code == 200
        assert (await async_client.get(f"/api/v1/archives/{no_hash_b.id}")).status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mixed_duplicate_and_unique_hashes(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Only the duplicated hash group is touched; unique-hash archives are preserved."""
        printer = await printer_factory()
        unique = await archive_factory(printer.id, content_hash="unique1", print_name="Unique")
        dup_a = await archive_factory(printer.id, content_hash="dupHash", print_name="Dup A")
        dup_b = await archive_factory(printer.id, content_hash="dupHash", print_name="Dup B")

        response = await async_client.post("/api/v1/archives/deduplicate")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 1

        assert (await async_client.get(f"/api/v1/archives/{unique.id}")).status_code == 200
        assert (await async_client.get(f"/api/v1/archives/{dup_a.id}")).status_code == 200
        assert (await async_client.get(f"/api/v1/archives/{dup_b.id}")).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_response_schema(
        self, async_client: AsyncClient
    ):
        """Response always has 'deleted' (int) and 'errors' (list) keys."""
        response = await async_client.post("/api/v1/archives/deduplicate")

        assert response.status_code == 200
        data = response.json()
        assert "deleted" in data
        assert "errors" in data
        assert isinstance(data["deleted"], int)
        assert isinstance(data["errors"], list)

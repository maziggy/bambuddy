"""Integration tests for External Links API endpoints."""

import pytest
from httpx import AsyncClient


class TestExternalLinksAPI:
    """Integration tests for /api/v1/external-links endpoints."""

    @pytest.fixture
    async def link_factory(self, db_session):
        """Factory to create test external links."""
        _counter = [0]

        async def _create_link(**kwargs):
            from backend.app.models.external_link import ExternalLink

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Link {counter}",
                "url": f"https://example.com/{counter}",
                "icon": "Link",
                "sort_order": counter,
            }
            defaults.update(kwargs)

            link = ExternalLink(**defaults)
            db_session.add(link)
            await db_session.commit()
            await db_session.refresh(link)
            return link

        return _create_link

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_external_links_empty(self, async_client: AsyncClient):
        """Verify empty list when no links exist."""
        response = await async_client.get("/api/v1/external-links/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_external_links_with_data(self, async_client: AsyncClient, link_factory, db_session):
        """Verify list returns existing links."""
        await link_factory(name="My Link")
        response = await async_client.get("/api/v1/external-links/")
        assert response.status_code == 200
        data = response.json()
        assert any(link["name"] == "My Link" for link in data)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_external_link(self, async_client: AsyncClient):
        """Verify external link can be created."""
        data = {
            "name": "New Link",
            "url": "https://new-link.example.com",
            "icon": "ExternalLink",
        }
        response = await async_client.post("/api/v1/external-links/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New Link"
        assert result["url"] == "https://new-link.example.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_external_link(self, async_client: AsyncClient, link_factory, db_session):
        """Verify single link can be retrieved."""
        link = await link_factory(name="Get Test Link")
        response = await async_client.get(f"/api/v1/external-links/{link.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Get Test Link"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_external_link_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent link."""
        response = await async_client.get("/api/v1/external-links/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_external_link(self, async_client: AsyncClient, link_factory, db_session):
        """Verify link can be updated."""
        link = await link_factory(name="Original")
        response = await async_client.patch(
            f"/api/v1/external-links/{link.id}", json={"name": "Updated", "url": "https://updated.example.com"}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Updated"
        assert result["url"] == "https://updated.example.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_link(self, async_client: AsyncClient, link_factory, db_session):
        """Verify link can be deleted."""
        link = await link_factory()
        response = await async_client.delete(f"/api/v1/external-links/{link.id}")
        assert response.status_code == 200
        # Verify deleted
        response = await async_client.get(f"/api/v1/external-links/{link.id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reorder_external_links(self, async_client: AsyncClient, link_factory, db_session):
        """Verify links can be reordered."""
        link1 = await link_factory(name="Link 1")
        link2 = await link_factory(name="Link 2")
        link3 = await link_factory(name="Link 3")

        # Reorder: 3, 1, 2
        response = await async_client.put(
            "/api/v1/external-links/reorder", json={"ids": [link3.id, link1.id, link2.id]}
        )
        assert response.status_code == 200
        data = response.json()
        # First link should be link3
        assert data[0]["id"] == link3.id
        assert data[0]["sort_order"] == 0


class TestExternalLinksIconAPI:
    """Tests for external link icon upload/delete."""

    @pytest.fixture
    async def link_factory(self, db_session):
        """Factory to create test external links."""

        async def _create_link(**kwargs):
            from backend.app.models.external_link import ExternalLink

            defaults = {
                "name": "Icon Test Link",
                "url": "https://example.com",
                "icon": "Link",
                "sort_order": 0,
            }
            defaults.update(kwargs)

            link = ExternalLink(**defaults)
            db_session.add(link)
            await db_session.commit()
            await db_session.refresh(link)
            return link

        return _create_link

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_icon_not_set(self, async_client: AsyncClient, link_factory, db_session):
        """Verify 404 when no custom icon is set."""
        link = await link_factory()
        response = await async_client.get(f"/api/v1/external-links/{link.id}/icon")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_icon_when_none(self, async_client: AsyncClient, link_factory, db_session):
        """Verify deleting non-existent icon succeeds silently."""
        link = await link_factory()
        response = await async_client.delete(f"/api/v1/external-links/{link.id}/icon")
        assert response.status_code == 200

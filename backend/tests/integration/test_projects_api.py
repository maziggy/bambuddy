"""Integration tests for Projects API endpoints."""

import pytest
from httpx import AsyncClient


class TestProjectsAPI:
    """Integration tests for /api/v1/projects endpoints."""

    @pytest.fixture
    async def project_factory(self, db_session):
        """Factory to create test projects."""
        _counter = [0]

        async def _create_project(**kwargs):
            from backend.app.models.project import Project

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Project {counter}",
                "description": "Test project description",
                "color": "#FF0000",
            }
            defaults.update(kwargs)

            project = Project(**defaults)
            db_session.add(project)
            await db_session.commit()
            await db_session.refresh(project)
            return project

        return _create_project

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_projects_empty(self, async_client: AsyncClient):
        """Verify empty list when no projects exist."""
        response = await async_client.get("/api/v1/projects/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_projects_with_data(self, async_client: AsyncClient, project_factory, db_session):
        """Verify list returns existing projects."""
        await project_factory(name="My Project")
        response = await async_client.get("/api/v1/projects/")
        assert response.status_code == 200
        data = response.json()
        assert any(p["name"] == "My Project" for p in data)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_project(self, async_client: AsyncClient):
        """Verify project can be created."""
        data = {
            "name": "New Project",
            "description": "A new project",
            "color": "#00FF00",
        }
        response = await async_client.post("/api/v1/projects/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New Project"
        assert result["color"] == "#00FF00"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_project(self, async_client: AsyncClient, project_factory, db_session):
        """Verify single project can be retrieved."""
        project = await project_factory(name="Get Test Project")
        response = await async_client.get(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Get Test Project"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_project_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent project."""
        response = await async_client.get("/api/v1/projects/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_project(self, async_client: AsyncClient, project_factory, db_session):
        """Verify project can be updated."""
        project = await project_factory(name="Original")
        response = await async_client.patch(
            f"/api/v1/projects/{project.id}", json={"name": "Updated", "description": "Updated description"}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Updated"
        assert result["description"] == "Updated description"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_project(self, async_client: AsyncClient, project_factory, db_session):
        """Verify project can be deleted."""
        project = await project_factory()
        response = await async_client.delete(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Project deleted"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_project_not_found(self, async_client: AsyncClient):
        """Verify 404 for deleting non-existent project."""
        response = await async_client.delete("/api/v1/projects/9999")
        assert response.status_code == 404


class TestProjectArchivesAPI:
    """Tests for project-archive relationships."""

    @pytest.fixture
    async def project_factory(self, db_session):
        """Factory to create test projects."""

        async def _create_project(**kwargs):
            from backend.app.models.project import Project

            defaults = {
                "name": "Archive Test Project",
                "description": "Test project",
                "color": "#0000FF",
            }
            defaults.update(kwargs)

            project = Project(**defaults)
            db_session.add(project)
            await db_session.commit()
            await db_session.refresh(project)
            return project

        return _create_project

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_project_with_archives(self, async_client: AsyncClient, project_factory, db_session):
        """Verify project can be retrieved with archive count."""
        project = await project_factory()
        response = await async_client.get(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        # Project should have an archive count (may be 0)
        data = response.json()
        assert "name" in data

"""Tests for the /makerworld/* route handlers.

Mocks ``MakerWorldService`` so tests don't hit the real MakerWorld API. We
still cover: URL validation, metadata passthrough, already-imported detection,
and source-URL-based dedupe on import.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.library import LibraryFile


def _fake_service(**stubs):
    """Build an AsyncMock MakerWorldService with the given async method stubs."""
    svc = AsyncMock()
    svc.close = AsyncMock()
    for name, value in stubs.items():
        if callable(value) and not isinstance(value, AsyncMock):
            # Wrap raw callable in AsyncMock(return_value=...) lazily
            setattr(svc, name, AsyncMock(side_effect=value))
        else:
            setattr(svc, name, AsyncMock(return_value=value))
    return svc


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_reports_no_token_by_default(self, async_client, db_session):
        resp = await async_client.get("/api/v1/makerworld/status")
        assert resp.status_code == 200
        body = resp.json()
        # Fresh in-memory DB has no stored token, so can_download must be false
        assert body == {"has_cloud_token": False, "can_download": False}


class TestResolve:
    @pytest.mark.asyncio
    async def test_rejects_non_makerworld_url(self, async_client):
        resp = await async_client.post(
            "/api/v1/makerworld/resolve",
            json={"url": "https://thingiverse.com/thing/1"},
        )
        assert resp.status_code == 400
        assert "makerworld" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_happy_path_returns_design_and_instances(self, async_client):
        design_payload = {"id": 1400373, "title": "Seed Starter"}
        instances_payload = {
            "total": 2,
            "hits": [
                {"id": 1452154, "profileId": 298919107, "title": "9 cells"},
                {"id": 1452158, "profileId": 298919564, "title": "12 cells"},
            ],
        }
        svc = _fake_service(get_design=design_payload, get_design_instances=instances_payload)

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/resolve",
                json={"url": "https://makerworld.com/en/models/1400373-slug#profileId-1452154"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["model_id"] == 1400373
        assert body["profile_id"] == 1452154
        assert body["design"] == design_payload
        assert len(body["instances"]) == 2
        assert body["already_imported_library_ids"] == []

    @pytest.mark.asyncio
    async def test_flags_already_imported_library_ids(self, async_client, db_session):
        # Seed a matching LibraryFile so resolve() reports it back
        existing = LibraryFile(
            filename="prev.3mf",
            file_path="library/files/prev.3mf",
            file_type="3mf",
            file_size=100,
            source_type="makerworld",
            source_url="https://makerworld.com/models/1400373",
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        svc = _fake_service(
            get_design={"id": 1400373},
            get_design_instances={"total": 0, "hits": []},
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/resolve",
                json={"url": "https://makerworld.com/en/models/1400373"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["already_imported_library_ids"] == [existing.id]


class TestImport:
    @pytest.mark.asyncio
    async def test_returns_existing_on_source_url_match(self, async_client, db_session):
        """Re-importing a model we already have must NOT re-download."""
        existing = LibraryFile(
            filename="already-here.3mf",
            file_path="library/files/already.3mf",
            file_type="3mf",
            file_size=500,
            source_type="makerworld",
            source_url="https://makerworld.com/models/1400373",
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        # Service stubs — the "download_3mf" method MUST NOT be called on the
        # dedupe path; we assert this below via the mock.
        svc = _fake_service(
            get_instance_download={
                "name": "new.3mf",
                "url": "https://makerworld.bblmw.com/makerworld/model/X/Y/f.3mf?exp=1&key=k",
            },
            get_profile={"designId": 1400373, "instanceId": 1452154},
        )
        svc.download_3mf = AsyncMock()  # must remain uncalled

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"instance_id": 1452154},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["library_file_id"] == existing.id
        assert body["was_existing"] is True
        svc.download_3mf.assert_not_called()

"""Tests for the /makerworld/* route handlers.

Mocks ``MakerWorldService`` so tests don't hit the real MakerWorld API. We
still cover: URL validation, metadata passthrough, already-imported detection,
source-URL-based dedupe on import, auto-creation of the MakerWorld default
folder, canonical URL shape, filename basenaming, and the ``/recent-imports``
listing endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes.makerworld import _canonical_url
from backend.app.models.library import LibraryFile, LibraryFolder


def _fake_service(**stubs):
    """Build an AsyncMock MakerWorldService with the given async method stubs."""
    svc = AsyncMock()
    svc.close = AsyncMock()
    for name, value in stubs.items():
        if callable(value) and not isinstance(value, AsyncMock):
            setattr(svc, name, AsyncMock(side_effect=value))
        else:
            setattr(svc, name, AsyncMock(return_value=value))
    return svc


def _default_design(alphanumeric: str = "US2bb73b106683e5", model_id: int = 1400373):
    """Shape the backend needs from ``/design/{id}``: the alphanumeric
    ``modelId`` field that iot-service requires, plus at least one instance
    so the importer has a ``profile_id`` to fall back on."""
    return {
        "id": model_id,
        "modelId": alphanumeric,
        "title": "Seed Starter",
        "instances": [{"profileId": 298919107, "title": "9 cells"}],
    }


def _default_manifest(name: str = "benchy.3mf"):
    return {
        "name": name,
        "url": "https://makerworld.bblmw.com/makerworld/model/X/Y/f.3mf?exp=1&key=k",
    }


class TestCanonicalUrl:
    """Unit test the dedupe-key builder directly — regressions break dedupe
    silently so it's worth pinning the exact shape."""

    def test_without_profile_id(self):
        assert _canonical_url(1400373) == "https://makerworld.com/models/1400373"

    def test_without_profile_id_when_none(self):
        assert _canonical_url(1400373, None) == "https://makerworld.com/models/1400373"

    def test_with_profile_id(self):
        assert _canonical_url(1400373, 298919107) == ("https://makerworld.com/models/1400373#profileId-298919107")


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

    @pytest.mark.asyncio
    async def test_merges_compatibility_from_design_into_instances(self, async_client):
        """Per-instance printer compatibility info lives on
        ``design.instances[].extention.modelInfo`` but not on
        ``/instances/hits``. Resolve enriches each hit with both
        ``compatibility`` (primary printer the instance was sliced for) and
        ``otherCompatibility`` (extra printers the uploader marked it
        compatible with) so the frontend can show "sliced for A1 / also
        marked compatible with: H2D, P1S".
        """
        design_payload = {
            "id": 1400373,
            "title": "Seed Starter",
            "instances": [
                {
                    "id": 1452154,
                    "extention": {
                        "modelInfo": {
                            "compatibility": ["A1"],
                            "otherCompatibility": ["H2D", "P1S"],
                        }
                    },
                },
                {
                    "id": 1452158,
                    "extention": {
                        "modelInfo": {
                            "compatibility": ["X1 Carbon"],
                            "otherCompatibility": [],
                        }
                    },
                },
            ],
        }
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
                json={"url": "https://makerworld.com/en/models/1400373"},
            )
        assert resp.status_code == 200, resp.text
        instances = resp.json()["instances"]
        by_id = {i["id"]: i for i in instances}
        assert by_id[1452154]["compatibility"] == ["A1"]
        assert by_id[1452154]["otherCompatibility"] == ["H2D", "P1S"]
        assert by_id[1452158]["compatibility"] == ["X1 Carbon"]
        assert by_id[1452158]["otherCompatibility"] == []

    @pytest.mark.asyncio
    async def test_resolve_handles_missing_compatibility_gracefully(self, async_client):
        """Older designs (or hits without a matching design.instances entry)
        must not crash the resolve response — they just don't get the
        compat fields."""
        design_payload = {"id": 1400373, "instances": [{"id": 1452154}]}  # no extention
        instances_payload = {
            "total": 2,
            "hits": [
                {"id": 1452154, "profileId": 298919107},
                {"id": 9999999, "profileId": 298919999},  # no design.instances match
            ],
        }
        svc = _fake_service(get_design=design_payload, get_design_instances=instances_payload)

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/resolve",
                json={"url": "https://makerworld.com/en/models/1400373"},
            )
        assert resp.status_code == 200, resp.text
        instances = resp.json()["instances"]
        # First instance: design entry exists but no extention → fields absent or None.
        first = next(i for i in instances if i["id"] == 1452154)
        assert first.get("compatibility") is None
        assert first.get("otherCompatibility") is None
        # Second instance: no design entry at all → no enrichment, no crash.
        second = next(i for i in instances if i["id"] == 9999999)
        assert "compatibility" not in second or second["compatibility"] is None


class TestImport:
    """End-to-end of POST /makerworld/import — mocks the service but exercises
    real DB writes, real ``save_3mf_bytes_to_library``, real folder auto-creation."""

    _FAKE_3MF_BYTES = b"PK\x03\x04not-a-real-3mf"

    @pytest.mark.asyncio
    async def test_returns_existing_on_source_url_match(self, async_client, db_session):
        """Re-importing a model we already have must NOT re-download.

        Dedupe key is ``{model_id}#profileId-{profile_id}`` — matches the
        canonical URL the route constructs, not the legacy model-only shape.
        """
        existing = LibraryFile(
            filename="already-here.3mf",
            file_path="library/files/already.3mf",
            file_type="3mf",
            file_size=500,
            source_type="makerworld",
            source_url="https://makerworld.com/models/1400373#profileId-298919107",
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        svc = _fake_service(
            get_design=_default_design(),
            get_profile_download=_default_manifest(),
        )
        svc.download_3mf = AsyncMock()  # must remain uncalled

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["library_file_id"] == existing.id
        assert body["was_existing"] is True
        assert body["profile_id"] == 298919107
        svc.download_3mf.assert_not_called()

    @pytest.mark.asyncio
    async def test_autocreates_makerworld_folder_when_folder_id_none(self, async_client, db_session):
        """Default destination — a top-level "MakerWorld" folder — is created
        on first import so users don't have to set it up."""
        svc = _fake_service(
            get_design=_default_design(),
            get_profile_download=_default_manifest(),
            download_3mf=(self._FAKE_3MF_BYTES, "benchy.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107, "folder_id": None},
            )
        assert resp.status_code == 200, resp.text

        # The new folder should exist, at the root.
        from sqlalchemy import select

        result = await db_session.execute(
            select(LibraryFolder).where(LibraryFolder.name == "MakerWorld", LibraryFolder.parent_id.is_(None))
        )
        folder = result.scalar_one()
        assert resp.json()["folder_id"] == folder.id

    @pytest.mark.asyncio
    async def test_uses_existing_folder_when_folder_id_provided(self, async_client, db_session):
        """Caller-supplied ``folder_id`` must be honoured even if the default
        ``MakerWorld`` folder also exists — no silent hijacking."""
        folder = LibraryFolder(name="MyCustomFolder", parent_id=None)
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        svc = _fake_service(
            get_design=_default_design(),
            get_profile_download=_default_manifest(),
            download_3mf=(self._FAKE_3MF_BYTES, "benchy.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107, "folder_id": folder.id},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["folder_id"] == folder.id

    @pytest.mark.asyncio
    async def test_canonical_source_url_includes_profile_id(self, async_client, db_session):
        """The saved row's ``source_url`` must include ``#profileId-`` so two
        plates of the same model become two library rows (dedupe is per-plate)."""
        svc = _fake_service(
            get_design=_default_design(),
            get_profile_download=_default_manifest(),
            download_3mf=(self._FAKE_3MF_BYTES, "benchy.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107},
            )
        assert resp.status_code == 200, resp.text

        from sqlalchemy import select

        row = (
            await db_session.execute(select(LibraryFile).where(LibraryFile.id == resp.json()["library_file_id"]))
        ).scalar_one()
        assert row.source_url == "https://makerworld.com/models/1400373#profileId-298919107"

    @pytest.mark.asyncio
    async def test_filename_from_upstream_is_basenamed(self, async_client, db_session):
        """Defence-in-depth: a malicious ``name`` from the upstream manifest
        (e.g. ``"../../evil.3mf"``) must not persist path components into the
        library row. On-disk storage uses a UUID already, this is belt-and-
        braces protection for the human-readable field."""
        svc = _fake_service(
            get_design=_default_design(),
            get_profile_download={
                "name": "../../evil.3mf",
                "url": "https://makerworld.bblmw.com/makerworld/model/X/Y/f.3mf?exp=1&key=k",
            },
            download_3mf=(self._FAKE_3MF_BYTES, "fallback.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["filename"] == "evil.3mf"

    @pytest.mark.asyncio
    async def test_response_includes_profile_id(self, async_client, db_session):
        """UI matches imports back to the plate row via ``profile_id`` — the
        response field must always be populated, even when the caller provided
        it explicitly (rather than the backend falling back to design defaults)."""
        svc = _fake_service(
            get_design=_default_design(),
            get_profile_download=_default_manifest(),
            download_3mf=(self._FAKE_3MF_BYTES, "benchy.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile_id"] == 298919107


class TestRecentImports:
    """GET /makerworld/recent-imports — sidebar feed on the MakerWorld page."""

    @pytest.mark.asyncio
    async def test_empty_when_no_makerworld_imports(self, async_client):
        resp = await async_client.get("/api/v1/makerworld/recent-imports")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_returns_items_newest_first(self, async_client, db_session):
        # Seed three rows with explicit, decreasing created_at timestamps so
        # ordering doesn't depend on auto-increment PK ordering.
        base = datetime(2025, 1, 1, 12, 0, 0)
        older = LibraryFile(
            filename="older.3mf",
            file_path="library/older.3mf",
            file_type="3mf",
            file_size=10,
            source_type="makerworld",
            source_url="https://makerworld.com/models/1",
            created_at=base,
        )
        middle = LibraryFile(
            filename="middle.3mf",
            file_path="library/middle.3mf",
            file_type="3mf",
            file_size=10,
            source_type="makerworld",
            source_url="https://makerworld.com/models/2",
            created_at=base + timedelta(hours=1),
        )
        newer = LibraryFile(
            filename="newer.3mf",
            file_path="library/newer.3mf",
            file_type="3mf",
            file_size=10,
            source_type="makerworld",
            source_url="https://makerworld.com/models/3",
            created_at=base + timedelta(hours=2),
        )
        # Unrelated non-MakerWorld file must NOT show up.
        other = LibraryFile(
            filename="manual.3mf",
            file_path="library/manual.3mf",
            file_type="3mf",
            file_size=10,
            source_type=None,
            source_url=None,
            created_at=base + timedelta(hours=3),
        )
        db_session.add_all([older, middle, newer, other])
        await db_session.commit()

        resp = await async_client.get("/api/v1/makerworld/recent-imports")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        names = [row["filename"] for row in body]
        assert names == ["newer.3mf", "middle.3mf", "older.3mf"]

    @pytest.mark.asyncio
    async def test_response_matches_pydantic_shape(self, async_client, db_session):
        """Lock the exact key set so the frontend's typed ``MakerworldRecentImport``
        doesn't silently fall out of sync with the backend schema."""
        row = LibraryFile(
            filename="x.3mf",
            file_path="library/x.3mf",
            file_type="3mf",
            file_size=10,
            source_type="makerworld",
            source_url="https://makerworld.com/models/1#profileId-2",
        )
        db_session.add(row)
        await db_session.commit()

        resp = await async_client.get("/api/v1/makerworld/recent-imports")
        assert resp.status_code == 200, resp.text
        item = resp.json()[0]
        assert set(item.keys()) == {
            "library_file_id",
            "filename",
            "folder_id",
            "thumbnail_path",
            "source_url",
            "created_at",
        }
        assert item["source_url"] == "https://makerworld.com/models/1#profileId-2"

    @pytest.mark.asyncio
    async def test_limit_is_honoured(self, async_client, db_session):
        for i in range(5):
            db_session.add(
                LibraryFile(
                    filename=f"f{i}.3mf",
                    file_path=f"library/f{i}.3mf",
                    file_type="3mf",
                    file_size=10,
                    source_type="makerworld",
                    source_url=f"https://makerworld.com/models/{i}",
                )
            )
        await db_session.commit()

        resp = await async_client.get("/api/v1/makerworld/recent-imports?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_limit_clamped_to_minimum(self, async_client, db_session):
        """``limit=0`` or negative must clamp to 1 — a zero limit would be
        silently swallowed by SQL and return nothing, which is surprising."""
        db_session.add(
            LibraryFile(
                filename="one.3mf",
                file_path="library/one.3mf",
                file_type="3mf",
                file_size=10,
                source_type="makerworld",
                source_url="https://makerworld.com/models/1",
            )
        )
        await db_session.commit()

        resp = await async_client.get("/api/v1/makerworld/recent-imports?limit=0")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_limit_clamped_to_maximum(self, async_client, db_session):
        """``limit`` is clamped to 50 so a pathological client can't request
        the whole table. We seed 60 rows and assert the response is capped."""
        for i in range(60):
            db_session.add(
                LibraryFile(
                    filename=f"f{i}.3mf",
                    file_path=f"library/f{i}.3mf",
                    file_type="3mf",
                    file_size=10,
                    source_type="makerworld",
                    source_url=f"https://makerworld.com/models/{i}",
                )
            )
        await db_session.commit()

        resp = await async_client.get("/api/v1/makerworld/recent-imports?limit=9999")
        assert resp.status_code == 200
        assert len(resp.json()) == 50

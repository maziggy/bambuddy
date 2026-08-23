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
from backend.app.services.model_providers.base import (
    ProviderDownload,
    ProviderDownloadInfo,
    ProviderResolvedModel,
    ProviderResourceRef,
)
from backend.app.services.model_providers.makerworld import makerworld_provider


def _download_info(
    model_id: int = 1400373,
    profile_id: int = 298919107,
    name: str = "benchy.3mf",
    url: str = "https://makerworld.bblmw.com/makerworld/model/X/Y/f.3mf?exp=1&key=k",
) -> ProviderDownloadInfo:
    """What ``service.get_download`` hands the route: signed URL + raw upstream
    name + the enriched resource ref (``sub_id`` carries the resolved profile)."""
    return ProviderDownloadInfo(
        ref=ProviderResourceRef(source_type="makerworld", external_id=str(model_id), sub_id=str(profile_id)),
        url=url,
        suggested_filename=name,
    )


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


class TestCanonicalUrl:
    """Unit test the dedupe-key builder directly — regressions break dedupe
    silently so it's worth pinning the exact shape. Asserts against the real
    provider: a mock would just pin a copy of the logic under test."""

    def test_without_profile_id(self):
        assert _canonical_url(makerworld_provider, 1400373) == "https://makerworld.com/models/1400373"

    def test_without_profile_id_when_none(self):
        assert _canonical_url(makerworld_provider, 1400373, None) == "https://makerworld.com/models/1400373"

    def test_with_profile_id(self):
        assert _canonical_url(makerworld_provider, 1400373, 298919107) == (
            "https://makerworld.com/models/1400373#profileId-298919107"
        )


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_reports_no_token_by_default(self, async_client, db_session):
        resp = await async_client.get("/api/v1/makerworld/status")
        assert resp.status_code == 200
        body = resp.json()
        # Fresh in-memory DB has no stored token, so can_download must be false.
        # sign_in_expired is False, not True: there is no sign-in to have expired.
        assert body == {"has_cloud_token": False, "can_download": False, "sign_in_expired": False}

    @pytest.mark.asyncio
    async def test_rejected_token_blocks_download_and_reports_expired(self, async_client, db_session):
        """A token Bambu has already rejected downloads nothing. ``can_download``
        used to be a bare alias for ``has_cloud_token``, so the import button
        stayed live against a dead credential and the user only found out via a
        401 toast."""
        from backend.app.models.settings import Settings
        from backend.app.services.bambu_cloud_credentials import CLOUD_TOKEN_INVALID_KEY, CLOUD_TOKEN_KEY

        db_session.add(Settings(key=CLOUD_TOKEN_KEY, value="dead-token"))
        db_session.add(Settings(key=CLOUD_TOKEN_INVALID_KEY, value="2026-07-14T07:00:00+00:00"))
        await db_session.commit()

        resp = await async_client.get("/api/v1/makerworld/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "has_cloud_token": True,
            "can_download": False,
            "sign_in_expired": True,
        }


class TestResolve:
    @pytest.mark.asyncio
    async def test_rejects_non_makerworld_url(self, async_client):
        resp = await async_client.post(
            "/api/v1/makerworld/resolve",
            json={"url": "https://thingiverse.com/thing/1"},
        )
        # A pasted link for an unsupported host is a clean client-input 400,
        # never a 500 — the registry guard runs before any provider call.
        assert resp.status_code == 400
        assert "provider" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_happy_path_returns_design_and_instances(self, async_client):
        design_payload = {"id": 1400373, "title": "Seed Starter"}
        instances_payload = [
            {"id": 1452154, "profileId": 298919107, "title": "9 cells"},
            {"id": 1452158, "profileId": 298919564, "title": "12 cells"},
        ]
        svc = _fake_service(
            resolve=ProviderResolvedModel(
                ref=ProviderResourceRef(source_type="makerworld", external_id="1400373", sub_id="1452154"),
                design=design_payload,
                instances=instances_payload,
            )
        )

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
            resolve=ProviderResolvedModel(
                ref=ProviderResourceRef(source_type="makerworld", external_id="1400373"),
                design={"id": 1400373},
                instances=[],
            )
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/resolve",
                json={"url": "https://makerworld.com/en/models/1400373"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["already_imported_library_ids"] == [existing.id]


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

        svc = _fake_service(get_download=_download_info())
        svc.download = AsyncMock()  # must remain uncalled

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
        svc.download.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_source_type_is_a_clean_400(self, async_client, db_session):
        """``source_type`` names the provider (there is no URL to route on);
        an unregistered value is a client-input problem — 400 before any
        service is built or bytes downloaded."""
        svc = _fake_service(
            get_download=_download_info(),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="benchy.3mf"),
        )
        svc.download = AsyncMock()

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "source_type": "thingiverse"},
            )
        assert resp.status_code == 400, resp.text
        assert "thingiverse" in resp.json()["detail"].lower()
        svc.download.assert_not_called()

    @pytest.mark.asyncio
    async def test_autocreates_makerworld_folder_when_folder_id_none(self, async_client, db_session):
        """Default destination — a top-level "MakerWorld" folder — is created
        on first import so users don't have to set it up."""
        svc = _fake_service(
            get_download=_download_info(),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="benchy.3mf"),
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
            get_download=_download_info(),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="benchy.3mf"),
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
            get_download=_download_info(),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="benchy.3mf"),
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
            get_download=_download_info(name="../../evil.3mf"),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="fallback.3mf"),
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
            get_download=_download_info(),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="benchy.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile_id"] == 298919107

    @pytest.mark.asyncio
    async def test_import_to_writable_external_writes_bytes_to_mount(self, async_client, db_session, tmp_path):
        """#1645: importing into a writable external folder writes the bytes to
        ``<external_path>/<filename>`` and tags the row ``is_external=True`` —
        same shape as the multipart-upload path (#1112). Previously the bytes
        landed in the internal library dir under a UUID name while the row
        showed up under the external folder in the UI, leaving a NAS/SMB user
        unable to find their file on the mount."""
        ext_dir = tmp_path / "nas-makerworld"
        ext_dir.mkdir()
        folder = LibraryFolder(
            name="NAS Imports",
            parent_id=None,
            is_external=True,
            external_path=str(ext_dir),
            external_readonly=False,
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        svc = _fake_service(
            get_download=_download_info(name="seed-starter.3mf"),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="seed-starter.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107, "folder_id": folder.id},
            )
        assert resp.status_code == 200, resp.text

        from sqlalchemy import select

        row = (
            await db_session.execute(select(LibraryFile).where(LibraryFile.id == resp.json()["library_file_id"]))
        ).scalar_one()
        assert row.folder_id == folder.id
        assert row.is_external is True, "Row must be tagged external so re-scan can reconcile it"
        # External rows persist the absolute mount path (matches scan + upload paths).
        assert row.file_path == str(ext_dir / "seed-starter.3mf")
        on_disk = ext_dir / "seed-starter.3mf"
        assert on_disk.is_file(), "Bytes must land on the external mount, not in the internal library dir"
        assert on_disk.read_bytes() == self._FAKE_3MF_BYTES

    @pytest.mark.asyncio
    async def test_import_to_readonly_external_rejected_at_route(self, async_client, db_session, tmp_path):
        """The route-layer gate in ``import_instance`` rejects read-only
        external folders with 403 before any download happens — so MakerWorld
        credentials and the upstream download bandwidth aren't wasted."""
        ext_dir = tmp_path / "nas-readonly"
        ext_dir.mkdir()
        folder = LibraryFolder(
            name="NAS read-only",
            parent_id=None,
            is_external=True,
            external_path=str(ext_dir),
            external_readonly=True,
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        svc = _fake_service(get_download=_download_info())
        svc.download = AsyncMock()

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107, "folder_id": folder.id},
            )
        assert resp.status_code == 403, resp.text
        svc.download.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_to_external_with_missing_path_returns_400(self, async_client, db_session, tmp_path):
        """If the external folder's mount has gone away (NAS unplugged, SMB
        share down), ``_resolve_upload_destination`` returns 400 before the
        write so we don't silently fall back to the internal library dir."""
        missing_dir = tmp_path / "vanished-mount"  # NOTE: deliberately not created
        folder = LibraryFolder(
            name="NAS gone",
            parent_id=None,
            is_external=True,
            external_path=str(missing_dir),
            external_readonly=False,
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        svc = _fake_service(
            get_download=_download_info(),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="benchy.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107, "folder_id": folder.id},
            )
        assert resp.status_code == 400, resp.text
        assert "not accessible" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_import_to_external_with_name_collision_returns_409(self, async_client, db_session, tmp_path):
        """A user-visible 409 fires when the filename already exists on the
        external mount, instead of silently overwriting a file the user put
        there outside Bambuddy."""
        ext_dir = tmp_path / "nas-collide"
        ext_dir.mkdir()
        (ext_dir / "benchy.3mf").write_bytes(b"pre-existing")

        folder = LibraryFolder(
            name="NAS collide",
            parent_id=None,
            is_external=True,
            external_path=str(ext_dir),
            external_readonly=False,
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        svc = _fake_service(
            get_download=_download_info(name="benchy.3mf"),
            download=ProviderDownload(file_bytes=self._FAKE_3MF_BYTES, filename="benchy.3mf"),
        )

        with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=svc)):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 1400373, "profile_id": 298919107, "folder_id": folder.id},
            )
        assert resp.status_code == 409, resp.text
        # Pre-existing file's contents must not be clobbered by the failed write.
        assert (ext_dir / "benchy.3mf").read_bytes() == b"pre-existing"


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

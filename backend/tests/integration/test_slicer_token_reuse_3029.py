"""Integration tests for reusable slicer download tokens (#3029).

The "Slice" action hands a URL to a *separate process* -- Bambu Studio or
OrcaSlicer, launched through a protocol handler that cannot carry an
``Authorization`` header. Until this fix the token in that URL was consumed by
the first request that reached the endpoint, which made the handoff dependent
on the slicer fetching the URL exactly once. Nothing guarantees that: Bambu
Studio's downloader retries three times after a failed attempt, transfers get
resumed, on-access scanners fetch. Whichever party arrived first won, and the
slicer was handed a 403.

So the three protocol-handler downloads now accept their token for the rest of
its five-minute TTL. Everything else about the token is unchanged, and these
tests pin the difference in both directions: the second fetch works, and the
token is still refused for the wrong resource, after expiry, and when unknown.

The two *browser* downloads that share the same primitive stay one-shot, and
are pinned here too -- the prepared printer bundle is deleted once streamed, so
reuse there could only ever mean a 404 with a misleading cause.

The second half covers a fault found while checking the first: the auth
middleware matches ``PUBLIC_API_PATTERNS`` by substring, and the source-3MF
route's segment is ``source-dl`` -- which does not contain ``/dl/``. With auth
enabled the middleware rejected the slicer's header-less request before the
route's own token check ever ran.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# Same reasoning as #3025's fixtures: the routes resolve paths relative to
# ``settings.base_dir``, which under test is the project root, so everything
# goes in one subdirectory that is removed after each test.
_FILE_DIR = "test_files_3029"


@pytest.fixture(autouse=True)
def _clean_files():
    from backend.app.core.config import settings

    yield
    shutil.rmtree(Path(settings.base_dir) / _FILE_DIR, ignore_errors=True)


def _write(name: str, body: bytes) -> str:
    """Write a file under the scratch dir and return its base_dir-relative path."""
    from backend.app.core.config import settings

    path = Path(settings.base_dir) / _FILE_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return f"{_FILE_DIR}/{name}"


async def _library_file(db_session, name: str, body: bytes = b"solid test\nendsolid test\n") -> int:
    from backend.app.models.library import LibraryFile

    row = LibraryFile(
        filename=f"{name}.stl",
        file_path=_write(f"{name}.stl", body),
        file_type="stl",
        file_size=len(body),
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


async def _archive(db_session, name: str, *, with_source: bool = False) -> int:
    from backend.app.models.archive import PrintArchive

    row = PrintArchive(
        filename=f"{name}.3mf",
        file_path=_write(f"{name}.3mf", b"PK\x03\x04sliced"),
        file_size=13,
        source_3mf_path=_write(f"{name}_source.3mf", b"PK\x03\x04source") if with_source else None,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


async def _stored_token(resource_type: str, resource_id: int, *, expires_in_minutes: int = 5) -> str:
    """Insert a slicer token directly, so expiry can be set to the past."""
    import secrets

    from backend.app.core.database import async_session
    from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType

    token = secrets.token_urlsafe(24)
    async with async_session() as db:
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type=TokenType.SLICER_DOWNLOAD,
                nonce=f"{resource_type}:{resource_id}",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes),
            )
        )
        await db.commit()
    return token


class TestTheSlicerThatFetchesTwice:
    """The reported fault: the second fetch of the same URL got a 403, and the
    slicer wrote that JSON body out as the model."""

    async def test_a_library_download_survives_a_second_fetch(self, async_client: AsyncClient, db_session):
        file_id = await _library_file(db_session, "reused")
        minted = await async_client.post(f"/api/v1/library/files/{file_id}/slicer-token")
        assert minted.status_code == 200, minted.text
        token = minted.json()["token"]

        url = f"/api/v1/library/files/{file_id}/dl/{token}/reused.stl"
        first = await async_client.get(url)
        assert first.status_code == 200, first.text
        assert first.content.startswith(b"solid test")

        second = await async_client.get(url)
        assert second.status_code == 200, second.text
        assert second.content == first.content

        third = await async_client.get(url)
        assert third.status_code == 200

    async def test_an_archive_download_survives_a_second_fetch(self, async_client: AsyncClient, db_session):
        archive_id = await _archive(db_session, "arc_reused")
        minted = await async_client.post(f"/api/v1/archives/{archive_id}/slicer-token")
        assert minted.status_code == 200, minted.text
        token = minted.json()["token"]

        url = f"/api/v1/archives/{archive_id}/dl/{token}/arc_reused.3mf"
        assert (await async_client.get(url)).status_code == 200
        assert (await async_client.get(url)).status_code == 200

    async def test_a_source_3mf_download_survives_a_second_fetch(self, async_client: AsyncClient, db_session):
        archive_id = await _archive(db_session, "src_reused", with_source=True)
        minted = await async_client.post(f"/api/v1/archives/{archive_id}/source-slicer-token")
        assert minted.status_code == 200, minted.text
        token = minted.json()["token"]

        url = f"/api/v1/archives/{archive_id}/source-dl/{token}/src_reused.3mf"
        first = await async_client.get(url)
        assert first.status_code == 200, first.text
        assert (await async_client.get(url)).status_code == 200


class TestWhatTheReusableTokenStillRefuses:
    """Reuse is the only thing that changed. Resource binding and expiry are
    what make these URLs safe to hand out, so each is checked explicitly."""

    async def test_it_is_still_bound_to_one_file(self, async_client: AsyncClient, db_session):
        mine = await _library_file(db_session, "bound_mine")
        theirs = await _library_file(db_session, "bound_theirs")
        token = (await async_client.post(f"/api/v1/library/files/{mine}/slicer-token")).json()["token"]

        wrong = await async_client.get(f"/api/v1/library/files/{theirs}/dl/{token}/bound_theirs.stl")
        assert wrong.status_code == 403

        # And the rejected attempt must not have burned the token for its own file.
        right = await async_client.get(f"/api/v1/library/files/{mine}/dl/{token}/bound_mine.stl")
        assert right.status_code == 200

    async def test_an_archive_token_does_not_open_the_source_3mf(self, async_client: AsyncClient, db_session):
        """The two archive downloads are separate resource keys on the same id."""
        archive_id = await _archive(db_session, "cross_key", with_source=True)
        token = (await async_client.post(f"/api/v1/archives/{archive_id}/slicer-token")).json()["token"]

        crossed = await async_client.get(f"/api/v1/archives/{archive_id}/source-dl/{token}/cross_key.3mf")
        assert crossed.status_code == 403

    async def test_an_expired_token_is_refused(self, async_client: AsyncClient, db_session):
        file_id = await _library_file(db_session, "stale")
        token = await _stored_token("library", file_id, expires_in_minutes=-1)

        response = await async_client.get(f"/api/v1/library/files/{file_id}/dl/{token}/stale.stl")
        assert response.status_code == 403

    async def test_an_unknown_token_is_refused(self, async_client: AsyncClient, db_session):
        file_id = await _library_file(db_session, "unknown")
        response = await async_client.get(f"/api/v1/library/files/{file_id}/dl/not-a-token/unknown.stl")
        assert response.status_code == 403


class TestTheOneShotDownloadsStayOneShot:
    """Reuse was granted per endpoint, not to the primitive. The two browser
    downloads keep consuming their token, and the default is still to consume
    -- a new caller has to ask for reuse deliberately."""

    async def test_the_primitive_still_consumes_by_default(self, async_client: AsyncClient, db_session):
        from backend.app.core.auth import verify_slicer_download_token

        token = await _stored_token("printer-files", 7)
        assert await verify_slicer_download_token(token, "printer-files", 7) is True
        assert await verify_slicer_download_token(token, "printer-files", 7) is False

    async def test_a_reusable_check_does_not_consume(self, async_client: AsyncClient, db_session):
        from backend.app.core.auth import verify_slicer_download_token

        token = await _stored_token("library", 7)
        assert await verify_slicer_download_token(token, "library", 7, single_use=False) is True
        assert await verify_slicer_download_token(token, "library", 7, single_use=False) is True
        # ...and a consuming check on the same row still works, so the row is
        # not a different kind of token -- only the redemption differs.
        assert await verify_slicer_download_token(token, "library", 7) is True
        assert await verify_slicer_download_token(token, "library", 7, single_use=False) is False

    async def test_the_archive_timelapse_download_is_still_single_use(self, async_client: AsyncClient, db_session):
        from backend.app.models.archive import PrintArchive

        row = PrintArchive(
            filename="tl.3mf",
            file_path=_write("tl.3mf", b"PK\x03\x04"),
            file_size=4,
            timelapse_path=_write("tl.mp4", b"\x00\x00\x00 ftypisom"),
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        token = (await async_client.post(f"/api/v1/archives/{row.id}/media-download-token")).json()["token"]
        url = f"/api/v1/archives/{row.id}/media/dl/{token}/tl.mp4"
        assert (await async_client.get(url)).status_code == 200
        assert (await async_client.get(url)).status_code == 403


class TestTheSourceDownloadReachesItsHandler:
    """``PUBLIC_API_PATTERNS`` is matched with ``in path``, and ``source-dl/``
    does not contain ``/dl/``. With auth enabled the middleware answered 401
    before the route's token check ran, so "Open source 3MF in slicer" could
    never work -- the slicer has no header to send."""

    async def test_the_pattern_list_covers_the_source_route(self):
        from backend.app.main import PUBLIC_API_PATTERNS

        path = "/api/v1/archives/5/source-dl/tok/model.3mf"
        assert not any(p in path for p in ["/dl/"]), "guard: /dl/ must not cover source-dl"
        assert any(p in path for p in PUBLIC_API_PATTERNS)

    async def test_the_source_download_works_with_auth_enabled(self, async_client: AsyncClient, db_session):
        setup = await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "slicer3029", "admin_password": "AdminPass1!"},
        )
        assert setup.status_code in (200, 201), setup.text
        login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "slicer3029", "password": "AdminPass1!"},
        )
        assert login.status_code == 200, login.text
        jwt = login.json()["access_token"]

        archive_id = await _archive(db_session, "authed_source", with_source=True)
        minted = await async_client.post(
            f"/api/v1/archives/{archive_id}/source-slicer-token",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert minted.status_code == 200, minted.text
        token = minted.json()["token"]

        # No Authorization header -- exactly what the protocol handler sends.
        response = await async_client.get(f"/api/v1/archives/{archive_id}/source-dl/{token}/authed_source.3mf")
        assert response.status_code == 200, response.text
        assert response.content == b"PK\x03\x04source"

    async def test_a_bad_token_is_refused_by_the_handler_not_the_middleware(
        self, async_client: AsyncClient, db_session
    ):
        """403, not 401: the middleware stepping aside must not make the route
        public, and the distinction is what proves the handler ran."""
        setup = await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "slicer3029b", "admin_password": "AdminPass1!"},
        )
        assert setup.status_code in (200, 201), setup.text
        archive_id = await _archive(db_session, "refused_source", with_source=True)

        response = await async_client.get(f"/api/v1/archives/{archive_id}/source-dl/nope/refused_source.3mf")
        assert response.status_code == 403

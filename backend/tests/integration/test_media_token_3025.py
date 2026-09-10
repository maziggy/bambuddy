"""Integration tests for the media token (#3025).

Thirteen non-camera media routes -- library and archive thumbnails, plate
previews, timelapses, print photos, QR codes, project covers, link icons --
were gated by the *camera stream* token. That had two consequences, and these
tests pin both fixes:

1. ``camera:view`` was a prerequisite for every image in the app. A user given
   library access to their own job folder saw broken thumbnails until they were
   also handed the live feed of the room the printer is in.
2. A camera stream token records no principal, so those routes had no identity
   to scope by and returned any row to any holder.

The media token is the replacement: minted behind plain authentication, and
identified, so each route applies the same permission and ownership rules as
its header-authenticated siblings.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# library:read_own + archives:read_own, and deliberately NOT camera:view --
# the reporter's exact group in #3025.
NO_CAMERA_PERMISSIONS = [
    "library:read_own",
    "library:upload",
    "archives:read_own",
    "projects:read",
    "external_links:read",
    "printers:read",
]


async def _admin_token(async_client: AsyncClient, suffix: str) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": f"mediaadmin{suffix}",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": f"mediaadmin{suffix}", "password": "AdminPass1!"},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _make_user(
    async_client: AsyncClient,
    admin_jwt: str,
    *,
    username: str,
    permissions: list[str],
) -> tuple[str, int]:
    """Create a user in a fresh group holding exactly *permissions*."""
    group = await async_client.post(
        "/api/v1/groups/",
        headers={"Authorization": f"Bearer {admin_jwt}"},
        json={"name": f"grp_{username}", "permissions": permissions},
    )
    assert group.status_code in (200, 201), group.text
    created = await async_client.post(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {admin_jwt}"},
        json={
            "username": username,
            "password": "UserPass1!",
            "group_ids": [group.json()["id"]],
        },
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "UserPass1!"},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"], created.json()["id"]


async def _mint_media_token(async_client: AsyncClient, jwt: str) -> str:
    response = await async_client.post(
        "/api/v1/auth/media-token",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def _mint_camera_token(async_client: AsyncClient, jwt: str) -> str:
    response = await async_client.post(
        "/api/v1/printers/camera/stream-token",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


# The routes resolve thumbnails relative to ``settings.base_dir``, so the
# fixtures have to write there rather than into tmp_path. Keep them in one
# subdirectory and delete it after every test so a run leaves the tree clean.
_THUMB_DIR = "test_thumbs_3025"


@pytest.fixture(autouse=True)
def _clean_thumbs():
    from backend.app.core.config import settings

    yield
    shutil.rmtree(Path(settings.base_dir) / _THUMB_DIR, ignore_errors=True)


async def _library_file(db_session, owner_id: int | None, name: str) -> int:
    """Insert a library row with a real thumbnail on disk."""
    from backend.app.core.config import settings
    from backend.app.models.library import LibraryFile

    thumb = Path(settings.base_dir) / _THUMB_DIR / f"{name}.png"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    row = LibraryFile(
        filename=f"{name}.3mf",
        file_path=f"library/files/{name}.3mf",
        thumbnail_path=f"{_THUMB_DIR}/{thumb.name}",
        file_type="3mf",
        file_size=1234,
        created_by_id=owner_id,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


class TestTheUserWhoCouldNotSeeTheirOwnThumbnails:
    """The reported fault: camera:view was load-bearing for every image."""

    async def test_a_user_without_camera_view_can_mint_a_media_token(self, async_client: AsyncClient):
        admin = await _admin_token(async_client, "_mint")
        jwt, _ = await _make_user(async_client, admin, username="nocamera_mint", permissions=NO_CAMERA_PERMISSIONS)
        response = await async_client.post("/api/v1/auth/media-token", headers={"Authorization": f"Bearer {jwt}"})
        assert response.status_code == 200, response.text
        assert response.json()["token"]

    async def test_the_camera_token_is_still_out_of_reach_for_them(self, async_client: AsyncClient):
        """The permission split is real, not cosmetic: the media token does not
        smuggle in camera access, and minting a camera token still costs
        camera:view."""
        admin = await _admin_token(async_client, "_nocam")
        jwt, _ = await _make_user(async_client, admin, username="nocamera_still", permissions=NO_CAMERA_PERMISSIONS)
        response = await async_client.post(
            "/api/v1/printers/camera/stream-token", headers={"Authorization": f"Bearer {jwt}"}
        )
        assert response.status_code == 403

    async def test_they_can_load_their_own_library_thumbnail(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_own")
        jwt, user_id = await _make_user(async_client, admin, username="nocamera_own", permissions=NO_CAMERA_PERMISSIONS)
        file_id = await _library_file(db_session, user_id, "own")
        token = await _mint_media_token(async_client, jwt)

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail?token={token}")
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"\x89PNG")


class TestTheBoundaryBetweenTheTwoTokens:
    """Neither token is accepted where the other belongs."""

    async def test_a_camera_stream_token_is_refused_on_a_media_route(self, async_client: AsyncClient, db_session):
        """The inverse of verify_camwall_token's rule. A camera-stream token is
        anonymous, so honouring it here would reinstate the unowned read."""
        admin = await _admin_token(async_client, "_xcam")
        file_id = await _library_file(db_session, None, "xcam")
        camera_token = await _mint_camera_token(async_client, admin)

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail?token={camera_token}")
        assert response.status_code == 401

    async def test_a_media_token_is_refused_on_the_live_camera(self, async_client: AsyncClient):
        admin = await _admin_token(async_client, "_xmedia")
        media_token = await _mint_media_token(async_client, admin)

        response = await async_client.get(f"/api/v1/printers/1/camera/snapshot?token={media_token}")
        assert response.status_code == 401

    async def test_no_token_at_all_is_refused(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client, "_notok")
        file_id = await _library_file(db_session, None, "notok")

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail")
        assert response.status_code == 401

    async def test_a_garbage_token_is_refused(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client, "_garbage")
        file_id = await _library_file(db_session, None, "garbage")

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail?token=not-a-real-token")
        assert response.status_code == 401


class TestWhoseRowsAMediaTokenCanRead:
    """The unreported half: the old guard had no principal, so it had nothing
    to scope by. These fail against the camera-token implementation."""

    async def test_it_cannot_read_another_users_library_thumbnail(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_cross")
        _, alice_id = await _make_user(async_client, admin, username="alice_lib", permissions=NO_CAMERA_PERMISSIONS)
        bob_jwt, _ = await _make_user(async_client, admin, username="bob_lib", permissions=NO_CAMERA_PERMISSIONS)
        alice_file = await _library_file(db_session, alice_id, "alice")
        bob_token = await _mint_media_token(async_client, bob_jwt)

        response = await async_client.get(f"/api/v1/library/files/{alice_file}/thumbnail?token={bob_token}")
        # 404 rather than 403 -- the same id-enumeration-proof answer
        # _ensure_library_file_visible gives on every other library route.
        assert response.status_code == 404

    async def test_an_ownerless_file_needs_read_all(self, async_client: AsyncClient, db_session):
        """Fail-closed, matching _ensure_library_file_visible."""
        admin = await _admin_token(async_client, "_orphan")
        jwt, _ = await _make_user(async_client, admin, username="orphan_reader", permissions=NO_CAMERA_PERMISSIONS)
        file_id = await _library_file(db_session, None, "orphan")
        token = await _mint_media_token(async_client, jwt)

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail?token={token}")
        assert response.status_code == 404

    async def test_an_admin_with_read_all_still_sees_everything(self, async_client: AsyncClient, db_session):
        """The gate must not over-correct into breaking legitimate access."""
        admin = await _admin_token(async_client, "_readall")
        _, alice_id = await _make_user(async_client, admin, username="alice_readall", permissions=NO_CAMERA_PERMISSIONS)
        alice_file = await _library_file(db_session, alice_id, "readall")
        admin_token = await _mint_media_token(async_client, admin)

        response = await async_client.get(f"/api/v1/library/files/{alice_file}/thumbnail?token={admin_token}")
        assert response.status_code == 200


class TestWhatTheTokenStillRequires:
    """A media token is authentication, not authorisation -- each route keeps
    asking for the permission its resource is governed by."""

    async def test_a_user_without_library_permission_is_refused(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_noperm")
        jwt, user_id = await _make_user(async_client, admin, username="noperm_user", permissions=["printers:read"])
        file_id = await _library_file(db_session, user_id, "noperm")
        token = await _mint_media_token(async_client, jwt)

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail?token={token}")
        assert response.status_code == 403

    async def test_a_deactivated_users_token_stops_working(self, async_client: AsyncClient, db_session):
        """The token outlives the session it was minted in, so the principal is
        re-resolved on every request rather than trusted from mint time."""
        admin = await _admin_token(async_client, "_deact")
        jwt, user_id = await _make_user(async_client, admin, username="deact_user", permissions=NO_CAMERA_PERMISSIONS)
        file_id = await _library_file(db_session, user_id, "deact")
        token = await _mint_media_token(async_client, jwt)
        assert (await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail?token={token}")).status_code == 200

        deactivate = await async_client.patch(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {admin}"},
            json={"is_active": False},
        )
        assert deactivate.status_code == 200, deactivate.text

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail?token={token}")
        assert response.status_code == 401


class TestTheHeaderPathStillWorks:
    """A media route is reachable with ordinary credentials too, so a fetch()
    or an API-keyed integration does not need a token at all."""

    async def test_a_bearer_jwt_reaches_a_media_route_without_any_token(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_bearer")
        jwt, user_id = await _make_user(async_client, admin, username="bearer_user", permissions=NO_CAMERA_PERMISSIONS)
        file_id = await _library_file(db_session, user_id, "bearer")

        response = await async_client.get(
            f"/api/v1/library/files/{file_id}/thumbnail",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert response.status_code == 200

    async def test_the_header_path_is_ownership_scoped_too(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_bearerx")
        _, alice_id = await _make_user(async_client, admin, username="alice_bearer", permissions=NO_CAMERA_PERMISSIONS)
        bob_jwt, _ = await _make_user(async_client, admin, username="bob_bearer", permissions=NO_CAMERA_PERMISSIONS)
        alice_file = await _library_file(db_session, alice_id, "alicebearer")

        response = await async_client.get(
            f"/api/v1/library/files/{alice_file}/thumbnail",
            headers={"Authorization": f"Bearer {bob_jwt}"},
        )
        assert response.status_code == 404


class TestAuthDisabled:
    async def test_media_routes_stay_open_when_auth_is_off(self, async_client: AsyncClient, db_session):
        """No setup call -- auth is off, and the routes must not start
        demanding a token that an unauthenticated install cannot mint."""
        file_id = await _library_file(db_session, None, "authoff")

        response = await async_client.get(f"/api/v1/library/files/{file_id}/thumbnail")
        assert response.status_code == 200


async def _archive(db_session, owner_id: int | None, name: str) -> int:
    """Insert an archive with a real thumbnail and timelapse on disk."""
    from backend.app.core.config import settings
    from backend.app.models.archive import PrintArchive

    base = Path(settings.base_dir) / _THUMB_DIR
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{name}_thumb.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    (base / f"{name}_tl.mp4").write_bytes(b"\x00\x00\x00 ftypisom" + b"0" * 32)

    row = PrintArchive(
        filename=f"{name}.3mf",
        file_path=f"archives/{name}.3mf",
        file_size=1234,
        thumbnail_path=f"{_THUMB_DIR}/{name}_thumb.png",
        timelapse_path=f"{_THUMB_DIR}/{name}_tl.mp4",
        created_by_id=owner_id,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


class TestTheArchiveMediaRoutes:
    """The seven archive routes are where the sensitive content lives -- a
    timelapse and the finish photos are a video of someone's room. They are
    covered separately from library because the existing integration suite runs
    with auth disabled, so nothing else exercises them with auth on."""

    async def test_an_owner_can_load_their_archive_thumbnail(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_arcown")
        jwt, uid = await _make_user(async_client, admin, username="arc_owner", permissions=NO_CAMERA_PERMISSIONS)
        archive_id = await _archive(db_session, uid, "arcown")
        token = await _mint_media_token(async_client, jwt)

        response = await async_client.get(f"/api/v1/archives/{archive_id}/thumbnail?token={token}")
        assert response.status_code == 200, response.text

    async def test_another_user_cannot_load_that_thumbnail(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_arcx")
        _, alice_id = await _make_user(async_client, admin, username="alice_arc", permissions=NO_CAMERA_PERMISSIONS)
        bob_jwt, _ = await _make_user(async_client, admin, username="bob_arc", permissions=NO_CAMERA_PERMISSIONS)
        archive_id = await _archive(db_session, alice_id, "arcx")
        bob_token = await _mint_media_token(async_client, bob_jwt)

        response = await async_client.get(f"/api/v1/archives/{archive_id}/thumbnail?token={bob_token}")
        assert response.status_code == 404

    async def test_another_user_cannot_load_that_timelapse(self, async_client: AsyncClient, db_session):
        """The one that matters most: a timelapse is footage of the room the
        printer is in."""
        admin = await _admin_token(async_client, "_arctl")
        _, alice_id = await _make_user(async_client, admin, username="alice_tl", permissions=NO_CAMERA_PERMISSIONS)
        bob_jwt, _ = await _make_user(async_client, admin, username="bob_tl", permissions=NO_CAMERA_PERMISSIONS)
        archive_id = await _archive(db_session, alice_id, "arctl")
        bob_token = await _mint_media_token(async_client, bob_jwt)

        assert (await async_client.get(f"/api/v1/archives/{archive_id}/timelapse?token={bob_token}")).status_code == 404

    async def test_a_camera_token_reaches_no_archive_media(self, async_client: AsyncClient, db_session):
        admin = await _admin_token(async_client, "_arccam")
        archive_id = await _archive(db_session, None, "arccam")
        camera_token = await _mint_camera_token(async_client, admin)

        for path in ("thumbnail", "timelapse", "plate-preview", "qrcode"):
            response = await async_client.get(f"/api/v1/archives/{archive_id}/{path}?token={camera_token}")
            assert response.status_code == 401, f"{path} accepted a camera token: {response.status_code}"


class TestTheFlatPermissionMediaRoutes:
    """printers/{id}/cover, external-links/{id}/icon and projects/{id}/cover-image
    have no per-row owner, so they gate on the resource's read permission."""

    async def test_the_link_icon_needs_external_links_read(self, async_client: AsyncClient):
        admin = await _admin_token(async_client, "_icon")
        jwt, _ = await _make_user(async_client, admin, username="icon_user", permissions=["printers:read"])
        token = await _mint_media_token(async_client, jwt)

        response = await async_client.get(f"/api/v1/external-links/1/icon?token={token}")
        assert response.status_code == 403

    async def test_the_link_icon_is_reachable_with_that_permission(self, async_client: AsyncClient):
        admin = await _admin_token(async_client, "_icon2")
        jwt, _ = await _make_user(async_client, admin, username="icon_user2", permissions=NO_CAMERA_PERMISSIONS)
        token = await _mint_media_token(async_client, jwt)

        # 404 because no such link exists -- the point is that it is not 401/403.
        response = await async_client.get(f"/api/v1/external-links/1/icon?token={token}")
        assert response.status_code == 404

    async def test_the_printer_cover_needs_printers_read(self, async_client: AsyncClient):
        admin = await _admin_token(async_client, "_cover")
        jwt, _ = await _make_user(async_client, admin, username="cover_user", permissions=["external_links:read"])
        token = await _mint_media_token(async_client, jwt)

        response = await async_client.get(f"/api/v1/printers/1/cover?token={token}")
        assert response.status_code == 403

    async def test_the_project_cover_needs_projects_read(self, async_client: AsyncClient):
        admin = await _admin_token(async_client, "_pcover")
        jwt, _ = await _make_user(async_client, admin, username="pcover_user", permissions=["printers:read"])
        token = await _mint_media_token(async_client, jwt)

        response = await async_client.get(f"/api/v1/projects/1/cover-image?token={token}")
        assert response.status_code == 403

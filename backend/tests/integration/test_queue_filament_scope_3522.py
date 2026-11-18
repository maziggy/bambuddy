"""Queueing a job refuses filaments the user's groups don't allow (#3522)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from backend.app.core.config import settings
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

QUEUE_PERMISSIONS = [
    "printers:read",
    "queue:read_all",
    "queue:create",
    "archives:read_all",
    "archives:reprint_all",
    "library:read_all",
]


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


@pytest.fixture(autouse=True)
def _base_dir(tmp_path, monkeypatch):
    # Archives resolve under settings.base_dir, which is the repo root in tests.
    monkeypatch.setattr(settings, "base_dir", tmp_path)


PETG_MATTE = '<filament id="1" used_g="5" type="PETG" tray_info_idx="GFG03" color="#FFFFFF"/>'
PLA = '<filament id="1" used_g="5" type="PLA" tray_info_idx="GFA00" color="#FFFFFF"/>'


def _write_3mf(rel: str, filaments: str) -> None:
    path = Path(settings.base_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        # A plate G-code member, or the queue refuses the file as unsliced (#3435).
        zf.writestr("Metadata/plate_1.gcode", "; gcode\n")
        zf.writestr(
            "Metadata/slice_info.config",
            f'<config><plate><metadata key="index" value="1"/>{filaments}</plate></config>',
        )


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "qadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "qadmin", "password": "AdminPass1!"})
    return login.json()["access_token"]


async def _friend(async_client: AsyncClient, admin: str, rules: list[dict], mode: str = "block") -> str:
    perms = await async_client.post(
        "/api/v1/groups/", headers=_auth(admin), json={"name": "perms", "permissions": QUEUE_PERMISSIONS}
    )
    friends = await async_client.post(
        "/api/v1/groups/",
        headers=_auth(admin),
        json={"name": "friends", "restrict_filaments": True, "filament_rule_mode": mode, "filament_rules": rules},
    )
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin),
        json={"username": "friend", "password": "UserPass1!", "group_ids": [perms.json()["id"], friends.json()["id"]]},
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post("/api/v1/auth/login", json={"username": "friend", "password": "UserPass1!"})
    return login.json()["access_token"]


BLOCK_PETG_MATTE = [{"match": "filament_id", "value": "GFG03"}]


async def _printer_and_archive(printer_factory, archive_factory, name: str, filaments: str):
    printer = await printer_factory(name="H3C")
    rel = f"archives/fil/{name}.gcode.3mf"
    _write_3mf(rel, filaments)
    return printer, await archive_factory(printer.id, file_path=rel)


def _library_file(db_session, name: str, filaments: str, metadata: dict | None = None) -> LibraryFile:
    rel = f"library/{name}.gcode.3mf"
    _write_3mf(rel, filaments)
    row = LibraryFile(
        filename=f"{name}.gcode.3mf", file_path=rel, file_size=100, file_type="gcode.3mf", file_metadata=metadata or {}
    )
    db_session.add(row)
    return row


async def _queue(async_client: AsyncClient, jwt: str, **job):
    return await async_client.post("/api/v1/queue/", headers=_auth(jwt), json=job)


class TestQueueFilamentScope:
    async def test_blocked_filament_is_refused_with_its_name(self, async_client, printer_factory, archive_factory):
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "matte", PETG_MATTE)
        friend = await _friend(async_client, await _admin_token(async_client), BLOCK_PETG_MATTE)

        refused = await _queue(async_client, friend, archive_id=archive.id, printer_id=printer.id)
        assert refused.status_code == 403, refused.text
        assert "PETG (GFG03)" in refused.json()["detail"]

    async def test_allowed_filament_is_queued(self, async_client, printer_factory, archive_factory):
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "pla", PLA)
        friend = await _friend(async_client, await _admin_token(async_client), [{"match": "material", "value": "PETG"}])

        ok = await _queue(async_client, friend, archive_id=archive.id, printer_id=printer.id)
        assert ok.status_code == 200, ok.text

    async def test_admin_is_never_refused(self, async_client, printer_factory, archive_factory):
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "admin", PETG_MATTE)
        admin = await _admin_token(async_client)
        await _friend(async_client, admin, BLOCK_PETG_MATTE)
        # The admin is in the restricted group too: being an admin is what lets them through.
        me = (await async_client.get("/api/v1/auth/me", headers=_auth(admin))).json()
        groups = (await async_client.get("/api/v1/groups/", headers=_auth(admin))).json()
        friends_id = next(g["id"] for g in groups if g["name"] == "friends")
        joined = await async_client.patch(
            f"/api/v1/users/{me['id']}",
            headers=_auth(admin),
            json={"group_ids": [g["id"] for g in me["groups"]] + [friends_id]},
        )
        assert joined.status_code == 200, joined.text
        assert "friends" in [g["name"] for g in joined.json()["groups"]]

        ok = await _queue(async_client, admin, archive_id=archive.id, printer_id=printer.id)
        assert ok.status_code == 200, ok.text

    async def test_refused_before_the_queue_is_locked(self, async_client, printer_factory, archive_factory):
        """The 3MF is read before any lock or write (#3522)."""
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "early", PETG_MATTE)
        friend = await _friend(async_client, await _admin_token(async_client), BLOCK_PETG_MATTE)

        lock = AsyncMock()
        with patch("backend.app.api.routes.print_queue.lock_queue_positions", lock):
            refused = await _queue(async_client, friend, archive_id=archive.id, printer_id=printer.id, quantity=2)
        assert refused.status_code == 403, refused.text
        lock.assert_not_awaited()

    async def test_a_library_file_is_judged_too(self, async_client, printer_factory, db_session):
        printer = await printer_factory(name="H3C", model="X1C")
        library_file = _library_file(db_session, "matte", PETG_MATTE)
        await db_session.commit()
        friend = await _friend(async_client, await _admin_token(async_client), BLOCK_PETG_MATTE)

        refused = await _queue(async_client, friend, library_file_id=library_file.id, printer_id=printer.id)
        assert refused.status_code == 403, refused.text
        assert "PETG (GFG03)" in refused.json()["detail"]

    async def test_each_cross_model_variant_is_judged(self, async_client, printer_factory, db_session):
        """A job offered to several models is refused if any of its files is (#3522)."""
        await printer_factory(name="S", model="H2S")
        await printer_factory(name="C", model="H2C")
        files = [
            _library_file(db_session, model, filaments, {"sliced_for_model": model})
            for model, filaments in (("H2S", PLA), ("H2C", PETG_MATTE))
        ]
        await db_session.commit()
        friend = await _friend(async_client, await _admin_token(async_client), BLOCK_PETG_MATTE)

        refused = await _queue(async_client, friend, variants=[{"library_file_id": f.id} for f in files])
        assert refused.status_code == 403, refused.text
        assert "PETG (GFG03)" in refused.json()["detail"]


class TestOverridesAreJudgedAsTheSchedulerJudgesThem:
    """The 403 applies the job's overrides the way the scheduler does, so both answer alike (#3522)."""

    async def test_a_colour_swap_is_left_to_the_tray_matcher(self, async_client, printer_factory, archive_factory):
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "swap", PETG_MATTE)
        friend = await _friend(async_client, await _admin_token(async_client), BLOCK_PETG_MATTE)

        ok = await _queue(
            async_client,
            friend,
            archive_id=archive.id,
            printer_id=printer.id,
            filament_overrides=[{"slot_id": 1, "type": "PETG", "color": "#000000", "force_color_match": False}],
        )
        assert ok.status_code == 200, ok.text

    async def test_a_forced_colour_naming_a_blocked_id_is_refused(self, async_client, printer_factory, archive_factory):
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "forced", PLA)
        friend = await _friend(async_client, await _admin_token(async_client), BLOCK_PETG_MATTE)

        refused = await _queue(
            async_client,
            friend,
            archive_id=archive.id,
            printer_id=printer.id,
            filament_overrides=[
                {"slot_id": 1, "type": "PETG", "color": "#FFFFFF", "tray_info_idx": "GFG03", "force_color_match": True}
            ],
        )
        assert refused.status_code == 403, refused.text
        assert "PETG (GFG03)" in refused.json()["detail"]


class TestDeletingALimitedUser:
    async def test_their_kept_jobs_wait_for_an_admin(self, async_client, printer_factory, archive_factory, db_session):
        # Ownerless jobs are unrestricted, so a job held, or about to be, by
        # the deleted user's filament rules is staged rather than released (#3522).
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "kept", PLA)
        admin = await _admin_token(async_client)
        friend = await _friend(async_client, admin, BLOCK_PETG_MATTE)
        queued = await _queue(async_client, friend, archive_id=archive.id, printer_id=printer.id)
        assert queued.status_code == 200, queued.text
        # Released from review, as an admin would: the job is due to run.
        await db_session.execute(
            update(PrintQueueItem).where(PrintQueueItem.id == queued.json()["id"]).values(manual_start=False)
        )
        await db_session.commit()
        friend_id = (await async_client.get("/api/v1/auth/me", headers=_auth(friend))).json()["id"]

        deleted = await async_client.delete(f"/api/v1/users/{friend_id}", headers=_auth(admin))
        assert deleted.status_code in (200, 204), deleted.text

        item = (await async_client.get(f"/api/v1/queue/{queued.json()['id']}", headers=_auth(admin))).json()
        assert (item["created_by_id"], item["manual_start"]) == (None, True)

    async def test_a_deactivated_user_without_filament_rules_stages_nothing(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        # A deactivated user's jobs are held for that, not by a filament rule:
        # with no rule on any of their groups, deleting them stages nothing.
        printer, archive = await _printer_and_archive(printer_factory, archive_factory, "kept", PLA)
        admin = await _admin_token(async_client)
        perms = await async_client.post(
            "/api/v1/groups/", headers=_auth(admin), json={"name": "perms", "permissions": QUEUE_PERMISSIONS}
        )
        created = await async_client.post(
            "/api/v1/users/",
            headers=_auth(admin),
            json={"username": "leaver", "password": "UserPass1!", "group_ids": [perms.json()["id"]]},
        )
        assert created.status_code in (200, 201), created.text
        leaver_id = created.json()["id"]
        login = await async_client.post("/api/v1/auth/login", json={"username": "leaver", "password": "UserPass1!"})
        queued = await _queue(async_client, login.json()["access_token"], archive_id=archive.id, printer_id=printer.id)
        assert queued.status_code == 200, queued.text
        await db_session.execute(
            update(PrintQueueItem).where(PrintQueueItem.id == queued.json()["id"]).values(manual_start=False)
        )
        await db_session.commit()
        deactivated = await async_client.patch(
            f"/api/v1/users/{leaver_id}", headers=_auth(admin), json={"is_active": False}
        )
        assert deactivated.status_code == 200, deactivated.text

        deleted = await async_client.delete(f"/api/v1/users/{leaver_id}", headers=_auth(admin))
        assert deleted.status_code in (200, 204), deleted.text

        item = (await async_client.get(f"/api/v1/queue/{queued.json()['id']}", headers=_auth(admin))).json()
        assert (item["created_by_id"], item["manual_start"]) == (None, False)

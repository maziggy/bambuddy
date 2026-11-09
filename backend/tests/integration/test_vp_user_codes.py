"""Personal virtual-printer access codes: issuing them and the check at sign-in (#1430)."""

import pytest
from sqlalchemy import insert, select

from backend.app.core.auth import get_password_hash
from backend.app.models.group import Group, group_printers
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.models.virtual_printer import VirtualPrinter
from backend.app.services.virtual_printer.user_codes import camera_codes, code_for, new_code, user_for_code

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _auth(db, on: bool = True) -> None:
    db.add(Settings(key="auth_enabled", value="true" if on else "false"))
    await db.commit()


async def _user(db, permissions, *, active=True, code="11112222", name="student", restrict_to=None) -> User:
    group = Group(name=f"g-{name}", description="t", permissions=permissions, is_system=False)
    if restrict_to is not None:
        group.restrict_printers = True
    db.add(group)
    await db.flush()
    if restrict_to is not None:
        for printer_id in restrict_to:
            await db.execute(insert(group_printers).values(group_id=group.id, printer_id=printer_id))
    user = User(
        username=name,
        password_hash=get_password_hash("Pass1word!"),
        is_active=active,
        groups=[group],
        slicer_access_code=code,
    )
    db.add(user)
    await db.commit()
    return user


UPLOAD = ["library:upload"]
QUEUE = ["library:upload", "queue:create"]


@pytest.mark.parametrize(
    ("mode", "permissions", "allowed"),
    [
        ("library", UPLOAD, True),
        ("review", UPLOAD, True),
        ("queue", UPLOAD, False),
        ("queue", QUEUE, True),
        ("library", ["queue:create"], False),
        ("archive", UPLOAD, True),  # the old name of library mode
        ("proxy", QUEUE, False),  # proxy sign-ins go to the real printer
    ],
)
async def test_the_mode_decides_what_the_user_must_be_allowed(db_session, mode, permissions, allowed):
    await _auth(db_session)
    user = await _user(db_session, permissions)

    found = await user_for_code(db_session, "11112222", mode=mode, target_printer_id=None)

    assert (found is not None and found.id == user.id) is allowed


async def test_refused_while_auth_is_off(db_session):
    await _auth(db_session, on=False)
    await _user(db_session, QUEUE)

    assert await user_for_code(db_session, "11112222", mode="queue", target_printer_id=None) is None


async def test_refused_for_a_disabled_account_and_an_unknown_code(db_session):
    await _auth(db_session)
    await _user(db_session, QUEUE, active=False)

    assert await user_for_code(db_session, "11112222", mode="queue", target_printer_id=None) is None
    assert await user_for_code(db_session, "99998888", mode="queue", target_printer_id=None) is None
    assert await user_for_code(db_session, "short", mode="queue", target_printer_id=None) is None


async def test_the_target_printer_must_be_in_the_users_scope(db_session, printer_factory):
    await _auth(db_session)
    mine = await printer_factory(name="Mine")
    other = await printer_factory(name="Other")
    await _user(db_session, [*QUEUE, "printers:read"], restrict_to=[mine.id])

    assert await user_for_code(db_session, "11112222", mode="queue", target_printer_id=mine.id) is not None
    assert await user_for_code(db_session, "11112222", mode="queue", target_printer_id=other.id) is None
    # No target: any printer of the model, which the queue checks per printer.
    assert await user_for_code(db_session, "11112222", mode="queue", target_printer_id=None) is not None


async def test_a_code_is_made_once_and_renewed_on_request(db_session):
    user = await _user(db_session, QUEUE, code=None)

    first = await code_for(db_session, user)
    again = await code_for(db_session, user)
    renewed = await code_for(db_session, user, renew=True)

    assert first == again
    assert renewed != first
    assert len(renewed) == 8 and renewed.isdigit()
    assert (await db_session.execute(select(User.slicer_access_code).where(User.id == user.id))).scalar() == renewed


async def test_a_new_code_is_nobody_elses(db_session, monkeypatch):
    # The first candidates are taken by a user and by a virtual printer.
    await _user(db_session, QUEUE, code="00000001")
    db_session.add(VirtualPrinter(name="VP", mode="library", access_code="00000002"))
    await db_session.commit()
    candidates = iter("00000001" + "00000002" + "00000003")
    monkeypatch.setattr(
        "backend.app.services.virtual_printer.user_codes.secrets.choice", lambda _digits: next(candidates)
    )

    assert await new_code(db_session) == "00000003"


async def _login(async_client, name="student") -> dict:
    response = await async_client.post("/api/v1/auth/login", json={"username": name, "password": "Pass1word!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_a_user_reads_and_renews_their_own_code(async_client, db_session):
    db_session.add(Settings(key="advanced_auth_enabled", value="false"))
    await _auth(db_session)
    await _user(db_session, ["library:read_own"], code=None)
    headers = await _login(async_client)

    first = await async_client.get("/api/v1/virtual-printers/my-access-code", headers=headers)
    again = await async_client.get("/api/v1/virtual-printers/my-access-code", headers=headers)
    renewed = await async_client.post("/api/v1/virtual-printers/my-access-code", headers=headers)

    assert first.status_code == 200, first.text
    assert first.json() == again.json()
    assert renewed.json()["code"] != first.json()["code"]


async def test_no_code_without_signing_in(async_client, db_session):
    await _auth(db_session)

    response = await async_client.get("/api/v1/virtual-printers/my-access-code")

    assert response.status_code == 401


async def test_an_approved_upload_and_a_reused_file_stay_the_senders(db_session, tmp_path, monkeypatch):
    # The File Manager file is the sender's, whoever approves it, and a later
    # upload by someone else never takes it over.
    from backend.app.core.config import settings
    from backend.app.services.virtual_printer.library_store import store_upload

    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    student = await _user(db_session, UPLOAD, name="student")
    other = await _user(db_session, UPLOAD, name="other", code="33334444")
    source = tmp_path / "plate.3mf"
    source.write_bytes(b"plate")

    mine, _ = await store_upload(
        db_session, source_path=source, filename="plate.3mf", folder_id=None, owner_id=student.id
    )
    theirs, reused = await store_upload(
        db_session, source_path=source, filename="plate.3mf", folder_id=None, owner_id=other.id
    )
    again, reused_again = await store_upload(
        db_session, source_path=source, filename="plate.3mf", folder_id=None, owner_id=student.id
    )

    assert mine.created_by_id == student.id
    assert reused is False and theirs.created_by_id == other.id
    assert reused_again is True and again.id == mine.id


async def test_a_vp_with_a_target_printer_needs_printers_read_too(db_session, printer_factory):
    # The slicer shows that printer's live state through the VP.
    await _auth(db_session)
    printer = await printer_factory(name="Mine")
    await _user(db_session, UPLOAD)

    assert await user_for_code(db_session, "11112222", mode="library", target_printer_id=None) is not None
    assert await user_for_code(db_session, "11112222", mode="library", target_printer_id=printer.id) is None


@pytest.mark.parametrize(
    ("permissions", "active", "in_scope", "allowed"),
    [
        (["printers:control"], True, True, True),
        (["printers:read"], True, True, False),
        (["printers:control"], False, True, False),
        (["printers:control"], True, False, False),
    ],
    ids=["control", "read-only", "disabled", "other-printer"],
)
async def test_commands_to_the_printer_need_printers_control_on_it(
    db_session, printer_factory, permissions, active, in_scope, allowed
):
    from backend.app.services.virtual_printer.user_codes import may_control_printer

    await _auth(db_session)
    mine = await printer_factory(name="Mine")
    other = await printer_factory(name="Other")
    user = await _user(db_session, permissions, active=active, restrict_to=[mine.id])

    assert await may_control_printer(db_session, user.id, mine.id if in_scope else other.id) is allowed


async def test_camera_codes_are_those_of_users_who_may_watch_the_printer(db_session, printer_factory):
    """The slicer's camera through a VP: camera:view, an active account and the printer in scope."""
    await _auth(db_session)
    mine = await printer_factory(name="Mine")
    other = await printer_factory(name="Other")
    viewer = await _user(db_session, ["camera:view"], code="11110001", name="viewer")
    scoped = await _user(db_session, ["camera:view"], code="11110002", name="scoped", restrict_to=[mine.id])
    await _user(db_session, ["library:upload"], code="11110003", name="no-camera")
    await _user(db_session, ["camera:view"], code="11110004", name="disabled", active=False)
    db_session.add(
        User(
            username="no-code",
            password_hash=get_password_hash("Pass1word!"),
            groups=[(await db_session.execute(select(Group).where(Group.name == "g-viewer"))).scalar_one()],
        )
    )
    await db_session.commit()

    assert sorted(await camera_codes(db_session, mine.id)) == [("11110001", viewer.id), ("11110002", scoped.id)]
    assert await camera_codes(db_session, other.id) == [("11110001", viewer.id)]


async def test_no_camera_codes_while_auth_is_off(db_session, printer_factory):
    await _auth(db_session, on=False)
    printer = await printer_factory(name="Mine")
    await _user(db_session, ["camera:view"])

    assert await camera_codes(db_session, printer.id) == []

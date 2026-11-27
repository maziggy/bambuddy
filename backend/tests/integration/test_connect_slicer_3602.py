"""The Connect slicer page's data: what a user enters in their slicer (#3602).

A user without Settings access gets their personal access code, the virtual
printers they may send to and the CA certificate. Which virtual printers is the
sign-in's own rule, so the page never lists one the sign-in would refuse.
"""

import pytest

from backend.app.models.settings import Settings
from backend.app.models.virtual_printer import VirtualPrinter
from backend.app.services.virtual_printer.user_codes import user_for_code
from backend.tests.integration.test_vp_user_codes import QUEUE, UPLOAD, _auth, _login, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

URL = "/api/v1/virtual-printers/connect-info"


async def _signed_in(async_client, db, permissions, **kwargs) -> dict:
    db.add(Settings(key="advanced_auth_enabled", value="false"))
    await _auth(db)
    await _user(db, permissions, **kwargs)
    return await _login(async_client)


def _vp(name, mode, *, enabled=True, target=None, suffix="391800001", **kwargs) -> VirtualPrinter:
    return VirtualPrinter(
        name=name,
        mode=mode,
        enabled=enabled,
        target_printer_id=target,
        serial_suffix=suffix,
        access_code="87654321",
        **kwargs,
    )


async def test_lists_what_the_user_may_send_to(async_client, db_session):
    headers = await _signed_in(async_client, db_session, UPLOAD)
    db_session.add_all(
        [
            _vp("Files", "library", suffix="391800001", bind_ip="10.0.0.5"),
            _vp("Review", "review", suffix="391800002", bind_ip="10.0.0.6", remote_interface_ip="100.64.0.9"),
            _vp("Queue", "queue", suffix="391800003"),  # needs queue:create too
            _vp("Proxy", "proxy", suffix="391800004"),  # personal codes never work there
            _vp("Off", "library", enabled=False, suffix="391800005"),
        ]
    )
    await db_session.commit()

    response = await async_client.get(URL, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auth_enabled"] is True
    printers = {p["name"]: p for p in body["printers"]}
    assert set(printers) == {"Files", "Review"}
    assert printers["Files"]["address"] == "10.0.0.5"
    # The address the virtual printer advertises, as its certificate names it.
    assert printers["Review"]["address"] == "100.64.0.9"
    assert printers["Files"]["mode"] == "library"
    assert printers["Files"]["serial"].endswith("391800001")
    assert printers["Files"]["target_printer_name"] is None
    assert "87654321" not in response.text  # never a virtual printer's own code


async def test_a_target_printer_must_be_readable_and_in_scope(async_client, db_session, printer_factory):
    mine = await printer_factory(name="Mine")
    other = await printer_factory(name="Other")
    headers = await _signed_in(async_client, db_session, [*QUEUE, "printers:read"], restrict_to=[mine.id])
    db_session.add_all(
        [
            _vp("To mine", "queue", target=mine.id, suffix="391800001"),
            _vp("To other", "queue", target=other.id, suffix="391800002"),
            _vp("Any X1C", "queue", suffix="391800003"),
        ]
    )
    await db_session.commit()

    printers = {p["name"]: p for p in (await async_client.get(URL, headers=headers)).json()["printers"]}

    assert set(printers) == {"To mine", "Any X1C"}
    assert printers["To mine"]["target_printer_name"] == "Mine"
    assert printers["Any X1C"]["target_printer_name"] is None


@pytest.mark.parametrize(
    "permissions",
    [UPLOAD, QUEUE, [*QUEUE, "printers:read"], ["library:read_own"]],
)
async def test_the_page_and_the_sign_in_agree(async_client, db_session, printer_factory, permissions):
    printer = await printer_factory(name="P")
    headers = await _signed_in(async_client, db_session, permissions)
    cases = [
        ("library", None),
        ("review", None),
        ("queue", None),
        ("queue", printer.id),
        ("library", printer.id),
        ("proxy", printer.id),
    ]
    for index, (mode, target) in enumerate(cases):
        db_session.add(_vp(f"vp{index}", mode, target=target, suffix=f"39180000{index}"))
    await db_session.commit()

    listed = {p["name"] for p in (await async_client.get(URL, headers=headers)).json()["printers"]}

    for index, (mode, target) in enumerate(cases):
        signs_in = await user_for_code(db_session, "11112222", mode=mode, target_printer_id=target) is not None
        assert (f"vp{index}" in listed) is signs_in, (mode, target)


async def test_with_auth_off_there_are_no_personal_codes(async_client, db_session):
    await _auth(db_session, on=False)
    db_session.add(Settings(key="virtual_printer_connect_instructions", value="Connect to the VPN first."))
    db_session.add(_vp("Files", "library"))
    await db_session.commit()

    body = (await async_client.get(URL)).json()

    assert body == {"auth_enabled": False, "instructions": "Connect to the VPN first.", "printers": []}


async def test_the_admins_instructions_reach_every_user(async_client, db_session):
    headers = await _signed_in(async_client, db_session, ["library:read_own"])
    db_session.add(Settings(key="virtual_printer_connect_instructions", value="**VPN** first"))
    await db_session.commit()

    body = (await async_client.get(URL, headers=headers)).json()

    assert body["instructions"] == "**VPN** first"
    assert body["printers"] == []


async def test_refused_without_signing_in_and_for_an_api_key(async_client, db_session):
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    await _auth(db_session)
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(APIKey(name="k", key_hash=key_hash, key_prefix=key_prefix, can_read_status=True))
    await db_session.commit()

    assert (await async_client.get(URL)).status_code == 401
    assert (await async_client.get(URL, headers={"X-API-Key": full_key})).status_code == 403


class TestTheCaCertificateIsForEverySignedInUser:
    @pytest.fixture(autouse=True)
    def _own_ca_dir(self, monkeypatch, tmp_path):
        from backend.app.services.virtual_printer import virtual_printer_manager

        monkeypatch.setattr(virtual_printer_manager, "_base_dir", tmp_path)

    @pytest.mark.parametrize(
        "path", ["/api/v1/virtual-printers/ca-certificate", "/api/v1/virtual-printers/ca-certificate/installer/unix"]
    )
    async def test_without_settings_access(self, async_client, db_session, path):
        headers = await _signed_in(async_client, db_session, ["library:read_own"])

        assert (await async_client.get(path, headers=headers)).status_code == 200
        assert (await async_client.get(path)).status_code == 401

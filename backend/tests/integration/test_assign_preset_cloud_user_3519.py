"""A custom cloud preset resolves the same way from an API key and on insert (#3519).

With login on, each user signs in to Bambu Cloud separately and the shared
sign-in is empty. An API key (a SpoolBuddy client) has no signed-in user, and
the replay on insert has none either, so both looked the PFUS preset up with
nobody's sign-in and sent Generic PLA (GFL99) where the web UI sent the
preset's own filament. Both now use a user: the key's owner, and whoever made
the assignment.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment

PFUS = "PFUSdfae86e31579cb"


def _status(ams_data):
    status = MagicMock()
    status.raw_data = {"ams": {"ams": ams_data}}
    status.nozzles = [MagicMock(nozzle_diameter="0.4")]
    status.ams_extruder_map = None
    return status


def _fake_cloud(seen: list):
    """build_authenticated_cloud stand-in: records whose sign-in was asked for.

    Only a user's own sign-in finds the preset, as on a per-user install
    where the shared one is empty.
    """
    cloud = MagicMock()
    cloud.is_authenticated = True
    cloud.get_setting_detail = AsyncMock(return_value={"filament_id": "SUN20011", "name": "SUNLU PLA META"})
    cloud.close = AsyncMock()

    async def build(_db, user):
        seen.append(user.id if user is not None else None)
        return cloud if user is not None else None

    return build


@pytest.fixture
async def auth_on(db_session: AsyncSession):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="auth_enabled", value="true"))
    await db_session.commit()


@pytest.fixture
async def owner(db_session: AsyncSession):
    from backend.app.models.user import User

    user = User(username="spoolowner", password_hash="x", role="admin", is_active=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def spool(db_session: AsyncSession):
    row = Spool(
        material="PLA",
        subtype="Basic",
        brand="SUNLU",
        rgba="FF0000FF",
        label_weight=1000,
        weight_used=0,
        slicer_filament=PFUS,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def _owned_key(db_session: AsyncSession, user_id: int | None) -> str:
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(
            name="espoolbuddy",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=user_id,
            can_manage_inventory=True,
            can_read_status=True,
            enabled=True,
        )
    )
    await db_session.commit()
    return full_key


async def _assign_with_key(async_client: AsyncClient, key: str, printer_id: int, spool_id: int, seen: list):
    mock_client = MagicMock()
    mock_client.ams_set_filament_setting.return_value = True
    mock_client.extrusion_cali_sel.return_value = True
    status = _status([{"id": 0, "tray": [{"id": 0, "tray_info_idx": "", "tray_type": "PLA", "state": 11}]}])
    with (
        patch("backend.app.services.printer_manager.printer_manager") as mock_pm,
        patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=_fake_cloud(seen)),
    ):
        mock_pm.get_client.return_value = mock_client
        mock_pm.get_status.return_value = status
        response = await async_client.post(
            "/api/v1/inventory/assignments",
            json={"spool_id": spool_id, "printer_id": printer_id, "ams_id": 0, "tray_id": 0},
            headers={"X-API-Key": key},
        )
    return response, mock_client


async def _replay(printer_id: int, seen: list):
    from backend.app.main import on_ams_change

    ams_data = [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}]}]
    mock_client = MagicMock()
    mock_client.ams_set_filament_setting.return_value = True
    mock_client.extrusion_cali_sel.return_value = True
    status = _status(ams_data)
    with (
        patch("backend.app.main.printer_manager") as mock_pm_main,
        patch("backend.app.services.printer_manager.printer_manager") as mock_pm_inv,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main.ws_manager") as mock_ws,
        patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=_fake_cloud(seen)),
    ):
        mock_pm_main.get_printer.return_value = MagicMock(name="H2C", serial_number="0948BB540200427")
        mock_pm_main.get_status.return_value = status
        mock_pm_main.get_client.return_value = mock_client
        mock_pm_main.get_model.return_value = "H2C"
        mock_pm_inv.get_client.return_value = mock_client
        mock_pm_inv.get_status.return_value = status
        mock_relay.on_ams_change = AsyncMock()
        mock_ws.send_printer_status = AsyncMock()
        mock_ws.broadcast = AsyncMock()
        await on_ams_change(printer_id, ams_data)
    return mock_client


class TestApiKeyAssign:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_keys_owner_resolves_the_preset(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory, auth_on, owner, spool
    ):
        printer = await printer_factory(name="H2C")
        key = await _owned_key(db_session, owner.id)
        seen: list = []

        response, mock_client = await _assign_with_key(async_client, key, printer.id, spool.id, seen)

        assert response.status_code == 200
        assert seen == [owner.id]
        kwargs = mock_client.ams_set_filament_setting.call_args.kwargs
        assert kwargs["tray_info_idx"] == "SUN20011"
        assert kwargs["setting_id"] == PFUS

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_assignment_remembers_the_owner(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory, auth_on, owner, spool
    ):
        printer = await printer_factory(name="H2C")
        key = await _owned_key(db_session, owner.id)

        response, _ = await _assign_with_key(async_client, key, printer.id, spool.id, [])

        assert response.status_code == 200
        row = await db_session.get(SpoolAssignment, response.json()["id"])
        await db_session.refresh(row)
        assert row.assigned_by_user_id == owner.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_ownerless_key_still_assigns(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory, auth_on, spool
    ):
        """A key from before keys had owners has no sign-in to use: generic, as before."""
        printer = await printer_factory(name="H2C")
        key = await _owned_key(db_session, None)
        seen: list = []

        response, mock_client = await _assign_with_key(async_client, key, printer.id, spool.id, seen)

        assert response.status_code == 200
        assert seen == [None]
        assert mock_client.ams_set_filament_setting.call_args.kwargs["tray_info_idx"] == "GFL99"


class TestReplayOnInsert:
    async def _pre_assigned(self, db_session: AsyncSession, printer_id: int, spool_id: int, user_id: int | None):
        db_session.add(
            SpoolAssignment(
                spool_id=spool_id,
                printer_id=printer_id,
                ams_id=0,
                tray_id=0,
                fingerprint_color=None,
                fingerprint_type=None,
                assigned_by_user_id=user_id,
            )
        )
        await db_session.commit()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_assigning_user_resolves_the_preset(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory, auth_on, owner, spool
    ):
        printer = await printer_factory(name="H2C")
        await self._pre_assigned(db_session, printer.id, spool.id, owner.id)
        seen: list = []

        mock_client = await _replay(printer.id, seen)

        assert seen == [owner.id]
        assert mock_client.ams_set_filament_setting.call_args.kwargs["tray_info_idx"] == "SUN20011"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_with_login_off_the_shared_sign_in_applies(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory, owner, spool
    ):
        """A user id kept from when login was on doesn't pick a per-user sign-in."""
        printer = await printer_factory(name="H2C")
        await self._pre_assigned(db_session, printer.id, spool.id, owner.id)
        seen: list = []

        await _replay(printer.id, seen)

        assert seen == [None]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_deactivated_user_is_not_used(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory, auth_on, owner, spool
    ):
        owner.is_active = False
        await db_session.commit()
        printer = await printer_factory(name="H2C")
        await self._pre_assigned(db_session, printer.id, spool.id, owner.id)
        seen: list = []

        await _replay(printer.id, seen)

        assert seen == [None]


class TestUnresolvedPresetIsLogged:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_custom_preset_without_a_sign_in_warns(self, db_session: AsyncSession, caplog):
        from backend.app.services.slicer_filament_resolver import resolve_slicer_filament

        with (
            patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=_fake_cloud([])),
            caplog.at_level(logging.WARNING, logger="backend.app.services.slicer_filament_resolver"),
        ):
            await resolve_slicer_filament(
                db=db_session, current_user=None, slicer_filament=PFUS, slicer_filament_name=None, material="PLA"
            )

        assert any("not looked up" in r.getMessage() and PFUS in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_official_preset_does_not(self, db_session: AsyncSession, caplog):
        """A GFS id parses without the cloud, so nothing is lost."""
        from backend.app.services.slicer_filament_resolver import resolve_slicer_filament

        with (
            patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=_fake_cloud([])),
            caplog.at_level(logging.WARNING, logger="backend.app.services.slicer_filament_resolver"),
        ):
            await resolve_slicer_filament(
                db=db_session, current_user=None, slicer_filament="GFSL05", slicer_filament_name=None, material="PLA"
            )

        assert not any("not looked up" in r.getMessage() for r in caplog.records)


class TestDeletedUser:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_the_user_clears_the_assignment_s_user(
        self, db_session: AsyncSession, printer_factory, owner, spool
    ):
        """Or the next user given the freed id would lend the slot their sign-in."""
        from sqlalchemy import select

        printer = await printer_factory(name="H2C")
        db_session.add(
            SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0, assigned_by_user_id=owner.id)
        )
        await db_session.commit()

        await db_session.delete(owner)
        await db_session.commit()

        db_session.expire_all()
        row = (await db_session.execute(select(SpoolAssignment))).scalar_one()
        assert row.assigned_by_user_id is None

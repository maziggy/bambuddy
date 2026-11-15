"""A support filament has to reach its AMS slot as a support type (#3559).

The reporter set a slot to "Bambu Support For PLA" and every PLA part still
matched it. BambuStudio writes a support preset into a slot as "PLA-S" (or
"PA-S"), and a plate sliced with one declares the same; Bambuddy wrote "PLA".
The Configure dialog reads the type off the preset name, and the reduction
every slot-writing route runs (#2902) then cut "PLA-S" itself back to "PLA" --
so the slot offered support filament to every plain PLA part, and to nothing
that asked for support.

All four routes that configure a slot are covered, plus the auto-unlink check
that has to recognise the slot it just wrote.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.utils.filament_types import printer_filament_type, support_filament_type
from backend.tests.integration.test_ams_slot_material_2902 import (
    _mqtt_mock,
    _spoolman_client,
    _spoolman_spool,
    _status,
)


class TestTheTypeRule:
    """BambuStudio's ``get_filament_type`` (libslic3r/PrintConfig.cpp)."""

    @pytest.mark.parametrize("support_type", ["PLA-S", "PA-S", "ABS-S"])
    def test_a_support_type_is_not_reduced_to_its_base(self, support_type):
        assert printer_filament_type(support_type) == support_type
        assert printer_filament_type(support_type.lower()) == support_type

    @pytest.mark.parametrize(
        ("filament_id", "expected"),
        [
            ("GFS00", "PLA-S"),  # Support W
            ("GFS01", "PA-S"),  # Support G
            ("GFS02", "PLA-S"),  # Support For PLA
            ("GFS03", "PA-S"),  # Support For PA/PET
            ("GFS05", "PLA-S"),  # Support For PLA/PETG
            ("GFS06", "ABS"),  # Support for ABS: the one Bambu leaves alone
        ],
    )
    def test_a_bambu_support_id_decides_the_type(self, filament_id, expected):
        # Whatever the name made of it: "Support For PLA/PETG" reads as PETG,
        # and Support W / G name no material at all.
        for guessed in ("PLA", "PETG", "PA"):
            assert support_filament_type(guessed, filament_id, "") == expected

    @pytest.mark.parametrize(
        ("tray_type", "name", "expected"),
        [
            ("PLA", "Bambu Support For PLA", "PLA-S"),
            ("PLA", "Sunlu Support for PLA @BBL X1C", "PLA-S"),
            ("PA", "Acme Support for PA", "PA-S"),
            # A third-party "X Support for Y" preset is a Y preset; the slicer
            # keeps its declared type, and so does this (330d77198).
            ("PETG", "PLA Support for PETG PETG Basic", "PETG"),
            ("ABS", "Acme Support for ABS", "ABS"),
            # A word, not a fragment of one.
            ("PLA", "PolySupport PLA", "PLA"),
            ("PLA", "Bambu PLA Basic", "PLA"),
            ("PLA", "", "PLA"),
        ],
    )
    def test_without_a_bambu_id_the_preset_name_says_whether_it_is_support(self, tray_type, name, expected):
        assert support_filament_type(tray_type, "P4d64437", name) == expected

    def test_an_unrelated_id_is_left_alone(self):
        assert support_filament_type("PLA", "GFA00", "Bambu PLA Basic") == "PLA"
        assert support_filament_type("PLA-S", "", "") == "PLA-S"


class TestConfigureSlotModal:
    async def _configure(self, async_client, printer_factory, **params):
        printer = await printer_factory(model="X1C")
        client = _mqtt_mock()
        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status()
            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_color": "E1E9E9FF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    **params,
                },
            )
        assert response.status_code == 200
        return client.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_reported_case_a_bambu_support_preset(self, async_client: AsyncClient, printer_factory):
        """What the dialog sends for "Bambu Support For PLA": the type it read off
        the name, and the preset's filament id."""
        sent = await self._configure(
            async_client,
            printer_factory,
            tray_info_idx="GFS02",
            tray_type="PLA",
            tray_sub_brands="Bambu Support For PLA",
        )

        assert sent["tray_type"] == "PLA-S"
        assert sent["tray_info_idx"] == "GFS02"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_support_for_pla_petg_is_pla_s_not_petg(self, async_client: AsyncClient, printer_factory):
        sent = await self._configure(
            async_client,
            printer_factory,
            tray_info_idx="GFS05",
            tray_type="PETG",
            tray_sub_brands="Bambu Support For PLA/PETG",
        )

        assert sent["tray_type"] == "PLA-S"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_user_preset_of_its_own_by_its_name(self, async_client: AsyncClient, printer_factory):
        """A non-Bambu support filament on a user preset: its own id, so only the
        name can say it is support -- the reporter's case."""
        sent = await self._configure(
            async_client,
            printer_factory,
            tray_info_idx="P4d64437",
            tray_type="PLA",
            tray_sub_brands="Jade White Support For PLA",
        )

        assert sent["tray_type"] == "PLA-S"
        assert sent["tray_info_idx"] == "P4d64437"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_plain_pla_preset_is_untouched(self, async_client: AsyncClient, printer_factory):
        sent = await self._configure(
            async_client,
            printer_factory,
            tray_info_idx="GFA00",
            tray_type="PLA",
            tray_sub_brands="Bambu PLA Basic",
        )

        assert sent["tray_type"] == "PLA"


class TestInternalInventoryAssign:
    async def _assign(self, async_client, db_session, **spool_kwargs):
        from backend.app.models.printer import Printer

        printer = Printer(
            name="X1C",
            serial_number="SUP3559INV",
            ip_address="192.168.1.80",
            access_code="12345678",
        )
        db_session.add(printer)
        spool = Spool(rgba="FFFFFFFF", label_weight=500, weight_used=0, **spool_kwargs)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(printer)
        await db_session.refresh(spool)

        client = _mqtt_mock()
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status()
            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
            )
        assert response.status_code == 200
        return client.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_spool_made_from_a_support_rfid_tray(self, async_client: AsyncClient, db_session: AsyncSession):
        """A Bambu support spool read by RFID is stored with the tray's
        sub-brand as its material. Moved to another slot by Bambuddy, it used to
        go in as plain PLA."""
        sent = await self._assign(
            async_client, db_session, material="Support For PLA", brand="Bambu Lab", slicer_filament="GFS02"
        )

        assert sent["tray_type"] == "PLA-S"
        assert sent["tray_info_idx"] == "GFS02"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_spool_whose_material_already_says_pla_s(self, async_client: AsyncClient, db_session: AsyncSession):
        sent = await self._assign(async_client, db_session, material="PLA-S", brand="Sunlu")

        assert sent["tray_type"] == "PLA-S"
        # The slot still gets a filament id the printer can resolve.
        assert sent["tray_info_idx"] == "GFL99"


class TestSpoolmanRoutes:
    @pytest.fixture
    async def settings(self, db_session):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
        await db_session.commit()

    @pytest.fixture
    async def printer(self, db_session):
        from backend.app.models.printer import Printer

        p = Printer(
            name="Spoolman X1C",
            serial_number="SUP3559SM",
            ip_address="192.168.1.81",
            access_code="12345678",
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        return p

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_slot_assignment_route(self, async_client: AsyncClient, settings, printer):
        mqtt = _mqtt_mock()
        spool = _spoolman_spool("Support For PLA")
        with (
            patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm,
            patch(
                "backend.app.api.routes.spoolman_inventory.get_spoolman_client",
                AsyncMock(return_value=_spoolman_client(spool)),
            ),
        ):
            pm.get_client.return_value = mqtt
            pm.get_status.return_value = _status()
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={"spoolman_spool_id": 11, "printer_id": printer.id, "ams_id": 0, "tray_id": 2},
            )
        assert response.status_code == 200
        assert mqtt.ams_set_filament_setting.call_args.kwargs["tray_type"] == "PLA-S"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_tag_link_route(self, async_client: AsyncClient, settings, printer):
        client = _spoolman_client(_spoolman_spool("Support For PLA", spool_id=12))
        mqtt = _mqtt_mock()
        with (
            patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
            patch("backend.app.api.routes.spoolman.init_spoolman_client", AsyncMock(return_value=client)),
            patch("backend.app.api.routes.spoolman.printer_manager") as pm,
        ):
            pm.get_client.return_value = mqtt
            pm.get_status.return_value = _status()
            response = await async_client.post(
                "/api/v1/spoolman/spools/12/link",
                json={
                    "tray_uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
                    "printer_id": printer.id,
                    "ams_id": 0,
                    "tray_id": 3,
                },
            )
        assert response.status_code == 200
        assert mqtt.ams_set_filament_setting.call_args.kwargs["tray_type"] == "PLA-S"


class TestTheAssignmentSurvivesTheSlotItJustConfigured:
    """on_ams_change unlinks an assignment whose slot no longer looks like its
    spool. The slot now carries "PLA-S", which no spool column says."""

    async def _push(self, db_session, printer_factory, reported_type, **spool_kwargs):
        from backend.app.main import on_ams_change
        from backend.app.models.spool_assignment import SpoolAssignment

        printer = await printer_factory(name="X1C")
        spool = Spool(rgba="FFFFFFFF", label_weight=500, weight_used=0, **spool_kwargs)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)

        assignment = SpoolAssignment(
            spool_id=spool.id,
            printer_id=printer.id,
            ams_id=0,
            tray_id=2,
            fingerprint_color="FFFFFFFF",
            fingerprint_type="PETG",
        )
        db_session.add(assignment)
        await db_session.commit()
        assignment_id = assignment.id

        ams_data = [{"id": 0, "tray": [{"id": 2, "tray_type": reported_type, "tray_color": "FFFFFFFF", "state": 11}]}]
        status = _status(ams_data)
        status.state = "IDLE"

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main.mqtt_relay") as relay,
            patch("backend.app.main.ws_manager") as ws,
        ):
            pm.get_printer.return_value = MagicMock(name="X1C", serial_number="00M00A000000001")
            pm.get_status.return_value = status
            pm.get_model.return_value = "X1C"
            relay.on_ams_change = AsyncMock()
            ws.send_printer_status = AsyncMock()
            ws.broadcast = AsyncMock()

            await on_ams_change(printer.id, ams_data)

        db_session.expunge_all()
        return await db_session.get(SpoolAssignment, assignment_id)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_support_spool_stays_on_the_pla_s_slot_it_configured(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        surviving = await self._push(
            db_session, printer_factory, "PLA-S", material="PLA", slicer_filament_name="Bambu Support For PLA"
        )

        assert surviving is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_so_does_one_made_from_a_support_rfid_tray(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        surviving = await self._push(db_session, printer_factory, "PLA-S", material="Support For PLA")

        assert surviving is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_and_a_slot_an_older_version_configured_as_pla(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        """Slots configured before this fix still report "PLA" until someone
        reconfigures them; their spools must not be unlinked meanwhile."""
        surviving = await self._push(
            db_session, printer_factory, "PLA", material="PLA", slicer_filament_name="Bambu Support For PLA"
        )

        assert surviving is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_but_a_plain_pla_spool_on_a_support_slot_is_a_swap(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        surviving = await self._push(
            db_session, printer_factory, "PLA-S", material="PLA", slicer_filament_name="Bambu PLA Basic"
        )

        assert surviving is None

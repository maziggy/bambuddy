"""What reaches an AMS slot when a spool's material is a product line (#2902).

The reporter assigned an eSUN PLA+ spool and the slot came out unusable: any
plate sliced with a PLA profile refused it. Four routes configure a slot and
all four wrote the spool's material straight into ``tray_type``, where "PLA+"
matches nothing -- not the slicer, and not Bambuddy's own dispatch matcher,
which compares the printer's reported ``tray_type`` to the 3MF's declared type
as plain equality.

Each test below asserts the whole slot, not just the type: an unrecognised
material also missed the generic-filament-id lookup, so the slot went out with
an empty ``tray_info_idx`` -- the half-configured state #2604 documents the
printer as reverting from -- and took the 200/240 catch-all temperatures
instead of PLA's.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


def _mqtt_mock():
    client = MagicMock()
    client.ams_set_filament_setting.return_value = True
    client.extrusion_cali_sel.return_value = True
    return client


def _status(ams_data=None):
    status = MagicMock()
    status.raw_data = {"ams": {"ams": ams_data if ams_data is not None else []}}
    status.nozzles = [MagicMock(nozzle_diameter="0.4")]
    status.ams_extruder_map = None
    status.kprofiles = []
    return status


def _spoolman_spool(material, spool_id=11, slicer_filament=None):
    extra = {}
    if slicer_filament is not None:
        extra["bambu_slicer_filament"] = json.dumps(str(slicer_filament))
    return {
        "id": spool_id,
        "filament": {
            "id": 1,
            "name": "Cool White",
            "material": material,
            "color_hex": "E1E9E9",
            "weight": 1000,
            "vendor": {"id": 1, "name": "eSUN"},
        },
        "remaining_weight": 800.0,
        "used_weight": 200.0,
        "archived": False,
        "extra": extra,
    }


def _spoolman_client(spool):
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=spool)
    client.get_spools = AsyncMock(return_value=[spool])
    client.merge_spool_extra = AsyncMock(return_value=spool)
    return client


class TestInternalInventoryAssign:
    async def _assign(self, async_client, db_session, material, **spool_kwargs):
        from backend.app.models.printer import Printer

        printer = Printer(
            name="P1S",
            serial_number=f"MAT2902{material[:4]}",
            ip_address="192.168.1.77",
            access_code="12345678",
        )
        db_session.add(printer)
        spool = Spool(
            material=material,
            brand="eSUN",
            color_name="Cool White",
            rgba="E1E9E9FF",
            label_weight=1000,
            weight_used=0,
            **spool_kwargs,
        )
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
        client.ams_set_filament_setting.assert_called_once()
        return client.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_pla_plus_spool_configures_the_slot_as_pla(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        sent = await self._assign(async_client, db_session, "PLA+")

        assert sent["tray_type"] == "PLA"
        # Not just the label: the id and its setting_id are what stop the
        # printer treating the slot as half configured, and the temperatures
        # are PLA's rather than the catch-all.
        assert sent["tray_info_idx"] == "GFL99"
        assert sent["setting_id"] == "GFSL99"
        assert (sent["nozzle_temp_min"], sent["nozzle_temp_max"]) == (190, 230)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_product_name_is_not_lost_it_moves_to_the_sub_brand(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Which is where Bambu itself puts it -- their own catalogue has a
        preset named "eSUN PLA+" (GFL03) whose type is PLA."""
        sent = await self._assign(async_client, db_session, "PLA+")

        assert "PLA+" in sent["tray_sub_brands"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_material_that_already_resolved_keeps_its_own_preset(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """ "PETG HF" has a generic preset of its own (GFG96, "Generic PETG HF").
        Reducing the material before the id lookup rather than after it would
        trade that away for plain PETG's GFG99 -- a quiet downgrade of slots
        that work today."""
        sent = await self._assign(async_client, db_session, "PETG HF")

        assert sent["tray_info_idx"] == "GFG96"
        assert sent["tray_type"] == "PETG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_it_can_now_reuse_the_calibrated_preset_already_in_the_slot(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """A slot already holding a specific preset keeps it when the incoming
        spool is the same material -- that is how a printer's calibration
        context survives an assignment. The comparison is against the slot's
        reported type, so a PLA+ spool could never match a PLA slot and the
        reuse branch was dead for every spool this issue is about."""
        from backend.app.models.printer import Printer

        printer = Printer(
            name="Reuse P1S",
            serial_number="MAT2902RU",
            ip_address="192.168.1.81",
            access_code="12345678",
        )
        db_session.add(printer)
        spool = Spool(material="PLA+", brand="eSUN", rgba="E1E9E9FF", label_weight=1000, weight_used=0)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(printer)
        await db_session.refresh(spool)

        client = _mqtt_mock()
        live_slot = [{"id": 0, "tray": [{"id": 1, "tray_info_idx": "P4d64437", "tray_type": "PLA"}]}]
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status(live_slot)
            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
            )

        assert response.status_code == 200
        sent = client.ams_set_filament_setting.call_args.kwargs
        assert sent["tray_info_idx"] == "P4d64437"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_but_it_does_not_reuse_a_product_name_a_previous_version_left_there(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """A spool whose slicer_filament was free text could send that text to
        the printer as the slot's filament id, and the printer reports it
        straight back -- so an upgraded install can be looking at a slot that
        says type PLA, id "PLA+". Reuse has always refused a bare material name
        in that field; refusing a product line too is what stops the bad id
        being carried forward on every assignment instead of replaced."""
        from backend.app.models.printer import Printer

        printer = Printer(
            name="Stale P1S",
            serial_number="MAT2902ST",
            ip_address="192.168.1.82",
            access_code="12345678",
        )
        db_session.add(printer)
        spool = Spool(material="PLA", brand="eSUN", rgba="E1E9E9FF", label_weight=1000, weight_used=0)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(printer)
        await db_session.refresh(spool)

        client = _mqtt_mock()
        stale_slot = [{"id": 0, "tray": [{"id": 1, "tray_info_idx": "PLA+", "tray_type": "PLA"}]}]
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status(stale_slot)
            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
            )

        assert response.status_code == 200
        sent = client.ams_set_filament_setting.call_args.kwargs
        assert sent["tray_info_idx"] == "GFL99"
        assert sent["tray_type"] == "PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_material_nothing_can_be_made_of_is_sent_unchanged(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """The catalogue ships a few names with no filament type in them at all.
        Guessing at those would be worse than leaving them: this route behaved
        exactly this way before #2902, and still does."""
        sent = await self._assign(async_client, db_session, "CPE HG100")

        assert sent["tray_type"] == "CPE HG100"
        assert sent["tray_info_idx"] == ""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_free_text_slicer_filament_naming_a_product_is_not_a_filament_id(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """slicer_filament is free text on older spools, so "PLA+" can be sitting
        in it. It is as unusable a tray_info_idx as the bare "PLA" the resolver
        already discarded, and letting it through would put a product name in
        the field the printer keys its calibration table by."""
        sent = await self._assign(async_client, db_session, "PLA+", slicer_filament="PLA+")

        assert sent["tray_info_idx"] == "GFL99"


class TestSpoolmanInventoryAssign:
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
            name="Spoolman P1S",
            serial_number="MAT2902SM",
            ip_address="192.168.1.78",
            access_code="12345678",
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        return p

    async def _assign(self, async_client, printer, material, slicer_filament=None):
        mqtt = _mqtt_mock()
        spool = _spoolman_spool(material, slicer_filament=slicer_filament)
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
        return mqtt.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolmans_free_text_material_is_reduced_the_same_way(
        self, async_client: AsyncClient, settings, printer
    ):
        """Spoolman's material field is free text too, so the same product names
        arrive by this route -- and it is the route the reporter used."""
        sent = await self._assign(async_client, printer, "PLA+")

        assert sent["tray_type"] == "PLA"
        assert sent["tray_info_idx"] == "GFL99"
        # Exactly, not just "contains PLA+": the filament's own name is in this
        # string too, so a substring check would pass even if the material had
        # been reduced before it was built.
        assert sent["tray_sub_brands"] == "eSUN PLA+ Cool White"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_it_keeps_this_routes_own_preset_for_a_material_that_had_one(
        self, async_client: AsyncClient, settings, printer
    ):
        sent = await self._assign(async_client, printer, "PETG HF")

        assert sent["tray_info_idx"] == "GFG96"
        assert sent["tray_type"] == "PETG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_resolver_is_handed_the_spools_own_wording_not_the_type(
        self, async_client: AsyncClient, settings, printer, db_session
    ):
        """This route also passes the material down to the slicer-filament
        resolver. Handing that the reduced type instead would look harmless and
        quietly downgrade GFG96 to GFG99 whenever the spool points at a local
        preset with no filament_id of its own."""
        from backend.app.models.local_preset import LocalPreset

        lp = LocalPreset(name="Generic PETG HF", preset_type="filament", source="orcaslicer", setting="{}")
        db_session.add(lp)
        await db_session.commit()
        await db_session.refresh(lp)

        sent = await self._assign(async_client, printer, "PETG HF", slicer_filament=lp.id)

        assert sent["tray_info_idx"] == "GFG96"


class TestConfigureSlotModal:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_product_line_typed_into_the_modal_is_reduced_too(self, async_client: AsyncClient, printer_factory):
        """The Configure Slot modal derives tray_type from a preset name or the
        spool's material, so it can hand the backend a product line as readily
        as the assignment routes can."""
        printer = await printer_factory(model="P1S")

        client = _mqtt_mock()
        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status()
            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/1/configure",
                params={
                    "tray_info_idx": "",
                    "tray_type": "PLA+",
                    "tray_sub_brands": "eSUN PLA+",
                    "tray_color": "E1E9E9FF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                },
            )

        assert response.status_code == 200
        sent = client.ams_set_filament_setting.call_args.kwargs
        assert sent["tray_type"] == "PLA"
        # The empty tray_info_idx the modal sent for a generic material is what
        # the reduced type now rescues.
        assert sent["tray_info_idx"] == "GFL99"
        assert sent["tray_sub_brands"] == "eSUN PLA+"


class TestSpoolmanLink:
    """The fourth route that configures a slot: linking a Spoolman spool to a
    slot's tag auto-configures it too, from the same free-text material."""

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
            name="Link P1S",
            serial_number="MAT2902LK",
            ip_address="192.168.1.79",
            access_code="12345678",
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        return p

    async def _link(self, async_client, printer, material):
        client = _spoolman_client(_spoolman_spool(material, spool_id=12))
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
        return mqtt.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_linking_a_pla_plus_spool_configures_the_slot_as_pla(
        self, async_client: AsyncClient, settings, printer
    ):
        sent = await self._link(async_client, printer, "PLA+")

        assert sent["tray_type"] == "PLA"
        assert sent["tray_info_idx"] == "GFL99"
        assert sent["tray_sub_brands"] == "eSUN PLA+ Cool White"
        assert (sent["nozzle_temp_min"], sent["nozzle_temp_max"]) == (190, 230)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_it_keeps_this_routes_own_preset_for_a_material_that_had_one(
        self, async_client: AsyncClient, settings, printer
    ):
        sent = await self._link(async_client, printer, "PETG HF")

        assert sent["tray_info_idx"] == "GFG96"
        assert sent["tray_type"] == "PETG"


class TestALocalPresetThatNamesNoFilamentId:
    """The one path where the material reaches the slicer-filament resolver
    rather than the route's own fallback: a spool pointing at an imported local
    preset whose setting JSON carries no filament_id. The resolver then has only
    the material to go on, so it has to read it the same way -- and be handed
    the spool's own wording, not the reduced type."""

    @pytest.fixture
    async def preset(self, db_session):
        from backend.app.models.local_preset import LocalPreset

        lp = LocalPreset(
            name="eSUN PLA+ @BBL P1S",
            preset_type="filament",
            source="orcaslicer",
            filament_type=None,
            setting="{}",
        )
        db_session.add(lp)
        await db_session.commit()
        await db_session.refresh(lp)
        return lp

    @pytest.fixture
    async def printer(self, db_session):
        from backend.app.models.printer import Printer

        p = Printer(
            name="LP P1S",
            serial_number="MAT2902LP",
            ip_address="192.168.1.80",
            access_code="12345678",
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        return p

    async def _assign(self, async_client, db_session, printer, preset, material):
        spool = Spool(
            material=material,
            brand="eSUN",
            rgba="E1E9E9FF",
            label_weight=1000,
            weight_used=0,
            slicer_filament=str(preset.id),
        )
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)

        client = _mqtt_mock()
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status()
            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )
        assert response.status_code == 200
        return client.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_resolver_places_a_product_line_too(
        self, async_client: AsyncClient, db_session: AsyncSession, printer, preset
    ):
        sent = await self._assign(async_client, db_session, printer, preset, "PLA+")

        assert sent["tray_info_idx"] == "GFL99"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_and_still_prefers_a_material_that_has_its_own_preset(
        self, async_client: AsyncClient, db_session: AsyncSession, printer, preset
    ):
        sent = await self._assign(async_client, db_session, printer, preset, "PETG HF")

        assert sent["tray_info_idx"] == "GFG96"


class TestTheAssignmentSurvivesTheSlotItJustConfigured:
    """The other side of the same coin, and the one that bites hardest.

    on_ams_change auto-unlinks an assignment whose slot no longer looks like it
    did when the spool was assigned. The fingerprint is snapshotted *before* the
    MQTT config goes out, so the very next AMS push after an assignment is a
    mismatch by construction -- and what saves the assignment is a second check:
    does the tray match the assigned spool now? That check read the spool's raw
    material, which the slot no longer carries, so every spool this issue is
    about would have been silently unlinked from the slot it had just been
    assigned to. Correct in isolation, ruinous together.
    """

    async def _push(
        self,
        db_session,
        printer_factory,
        spool_material,
        reported_type,
        fingerprint_type="PETG",
        **spool_kwargs,
    ):
        from unittest.mock import AsyncMock

        from backend.app.main import on_ams_change
        from backend.app.models.spool_assignment import SpoolAssignment

        printer = await printer_factory(name="H2D")
        spool = Spool(
            material=spool_material,
            brand="eSUN",
            rgba="E1E9E9FF",
            label_weight=1000,
            weight_used=0,
            **spool_kwargs,
        )
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)

        assignment = SpoolAssignment(
            spool_id=spool.id,
            printer_id=printer.id,
            ams_id=0,
            tray_id=2,
            fingerprint_color="E1E9E9FF",
            fingerprint_type=fingerprint_type,
        )
        db_session.add(assignment)
        await db_session.commit()
        assignment_id = assignment.id

        ams_data = [{"id": 0, "tray": [{"id": 2, "tray_type": reported_type, "tray_color": "E1E9E9FF", "state": 11}]}]
        status = _status(ams_data)
        status.state = "IDLE"

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main.mqtt_relay") as relay,
            patch("backend.app.main.ws_manager") as ws,
        ):
            pm.get_printer.return_value = MagicMock(name="H2D", serial_number="0948BB540200427")
            pm.get_status.return_value = status
            pm.get_model.return_value = "H2D"
            relay.on_ams_change = AsyncMock()
            ws.send_printer_status = AsyncMock()
            ws.broadcast = AsyncMock()

            await on_ams_change(printer.id, ams_data)

        # on_ams_change commits through its own session.
        db_session.expunge_all()
        return await db_session.get(SpoolAssignment, assignment_id)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_pla_plus_spool_is_not_unlinked_from_the_slot_now_reporting_pla(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        surviving = await self._push(db_session, printer_factory, "PLA+", reported_type="PLA")

        assert surviving is not None, "the slot reports what we wrote to it -- that is a match, not a swap"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nor_is_one_in_a_slot_an_older_version_configured(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        """An install upgrading into this fix has slots still reporting "PLA+"
        until something reconfigures them. Reducing only the spool's side would
        break those the moment they were left alone."""
        surviving = await self._push(db_session, printer_factory, "PLA+", reported_type="PLA+")

        assert surviving is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nor_is_one_whose_slot_took_its_presets_type_rather_than_its_material(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        """The preset outranks the material column when the spool has one, so
        the slot can legitimately carry a type the material never named. The
        check has to recognise that as its own handiwork or it unlinks the
        assignment on the very next AMS push."""
        surviving = await self._push(
            db_session,
            printer_factory,
            "PLA",
            reported_type="PLA-AERO",
            slicer_filament_name="Bambu PLA Aero @BBL H2D",
        )

        assert surviving is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_even_when_the_preset_name_was_never_stored(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        """slicer_filament_name is optional. An imported local preset carries
        its type outright, which is the value the assign path actually used."""
        from backend.app.models.local_preset import LocalPreset

        lp = LocalPreset(
            name="Bambu PLA Aero @BBL H2D",
            preset_type="filament",
            source="orcaslicer",
            filament_type="PLA-AERO",
            setting="{}",
        )
        db_session.add(lp)
        await db_session.commit()
        await db_session.refresh(lp)

        surviving = await self._push(
            db_session,
            printer_factory,
            "PLA",
            reported_type="PLA-AERO",
            slicer_filament=str(lp.id),
        )

        assert surviving is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_genuinely_different_filament_still_unlinks(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        """The check still has to do its job: someone swapping PLA for ABS in
        the slot must lose the assignment, or usage gets charged to the wrong
        spool."""
        surviving = await self._push(db_session, printer_factory, "PLA+", reported_type="ABS")

        assert surviving is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_and_a_preset_name_does_not_excuse_an_unrelated_slot(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        """Widening the check to the preset only accepts the types the assign
        path could actually have written. Anything else is still a swap."""
        surviving = await self._push(
            db_session,
            printer_factory,
            "PLA",
            reported_type="ABS",
            slicer_filament_name="Bambu PLA Aero @BBL H2D",
        )

        assert surviving is None


class TestAFilledOrFoamedVariantIsATypeOfItsOwn:
    """The first cut of this fix reduced PLA-AERO, PLA-GF, ASA-GF and PPS-GF
    onto their base material, because the reduction table was assembled from
    the cloud filament names and the frontend preset parser and never checked
    against ``filament_fields.json`` -- the list Bambuddy itself offers when a
    preset is created. @doncaruana caught PLA Aero on the issue.

    That is worse than the bug it replaced. "PLA-AERO" matched nothing before,
    which was useless but honest; "PLA" matches every plain PLA plate in the
    queue, so the dispatcher would have sent one to foaming filament.
    """

    @pytest.fixture
    async def printer(self, db_session):
        from backend.app.models.printer import Printer

        p = Printer(
            name="Aero P1S",
            serial_number="MAT2902AERO",
            ip_address="192.168.1.81",
            access_code="12345678",
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        return p

    async def _assign(self, async_client, db_session, printer, material, tray_id, **spool_kwargs):
        spool = Spool(
            material=material,
            brand="Bambu Lab",
            rgba="E1E9E9FF",
            label_weight=1000,
            weight_used=0,
            **spool_kwargs,
        )
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)

        client = _mqtt_mock()
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status()
            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": tray_id},
            )
        assert response.status_code == 200
        return client.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("material", "tray_id"),
        [("PLA-AERO", 0), ("PLA-GF", 1), ("ASA-GF", 2), ("PPS-GF", 3)],
    )
    async def test_it_reaches_the_slot_intact(
        self, async_client: AsyncClient, db_session: AsyncSession, printer, material, tray_id
    ):
        sent = await self._assign(async_client, db_session, printer, material, tray_id)

        assert sent["tray_type"] == material

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_written_with_a_space_it_still_reaches_the_slot_intact(
        self, async_client: AsyncClient, db_session: AsyncSession, printer
    ):
        """The table hyphenates because the slicers do; a spool says "PLA Aero"
        and so does every Bambu preset name."""
        sent = await self._assign(async_client, db_session, printer, "PLA Aero", 0)

        assert sent["tray_type"] == "PLA-AERO"


class TestThePresetOutranksTheMaterialColumn:
    """#2902 again, from @doncaruana: a preset has to be picked from a list the
    slicer defines, so it already knows its own type and nothing has to be read
    out of a product name. When a spool points at one, that answer wins.

    It cannot be the only answer. ``material`` is required on a spool and
    ``slicer_filament`` is not -- the spool this issue was reported for had no
    preset at all -- so the reduction stays as the fallback.
    """

    @pytest.fixture
    async def printer(self, db_session):
        from backend.app.models.printer import Printer

        p = Printer(
            name="Preset P1S",
            serial_number="MAT2902PRE",
            ip_address="192.168.1.82",
            access_code="12345678",
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        return p

    async def _preset(self, db_session, name, filament_type):
        from backend.app.models.local_preset import LocalPreset

        lp = LocalPreset(
            name=name,
            preset_type="filament",
            source="orcaslicer",
            filament_type=filament_type,
            setting="{}",
        )
        db_session.add(lp)
        await db_session.commit()
        await db_session.refresh(lp)
        return lp

    async def _assign(self, async_client, db_session, printer, material, preset, tray_id):
        spool = Spool(
            material=material,
            brand="Bambu Lab",
            rgba="E1E9E9FF",
            label_weight=1000,
            weight_used=0,
            slicer_filament=str(preset.id) if preset else None,
        )
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)

        client = _mqtt_mock()
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.get_client.return_value = client
            pm.get_status.return_value = _status()
            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": tray_id},
            )
        assert response.status_code == 200
        return client.ams_set_filament_setting.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_slot_gets_the_presets_type_not_one_read_from_the_material(
        self, async_client: AsyncClient, db_session: AsyncSession, printer
    ):
        """The material column says "PLA", which the reduction would happily
        accept. The preset says the spool is foaming PLA, and it is right."""
        preset = await self._preset(db_session, "Bambu PLA Aero @BBL P1S", "PLA-AERO")
        sent = await self._assign(async_client, db_session, printer, "PLA", preset, 0)

        assert sent["tray_type"] == "PLA-AERO"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_preset_that_names_no_type_leaves_the_reduction_in_charge(
        self, async_client: AsyncClient, db_session: AsyncSession, printer
    ):
        preset = await self._preset(db_session, "eSUN PLA+ @BBL P1S", None)
        sent = await self._assign(async_client, db_session, printer, "PLA+", preset, 1)

        assert sent["tray_type"] == "PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_and_a_spool_with_no_preset_at_all_still_gets_one(
        self, async_client: AsyncClient, db_session: AsyncSession, printer
    ):
        sent = await self._assign(async_client, db_session, printer, "PLA+", None, 2)

        assert sent["tray_type"] == "PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_hand_edited_preset_naming_a_product_line_is_still_reduced(
        self, async_client: AsyncClient, db_session: AsyncSession, printer
    ):
        """Preferring the preset does not mean trusting it blindly. A profile
        whose filament_type is a product line puts that product line in the
        slot, which is the exact failure this issue is about."""
        preset = await self._preset(db_session, "My PLA+ @BBL P1S", "PLA+")
        sent = await self._assign(async_client, db_session, printer, "PLA", preset, 3)

        assert sent["tray_type"] == "PLA"

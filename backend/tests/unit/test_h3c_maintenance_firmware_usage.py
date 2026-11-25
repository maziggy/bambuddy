"""H3C: firmware version source, stopped-print usage, and the maintenance schedule.

- The H3C wiki page is /en/h3c/manual/firmware-release-history and marks
  versions as id="ota-01010300" with no date.
- The H3C's mc_percent counts its ~8 min start sequence (22% at layer 0 on a
  31 min print), so a stopped print is charged by layers printed, not percent.
- The H3C gets its own maintenance tasks from Bambu's H3C guides, in place of
  the generic rail, hotend and PTFE ones (and never "Clean Carbon Rods").
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes.maintenance import (
    DEFAULT_MAINTENANCE_TYPES,
    H3C_MAINTENANCE_TYPES,
    _should_apply_to_printer,
)
from backend.app.services.firmware_check import API_KEY_TO_WIKI_PATH, FirmwareCheckService
from backend.app.utils.printer_models import get_rod_type, usage_progress

# Trimmed from wiki.bambulab.com/en/h3c/manual/firmware-release-history (2026-11-07),
# with a second, older heading added and an AMS-firmware-style number in prose.
H3C_WIKI_SAMPLE = """
<h2 id="ota-01010300" class="toc-header"><a class="toc-anchor" href="#ota-01010300">¶</a> OTA 01.01.03.00</h2>
<p><strong>New Features</strong></p>
<ol><li><p>Added support for connecting up to 4 auxiliary AMS / AMS 2 Pro / AMS HT units
(requires AMS firmware 02.00.19.47).</p></li></ol>
<h2 id="ota-01010200" class="toc-header"><a class="toc-anchor" href="#ota-01010200">¶</a> OTA 01.01.02.00</h2>
<p>Bug fixes.</p>
"""


class TestH3CFirmwareSource:
    def test_wiki_path_is_the_one_bambu_publishes(self):
        assert API_KEY_TO_WIKI_PATH["h3c"] == "/en/h3c/manual/firmware-release-history"

    @pytest.mark.asyncio
    async def test_ota_anchors_parse_without_dates(self):
        svc = FirmwareCheckService()
        resp = AsyncMock()
        resp.status_code = 200
        resp.text = H3C_WIKI_SAMPLE
        with patch.object(svc._client, "get", AsyncMock(return_value=resp)) as get:
            versions = await svc._fetch_all_versions_from_wiki("h3c")

        assert get.call_args.args[0] == "https://wiki.bambulab.com/en/h3c/manual/firmware-release-history"
        assert versions == [("01.01.03.00", None), ("01.01.02.00", None)]

    @pytest.mark.asyncio
    async def test_dated_anchors_still_win_over_ota_ones(self):
        svc = FirmwareCheckService()
        resp = AsyncMock()
        resp.status_code = 200
        resp.text = '<h2 id="h-01030000-20260303">01.03.00.00</h2>' + H3C_WIKI_SAMPLE
        with patch.object(svc._client, "get", AsyncMock(return_value=resp)):
            versions = await svc._fetch_all_versions_from_wiki("h2d")

        assert versions == [("01.03.00.00", "20260303")]


class TestStoppedPrintProgress:
    def test_h3c_warm_up_is_charged_nothing(self):
        # Captured: 22% with layer_num 0 during the start sequence.
        assert usage_progress("H3C", 22.0, 0, 180) == 0.0
        assert usage_progress("N10", 22.0, None, 180) == 0.0

    def test_h3c_is_charged_by_layers_printed(self):
        assert usage_progress("H3C", 61.0, 45, 180) == 25.0
        assert usage_progress("H3C", 99.0, 200, 180) == 100.0

    def test_h3c_without_layer_count_is_charged_nothing(self):
        assert usage_progress("H3C", 50.0, 90, 0) == 0.0

    @pytest.mark.parametrize("model", ["X1C", "H2D", "P2S", "A1", None, ""])
    def test_other_models_keep_the_printer_percent(self, model):
        assert usage_progress(model, 22.0, 0, 180) == 22.0
        assert usage_progress(model, 0, 0, 0) == 0

    def test_non_string_model_is_not_h3c(self):
        assert usage_progress(object(), 30, 0, 100) == 30

    def test_completion_payload_reports_layer_progress_for_h3c(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST", access_code="1", model="H3C")
        done = {}
        client.on_print_start = lambda data: None
        client.on_print_complete = lambda data: done.update(data)

        client._process_message(
            {"print": {"gcode_state": "PREPARE", "gcode_file": "/data/Metadata/plate_2.gcode", "subtask_name": "T"}}
        )
        client._process_message({"print": {"gcode_state": "RUNNING", "mc_percent": 22, "layer_num": 0}})
        client._process_message({"print": {"gcode_state": "FAILED", "mc_percent": 22, "layer_num": 0}})

        assert done.get("status") in ("failed", "aborted", "cancelled")
        assert done["last_progress"] == 0.0
        assert done["last_layer_num"] == 0

    def test_completion_payload_unchanged_for_other_models(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST", access_code="1", model="X1C")
        done = {}
        client.on_print_start = lambda data: None
        client.on_print_complete = lambda data: done.update(data)

        client._process_message(
            {"print": {"gcode_state": "PREPARE", "gcode_file": "/data/Metadata/plate_1.gcode", "subtask_name": "T"}}
        )
        client._process_message({"print": {"gcode_state": "RUNNING", "mc_percent": 22, "layer_num": 0}})
        client._process_message({"print": {"gcode_state": "FAILED", "mc_percent": 22, "layer_num": 0}})

        assert done["last_progress"] == 22


class TestH3CMaintenance:
    def test_h3c_has_linear_rails_not_carbon_rods(self):
        assert get_rod_type("H3C") == "linear_rail"
        assert get_rod_type("N10") == "linear_rail"

    @pytest.mark.parametrize("model", ["H3C", "N10"])
    def test_h3c_gets_its_own_tasks_and_the_shared_ones(self, model):
        every = DEFAULT_MAINTENANCE_TYPES + H3C_MAINTENANCE_TYPES
        shown = {t["name"] for t in every if _should_apply_to_printer(t["name"], model)}
        assert {t["name"] for t in H3C_MAINTENANCE_TYPES} <= shown
        assert {"Check Belt Tension", "Clean Build Plate"} <= shown
        for replaced in (
            "Clean Carbon Rods",
            "Lubricate Linear Rails",
            "Clean Linear Rails",
            "Clean Nozzle/Hotend",
            "Check PTFE Tube",
        ):
            assert replaced not in shown

    @pytest.mark.parametrize("model", ["X1C", "P1S", "H2D", "H2C", "P2S", "A1", "UNKNOWN", None])
    def test_other_models_never_get_h3c_tasks(self, model):
        for t in H3C_MAINTENANCE_TYPES:
            assert _should_apply_to_printer(t["name"], model) is False

    def test_other_models_keep_their_generic_tasks(self):
        assert _should_apply_to_printer("Lubricate Linear Rails", "H2D") is True
        assert _should_apply_to_printer("Clean Nozzle/Hotend", "H2D") is True
        assert _should_apply_to_printer("Check PTFE Tube", "X1C") is True
        assert _should_apply_to_printer("Clean Carbon Rods", "X1C") is True

    def test_task_definitions_are_complete(self):
        # The icon names the frontend's iconMap knows (MaintenancePage.tsx).
        icons = {
            "Droplet", "Flame", "Ruler", "Sparkles", "Square", "Cable", "Wrench", "Calendar", "Timer", "Cog",
            "Fan", "Zap", "Wind", "Thermometer", "Layers", "Box", "Target", "RefreshCw", "Settings", "Filter",
            "CircleDot",
        }  # fmt: skip
        names = [t["name"] for t in DEFAULT_MAINTENANCE_TYPES + H3C_MAINTENANCE_TYPES]
        assert len(names) == len(set(names))
        for t in H3C_MAINTENANCE_TYPES:
            assert len(t["name"]) <= 100
            assert t["interval_type"] in ("hours", "days")
            assert t["default_interval_hours"] > 0
            assert t["icon"] in icons
            assert t["wiki_url"].startswith("https://wiki.bambulab.com/en/h3c/")

    def test_air_filter_uses_bambus_rated_print_hours(self):
        f = next(t for t in H3C_MAINTENANCE_TYPES if t["name"] == "Replace Air Filter (H3C)")
        assert (f["default_interval_hours"], f["interval_type"]) == (1440.0, "hours")


def _printer(model: str, serial: str):
    from backend.app.models.printer import Printer

    return Printer(name=model, serial_number=serial, ip_address="192.0.2.9", access_code="1", model=model)


async def _type_names(db_session) -> tuple[set[str], set[str]]:
    """(names GET /maintenance/types lists, names stored) after it ran."""
    from sqlalchemy import select

    from backend.app.api.routes.maintenance import get_maintenance_types
    from backend.app.models.maintenance import MaintenanceType

    listed = {t.name for t in await get_maintenance_types(db=db_session, _=None)}
    stored = {t.name for t in (await db_session.execute(select(MaintenanceType))).scalars()}
    return listed, stored


@pytest.mark.asyncio
async def test_without_an_h3c_the_types_are_exactly_the_defaults(db_session):
    db_session.add(_printer("X1C", "SN-X1C-9"))
    await db_session.commit()

    listed, stored = await _type_names(db_session)
    assert listed == stored == {t["name"] for t in DEFAULT_MAINTENANCE_TYPES}


@pytest.mark.asyncio
async def test_an_h3c_adds_its_own_types(db_session):
    h3c = _printer("N10", "SN-H3C-9")
    db_session.add(h3c)
    await db_session.commit()

    listed, stored = await _type_names(db_session)
    expected = {t["name"] for t in DEFAULT_MAINTENANCE_TYPES + H3C_MAINTENANCE_TYPES}
    assert listed == stored == expected

    # Removing the H3C hides them again; they are kept for a printer added later.
    await db_session.delete(h3c)
    await db_session.commit()
    listed, stored = await _type_names(db_session)
    assert listed == {t["name"] for t in DEFAULT_MAINTENANCE_TYPES}
    assert stored == expected


@pytest.mark.asyncio
async def test_ensure_default_types_stores_interval_type_and_wiki_url(db_session):
    from sqlalchemy import select

    from backend.app.api.routes.maintenance import ensure_default_types
    from backend.app.models.maintenance import MaintenanceType

    db_session.add(_printer("H3C", "SN-H3C-8"))
    await db_session.commit()
    await ensure_default_types(db_session)
    rows = {t.name: t for t in (await db_session.execute(select(MaintenanceType))).scalars()}

    desiccant = rows["AMS 3H: Check Desiccant"]
    assert (desiccant.interval_type, desiccant.default_interval_hours) == ("days", 30.0)
    assert desiccant.wiki_url == "https://wiki.bambulab.com/en/h3c/maintenance/ams_3h"
    assert desiccant.is_system is True
    # The generic ones are created as before.
    assert rows["Clean Build Plate"].interval_type == "hours"
    assert rows["Clean Build Plate"].wiki_url is None


@pytest.mark.asyncio
async def test_h3c_tasks_count_from_when_the_printer_was_added(db_session):
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from backend.app.api.routes.maintenance import _get_printer_maintenance_internal
    from backend.app.models.maintenance import MaintenanceHistory
    from backend.app.models.printer import Printer

    added = datetime.utcnow() - timedelta(days=4)
    printer = Printer(
        name="H3C", serial_number="SN-H3C-1", ip_address="192.0.2.1", access_code="1", model="H3C", created_at=added
    )
    db_session.add(printer)
    await db_session.commit()

    overview = await _get_printer_maintenance_internal(printer.id, db_session)
    items = {i.maintenance_type_name: i for i in overview.maintenance_items}

    rails = items["Lubricate X/Y Rails (H3C)"]
    assert rails.is_due is False
    assert 25.5 < rails.days_until_due < 26.5  # 30-day task, printer added 4 days ago
    assert items["Clean Extruder Wheel (H3C)"].is_due is False  # 7 days, 4 gone
    assert overview.due_count == 0
    assert (await db_session.execute(select(MaintenanceHistory))).scalars().first() is None


@pytest.mark.asyncio
async def test_generic_tasks_keep_never_done_semantics(db_session):
    from backend.app.api.routes.maintenance import _get_printer_maintenance_internal
    from backend.app.models.printer import Printer

    printer = Printer(name="X1C", serial_number="SN-X1C-1", ip_address="192.0.2.2", access_code="1", model="X1C")
    db_session.add(printer)
    await db_session.commit()

    overview = await _get_printer_maintenance_internal(printer.id, db_session)
    assert all(i.last_performed_at is None for i in overview.maintenance_items)

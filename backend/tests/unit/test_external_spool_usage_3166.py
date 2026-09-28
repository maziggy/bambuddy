"""External-spool usage charged to an AMS spool (#3166).

The firmware rejects 254/255 in a print command's flat ``ams_mapping``, so
BambuStudio and our own dispatch both write an external spool there as -1 and
put the real target in ``ams_mapping2``. The MQTT client captured only the flat
list, so a print fed from the external spool reached the usage tracker as
``[-1]`` -- and the tracker's position-based fallback then charged it to the
first loaded AMS tray. The reporter's H2D logged this for every external-spool
print: 225 g of ABS from the right external spool deducted from the PLA spool
in AMS slot 1.

The command payloads below are verbatim from that reporter's support bundle
(H2D, firmware 01.03.00.00, one AMS 2 Pro).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.spool import Spool
from backend.app.services.bambu_mqtt import BambuMQTTClient, resolve_external_spools_in_mapping
from backend.app.services.usage_tracker import _track_from_3mf

pytestmark = pytest.mark.unit


def project_file(ams_mapping, ams_mapping2=None) -> dict:
    print_data = {
        "sequence_id": "20000",
        "command": "project_file",
        "param": "Metadata/plate_1.gcode",
        "url": "ftp://Small_Spool_Adapter_(PLA)_A_Side.3mf",
        "ams_mapping": ams_mapping,
    }
    if ams_mapping2 is not None:
        print_data["ams_mapping2"] = ams_mapping2
    return {"print": print_data}


EXT_RIGHT = {"ams_id": 255, "slot_id": 0}
EXT_LEFT = {"ams_id": 254, "slot_id": 0}
UNMAPPED = {"ams_id": 255, "slot_id": 255}


class TestResolvingTheExternalSpool:
    def test_dual_nozzle_right_external(self):
        assert resolve_external_spools_in_mapping([-1], [EXT_RIGHT], is_dual_nozzle=True) == [255]

    def test_dual_nozzle_left_external(self):
        assert resolve_external_spools_in_mapping([-1], [EXT_LEFT], is_dual_nozzle=True) == [254]

    def test_single_nozzle_external_is_254_whatever_the_wire_says(self):
        # BambuStudio always sends ams_id 255 for the one external spool of a
        # single-nozzle printer; Bambuddy knows that spool as global tray 254.
        assert resolve_external_spools_in_mapping([-1], [EXT_RIGHT], is_dual_nozzle=False) == [254]

    def test_mixed_mapping_only_the_external_entry_changes(self):
        mapping2 = [
            {"ams_id": 0, "slot_id": 0},
            EXT_RIGHT,
            {"ams_id": 0, "slot_id": 2},
            {"ams_id": 0, "slot_id": 3},
        ]
        assert resolve_external_spools_in_mapping([0, -1, 2, 3], mapping2, is_dual_nozzle=True) == [0, 255, 2, 3]

    def test_an_unmapped_slot_stays_unmapped(self):
        assert resolve_external_spools_in_mapping([0, -1], [{"ams_id": 0, "slot_id": 0}, UNMAPPED], True) == [0, -1]

    @pytest.mark.parametrize("mapping2", [None, "junk", [EXT_RIGHT, EXT_RIGHT]])
    def test_without_a_usable_mapping2_the_capture_is_untouched(self, mapping2):
        assert resolve_external_spools_in_mapping([-1], mapping2, is_dual_nozzle=True) == [-1]

    def test_non_list_mapping_is_returned_as_is(self):
        assert resolve_external_spools_in_mapping(None, [EXT_RIGHT], is_dual_nozzle=True) is None


class TestTheCapturedMapping:
    def test_h2d_command_captures_the_right_external_spool(self):
        client = BambuMQTTClient(ip_address="10.0.0.7", serial_number="H2D3166", access_code="12345678", model="H2D")
        client._handle_request_message(project_file([-1], [EXT_RIGHT]))
        assert client._captured_ams_mapping == [255]

    def test_h2d_mixed_command(self):
        client = BambuMQTTClient(ip_address="10.0.0.7", serial_number="H2D3166", access_code="12345678", model="H2D")
        mapping2 = [
            {"ams_id": 0, "slot_id": 0},
            EXT_RIGHT,
            {"ams_id": 0, "slot_id": 2},
            {"ams_id": 0, "slot_id": 3},
        ]
        client._handle_request_message(project_file([0, -1, 2, 3], mapping2))
        assert client._captured_ams_mapping == [0, 255, 2, 3]

    def test_p2s_command_captures_tray_254(self):
        client = BambuMQTTClient(ip_address="10.0.0.7", serial_number="P2S3166", access_code="12345678", model="P2S")
        client._handle_request_message(project_file([-1], [EXT_RIGHT]))
        assert client._captured_ams_mapping == [254]

    def test_command_without_mapping2_is_captured_as_before(self):
        client = BambuMQTTClient(ip_address="10.0.0.7", serial_number="X1C3166", access_code="12345678", model="X1C")
        client._handle_request_message(project_file([-1]))
        assert client._captured_ams_mapping == [-1]


# --- The usage tracker ---------------------------------------------------------


def _spool(spool_id: int):
    spool = MagicMock()
    spool.id = spool_id
    spool.label_weight = 1000
    spool.weight_used = 0
    spool.cost_per_kg = None
    spool.material = "ABS"
    spool.rgba = None
    return spool


def _db(spools: dict[int, MagicMock]):
    """Answer the tracker's archive and spool lookups by what they select."""
    archive = MagicMock()
    archive.id = 142
    archive.file_path = "archives/142/test.3mf"
    archive.extra_data = None
    archive.plate_id = None
    selected: list = []

    async def execute(stmt, *args, **kwargs):
        entity = stmt.column_descriptions[0].get("entity")
        result = MagicMock()
        value = None
        if entity is PrintArchive:
            value = archive
        elif entity is Spool:
            spool_id = stmt.whereclause.right.value
            value = spools.get(spool_id)
            selected.append(spool_id)
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.first.return_value = None
        result.scalar.return_value = None
        return result

    db = AsyncMock()
    db.execute = execute
    db.add = MagicMock()
    return db


# The reporter's printer: PLA in AMS slot 1 (spool 20), ABS on the right
# external spool (spool 4). The AMS slot is loaded, so the old position-based
# fallback had a tray to land on.
ASSIGNED = {(0, 0): 20, (255, 1): 4}
RAW_DATA = {
    "ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}]}],
    "vt_tray": [{"id": "255", "tray_type": "ABS"}],
}


async def _track(ams_mapping, filament_usage, tray_now_at_start=-1, tray_now=255):
    charged: list[tuple[int, int]] = []

    async def resolve(printer_id, ams_id, tray_id, **kwargs):
        charged.append((ams_id, tray_id))
        return ASSIGNED.get((ams_id, tray_id))

    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        progress=100,
        layer_num=50,
        tray_now=tray_now,
        last_loaded_tray=-1,
        tray_change_log=[],
        raw_data=RAW_DATA,
    )
    with (
        patch("backend.app.core.config.settings") as mock_settings,
        patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        patch("backend.app.services.usage_tracker._resolve_spool_id_for_tray", side_effect=resolve),
    ):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_settings.base_dir = MagicMock()
        mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)
        results = await _track_from_3mf(
            printer_id=1,
            archive_id=142,
            status="completed",
            print_name="Daft_Punk_Helmet_Back",
            handled_trays=set(),
            printer_manager=printer_manager,
            db=_db({20: _spool(20), 4: _spool(4)}),
            ams_mapping=ams_mapping,
            tray_now_at_start=tray_now_at_start,
            print_started_at=datetime.now(timezone.utc),
        )
    return results, charged


ABS_ONLY = [{"slot_id": 1, "used_g": 225.14, "type": "ABS", "color": "#000000"}]


class TestTheTrackerCharges:
    @pytest.mark.asyncio
    async def test_the_resolved_mapping_charges_the_external_spool(self):
        results, charged = await _track([255], ABS_ONLY)
        assert [r["spool_id"] for r in results] == [4]
        assert charged == [(255, 1)]

    @pytest.mark.asyncio
    async def test_an_unmapped_slot_does_not_fall_back_to_an_ams_tray(self):
        # A two-filament print where the mapping leaves slot 2 unfed. The old
        # fallback handed slot 2 the second loaded tray instead.
        usage = [
            {"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#000000"},
            {"slot_id": 2, "used_g": 5.0, "type": "ABS", "color": "#000000"},
        ]
        results, charged = await _track([0, -1], usage)
        assert [r["spool_id"] for r in results] == [20]
        assert charged == [(0, 0)]

    @pytest.mark.asyncio
    async def test_an_all_unmapped_capture_defers_to_tray_now(self):
        # A command without ams_mapping2 still captures [-1]. That names no
        # tray, so the printer's own tray_now decides -- not AMS slot 1.
        results, charged = await _track([-1], ABS_ONLY, tray_now_at_start=255)
        assert (0, 0) not in charged
        assert [r["spool_id"] for r in results] == [4]

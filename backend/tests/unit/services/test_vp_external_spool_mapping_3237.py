"""A VP with "Save AMS mapping" keeps the slicer's external-spool pick (#3237).

The slicer writes an external spool as -1 in the flat ``ams_mapping``, the same
as a filament with no tray, and names it only in ``ams_mapping2``. Saving the
flat list alone made dispatch send the external filament as unmapped, and the
printer stopped with 0700-8012 before the first layer.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.virtual_printer.manager import (
    VirtualPrinterInstance,
    _extract_slicer_ams_mapping_json,
)

EXT_LEFT = {"ams_id": 254, "slot_id": 0}
EXT_RIGHT = {"ams_id": 255, "slot_id": 0}
UNMAPPED = {"ams_id": 255, "slot_id": 255}

# The reporter's H2C job: left external spool plus AMS 0 slots 3 and 2.
H2C_PAYLOAD = {
    "command": "project_file",
    "ams_mapping": [-1, 3, 2],
    "ams_mapping2": [EXT_LEFT, {"ams_id": 0, "slot_id": 3}, {"ams_id": 0, "slot_id": 2}],
}


def _extract(data, is_dual_nozzle):
    raw = _extract_slicer_ams_mapping_json(data, "[test]", is_dual_nozzle=is_dual_nozzle)
    return None if raw is None else json.loads(raw)


class TestExtract:
    def test_left_external_spool_on_dual_nozzle(self):
        assert _extract(H2C_PAYLOAD, is_dual_nozzle=True) == [254, 3, 2]

    def test_right_external_spool_on_dual_nozzle(self):
        data = {"ams_mapping": [-1, 3], "ams_mapping2": [EXT_RIGHT, {"ams_id": 0, "slot_id": 3}]}
        assert _extract(data, is_dual_nozzle=True) == [255, 3]

    def test_single_nozzle_external_spool_is_254(self):
        # Single-nozzle printers have one external spool; the wire calls it
        # 255, Bambuddy's global tray is 254.
        data = {"ams_mapping": [0, -1], "ams_mapping2": [{"ams_id": 0, "slot_id": 0}, EXT_RIGHT]}
        assert _extract(data, is_dual_nozzle=False) == [0, 254]

    def test_unused_filament_stays_unmapped(self):
        data = {"ams_mapping": [-1, 3], "ams_mapping2": [UNMAPPED, {"ams_id": 0, "slot_id": 3}]}
        assert _extract(data, is_dual_nozzle=True) == [-1, 3]

    def test_external_only_job_is_kept(self):
        # Every flat entry is -1, but this is a real pick, not the #2589
        # unresolved sentinel.
        assert _extract({"ams_mapping": [-1], "ams_mapping2": [EXT_LEFT]}, is_dual_nozzle=True) == [254]

    def test_unresolved_sentinel_still_dropped(self):
        data = {"ams_mapping": [-1, -1], "ams_mapping2": [UNMAPPED, UNMAPPED]}
        assert _extract(data, is_dual_nozzle=True) is None

    def test_stringified_mapping2_is_parsed(self):
        data = {"ams_mapping": "[-1, 3, 2]", "ams_mapping2": json.dumps(H2C_PAYLOAD["ams_mapping2"])}
        assert _extract(data, is_dual_nozzle=True) == [254, 3, 2]

    def test_missing_or_unusable_mapping2_keeps_flat_list(self):
        assert _extract({"ams_mapping": [-1, 3, 2]}, is_dual_nozzle=True) == [-1, 3, 2]
        assert _extract({"ams_mapping": [-1, 3, 2], "ams_mapping2": "not json"}, is_dual_nozzle=True) == [-1, 3, 2]
        # Length mismatch: positions can't be paired, leave it alone.
        assert _extract({"ams_mapping": [-1, 3, 2], "ams_mapping2": [EXT_LEFT]}, is_dual_nozzle=True) == [-1, 3, 2]


def _instance(tmp_path, *, model="O1C", printer_manager=None, session_factory=None):
    return VirtualPrinterInstance(
        vp_id=3237,
        name="ExtSpool",
        mode="queue",
        model=model,
        access_code="12345678",
        serial_suffix="391803237",
        base_dir=tmp_path,
        session_factory=session_factory,
        save_ams_mapping=True,
        target_printer_id=7,
        printer_manager=printer_manager,
    )


def _manager_with(client):
    manager = MagicMock()
    manager.get_client = MagicMock(return_value=client)
    return manager


class TestTargetIsDualNozzle:
    def test_live_detection_wins(self, tmp_path):
        client = MagicMock(_is_dual_nozzle=True, model="Some future model")
        assert _instance(tmp_path, model="BL-P001", printer_manager=_manager_with(client))._target_is_dual_nozzle()

    def test_client_model(self, tmp_path):
        client = MagicMock(_is_dual_nozzle=False, model="H2C")
        assert _instance(tmp_path, model="BL-P001", printer_manager=_manager_with(client))._target_is_dual_nozzle()

    def test_single_nozzle_client(self, tmp_path):
        client = MagicMock(_is_dual_nozzle=False, model="X1C")
        assert not _instance(tmp_path, printer_manager=_manager_with(client))._target_is_dual_nozzle()

    def test_falls_back_to_vp_model_without_client(self, tmp_path):
        assert _instance(tmp_path, model="O1C", printer_manager=_manager_with(None))._target_is_dual_nozzle()
        assert not _instance(tmp_path, model="BL-P001")._target_is_dual_nozzle()


@pytest.mark.asyncio
async def test_queue_item_and_archive_keep_left_external_spool(tmp_path):
    added_items = []
    mock_db = AsyncMock()
    mock_db.add = MagicMock(side_effect=added_items.append)
    mock_db.commit = AsyncMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=session_ctx)

    client = MagicMock(_is_dual_nozzle=True, model="H2C")
    inst = _instance(tmp_path, printer_manager=_manager_with(client), session_factory=session_factory)

    file_path = tmp_path / "test.3mf"
    file_path.write_bytes(b"fake3mf")
    await inst.on_print_command(file_path.name, dict(H2C_PAYLOAD))

    mock_archive = MagicMock(id=1, print_name="test")
    with (
        patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None),
        patch(
            "backend.app.services.archive.ArchiveService.archive_print",
            new_callable=AsyncMock,
            return_value=mock_archive,
        ) as archive_print,
    ):
        await inst._add_to_print_queue(file_path, "192.168.1.100")

    assert len(added_items) == 1
    assert json.loads(added_items[0].ams_mapping) == [254, 3, 2]
    assert archive_print.await_args.kwargs["slicer_ams_mapping"] == [254, 3, 2]

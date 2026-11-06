"""What a VP in queue mode takes from the slicer onto the queue item.

Nozzle offset calibration was never read, so every VP-queued print ran with
"auto" whatever Bambu Studio or the default print options said. The slicer's
nozzle positions were stored for an "Any <model>" VP too, and the dispatch
sends a stored mapping as it is instead of resolving it against the rack of the
printer it picked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.virtual_printer.manager import VirtualPrinterInstance


def _session(added: list, execute_results=None):
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda item: (added.append(item), setattr(item, "id", 100 + len(added)))[0])
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    if execute_results is not None:
        db.execute = AsyncMock(side_effect=execute_results)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx), db


def _vp(tmp_path, factory, **kwargs):
    return VirtualPrinterInstance(
        vp_id=77,
        name="Intake",
        mode="queue",
        model="O1C2",
        access_code="12345678",
        serial_suffix="391800077",
        base_dir=tmp_path,
        session_factory=factory,
        **kwargs,
    )


async def _enqueue(inst, tmp_path, slicer_opts: dict | None, settings: dict | None = None, announce=None):
    file_path = tmp_path / "test.3mf"
    file_path.write_bytes(b"fake3mf")
    if slicer_opts is not None:
        await inst.on_print_command(file_path.name, {"command": "project_file", **slicer_opts})
    archive = MagicMock(id=1, print_name="test")
    values = settings or {}

    async def _setting(_db, key):
        return values.get(key)

    with (
        patch("backend.app.api.routes.settings.get_setting", side_effect=_setting),
        patch(
            "backend.app.services.archive.ArchiveService.archive_print", new_callable=AsyncMock, return_value=archive
        ),
        patch("backend.app.services.virtual_printer.manager._SLICER_OPTIONS_WAIT_TIMEOUT", 0.05),
        # Its own query would take a result the session mock holds for the test.
        patch("backend.app.services.queue_announcements.announce_queue_jobs_added", announce or AsyncMock()),
    ):
        await inst._add_to_print_queue(file_path, "192.168.1.100")
    return file_path


class TestNozzleOffsetCalibration:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("wire", "stored"), [(0, "off"), (1, "on"), (2, "auto")])
    async def test_the_slicers_choice_is_kept(self, tmp_path, wire, stored):
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)

        await _enqueue(inst, tmp_path, {"nozzle_offset_cali": wire}, {"default_nozzle_offset_cali": "on"})

        assert added[0].nozzle_offset_cali == stored

    @pytest.mark.asyncio
    async def test_without_a_slicer_choice_the_default_applies(self, tmp_path):
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)

        await _enqueue(inst, tmp_path, {"timelapse": True}, {"default_nozzle_offset_cali": "off"})

        assert added[0].nozzle_offset_cali == "off"

    @pytest.mark.asyncio
    async def test_a_late_slicer_choice_is_patched_on(self, tmp_path):
        added: list = []
        position_max = MagicMock(scalar=MagicMock(return_value=None))
        pending = MagicMock(all=MagicMock(return_value=[(101, None, None)]))
        factory, db = _session(added, [position_max, pending, MagicMock()])
        inst = _vp(tmp_path, factory, target_printer_id=7)
        inst._mqtt = MagicMock()

        file_path = await _enqueue(inst, tmp_path, None)
        await inst.on_print_command(
            file_path.name, {"command": "project_file", "file": file_path.name, "nozzle_offset_cali": 0}
        )

        params = dict(db.execute.await_args_list[2].args[0].compile().params)
        assert params["nozzle_offset_cali"] == "off"


class TestNozzlePositionsNeedOnePrinter:
    @pytest.mark.asyncio
    async def test_a_vp_bound_to_a_printer_keeps_them(self, tmp_path):
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)

        await _enqueue(inst, tmp_path, {"nozzle_mapping": [16, -1]})

        assert json.loads(added[0].nozzle_mapping) == [16, -1]

    @pytest.mark.asyncio
    async def test_an_any_model_vp_leaves_them_to_the_dispatch(self, tmp_path):
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory)

        await _enqueue(inst, tmp_path, {"nozzle_mapping": [16, -1]})

        assert added[0].target_model is not None
        assert added[0].nozzle_mapping is None

    @pytest.mark.asyncio
    async def test_an_any_model_vp_does_not_patch_them_on_late(self, tmp_path):
        added: list = []
        position_max = MagicMock(scalar=MagicMock(return_value=None))
        pending = MagicMock(all=MagicMock(return_value=[(101, None, None)]))
        factory, db = _session(added, [position_max, pending, MagicMock()])
        inst = _vp(tmp_path, factory)
        inst._mqtt = MagicMock()

        file_path = await _enqueue(inst, tmp_path, None)
        await inst.on_print_command(
            file_path.name,
            {"command": "project_file", "file": file_path.name, "nozzle_mapping": [16, -1], "timelapse": True},
        )

        params = dict(db.execute.await_args_list[2].args[0].compile().params)
        assert params["timelapse"] is True
        assert "nozzle_mapping" not in params


class TestAnyH2DKeepsNozzleIndices:
    @pytest.mark.asyncio
    async def test_extruder_indices_are_the_same_on_every_printer(self, tmp_path):
        """Off a rack model the mapping names extruders, not a printer's rack."""
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory)
        inst.model = "O1D"  # H2D

        await _enqueue(inst, tmp_path, {"nozzle_mapping": [0, 1]})

        assert json.loads(added[0].nozzle_mapping) == [0, 1]


class TestAmsOnOff:
    @pytest.mark.asyncio
    async def test_the_slicers_choice_is_kept_when_it_arrives_in_time(self, tmp_path):
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)

        await _enqueue(inst, tmp_path, {"use_ams": False})

        assert added[0].use_ams is False

    @pytest.mark.asyncio
    async def test_on_when_the_slicer_does_not_say(self, tmp_path):
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)

        await _enqueue(inst, tmp_path, {"timelapse": True})

        assert added[0].use_ams is True

    @pytest.mark.asyncio
    async def test_a_vp_for_any_printer_leaves_it_on(self, tmp_path):
        """The slicer saw the VP, which has no AMS; the printer the job goes to
        is picked later and may have one (the dispatch reconciles it, #2595)."""
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory)

        await _enqueue(inst, tmp_path, {"use_ams": False})

        assert added[0].use_ams is True

    @pytest.mark.asyncio
    async def test_a_vp_for_any_printer_does_not_patch_it_on_late(self, tmp_path):
        added: list = []
        position_max = MagicMock(scalar=MagicMock(return_value=None))
        pending = MagicMock(all=MagicMock(return_value=[(101, None, None)]))
        factory, db = _session(added, [position_max, pending, MagicMock()])
        inst = _vp(tmp_path, factory)
        inst._mqtt = MagicMock()

        file_path = await _enqueue(inst, tmp_path, None)
        await inst.on_print_command(
            file_path.name,
            {"command": "project_file", "file": file_path.name, "use_ams": False, "timelapse": True},
        )

        params = dict(db.execute.await_args_list[2].args[0].compile().params)
        assert params["timelapse"] is True
        assert "use_ams" not in params


class TestLateAmsMappingPerPlate:
    @pytest.mark.asyncio
    async def test_each_queued_plate_records_the_highest_slot_it_prints(self, tmp_path):
        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)

        with patch(
            "backend.app.services.filament_requirements.extract_filament_requirements",
            return_value=[{"slot_id": 1, "type": "PLA"}, {"slot_id": 4, "type": "PLA"}],
        ):
            file_path = await _enqueue(inst, tmp_path, {"timelapse": True})

        assert inst._recent_queue_item_reach[file_path.name] == {added[0].id: 4}

    @pytest.mark.asyncio
    async def test_a_plate_the_mapping_cannot_reach_keeps_the_rest_of_the_patch(self, tmp_path):
        import time

        added: list = []
        pending = MagicMock(all=MagicMock(return_value=[(101, None, None), (102, None, None)]))
        factory, db = _session(added, [pending, MagicMock(), MagicMock()])
        inst = VirtualPrinterInstance(
            vp_id=78,
            name="LatePlates",
            mode="queue",
            model="X1C",
            access_code="12345678",
            serial_suffix="391800078",
            base_dir=tmp_path,
            session_factory=factory,
            save_ams_mapping=True,
            target_printer_id=7,
        )
        inst._recent_queue_items["test.3mf"] = ([101, 102], time.monotonic())
        inst._recent_queue_item_reach["test.3mf"] = {101: 2, 102: 6}

        await inst.on_print_command(
            "test.3mf",
            {"command": "project_file", "file": "test.3mf", "ams_mapping": [0, 1, -1], "timelapse": True},
        )

        reached, short = (call.args[0] for call in db.execute.await_args_list[1:3])
        reached_params = dict(reached.compile().params)
        short_params = dict(short.compile().params)
        assert json.loads(reached_params["ams_mapping"]) == [0, 1, -1]
        assert [101] in reached_params.values()
        assert "ams_mapping" not in short_params and short_params["timelapse"] is True
        assert [102] in short_params.values()

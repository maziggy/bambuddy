"""#3736: the slots a slice's process settings print with.

The slice route used to read the support slots from the source 3MF only, so
a support slot picked in the slice dialog looked unused and was overwritten
with the plate's other filament before the slice.
"""

import contextlib
import json
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.library import _process_filament_slots, _run_slicer_with_fallback
from backend.app.core.printer_scope import ALL_PRINTERS
from backend.app.schemas.slicer import PresetRef, SliceRequest
from backend.app.utils.threemf_tools import process_slots_in_config


def _source(project_settings: dict | None) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        if project_settings is not None:
            zf.writestr("Metadata/project_settings.config", json.dumps(project_settings))
    return buf.getvalue()


FILE_NO_SUPPORT_SLOT = {"enable_support": "1", "support_filament": "0", "support_interface_filament": "0"}
FILE_PVA_SUPPORT = {"enable_support": "1", "support_filament": "2", "support_interface_filament": "2"}


class TestProcessFilamentSlots:
    def test_a_slot_picked_in_the_dialog_counts(self):
        process = json.dumps({"inherits": "0.20mm Standard", "support_interface_filament": "3"})

        assert _process_filament_slots(process, _source(FILE_NO_SUPPORT_SLOT)) == {3}

    def test_a_standard_preset_stub_keeps_the_files_support_slots(self):
        """A standard preset is an ``inherits`` stub without the support keys."""
        process = json.dumps({"inherits": "0.20mm Standard", "from": "system"})

        assert _process_filament_slots(process, _source(FILE_PVA_SUPPORT)) == {2}

    def test_supports_switched_off_in_the_dialog_drop_the_files_slot(self):
        process = json.dumps({"inherits": "0.20mm Standard", "enable_support": "0"})

        assert _process_filament_slots(process, _source(FILE_PVA_SUPPORT)) == set()

    def test_a_slot_set_back_to_zero_in_the_dialog_drops_it(self):
        process = json.dumps({"support_filament": "0", "support_interface_filament": "0"})

        assert _process_filament_slots(process, _source(FILE_PVA_SUPPORT)) == set()

    def test_supports_on_in_the_preset_without_a_file_config(self):
        process = json.dumps({"enable_support": "1", "support_filament": "4"})

        assert _process_filament_slots(process, _source(None)) == {4}

    def test_unreadable_process_json_falls_back_to_the_file(self):
        assert _process_filament_slots("not json", _source(FILE_PVA_SUPPORT)) is None


class TestProcessSlotsInConfig:
    def test_region_pickers_count_without_supports(self):
        cfg = {"enable_support": "0", "sparse_infill_filament_id": "2", "outer_wall_filament_id": 3}

        assert process_slots_in_config(cfg) == {2, 3}

    def test_support_slots_need_supports_on(self):
        assert process_slots_in_config({"enable_support": "0", "support_filament": "2"}) == set()
        assert process_slots_in_config({"support_filament": "2"}) == set()
        assert process_slots_in_config({"enable_support": "1", "support_filament": "2"}) == {2}

    def test_one_element_lists_are_read(self):
        cfg = {"enable_support": ["1"], "support_interface_filament": ["3"]}

        assert process_slots_in_config(cfg) == {3}

    def test_zero_and_junk_are_no_slot(self):
        cfg = {"enable_support": "1", "support_filament": "0", "support_interface_filament": "x"}

        assert process_slots_in_config(cfg) == set()


# --- The slice route: which filament profiles reach the slicer ---------------

PLA, PLA_2, PETG = "Bambu PLA Basic @BBL A1M", "Bambu PLA Matte @BBL A1M", "Bambu PETG HF @BBL A1M"


def _plate_uses_slot_1(project_settings: dict) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/project_settings.config", json.dumps(project_settings))
        zf.writestr(
            "Metadata/model_settings.config",
            '<?xml version="1.0" encoding="UTF-8"?>\n<config>'
            '<object id="1"><metadata key="extruder" value="1"/></object>'
            '<plate><metadata key="plater_id" value="1"/><model_instance>'
            '<metadata key="object_id" value="1"/><metadata key="instance_id" value="0"/>'
            '<metadata key="identify_id" value="1"/></model_instance></plate></config>',
        )
    return buf.getvalue()


async def _slice(source: bytes, **request_fields) -> tuple[list[str], set[int] | None]:
    """The filament profile names the slicer gets, and the slots the nozzle map plans for."""
    from backend.app.services import preset_resolver, slicer_api as slicer_api_module

    request = SliceRequest(
        printer_preset=PresetRef(source="standard", id="Bambu Lab A1 mini 0.4 nozzle"),
        process_preset=PresetRef(source="standard", id="0.20mm Standard @BBL A1M"),
        filament_presets=[PresetRef(source="standard", id=name) for name in (PLA, PLA_2, PETG)],
        plate=1,
        **request_fields,
    )
    service = MagicMock()
    service.close = AsyncMock()
    service.slice_with_profiles = AsyncMock(side_effect=slicer_api_module.SlicerApiServerError("stop here"))
    service.slice_without_profiles = AsyncMock(side_effect=slicer_api_module.SlicerApiServerError("stop here"))

    async def _setting(_db, key):
        return {"preferred_slicer": "bambu_studio", "bambu_studio_api_url": "http://sidecar:3000"}.get(key)

    async def _resolve(_db, _user, ref, slot):
        return preset_resolver._resolve_standard(ref, slot)

    with (
        patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(side_effect=_setting)),
        patch("backend.app.services.preset_resolver.resolve_preset_ref", new=AsyncMock(side_effect=_resolve)),
        patch("backend.app.services.slicer_3mf_convert.extract_source_printer_model", return_value=None),
        patch("backend.app.api.routes.library._resolve_target_printer_model", new=AsyncMock(return_value=None)),
        patch.object(slicer_api_module, "SlicerApiService", return_value=service),
        patch.object(slicer_api_module, "get_stall_timeout_seconds", new=AsyncMock(return_value=60.0)),
        patch("backend.app.services.filament_map.auto_filament_map", new=AsyncMock(return_value=None)) as plan,
        # The slicer stops the slice once the profiles are captured.
        contextlib.suppress(Exception),
    ):
        await _run_slicer_with_fallback(
            SimpleNamespace(get=AsyncMock(return_value=None)),
            model_bytes=source,
            model_filename="part.3mf",
            request=request,
            current_user_id=None,
            printer_scope=ALL_PRINTERS,
        )
    sent = service.slice_with_profiles.await_args.kwargs["filament_profile_jsons"]
    return [json.loads(item)["name"] for item in sent], plan.await_args.kwargs["used_slots"]


async def _sent_filaments(source: bytes, **request_fields) -> list[str]:
    return (await _slice(source, **request_fields))[0]


@pytest.mark.asyncio
class TestTheSlice:
    async def test_the_reporters_interface_slot_reaches_the_slicer(self):
        sent = await _sent_filaments(
            _plate_uses_slot_1(FILE_NO_SUPPORT_SLOT), process_overrides={"support_interface_filament": "3"}
        )

        assert sent == [PLA, PLA, PETG]

    async def test_without_the_pick_the_unused_slots_are_substituted(self):
        assert await _sent_filaments(_plate_uses_slot_1(FILE_NO_SUPPORT_SLOT)) == [PLA, PLA, PLA]

    async def test_supports_switched_off_in_the_dialog_substitute_the_files_slot(self):
        source = _plate_uses_slot_1({"enable_support": "1", "support_filament": "3", "support_interface_filament": "3"})

        assert await _sent_filaments(source) == [PLA, PLA, PETG]
        assert await _sent_filaments(source, process_overrides={"enable_support": "0"}) == [PLA, PLA, PLA]

    async def test_the_nozzle_map_plans_for_the_interface_slot(self):
        """A dual-nozzle printer puts the PETG interface on its own nozzle only
        when the map knows the slot is used."""
        _, used = await _slice(
            _plate_uses_slot_1(FILE_NO_SUPPORT_SLOT), process_overrides={"support_interface_filament": "3"}
        )

        assert used == {1, 3}

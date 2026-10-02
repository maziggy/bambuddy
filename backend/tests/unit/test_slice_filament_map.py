"""Dual-nozzle filament map for multi-colour slices.

Without a map the slicer CLI prints every filament from one extruder, so a
two-colour job on an H2D or X2D swaps filament at each colour change while the
second nozzle stays cold. Bambuddy works the map out from the spools loaded on
the target printer and sends it to the sidecar as ``filamentMap``.

The rule these tests hold it to: whenever the map cannot be worked out with
confidence, the slice goes out exactly as it did before the feature existed.
"""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.api.routes.library import _run_slicer_with_fallback
from backend.app.schemas.slicer import PresetRef, SliceRequest
from backend.app.services.filament_map import auto_filament_map, plan_filament_map
from backend.app.services.slicer_api import SlicerApiService

pytestmark = pytest.mark.unit

# Made-up profiles. The printer is a dual-nozzle machine whose AMS feeds the
# left extruder (MQTT id 1, slicer extruder 1) and whose external spool feeds
# the right one (MQTT id 0, slicer extruder 2).
DUAL_PRINTER = json.dumps(
    {
        "name": "Bambu Lab X2D 0.4 nozzle",
        "printer_model": "Bambu Lab X2D",
        "nozzle_diameter": ["0.4", "0.4"],
        "physical_extruder_map": ["1", "0"],
    }
)
# A leaf preset as the resolver hands it over: nothing but the inherits chain.
LEAF_PRINTER = json.dumps({"name": "Bambu Lab X2D 0.4 nozzle", "inherits": "fdm_bbl_3dp_002_common"})
SINGLE_PRINTER = json.dumps({"name": "Bambu Lab X1 Carbon 0.4 nozzle", "nozzle_diameter": ["0.4"]})


def _filament(name: str, colour: str) -> str:
    return json.dumps({"name": name, "filament_colour": [colour]})


WHITE = _filament("Test PLA White", "#FFFFFF")
GREEN = _filament("Test PLA Green", "#00AE42")
RED = _filament("Test PLA Red", "#C12E1F")

AMS_ONLY = [
    {"type": "PLA", "color": "#FFFFFFFF", "extruder_id": 1},
    {"type": "PLA", "color": "#00AE42FF", "extruder_id": 1},
]
GREEN_ON_EXTERNAL = [*AMS_ONLY[:1], {"type": "PLA", "color": "#00ae42ff", "extruder_id": 0}]
WHITE_ON_EXTERNAL = [*AMS_ONLY[1:], {"type": "PLA", "color": "#FFFFFF", "extruder_id": 0}]


class TestPlanFilamentMap:
    def test_the_colour_on_the_second_extruder_goes_to_nozzle_2(self):
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN], GREEN_ON_EXTERNAL) == [1, 2]

    def test_colour_matching_ignores_case_and_alpha(self):
        spools = [{"color": "00ae42", "extruder_id": 0}]
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN], spools) == [1, 2]

    def test_a_filament_after_the_first_is_preferred(self):
        # White and green are both on the external spool side; the base
        # (filament 1) stays on the main extruder.
        spools = [
            {"color": "#FFFFFF", "extruder_id": 0},
            {"color": "#00AE42", "extruder_id": 0},
        ]
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN, RED], spools) == [1, 2, 1]

    def test_the_first_filament_is_moved_when_it_is_the_only_match(self):
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN], WHITE_ON_EXTERNAL) == [2, 1]

    def test_the_profile_extruder_map_decides_which_side_is_second(self):
        # Reversed wiring: slicer extruder 2 is MQTT id 1.
        printer = json.dumps({"nozzle_diameter": ["0.4", "0.4"], "physical_extruder_map": ["0", "1"]})
        spools = [{"color": "#00AE42", "extruder_id": 1}]
        assert plan_filament_map(printer, [WHITE, GREEN], spools) == [1, 2]
        # Green on MQTT id 0 is the first extruder on this machine: nothing to move.
        assert plan_filament_map(printer, [WHITE, GREEN], [{"color": "#00AE42", "extruder_id": 0}]) is None

    def test_a_leaf_profile_uses_the_inherited_extruder_map(self):
        assert plan_filament_map(LEAF_PRINTER, [WHITE, GREEN], GREEN_ON_EXTERNAL) == [1, 2]

    def test_single_colour_is_left_alone(self):
        assert plan_filament_map(DUAL_PRINTER, [GREEN], GREEN_ON_EXTERNAL) is None

    def test_copies_of_one_filament_count_as_single_colour(self):
        # The slice route fills slots a plate does not print with a copy of
        # the plate's own filament; that is still a one-colour plate.
        assert plan_filament_map(DUAL_PRINTER, [GREEN, GREEN, GREEN], GREEN_ON_EXTERNAL) is None

    def test_a_copied_slot_is_never_the_one_moved(self):
        spools = [{"color": "#FFFFFF", "extruder_id": 0}]
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN, WHITE], spools) == [2, 1, 1]

    def test_single_nozzle_profile_is_left_alone(self):
        assert plan_filament_map(SINGLE_PRINTER, [WHITE, GREEN], GREEN_ON_EXTERNAL) is None

    def test_nothing_loaded_on_the_second_extruder(self):
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN], AMS_ONLY) is None
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN], []) is None

    def test_no_job_colour_on_the_second_extruder(self):
        spools = [*AMS_ONLY, {"color": "#123456", "extruder_id": 0}]
        assert plan_filament_map(DUAL_PRINTER, [WHITE, GREEN], spools) is None

    def test_filaments_without_a_colour_are_never_moved(self):
        blank = json.dumps({"name": "Test PLA"})
        assert plan_filament_map(DUAL_PRINTER, [WHITE, blank], GREEN_ON_EXTERNAL) is None

    def test_unreadable_input_is_left_alone(self):
        assert plan_filament_map("not json", [WHITE, GREEN], GREEN_ON_EXTERNAL) is None
        bad_map = json.dumps({"physical_extruder_map": ["left", "right"]})
        assert plan_filament_map(bad_map, [WHITE, GREEN], GREEN_ON_EXTERNAL) is None
        assert plan_filament_map(DUAL_PRINTER, ["not json", GREEN], [{"color": None, "extruder_id": 0}]) is None


class TestAutoFilamentMap:
    async def _run(self, model: str | None, spools=None, error: Exception | None = None):
        lookup = AsyncMock(return_value=spools or [], side_effect=error)
        with patch("backend.app.api.routes.printers.get_available_filaments", new=lookup):
            result = await auto_filament_map(
                object(),
                target_model=model,
                printer_json=DUAL_PRINTER,
                filament_jsons=[WHITE, GREEN],
            )
        return result, lookup

    async def test_looks_up_the_target_model_and_plans(self):
        result, lookup = await self._run("X2D", GREEN_ON_EXTERNAL)
        assert result == [1, 2]
        assert lookup.await_args.kwargs["model"] == "X2D"

    async def test_single_nozzle_model_does_not_look_anything_up(self):
        result, lookup = await self._run("X1C", GREEN_ON_EXTERNAL)
        assert result is None
        lookup.assert_not_awaited()

    async def test_unknown_model_does_not_look_anything_up(self):
        result, lookup = await self._run(None, GREEN_ON_EXTERNAL)
        assert result is None
        lookup.assert_not_awaited()

    async def test_nozzle_rack_model_is_left_alone(self):
        result, lookup = await self._run("H2C", GREEN_ON_EXTERNAL)
        assert result is None
        lookup.assert_not_awaited()

    async def test_a_failed_lookup_leaves_the_slice_unchanged(self):
        result, _ = await self._run("X2D", error=RuntimeError("printer offline"))
        assert result is None


class TestSliceRequestFilamentMap:
    def _request(self, **kwargs) -> SliceRequest:
        return SliceRequest(
            printer_preset=PresetRef(source="local", id="1"),
            process_preset=PresetRef(source="local", id="2"),
            filament_presets=[PresetRef(source="local", id="3"), PresetRef(source="local", id="4")],
            **kwargs,
        )

    def test_defaults_to_none(self):
        assert self._request().filament_map is None

    def test_accepts_one_extruder_per_filament(self):
        assert self._request(filament_map=[2, 1]).filament_map == [2, 1]

    def test_rejects_a_map_of_another_length(self):
        with pytest.raises(ValidationError):
            self._request(filament_map=[1])

    def test_rejects_extruder_zero(self):
        with pytest.raises(ValidationError):
            self._request(filament_map=[0, 1])


class TestSidecarPayload:
    async def _body(self, **kwargs) -> bytes:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(
                status_code=200,
                content=b"3MF",
                headers={"x-print-time-seconds": "0", "x-filament-used-g": "0", "x-filament-used-mm": "0"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0)
        service = SlicerApiService("http://sidecar:3000", client=client)
        await service.slice_with_profiles(
            model_bytes=b"x",
            model_filename="Cube.3mf",
            printer_profile_json="{}",
            process_profile_json="{}",
            filament_profile_jsons=["{}", "{}"],
            **kwargs,
        )
        return captured["body"]

    async def test_map_is_sent_as_filament_map(self):
        body = await self._body(filament_map=[1, 2])
        assert b'name="filamentMap"\r\n\r\n1,2\r\n' in body

    async def test_no_map_sends_no_field(self):
        assert b'name="filamentMap"' not in await self._body()
        assert b'name="filamentMap"' not in await self._body(filament_map=None)
        assert b'name="filamentMap"' not in await self._body(filament_map=[])


def _sliced_3mf() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
    return buffer.getvalue()


class TestSliceRouteWiring:
    """``_run_slicer_with_fallback`` sends the map, and only when it should."""

    def _request(self, **kwargs) -> SliceRequest:
        return SliceRequest(
            printer_preset=PresetRef(source="local", id="printer"),
            process_preset=PresetRef(source="local", id="process"),
            filament_presets=[PresetRef(source="local", id="white"), PresetRef(source="local", id="green")],
            filament_colours=["#FFFFFF", "#00AE42"],
            export_3mf=True,
            **kwargs,
        )

    async def _run(self, request: SliceRequest, *, printer_json=DUAL_PRINTER, lookup=None, slice_effect=None):
        from backend.app.services import slicer_api as slicer_api_module

        result = slicer_api_module.SliceResult(
            content=_sliced_3mf(), print_time_seconds=600, filament_used_g=12.0, filament_used_mm=4000.0
        )
        service = MagicMock()
        service.close = AsyncMock()
        service.slice_with_profiles = AsyncMock(return_value=result, side_effect=slice_effect)
        service.slice_without_profiles = AsyncMock(return_value=result)

        async def _setting(_db, key):
            return {"preferred_slicer": "orcaslicer", "orcaslicer_api_url": "http://sidecar:3000"}.get(key)

        async def _resolve(_db, _user, ref, slot):
            if slot == "printer":
                return printer_json
            if slot == "process":
                return json.dumps({"name": "process"})
            return json.dumps({"name": f"Test PLA {ref.id}"})

        if lookup is None:
            lookup = AsyncMock(return_value=GREEN_ON_EXTERNAL)

        with (
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(side_effect=_setting)),
            patch("backend.app.services.preset_resolver.resolve_preset_ref", new=AsyncMock(side_effect=_resolve)),
            patch("backend.app.api.routes.printers.get_available_filaments", new=lookup),
            patch.object(slicer_api_module, "SlicerApiService", return_value=service),
            patch.object(slicer_api_module, "get_stall_timeout_seconds", new=AsyncMock(return_value=60.0)),
        ):
            await _run_slicer_with_fallback(
                SimpleNamespace(get=AsyncMock(return_value=None)),
                model_bytes=b"solid cube\nendsolid cube\n",
                model_filename="cube.stl",
                request=request,
                current_user_id=None,
            )
        return service, lookup

    async def test_the_planned_map_is_sent(self):
        service, _ = await self._run(self._request())
        assert service.slice_with_profiles.await_args.kwargs["filament_map"] == [1, 2]

    async def test_an_explicit_map_is_not_overridden(self):
        service, lookup = await self._run(self._request(filament_map=[2, 1]))
        assert service.slice_with_profiles.await_args.kwargs["filament_map"] == [2, 1]
        lookup.assert_not_awaited()

    async def test_a_failed_lookup_sends_the_old_request(self):
        service, _ = await self._run(self._request(), lookup=AsyncMock(side_effect=RuntimeError("offline")))
        assert "filament_map" not in service.slice_with_profiles.await_args.kwargs

    async def test_a_single_nozzle_printer_sends_the_old_request(self):
        service, lookup = await self._run(self._request(), printer_json=SINGLE_PRINTER)
        assert "filament_map" not in service.slice_with_profiles.await_args.kwargs
        lookup.assert_not_awaited()

    async def test_a_refused_planned_map_is_retried_without_it(self):
        from backend.app.services.slicer_api import SlicerApiServerError, SliceResult

        ok = SliceResult(content=_sliced_3mf(), print_time_seconds=1, filament_used_g=1.0, filament_used_mm=1.0)
        service, _ = await self._run(
            self._request(),
            slice_effect=[SlicerApiServerError("unprintable area"), ok],
        )
        first, second = service.slice_with_profiles.await_args_list
        assert first.kwargs["filament_map"] == [1, 2]
        assert "filament_map" not in second.kwargs

    async def test_a_refused_explicit_map_is_not_retried(self):
        from backend.app.services.slicer_api import SlicerInputError

        with pytest.raises(HTTPException) as exc:
            await self._run(self._request(filament_map=[1, 2]), slice_effect=SlicerInputError("bad map"))
        assert exc.value.status_code == 400
        assert "bad map" in exc.value.detail

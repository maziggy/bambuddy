"""The nozzle flow a slice is cut for (#3136).

Sliced with profiles, the slicer CLI cuts every extruder for a Standard nozzle
unless ``--nozzle-volume-type`` says otherwise, so a P2S with a High Flow
nozzle warned at the start of every print that "the current nozzle setting
does not match the slicing file". Bambuddy now sends the flow: the caller's,
or the one the printers report.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.models.printer import Printer
from backend.app.schemas.slicer import PresetRef, SliceRequest
from backend.app.services import nozzle_volume_type as nvt
from backend.app.services.nozzle_volume_type import (
    FittedNozzle,
    auto_nozzle_volume_type,
    fitted_nozzles,
    flow_of,
    plan_nozzle_volume_type,
)
from backend.app.services.slicer_api import SlicerApiService
from backend.tests.unit.test_slice_filament_map import TestSliceRouteWiring as FilamentMapRouteWiring

pytestmark = pytest.mark.unit

P2S_08 = json.dumps({"name": "Bambu Lab P2S 0.8 nozzle", "printer_model": "Bambu Lab P2S", "nozzle_diameter": ["0.8"]})
H2D_04 = json.dumps(
    {
        "name": "Bambu Lab H2D 0.4 nozzle",
        "printer_model": "Bambu Lab H2D",
        "nozzle_diameter": ["0.4", "0.4"],
        "physical_extruder_map": ["1", "0"],
    }
)


def _status(*nozzles: tuple[str, str], connected: bool = True):
    return SimpleNamespace(
        connected=connected,
        nozzles=[SimpleNamespace(nozzle_type=t, nozzle_diameter=d) for t, d in nozzles],
    )


class TestFlowOf:
    @pytest.mark.parametrize(
        ("reported", "flow"),
        [
            ("HH01", "High Flow"),
            ("hh00", "High Flow"),
            ("HS01", "Standard"),
            # X1 / P1 report the material, which says nothing about flow.
            ("hardened_steel", None),
            ("stainless_steel", None),
            ("", None),
            (None, None),
        ],
    )
    def test_reads_the_flow_code(self, reported, flow):
        assert flow_of(reported) == flow


class TestFittedNozzles:
    def test_a_single_nozzle_printer_has_one(self):
        # The status always carries two entries.
        status = _status(("HH01", "0.8"), ("", ""))
        assert fitted_nozzles(status, 1) == [FittedNozzle(0, "0.8", "High Flow")]

    def test_a_dual_nozzle_printer_has_two_by_mqtt_id(self):
        status = _status(("HS01", "0.4"), ("HH01", "0.6"))
        assert fitted_nozzles(status, 2) == [FittedNozzle(0, "0.4", "Standard"), FittedNozzle(1, "0.6", "High Flow")]


class TestPlanNozzleVolumeType:
    def test_the_p2s_of_the_report(self):
        # Support package: P2S, nozzle type HH01, 0.8 mm.
        assert plan_nozzle_volume_type(P2S_08, [FittedNozzle(0, "0.8", "High Flow")]) == ["High Flow"]

    def test_a_nozzle_of_another_size_says_nothing(self):
        assert plan_nozzle_volume_type(P2S_08, [FittedNozzle(0, "0.4", "High Flow")]) is None

    def test_a_nozzle_that_does_not_state_its_flow_says_nothing(self):
        assert plan_nozzle_volume_type(P2S_08, [FittedNozzle(0, "0.8", None)]) is None

    def test_dual_nozzle_flows_follow_the_slicer_extruder_order(self):
        # Slicer extruder 1 is MQTT id 1 (left), extruder 2 is MQTT id 0.
        nozzles = [FittedNozzle(0, "0.4", "Standard"), FittedNozzle(1, "0.4", "High Flow")]
        assert plan_nozzle_volume_type(H2D_04, nozzles) == ["High Flow", "Standard"]

    def test_a_profile_without_its_extruder_map_uses_the_bambu_default(self):
        profile = json.loads(H2D_04)
        del profile["physical_extruder_map"]
        nozzles = [FittedNozzle(0, "0.4", "Standard"), FittedNozzle(1, "0.4", "High Flow")]
        assert plan_nozzle_volume_type(json.dumps(profile), nozzles) == ["High Flow", "Standard"]

    def test_both_nozzles_have_to_be_known(self):
        assert plan_nozzle_volume_type(H2D_04, [FittedNozzle(0, "0.4", "High Flow")]) is None

    @pytest.mark.parametrize("profile", ["not json", "[]", json.dumps({"name": "x"})])
    def test_an_unreadable_profile_says_nothing(self, profile):
        assert plan_nozzle_volume_type(profile, [FittedNozzle(0, "0.4", "High Flow")]) is None


class TestAutoNozzleVolumeType:
    @pytest.fixture
    def printers(self, monkeypatch):
        """Printers in the database and their live status, by id."""
        rows: list[Printer] = []
        statuses: dict[int, object] = {}
        monkeypatch.setattr(nvt.printer_manager, "get_status", lambda pid: statuses.get(pid))
        db = MagicMock()

        async def execute(query):
            # The real query sorts by name; hand the rows back that way.
            compiled = str(query)
            assert "ORDER BY printers.name" in compiled
            return MagicMock(scalars=lambda: MagicMock(all=lambda: sorted(rows, key=lambda p: p.name)))

        db.execute = execute

        def add(pid, name, status, model="P2S", nozzle_count=1):
            rows.append(Printer(id=pid, name=name, model=model, nozzle_count=nozzle_count, is_active=True))
            statuses[pid] = status

        return SimpleNamespace(db=db, add=add)

    async def _auto(self, printers, *, model="P2S", profile=P2S_08, allows=lambda _pid: True):
        return await auto_nozzle_volume_type(printers.db, target_model=model, printer_json=profile, allows=allows)

    async def test_the_first_printer_by_name_decides(self, printers):
        printers.add(1, "Workshop", _status(("HS01", "0.8")))
        printers.add(2, "Attic", _status(("HH01", "0.8")))
        assert await self._auto(printers) == ["High Flow"]

    async def test_a_printer_that_cannot_say_is_skipped(self, printers):
        printers.add(1, "Attic", _status(("HH01", "0.4")))  # another size
        printers.add(2, "Basement", _status(("HH01", "0.8"), connected=False))
        printers.add(3, "Cellar", _status(("hardened_steel", "0.8")))
        printers.add(4, "Workshop", _status(("HS01", "0.8")))
        assert await self._auto(printers) == ["Standard"]

    async def test_only_printers_the_caller_may_use(self, printers):
        printers.add(1, "Attic", _status(("HH01", "0.8")))
        printers.add(2, "Workshop", _status(("HS01", "0.8")))
        assert await self._auto(printers, allows=lambda pid: pid == 2) == ["Standard"]

    async def test_nothing_to_go_on_slices_as_before(self, printers):
        printers.add(1, "Cellar", _status(("hardened_steel", "0.8")))
        assert await self._auto(printers) is None

    @pytest.mark.parametrize("model", ["A1", "A1 Mini", "H2C", None])
    async def test_models_without_a_flow_choice_or_with_a_rack_are_left_alone(self, printers, model):
        printers.add(1, "Attic", _status(("HH01", "0.8"), ("HH01", "0.8")), model=model or "P2S", nozzle_count=2)
        assert await self._auto(printers, model=model) is None

    async def test_a_failing_lookup_slices_as_before(self):
        db = MagicMock(execute=AsyncMock(side_effect=RuntimeError("db gone")))
        assert (
            await auto_nozzle_volume_type(db, target_model="P2S", printer_json=P2S_08, allows=lambda _p: True) is None
        )


class TestSliceRequestNozzleVolumeType:
    def _request(self, **kwargs) -> SliceRequest:
        return SliceRequest(
            printer_preset=PresetRef(source="local", id="p"),
            process_preset=PresetRef(source="local", id="q"),
            filament_presets=[PresetRef(source="local", id="f")],
            **kwargs,
        )

    def test_takes_one_flow_per_extruder(self):
        assert self._request(nozzle_volume_type=["High Flow", "Standard"]).nozzle_volume_type == [
            "High Flow",
            "Standard",
        ]

    @pytest.mark.parametrize("bad", [[], ["HH01"], ["High Flow"] * 3, ["high flow"]])
    def test_refuses_anything_else(self, bad):
        with pytest.raises(ValidationError):
            self._request(nozzle_volume_type=bad)


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
            model_filename="Cube.stl",
            printer_profile_json="{}",
            process_profile_json="{}",
            filament_profile_jsons=["{}"],
            **kwargs,
        )
        return captured["body"]

    async def test_flows_are_sent_as_nozzle_volume_type(self):
        body = await self._body(nozzle_volume_type=["High Flow", "Standard"])
        assert b'name="nozzleVolumeType"\r\n\r\nHigh Flow,Standard\r\n' in body

    async def test_no_flow_sends_no_field(self):
        for kwargs in ({}, {"nozzle_volume_type": None}, {"nozzle_volume_type": []}):
            assert b'name="nozzleVolumeType"' not in await self._body(**kwargs)


class TestSliceRouteWiring:
    """``_run_slicer_with_fallback`` sends the flow, the caller's or the printers'."""

    # The filament map tests' harness: same route, same mocked sidecar.
    wiring = FilamentMapRouteWiring()

    def _request(self, **kwargs) -> SliceRequest:
        return SliceRequest(
            printer_preset=PresetRef(source="local", id="printer"),
            process_preset=PresetRef(source="local", id="process"),
            filament_presets=[PresetRef(source="local", id="white")],
            export_3mf=True,
            **kwargs,
        )

    async def _run(self, request, *, auto=None, printer_json=P2S_08, **kwargs):
        auto = auto if auto is not None else AsyncMock(return_value=["High Flow"])
        patches = (patch("backend.app.services.nozzle_volume_type.auto_nozzle_volume_type", new=auto),)
        service, _ = await self.wiring._run(request, printer_json=printer_json, extra_patches=patches, **kwargs)
        return service, auto

    async def test_the_printers_flow_is_sent(self):
        service, auto = await self._run(self._request(), target_printer_id=7)
        assert service.slice_with_profiles.await_args.kwargs["nozzle_volume_type"] == ["High Flow"]
        kwargs = auto.await_args.kwargs
        assert kwargs["target_model"] == "P2S"
        assert kwargs["printer_json"] == P2S_08
        # Read from the target printer only.
        assert kwargs["allows"](7) is True
        assert kwargs["allows"](8) is False

    async def test_the_callers_flow_is_used_as_given(self):
        service, auto = await self._run(self._request(nozzle_volume_type=["Standard"]))
        assert service.slice_with_profiles.await_args.kwargs["nozzle_volume_type"] == ["Standard"]
        auto.assert_not_awaited()

    async def test_a_flow_list_that_does_not_fit_the_profile_is_refused(self):
        with pytest.raises(HTTPException) as exc:
            await self._run(self._request(nozzle_volume_type=["High Flow", "Standard"]))
        assert exc.value.status_code == 400
        assert "1 extruder" in exc.value.detail
        self.wiring.service.slice_with_profiles.assert_not_awaited()

    async def test_nothing_known_slices_as_before(self):
        service, _ = await self._run(self._request(), auto=AsyncMock(return_value=None))
        assert service.slice_with_profiles.await_args.kwargs["nozzle_volume_type"] is None

    async def test_the_files_own_settings_keep_the_files_flow(self):
        from backend.tests.unit.test_slice_filament_map import _project_3mf

        service, auto = await self._run(
            self._request(use_embedded_settings=True, plate=1),
            model_bytes=_project_3mf(),
            model_filename="project.3mf",
        )
        auto.assert_not_awaited()
        service.slice_with_profiles.assert_not_awaited()

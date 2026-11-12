"""Integration tests for GET /api/v1/printers/available-filaments endpoint.

Tests that the endpoint returns deduplicated filaments with tray_sub_brands,
correctly distinguishing subtypes like "PLA Basic" vs "PLA Matte".
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


def _make_mock_status(ams_data: list, vt_tray: list | None = None, ams_extruder_map: dict | None = None) -> MagicMock:
    """Create a mock printer status with raw_data containing AMS info."""
    status = MagicMock()
    raw = {"ams": ams_data}
    if vt_tray is not None:
        raw["vt_tray"] = vt_tray
    if ams_extruder_map is not None:
        raw["ams_extruder_map"] = ams_extruder_map
    else:
        raw["ams_extruder_map"] = {}
    status.raw_data = raw
    return status


class TestAvailableFilaments:
    """Tests for /api/v1/printers/available-filaments endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_tray_sub_brands(self, async_client: AsyncClient, printer_factory):
        """Verify tray_sub_brands is included in the response."""
        await printer_factory(name="Test Printer", model="X1C")

        status = _make_mock_status(
            ams_data=[
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PLA",
                            "tray_color": "000000FF",
                            "tray_info_idx": "GFL99",
                            "tray_sub_brands": "PLA Basic",
                        },
                    ],
                },
            ]
        )

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status

            response = await async_client.get("/api/v1/printers/available-filaments?model=X1C")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["tray_sub_brands"] == "PLA Basic"
        assert data[0]["type"] == "PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_dedup_distinguishes_subtypes(self, async_client: AsyncClient, printer_factory):
        """PLA Basic Black and PLA Matte Black should be separate entries."""
        await printer_factory(name="Printer 1", model="X1C")

        status = _make_mock_status(
            ams_data=[
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PLA",
                            "tray_color": "000000FF",
                            "tray_info_idx": "GFL99",
                            "tray_sub_brands": "PLA Basic",
                        },
                        {
                            "id": 1,
                            "tray_type": "PLA",
                            "tray_color": "000000FF",
                            "tray_info_idx": "GFL05",
                            "tray_sub_brands": "PLA Matte",
                        },
                    ],
                },
            ]
        )

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status

            response = await async_client.get("/api/v1/printers/available-filaments?model=X1C")

        assert response.status_code == 200
        data = response.json()
        # Same type + color but different tray_sub_brands → 2 entries
        assert len(data) == 2
        sub_brands = {d["tray_sub_brands"] for d in data}
        assert sub_brands == {"PLA Basic", "PLA Matte"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_dedup_same_subtype_same_color(self, async_client: AsyncClient, printer_factory):
        """Same subtype + same color across two printers should be deduped to one entry."""
        await printer_factory(name="Printer 1", model="X1C")
        await printer_factory(name="Printer 2", model="X1C")

        status1 = _make_mock_status(
            ams_data=[
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PLA",
                            "tray_color": "FF0000FF",
                            "tray_info_idx": "GFL99",
                            "tray_sub_brands": "PLA Basic",
                        }
                    ],
                },
            ]
        )
        status2 = _make_mock_status(
            ams_data=[
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PLA",
                            "tray_color": "FF0000FF",
                            "tray_info_idx": "GFL99",
                            "tray_sub_brands": "PLA Basic",
                        }
                    ],
                },
            ]
        )

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.side_effect = [status1, status2]

            response = await async_client.get("/api/v1/printers/available-filaments?model=X1C")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_sub_brands_handled(self, async_client: AsyncClient, printer_factory):
        """Filaments with empty/missing tray_sub_brands should still be returned."""
        await printer_factory(name="Test Printer", model="X1C")

        status = _make_mock_status(
            ams_data=[
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "tray_info_idx": "GFL99"},
                    ],
                },
            ]
        )

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status

            response = await async_client.get("/api/v1/printers/available-filaments?model=X1C")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["tray_sub_brands"] == ""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_spool_includes_sub_brands(self, async_client: AsyncClient, printer_factory):
        """External spools (vt_tray) should also include tray_sub_brands."""
        await printer_factory(name="Test Printer", model="X1C")

        status = _make_mock_status(
            ams_data=[],
            vt_tray=[
                {
                    "id": 254,
                    "tray_type": "PETG",
                    "tray_color": "00FF00FF",
                    "tray_info_idx": "GFG00",
                    "tray_sub_brands": "PETG HF",
                },
            ],
        )

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status

            response = await async_client.get("/api/v1/printers/available-filaments?model=X1C")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["tray_sub_brands"] == "PETG HF"
        assert data[0]["type"] == "PETG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_printers_returns_empty(self, async_client: AsyncClient):
        """Verify empty list when no printers match the model."""
        response = await async_client.get("/api/v1/printers/available-filaments?model=X1C")

        assert response.status_code == 200
        assert response.json() == []


class TestExternalSpoolsOnADualNozzlePrinterWithoutAms:
    """``ams_extruder_map`` is derived from AMS info bits, so a dual-nozzle
    printer without an AMS reports an empty one. The route took that as
    "single nozzle": both external spools got ``extruder_id=None``, identical
    spools merged into one, and the override picker offered it for both
    nozzles. The scheduler fixed the same signal for #2771."""

    @staticmethod
    def _spool(vt_id: str) -> dict:
        return {"id": vt_id, "tray_type": "PETG", "tray_color": "000000FF", "tray_sub_brands": "PETG HF"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_second_nozzle_routes_each_external_spool(self, async_client: AsyncClient, printer_factory):
        from types import SimpleNamespace

        await printer_factory(name="H2D", model="H2D")
        status = _make_mock_status(ams_data=[], vt_tray=[self._spool("254"), self._spool("255")])
        status.nozzles = [SimpleNamespace(nozzle_diameter="0.4"), SimpleNamespace(nozzle_diameter="0.4")]

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status
            response = await async_client.get("/api/v1/printers/available-filaments?model=H2D")

        assert response.status_code == 200
        assert sorted(f["extruder_id"] for f in response.json()) == [0, 1]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_second_nozzle_alone_means_two_nozzles(self, async_client: AsyncClient, printer_factory):
        """A firmware revision that lists only the loaded external feed."""
        from types import SimpleNamespace

        await printer_factory(name="H2D", model="H2D")
        status = _make_mock_status(ams_data=[], vt_tray=[self._spool("254")])
        status.nozzles = [SimpleNamespace(nozzle_diameter="0.4"), SimpleNamespace(nozzle_diameter="0.4")]

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status
            response = await async_client.get("/api/v1/printers/available-filaments?model=H2D")

        assert [f["extruder_id"] for f in response.json()] == [1]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_two_external_feeds_alone_mean_two_nozzles(self, async_client: AsyncClient, printer_factory):
        await printer_factory(name="H2D", model="H2D")
        status = _make_mock_status(ams_data=[], vt_tray=[self._spool("254"), self._spool("255")])
        status.nozzles = []

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status
            response = await async_client.get("/api/v1/printers/available-filaments?model=H2D")

        assert sorted(f["extruder_id"] for f in response.json()) == [0, 1]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_single_nozzle_external_spool_has_no_extruder(self, async_client: AsyncClient, printer_factory):
        from types import SimpleNamespace

        await printer_factory(name="X1C", model="X1C")
        status = _make_mock_status(ams_data=[], vt_tray=[self._spool("254")])
        # The state always seeds two nozzle stubs; an empty second one is no nozzle.
        status.nozzles = [SimpleNamespace(nozzle_diameter="0.4"), SimpleNamespace(nozzle_diameter="")]

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status
            response = await async_client.get("/api/v1/printers/available-filaments?model=X1C")

        assert [f["extruder_id"] for f in response.json()] == [None]


class TestFilamentMapReadsLoadedSpools:
    """The slicer's filament map reads the spools without going through the route."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_map_planned_from_a_real_lookup(self, db_session, printer_factory):
        import json

        from backend.app.services.filament_map import auto_filament_map

        await printer_factory(name="Dual", model="X2D")
        # White in the AMS (left extruder), green on the external spool (right).
        status = _make_mock_status(
            ams_data=[{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FFFFFFFF"}]}],
            vt_tray=[{"id": 255, "tray_type": "PLA", "tray_color": "00AE42FF"}],
            ams_extruder_map={"0": 1},
        )
        printer = json.dumps(
            {
                "name": "Bambu Lab X2D 0.4 nozzle",
                "printer_model": "Bambu Lab X2D",
                "nozzle_diameter": ["0.4", "0.4"],
                "physical_extruder_map": ["1", "0"],
            }
        )
        white = json.dumps({"name": "Test PLA White", "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"]})
        green = json.dumps({"name": "Test PLA Green", "filament_type": ["PLA"], "filament_colour": ["#00AE42"]})

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = status
            result = await auto_filament_map(
                db_session,
                target_model="X2D",
                printer_json=printer,
                filament_jsons=[white, green],
                allows=lambda _printer_id: True,
            )

        assert result == [1, 2]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_printer_outside_the_scope_is_not_read(self, db_session, printer_factory):
        from backend.app.services.loaded_filaments import loaded_filaments

        ours = await printer_factory(name="Ours", model="X2D")
        theirs = await printer_factory(name="Theirs", model="X2D")
        statuses = {
            ours.id: _make_mock_status(ams_data=[{"id": 0, "tray": [{"tray_type": "PLA", "tray_color": "FFFFFFFF"}]}]),
            theirs.id: _make_mock_status(
                ams_data=[{"id": 0, "tray": [{"tray_type": "PLA", "tray_color": "00AE42FF"}]}]
            ),
        }

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.side_effect = statuses.get
            loaded = await loaded_filaments(db_session, "X2D", allows=lambda printer_id: printer_id == ours.id)

        assert [f["color"] for f in loaded] == ["#FFFFFFFF"]

    def test_the_scope_has_to_be_given(self):
        import inspect

        from backend.app.services.loaded_filaments import loaded_filaments

        allows = inspect.signature(loaded_filaments).parameters["allows"]
        assert allows.default is inspect.Parameter.empty
        assert allows.kind is inspect.Parameter.KEYWORD_ONLY


class TestAvailableFilamentsScope:
    """The route keeps to the caller's printers after moving into the service (#1727)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_member_sees_only_the_spools_of_their_printers(self, async_client: AsyncClient, printer_factory):
        from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _team_member

        ours = await printer_factory(name="Ours", model="X1C")
        theirs = await printer_factory(name="Theirs", model="X1C")
        admin = await _admin_token(async_client)
        member, _ = await _team_member(async_client, admin, "member", [ours.id])
        statuses = {
            ours.id: _make_mock_status(ams_data=[{"id": 0, "tray": [{"tray_type": "PLA", "tray_color": "FFFFFFFF"}]}]),
            theirs.id: _make_mock_status(
                ams_data=[{"id": 0, "tray": [{"tray_type": "PLA", "tray_color": "00AE42FF"}]}]
            ),
        }

        with patch("backend.app.services.loaded_filaments.printer_manager") as mock_pm:
            mock_pm.get_status.side_effect = statuses.get
            as_member = await async_client.get("/api/v1/printers/available-filaments?model=X1C", headers=_auth(member))
            as_admin = await async_client.get("/api/v1/printers/available-filaments?model=X1C", headers=_auth(admin))

        assert as_member.status_code == 200, as_member.text
        assert [f["color"] for f in as_member.json()] == ["#FFFFFFFF"]
        assert sorted(f["color"] for f in as_admin.json()) == ["#00AE42FF", "#FFFFFFFF"]

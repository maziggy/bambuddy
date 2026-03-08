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

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
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

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
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

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
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

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
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

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
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

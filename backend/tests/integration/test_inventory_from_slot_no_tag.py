"""No-tag guard on POST /api/v1/inventory/spools/from-slot.

A slot that has tray_type set but no RFID tag (generic third-party PLA the
user manually configured on the printer panel) has no stable identity, so
auto-creating an inventory row for it would just duplicate on every confirm
without ever re-linking to the physical spool. Verify the endpoint refuses
that case with 400.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


def _mock_status_for_tray(ams_id: int, tray_id: int, tray: dict) -> MagicMock:
    status = MagicMock()
    status.raw_data = {"ams": {"ams": [{"id": ams_id, "tray": [{"id": tray_id, **tray}]}]}}
    return status


@pytest.mark.asyncio
@pytest.mark.integration
async def test_from_slot_rejects_no_tag(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="X1C-no-tag")

    no_tag_tray = {
        "tray_type": "PLA",
        "tray_color": "FF0000FF",
        "tag_uid": "0000000000000000",
        "tray_uuid": "00000000000000000000000000000000",
    }

    with patch(
        "backend.app.services.printer_manager.printer_manager.get_status",
        return_value=_mock_status_for_tray(0, 1, no_tag_tray),
    ):
        resp = await async_client.post(
            "/api/v1/inventory/spools/from-slot",
            json={"printer_id": printer.id, "ams_id": 0, "tray_id": 1},
        )

    assert resp.status_code == 400
    assert "RFID tag" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_from_slot_rejects_empty_tag_strings(async_client: AsyncClient, printer_factory):
    """Empty strings (not zero-filled) must also be refused."""
    printer = await printer_factory(name="X1C-empty-tag")

    empty_tag_tray = {
        "tray_type": "PETG",
        "tray_color": "00FF00FF",
        "tag_uid": "",
        "tray_uuid": "",
    }

    with patch(
        "backend.app.services.printer_manager.printer_manager.get_status",
        return_value=_mock_status_for_tray(0, 2, empty_tag_tray),
    ):
        resp = await async_client.post(
            "/api/v1/inventory/spools/from-slot",
            json={"printer_id": printer.id, "ams_id": 0, "tray_id": 2},
        )

    assert resp.status_code == 400

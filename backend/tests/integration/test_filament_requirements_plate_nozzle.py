"""A plate's filament requirements name the nozzle that plate prints each slot on."""

from __future__ import annotations

import json
import zipfile
from unittest.mock import patch

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# Slot 1 prints on group 0 on plate 1 and on group 1 on plate 2.
SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <filament id="1" type="PLA" color="#FF0000" used_g="5.0" group_id="0"/>
  </plate>
  <plate>
    <metadata key="index" value="2"/>
    <filament id="1" type="PLA" color="#FF0000" used_g="7.0" group_id="1"/>
  </plate>
</config>"""


async def test_each_plate_gets_its_own_nozzle(async_client: AsyncClient, printer_factory, archive_factory, tmp_path):
    from backend.app.core.config import settings

    path = tmp_path / "two-plates.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", SLICE_INFO)
        zf.writestr("Metadata/project_settings.config", json.dumps({"physical_extruder_map": ["1", "0"]}))
    printer = await printer_factory(model="H2D")
    archive = await archive_factory(printer.id, file_path="two-plates.3mf")

    with patch.object(settings, "base_dir", tmp_path):
        nozzles = {}
        for plate in (1, 2):
            response = await async_client.get(
                f"/api/v1/archives/{archive.id}/filament-requirements", params={"plate_id": plate}
            )
            assert response.status_code == 200, response.text
            nozzles[plate] = [f["nozzle_id"] for f in response.json()["filaments"]]

    assert nozzles == {1: [1], 2: [0]}

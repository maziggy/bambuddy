"""Integration tests for the Slicer Pipelines API (#1425 PR A)."""

import pytest
from httpx import AsyncClient


def _preset_ref(source: str, id_: str) -> dict:
    return {"source": source, "id": id_}


def _payload(**overrides) -> dict:
    payload = {
        "name": "Production Batch",
        "description": "High speed PLA on X1C",
        "printer_preset": _preset_ref("local", "42"),
        "process_preset": _preset_ref("local", "7"),
        "filament_presets": [_preset_ref("local", "11"), _preset_ref("standard", "PLA Basic")],
        "bed_type": "Textured PEI Plate",
    }
    payload.update(overrides)
    return payload


class TestSlicerPipelinesAPI:
    """CRUD + edge cases for /api/v1/slicer-pipelines."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_empty(self, async_client: AsyncClient):
        """Empty list response uses the canonical {pipelines: []} envelope."""
        resp = await async_client.get("/api/v1/slicer-pipelines/")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"pipelines": []}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_and_list(self, async_client: AsyncClient):
        """A newly-created pipeline appears in the list with its full shape."""
        resp = await async_client.post("/api/v1/slicer-pipelines/", json=_payload())
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["name"] == "Production Batch"
        assert created["printer_preset"] == _preset_ref("local", "42")
        assert created["process_preset"] == _preset_ref("local", "7")
        assert created["filament_presets"] == [
            _preset_ref("local", "11"),
            _preset_ref("standard", "PLA Basic"),
        ]
        assert created["bed_type"] == "Textured PEI Plate"
        # PR A defaults persisted but not user-set
        assert created["target_kind"] == "printer_class"
        assert created["target_printer_id"] is None
        assert created["fanout_strategy"] == "max_parallel"

        list_resp = await async_client.get("/api/v1/slicer-pipelines/")
        assert list_resp.status_code == 200
        ids = [p["id"] for p in list_resp.json()["pipelines"]]
        assert created["id"] in ids

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_by_id(self, async_client: AsyncClient):
        """Round-trips the preset slots through JSON storage faithfully."""
        created = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload())).json()
        resp = await async_client.get(f"/api/v1/slicer-pipelines/{created['id']}")
        assert resp.status_code == 200
        fetched = resp.json()
        assert fetched["printer_preset"] == _preset_ref("local", "42")
        assert fetched["filament_presets"] == [
            _preset_ref("local", "11"),
            _preset_ref("standard", "PLA Basic"),
        ]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_not_found(self, async_client: AsyncClient):
        resp = await async_client.get("/api/v1/slicer-pipelines/99999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_partial(self, async_client: AsyncClient):
        """PUT writes only fields that are present; others stay unchanged."""
        created = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload())).json()
        resp = await async_client.put(
            f"/api/v1/slicer-pipelines/{created['id']}",
            json={"name": "Renamed", "bed_type": "Cool Plate"},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["name"] == "Renamed"
        assert updated["bed_type"] == "Cool Plate"
        # Untouched fields preserved
        assert updated["printer_preset"] == _preset_ref("local", "42")
        assert updated["filament_presets"] == created["filament_presets"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_filament_list_replaces_wholesale(self, async_client: AsyncClient):
        """Setting filament_presets replaces the entire list."""
        created = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload())).json()
        new_filaments = [_preset_ref("cloud", "PFUS1"), _preset_ref("cloud", "PFUS2"), _preset_ref("cloud", "PFUS3")]
        resp = await async_client.put(
            f"/api/v1/slicer-pipelines/{created['id']}",
            json={"filament_presets": new_filaments},
        )
        assert resp.status_code == 200
        assert resp.json()["filament_presets"] == new_filaments

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_is_soft(self, async_client: AsyncClient):
        """DELETE hides from list + GET-by-id but doesn't drop the row (PR B+
        run history must still resolve pipeline metadata)."""
        created = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload())).json()
        resp = await async_client.delete(f"/api/v1/slicer-pipelines/{created['id']}")
        assert resp.status_code == 204
        # Hidden from list
        list_resp = await async_client.get("/api/v1/slicer-pipelines/")
        assert created["id"] not in [p["id"] for p in list_resp.json()["pipelines"]]
        # Hidden from GET
        get_resp = await async_client.get(f"/api/v1/slicer-pipelines/{created['id']}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_not_found(self, async_client: AsyncClient):
        resp = await async_client.delete("/api/v1/slicer-pipelines/99999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_empty_filament_list(self, async_client: AsyncClient):
        """The schema requires at least one filament slot."""
        payload = _payload(filament_presets=[])
        resp = await async_client.post("/api/v1/slicer-pipelines/", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_invalid_preset_source(self, async_client: AsyncClient):
        """PresetRef.source is constrained to the four known tiers."""
        bad = _preset_ref("bogus_source", "1")
        payload = _payload(printer_preset=bad)
        resp = await async_client.post("/api/v1/slicer-pipelines/", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_orders_newest_first(self, async_client: AsyncClient):
        first = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload(name="First"))).json()
        second = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload(name="Second"))).json()
        third = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload(name="Third"))).json()
        listing = (await async_client.get("/api/v1/slicer-pipelines/")).json()["pipelines"]
        # Filter to the three we just made (DB may have other rows from other tests)
        ours = [p for p in listing if p["id"] in {first["id"], second["id"], third["id"]}]
        assert [p["name"] for p in ours] == ["Third", "Second", "First"]

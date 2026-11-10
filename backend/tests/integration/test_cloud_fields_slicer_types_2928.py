"""The profile editor's Material type follows the preferred slicer (#2928).

Bambu Studio and OrcaSlicer do not accept the same ``filament_type`` values
(Orca has SBS, Bambu Studio has TPU-AMS), so GET /cloud/fields/filament offers
those of the slicer chosen in Settings -> Slicer, and nothing else about the
field definitions changes.
"""

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from backend.app.api.routes.cloud import _load_fields
from backend.app.models.settings import Settings

DATA = Path(__file__).resolve().parents[2] / "app" / "data"


def _type_field(fields: dict) -> dict:
    return next(f for f in fields["fields"] if f["key"] == "filament_type")


def _values(fields: dict) -> list[str]:
    return [option["value"] for option in _type_field(fields)["options"]]


async def _prefer(db_session, slicer: str) -> None:
    """Set the preferred slicer, whether or not the setting exists yet."""
    changed = await db_session.execute(update(Settings).where(Settings.key == "preferred_slicer").values(value=slicer))
    if not changed.rowcount:
        db_session.add(Settings(key="preferred_slicer", value=slicer))
    await db_session.commit()


class TestFilamentFieldsFollowThePreferredSlicer:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bambu_studio_is_the_default(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/cloud/fields/filament")

        assert response.status_code == 200
        values = _values(response.json())
        assert "TPU-AMS" in values
        assert "SBS" not in values

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bambu_studio_offers_its_own_types(self, async_client: AsyncClient, db_session):
        await _prefer(db_session, "bambu_studio")

        values = _values((await async_client.get("/api/v1/cloud/fields/filament")).json())

        assert (
            values
            == json.loads((DATA / "slicer_filament_types.json").read_text(encoding="utf-8"))["bambu_studio"]["values"]
        )
        assert {"TPU-AMS", "ASA-AERO", "PE-CF"} <= set(values)
        assert {"SBS", "PA6-GF", "PEEK"}.isdisjoint(values)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_orcaslicer_offers_its_own_types(self, async_client: AsyncClient, db_session):
        await _prefer(db_session, "orcaslicer")

        values = _values((await async_client.get("/api/v1/cloud/fields/filament")).json())

        assert (
            values
            == json.loads((DATA / "slicer_filament_types.json").read_text(encoding="utf-8"))["orcaslicer"]["values"]
        )
        assert {"SBS", "PA6-GF", "PEEK"} <= set(values)
        assert "TPU-AMS" not in values

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_changing_the_slicer_changes_the_list_on_the_next_request(
        self, async_client: AsyncClient, db_session
    ):
        await _prefer(db_session, "orcaslicer")
        assert "SBS" in _values((await async_client.get("/api/v1/cloud/fields/filament")).json())

        await _prefer(db_session, "bambu_studio")

        assert "SBS" not in _values((await async_client.get("/api/v1/cloud/fields/filament")).json())

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_unknown_slicer_leaves_the_list_as_the_field_file_has_it(
        self, async_client: AsyncClient, db_session
    ):
        await _prefer(db_session, "someslicer")

        values = _values((await async_client.get("/api/v1/cloud/fields/filament")).json())

        assert values == _values(_load_fields("filament"))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_only_the_material_type_dropdown_changes(self, async_client: AsyncClient, db_session):
        await _prefer(db_session, "orcaslicer")

        served = (await async_client.get("/api/v1/cloud/fields/filament")).json()

        static = _load_fields("filament")
        assert [f["key"] for f in served["fields"]] == [f["key"] for f in static["fields"]]
        others = [
            (f, s) for f, s in zip(served["fields"], static["fields"], strict=True) if f["key"] != "filament_type"
        ]
        assert all(f == s for f, s in others)
        served_type = _type_field(served)
        static_type = _type_field(static)
        assert {k: v for k, v in served_type.items() if k != "options"} == {
            k: v for k, v in static_type.items() if k != "options"
        }

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_other_preset_types_are_not_touched(self, async_client: AsyncClient, db_session):
        await _prefer(db_session, "orcaslicer")

        for preset_type in ("process", "printer"):
            served = (await async_client.get(f"/api/v1/cloud/fields/{preset_type}")).json()
            assert served == _load_fields(preset_type)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_all_fields_route_follows_the_slicer_too(self, async_client: AsyncClient, db_session):
        await _prefer(db_session, "orcaslicer")

        served = (await async_client.get("/api/v1/cloud/fields")).json()

        assert "SBS" in _values(served["filament"])

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_serving_one_slicer_does_not_change_the_next(self, async_client: AsyncClient, db_session):
        """The field file is cached; the list swap must work on a copy."""
        await _prefer(db_session, "orcaslicer")
        await async_client.get("/api/v1/cloud/fields/filament")

        await _prefer(db_session, "someslicer")
        values = _values((await async_client.get("/api/v1/cloud/fields/filament")).json())

        assert "SBS" not in values


def test_the_lists_are_well_formed():
    data = json.loads((DATA / "slicer_filament_types.json").read_text(encoding="utf-8"))
    for slicer in ("bambu_studio", "orcaslicer"):
        values = data[slicer]["values"]
        assert len(values) == len(set(values)), f"{slicer} lists a type twice"
        assert "PLA" in values and "PETG" in values and "ABS" in values and "TPU" in values
        assert data[slicer]["source"].startswith("https://")

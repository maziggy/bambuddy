"""Inventory CSV export carries every spool field (#3324).

The columns beyond the importable ones are read from the Spool model, so a
column added in a later release is exported without touching the CSV module.
"""

import csv
import io

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect as sa_inspect

from backend.app.models.spool import Spool
from backend.app.services.spool_csv import _EXPORT_SKIP, CSV_COLUMNS, export_columns


def _parse(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    return header, [dict(zip(header, row, strict=True)) for row in reader]


class TestExportColumns:
    def test_the_importable_columns_lead_in_their_fixed_order(self):
        assert export_columns()[: len(CSV_COLUMNS)] == CSV_COLUMNS

    def test_every_column_of_the_model_is_exported(self):
        model_columns = {attr.key for attr in sa_inspect(Spool).column_attrs}

        assert set(export_columns()) == set(CSV_COLUMNS) | (model_columns - _EXPORT_SKIP)

    def test_no_column_is_written_twice(self):
        columns = export_columns()

        assert len(columns) == len(set(columns))


@pytest.mark.asyncio
class TestExportContent:
    async def test_fields_beyond_the_importable_ones_are_written(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        db_session.add(
            Spool(
                material="PLA",
                brand="Bambu Lab",
                color_name="Jade White",
                core_weight=212,
                weight_locked=True,
                tag_uid="04A1B2C3D4E5F6",
                tray_uuid="0123456789ABCDEF0123456789ABCDEF",
                slicer_filament="GFA00",
                data_origin="rfid_auto",
                tag_type="bambulab",
            )
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/inventory/spools/export")

        assert response.status_code == 200, response.text
        header, rows = _parse(response.text)
        assert header == export_columns()
        record = rows[0]
        assert record["core_weight"] == "212"
        assert record["weight_locked"] == "True"
        assert record["tag_uid"] == "04A1B2C3D4E5F6"
        assert record["tray_uuid"] == "0123456789ABCDEF0123456789ABCDEF"
        assert record["slicer_filament"] == "GFA00"
        assert record["data_origin"] == "rfid_auto"
        assert record["tag_type"] == "bambulab"
        assert record["id"] != ""
        assert record["created_at"] != ""

    async def test_archived_spools_are_still_left_out(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(Spool(material="PLA", color_name="Active"))
        db_session.add(Spool(material="PETG", color_name="Archived", archived_at=func.now()))
        await db_session.commit()

        response = await async_client.get("/api/v1/inventory/spools/export")

        _, rows = _parse(response.text)
        assert [r["color_name"] for r in rows] == ["Active"]
        assert "archived_at" not in response.text.splitlines()[0].split(",")

    async def test_formula_looking_values_in_the_new_columns_are_neutralised(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        db_session.add(Spool(material="PLA", slicer_filament_name="=HYPERLINK(1)"))
        await db_session.commit()

        response = await async_client.get("/api/v1/inventory/spools/export")

        _, rows = _parse(response.text)
        assert rows[0]["slicer_filament_name"] == "'=HYPERLINK(1)"


@pytest.mark.asyncio
class TestImportOfAFullExport:
    async def test_the_extra_columns_are_named_as_not_imported_not_as_unknown(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        db_session.add(Spool(material="PLA", brand="Polymaker", color_name="Teal", tag_uid="AABB"))
        await db_session.commit()
        exported = (await async_client.get("/api/v1/inventory/spools/export")).content

        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("inventory.csv", exported, "text/csv")},
        )

        assert response.status_code == 200, response.text
        warnings = response.json()["warnings"]
        assert not any(w.startswith("Ignoring unknown columns") for w in warnings)
        not_imported = [w for w in warnings if w.startswith("Export-only columns not imported")]
        assert len(not_imported) == 1
        assert "tag_uid" in not_imported[0]
        assert response.json()["valid_count"] == 1

    async def test_a_full_export_imports_back_as_before(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(
            Spool(material="PETG", brand="Prusament", color_name="Orange", label_weight=1000, weight_used=100)
        )
        await db_session.commit()
        exported = (await async_client.get("/api/v1/inventory/spools/export")).content

        response = await async_client.post(
            "/api/v1/inventory/spools/import",
            files={"file": ("inventory.csv", exported, "text/csv")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1
        count = (await db_session.execute(select(func.count()).select_from(Spool))).scalar_one()
        assert count == 2

    async def test_a_really_unknown_column_is_still_reported(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", b"material,bogus_column\nPLA,1\n", "text/csv")},
        )

        assert any("Ignoring unknown columns: bogus_column" in w for w in response.json()["warnings"])

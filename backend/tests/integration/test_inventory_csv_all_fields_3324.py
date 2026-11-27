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
        db_session.add(Spool(material="PLA", brand="Polymaker", color_name="Teal", location_id=None))
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
        assert "created_at" in not_imported[0]
        assert "tag_uid" not in not_imported[0]
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


@pytest.mark.asyncio
class TestTagFieldsAreImported:
    TAG = "04A1B2C3D4E5F6"
    UUID = "0123456789ABCDEF0123456789ABCDEF"

    async def test_a_row_keeps_its_tag_and_preset(self, async_client: AsyncClient, db_session: AsyncSession):
        csv_text = (
            "material,brand,tag_uid,tray_uuid,slicer_filament,slicer_filament_name,core_weight,data_origin,tag_type\n"
            f"PLA,Bambu Lab,{self.TAG},{self.UUID},GFA00,Bambu PLA Basic,212,rfid_auto,bambulab\n"
        )

        response = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("x.csv", csv_text.encode(), "text/csv")}
        )

        assert response.json()["created"] == 1, response.text
        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert (spool.tag_uid, spool.tray_uuid) == (self.TAG, self.UUID)
        assert (spool.slicer_filament, spool.slicer_filament_name) == ("GFA00", "Bambu PLA Basic")
        assert (spool.core_weight, spool.data_origin, spool.tag_type) == (212, "rfid_auto", "bambulab")

    async def test_a_bad_core_weight_is_a_row_error(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", b"material,core_weight\nPLA,heavy\n", "text/csv")},
        )

        row = response.json()["rows"][0]
        assert row["status"] == "error"
        assert "core_weight" in row["reason"]

    @pytest.mark.parametrize("field", ["slicer_filament", "slicer_filament_name", "data_origin", "tag_type"])
    async def test_a_value_longer_than_its_column_is_a_row_error(
        self, async_client: AsyncClient, db_session: AsyncSession, field: str
    ):
        """PostgreSQL would refuse it and fail the whole import; only this row fails."""
        limit = getattr(Spool.__table__.c[field].type, "length", None)
        csv_text = f"material,{field}\nPLA,{'A' * (limit + 1)}\nPETG,{'A' * limit}\n"

        response = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("x.csv", csv_text.encode(), "text/csv")}
        )

        body = response.json()
        assert body["created"] == 1, response.text
        assert (await db_session.execute(select(Spool.material))).scalars().all() == ["PETG"]

    async def test_a_tag_columns_file_has_no_unknown_column_warning(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", b"material,tag_uid,tray_uuid,tag_type\nPLA,AA,BB,bambulab\n", "text/csv")},
        )

        assert response.json()["warnings"] == []

    async def test_a_full_export_round_trips_the_tag(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(
            Spool(material="PLA", brand="Bambu Lab", color_name="Jade", tag_uid=self.TAG, tray_uuid=self.UUID)
        )
        await db_session.commit()
        exported = (await async_client.get("/api/v1/inventory/spools/export")).content
        await db_session.execute(Spool.__table__.delete())
        await db_session.commit()

        await async_client.post("/api/v1/inventory/spools/import", files={"file": ("x.csv", exported, "text/csv")})

        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert (spool.tag_uid, spool.tray_uuid) == (self.TAG, self.UUID)

    @pytest.mark.parametrize(
        "written,stored",
        [("04a1b2c3d4e5f6", "04A1B2C3D4E5F6"), ("04:A1:B2:C3:D4:E5:F6", "04A1B2C3D4E5F6"), (" 04-a1-b2 ", "04A1B2")],
    )
    async def test_a_tag_uid_is_stored_as_the_printer_reports_it(
        self, async_client: AsyncClient, db_session: AsyncSession, written, stored
    ):
        csv_text = f'material,tag_uid,tray_uuid\nPLA,"{written}",{self.UUID.lower()}\n'

        response = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("x.csv", csv_text.encode(), "text/csv")}
        )

        assert response.json()["created"] == 1, response.text
        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert (spool.tag_uid, spool.tray_uuid) == (stored, self.UUID)

    @pytest.mark.parametrize("value", ["xyz", "::", "0000000000000000"])
    async def test_a_tag_without_an_id_is_none(self, async_client: AsyncClient, db_session: AsyncSession, value):
        csv_text = f"material,tag_uid,tray_uuid\nPLA,{value},{value}\n"

        response = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("x.csv", csv_text.encode(), "text/csv")}
        )

        assert response.json()["created"] == 1, response.text
        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert (spool.tag_uid, spool.tray_uuid) == (None, None)

    @pytest.mark.parametrize(
        "column,value", [("tray_uuid", UUID.lower()), ("tag_uid", TAG.lower()), ("tag_uid", "04:a1:b2:c3:d4:e5:f6")]
    )
    async def test_a_tag_linked_to_an_active_spool_is_a_row_error(
        self, async_client: AsyncClient, db_session: AsyncSession, column, value
    ):
        """Two active spools on one tag make the AMS pick either (#3110)."""
        db_session.add(
            Spool(material="PLA", brand="Bambu Lab", color_name="Jade", tag_uid=self.TAG, tray_uuid=self.UUID)
        )
        await db_session.commit()
        csv_text = f'material,color_name,{column}\nPETG,Other name,"{value}"\nABS,Free,\n'

        response = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("x.csv", csv_text.encode(), "text/csv")}
        )

        assert response.json()["created"] == 1, response.text
        materials = (await db_session.execute(select(Spool.material).order_by(Spool.id))).scalars().all()
        assert materials == ["PLA", "ABS"]

    async def test_the_preview_names_the_spool_that_has_the_tag(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        holder = Spool(material="PLA", tag_uid=self.TAG)
        db_session.add(holder)
        await db_session.commit()

        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", f"material,tag_uid\nPETG,{self.TAG}\n".encode(), "text/csv")},
        )

        row = response.json()["rows"][0]
        assert row["status"] == "error"
        assert row["reason"] == f"tag_uid {self.TAG} is already linked to spool #{holder.id}"

    async def test_a_tag_on_an_archived_spool_does_not_count(self, async_client: AsyncClient, db_session: AsyncSession):
        from datetime import datetime, timezone

        db_session.add(Spool(material="PLA", tag_uid=self.TAG, archived_at=datetime.now(timezone.utc)))
        await db_session.commit()

        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", f"material,tag_uid\nPETG,{self.TAG}\n".encode(), "text/csv")},
        )

        assert response.json()["rows"][0]["status"] == "valid"

    async def test_a_tag_repeated_from_an_earlier_row_is_a_row_error(self, async_client: AsyncClient):
        csv_text = f"material,tray_uuid\nPLA,{self.UUID}\nPETG,{self.UUID.lower()}\nABS,\n"

        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", csv_text.encode(), "text/csv")},
        )

        rows = response.json()["rows"]
        assert [r["status"] for r in rows] == ["valid", "error", "valid"]
        assert rows[1]["reason"] == f"tray_uuid {self.UUID} is already on row {rows[0]['row_number']}"

    async def test_a_row_that_failed_does_not_claim_its_tag(self, async_client: AsyncClient):
        csv_text = f"material,tag_uid,core_weight\nPLA,{self.TAG},heavy\nPETG,{self.TAG},\n"

        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", csv_text.encode(), "text/csv")},
        )

        assert [r["status"] for r in response.json()["rows"]] == ["error", "valid"]

    async def test_an_empty_or_all_zero_tag_identifies_nothing(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        db_session.add(Spool(material="PLA", color_name="A", tag_uid="00000000000000"))
        await db_session.commit()
        csv_text = "material,color_name,tag_uid\nPETG,B,00000000000000\nABS,C,\nASA,D,00000000000000\n"

        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", csv_text.encode(), "text/csv")},
        )

        assert [r["status"] for r in response.json()["rows"]] == ["valid", "valid", "valid"]


@pytest.mark.asyncio
class TestWeightLockIsImported:
    """A stated weight_locked is kept; without one an entered used weight locks."""

    async def _import(self, async_client: AsyncClient, csv_text: str):
        return await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("x.csv", csv_text.encode(), "text/csv")}
        )

    @pytest.mark.parametrize(("cell", "locked"), [("False", False), ("True", True), ("no", False), ("1", True)])
    async def test_a_stated_lock_is_kept(self, async_client: AsyncClient, db_session: AsyncSession, cell, locked):
        response = await self._import(async_client, f"material,weight_used,weight_locked\nPLA,300,{cell}\n")

        assert response.json()["created"] == 1, response.text
        assert (await db_session.execute(select(Spool.weight_locked))).scalar_one() is locked

    @pytest.mark.parametrize(
        "csv_text", ["material,weight_used\nPLA,300\n", "material,weight_used,weight_locked\nPLA,300,\n"]
    )
    async def test_without_a_stated_lock_a_used_weight_locks(
        self, async_client: AsyncClient, db_session: AsyncSession, csv_text
    ):
        response = await self._import(async_client, csv_text)

        assert response.json()["created"] == 1, response.text
        assert (await db_session.execute(select(Spool.weight_locked))).scalar_one() is True

    async def test_an_unlocked_spool_round_trips_unlocked(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(Spool(material="PLA", weight_used=300, weight_locked=False))
        await db_session.commit()
        exported = (await async_client.get("/api/v1/inventory/spools/export")).content

        response = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("x.csv", exported, "text/csv")}
        )

        assert response.json()["created"] == 1, response.text
        assert (await db_session.execute(select(Spool.weight_locked).order_by(Spool.id))).scalars().all() == [
            False,
            False,
        ]

    async def test_a_bad_lock_value_is_a_row_error(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", b"material,weight_locked\nPLA,maybe\n", "text/csv")},
        )

        row = response.json()["rows"][0]
        assert (row["status"], row["reason"]) == ("error", "weight_locked must be true or false (got 'maybe')")


@pytest.mark.asyncio
class TestAnImportedTagLeavesArchivedSpools:
    """Like link-tag's tag recycling: an archived spool gives up a tag that is imported."""

    TAG = "04A1B2C3D4E5F6"
    UUID = "0123456789ABCDEF0123456789ABCDEF"

    async def _archived(self, db_session: AsyncSession, **tags) -> Spool:
        from datetime import datetime, timezone

        spool = Spool(material="PLA", archived_at=datetime.now(timezone.utc), **tags)
        db_session.add(spool)
        await db_session.commit()
        return spool

    @pytest.mark.parametrize("field", ["tag_uid", "tray_uuid"])
    async def test_the_archived_spool_loses_the_tag(self, async_client: AsyncClient, db_session: AsyncSession, field):
        value = self.TAG if field == "tag_uid" else self.UUID
        old = await self._archived(db_session, tag_uid=self.TAG.lower(), tray_uuid=self.UUID.lower())

        response = await async_client.post(
            "/api/v1/inventory/spools/import",
            files={"file": ("x.csv", f"material,{field}\nPETG,{value}\n".encode(), "text/csv")},
        )

        assert response.json()["created"] == 1, response.text
        await db_session.refresh(old)
        other = "tray_uuid" if field == "tag_uid" else "tag_uid"
        assert getattr(old, field) is None
        assert getattr(old, other) is not None
        holders = (await db_session.execute(select(Spool.id).where(getattr(Spool, field) == value))).scalars().all()
        assert len(holders) == 1 and holders[0] != old.id

    async def test_a_dry_run_leaves_the_archived_spool_alone(self, async_client: AsyncClient, db_session: AsyncSession):
        old = await self._archived(db_session, tag_uid=self.TAG)

        await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("x.csv", f"material,tag_uid\nPETG,{self.TAG}\n".encode(), "text/csv")},
        )

        await db_session.refresh(old)
        assert old.tag_uid == self.TAG

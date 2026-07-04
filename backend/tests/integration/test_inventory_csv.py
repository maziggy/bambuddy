"""Integration tests for inventory CSV import/export (#1576).

Covers the export → import round-trip, dry-run preview (no writes), real
import (only valid rows persisted, atomically), and Color Catalog resolution
of brand + color_name → rgba.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.spool import Spool


def _csv_upload(text: str):
    """Build the multipart `files=` payload for the import endpoint."""
    return {"file": ("inventory.csv", text.encode("utf-8"), "text/csv")}


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvExport:
    async def test_export_returns_csv_with_header_and_rows(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(
            Spool(material="PLA", brand="Polymaker", color_name="Jade White", rgba="e8e8e8ff", label_weight=1000)
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/inventory/spools/export")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        body = response.text
        lines = body.strip().splitlines()
        # Header row uses the fixed schema.
        assert lines[0].split(",")[0] == "material"
        assert "rgba" in lines[0]
        # Data row present, rgba written without leading '#'.
        assert "Polymaker" in body
        assert "e8e8e8ff" in body
        assert "#e8e8e8ff" not in body

    async def test_export_excludes_archived(self, async_client: AsyncClient, db_session: AsyncSession):
        from datetime import datetime, timezone

        db_session.add(Spool(material="PLA", brand="Active", color_name="A", rgba="ffffffff"))
        db_session.add(
            Spool(
                material="PETG",
                brand="Archived",
                color_name="B",
                rgba="000000ff",
                archived_at=datetime.now(timezone.utc),
            )
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/inventory/spools/export")

        assert response.status_code == 200, response.text
        assert "Active" in response.text
        assert "Archived" not in response.text


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvImportDryRun:
    async def test_dry_run_classifies_rows_and_writes_nothing(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        csv_text = (
            "material,brand,color_name,rgba,label_weight\n"
            "PLA,Polymaker,Jade White,e8e8e8ff,1000\n"  # valid
            ",Polymaker,No Material,ffffffff,1000\n"  # error: material missing
            "PETG,Brand,Bad Hex,zzzz,1000\n"  # error: invalid rgba
            "\n"  # skipped: blank
            "ABS,Brand,Color,#00ff00,500\n"  # valid: 6-char + '#' tolerated
        )

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["valid_count"] == 2
        assert data["error_count"] == 2
        assert data["skipped_count"] == 1
        # 6-char hex got normalised to 8-char.
        valid_rows = [r for r in data["rows"] if r["status"] == "valid"]
        green = next(r for r in valid_rows if r["color_name"] == "Color")
        assert green["rgba"] == "00ff00ff"

        # Nothing was written.
        result = await db_session.execute(select(Spool))
        assert result.scalars().first() is None

    async def test_missing_material_column_fails_whole_file(self, async_client: AsyncClient):
        csv_text = "brand,color_name\nPolymaker,Jade White\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["valid_count"] == 0
        assert any("material" in w for w in data["warnings"])


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvImportReal:
    async def test_import_persists_only_valid_rows(self, async_client: AsyncClient, db_session: AsyncSession):
        csv_text = (
            "material,brand,color_name,rgba\n"
            "PLA,Polymaker,White,ffffffff\n"  # valid
            ",Polymaker,No Material,ffffffff\n"  # error
            "PETG,Brand,Color,ff0000ff\n"  # valid
        )

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["created"] == 2
        assert data["errors"] == 1
        assert len(data["error_rows"]) == 1

        result = await db_session.execute(select(Spool).order_by(Spool.material))
        spools = result.scalars().all()
        assert len(spools) == 2
        assert {s.material for s in spools} == {"PLA", "PETG"}

    async def test_case_and_space_tolerant_headers(self, async_client: AsyncClient, db_session: AsyncSession):
        csv_text = "Material, Color Name ,RGBA\nPLA,Snow,ffffffff\n"

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1
        result = await db_session.execute(select(Spool))
        spool = result.scalars().one()
        assert spool.color_name == "Snow"


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvColorResolution:
    async def test_brand_and_color_resolve_rgba_from_catalog(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(
            ColorCatalogEntry(
                manufacturer="Polymaker",
                color_name="Jade White",
                hex_color="#E8E8E8",
                material="PLA",
                is_default=False,
            )
        )
        await db_session.commit()

        # No rgba in CSV — resolved from catalog (case-insensitive match).
        csv_text = "material,brand,color_name\nPLA,polymaker,jade white\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["valid_count"] == 1
        row = data["rows"][0]
        assert row["resolved_color"] is True
        assert row["rgba"] == "e8e8e8ff"

    async def test_explicit_rgba_wins_over_catalog(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(
            ColorCatalogEntry(manufacturer="Polymaker", color_name="Jade White", hex_color="#E8E8E8", material="PLA")
        )
        await db_session.commit()

        csv_text = "material,brand,color_name,rgba\nPLA,Polymaker,Jade White,123456ff\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        row = response.json()["rows"][0]
        assert row["rgba"] == "123456ff"
        assert row["resolved_color"] is False

    async def test_cross_material_fallback_is_flagged(self, async_client: AsyncClient, db_session: AsyncSession):
        # Catalog only has a PLA variant of this colour; a PETG row resolves it
        # via cross-material fallback and must be flagged.
        db_session.add(
            ColorCatalogEntry(manufacturer="Polymaker", color_name="Jade White", hex_color="#E8E8E8", material="PLA")
        )
        await db_session.commit()

        csv_text = "material,brand,color_name\nPETG,Polymaker,Jade White\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        row = response.json()["rows"][0]
        assert row["resolved_color"] is True
        assert row["cross_material_color"] is True
        assert row["rgba"] == "e8e8e8ff"

    async def test_exact_material_match_not_flagged(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(
            ColorCatalogEntry(manufacturer="Polymaker", color_name="Jade White", hex_color="#E8E8E8", material="PETG")
        )
        await db_session.commit()

        csv_text = "material,brand,color_name\nPETG,Polymaker,Jade White\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        row = response.json()["rows"][0]
        assert row["resolved_color"] is True
        assert row["cross_material_color"] is False

    async def test_generic_material_catalog_entry_not_flagged(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        # A NULL-material catalog entry is the project's "matches any material"
        # convention — resolving a PLA row from it is an exact match, not a
        # cross-material fallback, so it must not raise the yellow warning.
        db_session.add(
            ColorCatalogEntry(manufacturer="Polymaker", color_name="Jade White", hex_color="#E8E8E8", material=None)
        )
        await db_session.commit()

        csv_text = "material,brand,color_name\nPLA,Polymaker,Jade White\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        row = response.json()["rows"][0]
        assert row["resolved_color"] is True
        assert row["cross_material_color"] is False
        assert row["rgba"] == "e8e8e8ff"


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvReviewFollowups:
    """Covers the maintainer-requested hardening (PR #1659 review)."""

    async def test_oversized_upload_rejected_413(self, async_client: AsyncClient):
        # Build a body just over the 5 MB cap.
        from backend.app.services.spool_csv import MAX_CSV_IMPORT_BYTES

        header = "material\n"
        filler = "PLA\n" * ((MAX_CSV_IMPORT_BYTES // 4) + 10)
        big = header + filler

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(big))

        assert response.status_code == 413, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "csv_import_too_large"

    async def test_weight_used_negative_is_error(self, async_client: AsyncClient):
        csv_text = "material,color_name,rgba,weight_used\nPLA,X,ffffffff,-5\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["error_count"] == 1
        assert "weight_used" in data["rows"][0]["reason"]

    async def test_weight_used_exceeds_label_is_error(self, async_client: AsyncClient):
        csv_text = "material,color_name,rgba,label_weight,weight_used\nPLA,X,ffffffff,1000,1500\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["error_count"] == 1
        assert "exceeds" in data["rows"][0]["reason"]

    async def test_export_neutralises_formula_injection(self, async_client: AsyncClient, db_session: AsyncSession):
        # A note starting with '=' must be prefixed with a quote on export so
        # spreadsheets don't evaluate it as a formula.
        db_session.add(Spool(material="PLA", color_name="X", rgba="ffffffff", note="=SUM(A1:A9)"))
        await db_session.commit()

        response = await async_client.get("/api/v1/inventory/spools/export")

        assert response.status_code == 200, response.text
        assert "'=SUM(A1:A9)" in response.text

    async def test_formula_injection_round_trips_without_quote_accumulation(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        # The export quote-guard must be undone on import so a formula-looking
        # note survives export → import unchanged (no accumulating leading ').
        db_session.add(Spool(material="PLA", color_name="X", rgba="ffffffff", note="=SUM(A1)"))
        await db_session.commit()

        export = await async_client.get("/api/v1/inventory/spools/export")
        assert export.status_code == 200, export.text
        assert "'=SUM(A1)" in export.text  # guarded on export

        # Wipe, re-import the exact export, and confirm the note is restored
        # to its original value (not "'=SUM(A1)").
        existing = await db_session.execute(select(Spool))
        for spool in existing.scalars().all():
            await db_session.delete(spool)
        await db_session.commit()

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(export.text))
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1

        result = await db_session.execute(select(Spool))
        spool = result.scalars().one()
        assert spool.note == "=SUM(A1)"  # original value, no leading quote

    async def test_export_filename_is_date_stamped(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/inventory/spools/export")

        assert response.status_code == 200, response.text
        disposition = response.headers.get("content-disposition", "")
        assert "bambuddy_inventory_" in disposition
        assert disposition.rstrip('"').endswith(".csv")


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvRoundTrip:
    async def test_export_then_import_recreates_spools(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(
            Spool(
                material="PLA",
                brand="Polymaker",
                subtype="Matte",
                color_name="Jade White",
                rgba="e8e8e8ff",
                label_weight=1000,
                weight_used=250,
                cost_per_kg=24.99,
                note="batch order",
            )
        )
        await db_session.commit()

        export = await async_client.get("/api/v1/inventory/spools/export")
        assert export.status_code == 200, export.text
        csv_text = export.text

        # Wipe and re-import the exact export.
        existing = await db_session.execute(select(Spool))
        for spool in existing.scalars().all():
            await db_session.delete(spool)
        await db_session.commit()

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1

        result = await db_session.execute(select(Spool))
        spool = result.scalars().one()
        assert spool.material == "PLA"
        assert spool.brand == "Polymaker"
        assert spool.subtype == "Matte"
        assert spool.color_name == "Jade White"
        assert spool.rgba == "e8e8e8ff"
        assert spool.label_weight == 1000
        assert spool.weight_used == 250  # usage round-trips
        assert spool.cost_per_kg == 24.99
        assert spool.note == "batch order"


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvUsageColumns:
    async def test_export_writes_weight_used_and_derived_remaining(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        from datetime import datetime, timezone

        db_session.add(
            Spool(
                material="PLA",
                brand="Polymaker",
                color_name="White",
                rgba="ffffffff",
                label_weight=1000,
                weight_used=300,
                last_used=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
            )
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/inventory/spools/export")

        assert response.status_code == 200, response.text
        header, row = response.text.strip().splitlines()[:2]
        cols = header.split(",")
        cells = row.split(",")
        record = dict(zip(cols, cells, strict=False))
        assert record["weight_used"] == "300"
        assert record["remaining"] == "700"  # 1000 - 300, derived
        assert record["last_used"].startswith("2026-06-01T12:30")

    async def test_import_reads_weight_used_ignores_remaining(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        # remaining is intentionally contradictory — it must be ignored; only
        # weight_used is read back.
        csv_text = (
            "material,brand,color_name,rgba,label_weight,weight_used,remaining\nPLA,Brand,White,ffffffff,1000,400,999\n"
        )

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1
        result = await db_session.execute(select(Spool))
        spool = result.scalars().one()
        assert spool.weight_used == 400  # from CSV
        assert spool.label_weight == 1000  # remaining=999 ignored, not used to back-compute

    async def test_import_parses_last_used_iso(self, async_client: AsyncClient, db_session: AsyncSession):
        csv_text = "material,color_name,rgba,last_used\nPLA,White,ffffffff,2026-06-01T12:30:00+00:00\n"

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1
        result = await db_session.execute(select(Spool))
        spool = result.scalars().one()
        assert spool.last_used is not None
        assert spool.last_used.year == 2026 and spool.last_used.month == 6 and spool.last_used.day == 1

    async def test_import_rejects_bad_last_used(self, async_client: AsyncClient):
        csv_text = "material,color_name,rgba,last_used\nPLA,White,ffffffff,not-a-date\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["error_count"] == 1
        assert "last_used" in data["rows"][0]["reason"]


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvExtraColumns:
    async def test_storage_category_threshold_round_trip(self, async_client: AsyncClient, db_session: AsyncSession):
        # storage_location / category / low_stock_threshold_pct must survive an
        # export → import cycle (would otherwise be silently lost).
        db_session.add(
            Spool(
                material="PLA",
                brand="Polymaker",
                color_name="White",
                rgba="ffffffff",
                storage_location="Shelf B3",
                category="Production",
                low_stock_threshold_pct=20,
            )
        )
        await db_session.commit()

        csv_text = (await async_client.get("/api/v1/inventory/spools/export")).text
        for spool in (await db_session.execute(select(Spool))).scalars().all():
            await db_session.delete(spool)
        await db_session.commit()

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1

        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert spool.storage_location == "Shelf B3"
        assert spool.category == "Production"
        assert spool.low_stock_threshold_pct == 20

    async def test_low_stock_threshold_out_of_range_is_error(self, async_client: AsyncClient):
        # SpoolCreate bounds low_stock_threshold_pct to 1..99; the CSV path must
        # reject an out-of-range value rather than persist it.
        csv_text = "material,color_name,rgba,low_stock_threshold_pct\nPLA,White,ffffffff,150\n"

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["error_count"] == 1
        assert "low_stock_threshold_pct" in data["rows"][0]["reason"]

    async def test_barcode_round_trip(self, async_client: AsyncClient, db_session: AsyncSession):
        # barcode must survive an export → import cycle, canonicalized the
        # same way a manual form edit or a scan would be (no leading zeros).
        db_session.add(
            Spool(
                material="PLA",
                brand="Sunlu",
                color_name="Black",
                rgba="000000ff",
                barcode="6938936716785",
            )
        )
        await db_session.commit()

        csv_text = (await async_client.get("/api/v1/inventory/spools/export")).text
        assert "barcode" in csv_text.splitlines()[0]
        assert "6938936716785" in csv_text

        for spool in (await db_session.execute(select(Spool))).scalars().all():
            await db_session.delete(spool)
        await db_session.commit()

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1

        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert spool.barcode == "6938936716785"

    async def test_barcode_is_canonicalized_on_import(self, async_client: AsyncClient, db_session: AsyncSession):
        # A leading-zero EAN-13 typed straight into the CSV must normalize to
        # the same canonical form the scan-to-add lookup uses, so it still
        # matches a later scan of the UPC-A printing of the same barcode.
        csv_text = "material,barcode\nPLA,0012345678905\n"

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1

        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert spool.barcode == "12345678905"

    async def test_import_without_barcode_column_defaults_to_none(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        # A CSV that never had the barcode column (e.g. exported before this
        # feature existed, or hand-written) must import cleanly with barcode
        # left unset — not an error, not a missing-column failure.
        csv_text = "material,brand,color_name,rgba\nPLA,Sunlu,Black,000000ff\n"

        response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1

        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert spool.barcode is None


@pytest.mark.asyncio
@pytest.mark.integration
class TestInventoryCsvDuplicateWarning:
    async def test_existing_spool_flags_duplicate_but_still_imports(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        db_session.add(Spool(material="PLA", brand="Polymaker", color_name="Jade White", rgba="e8e8e8ff"))
        await db_session.commit()

        # Row 1 matches the existing spool (case-insensitively); row 2 is new.
        csv_text = (
            "material,brand,color_name,rgba\n"
            "pla,polymaker,jade white,e8e8e8ff\n"  # duplicate of existing
            "PETG,OtherBrand,Black,000000ff\n"  # new
        )

        response = await async_client.post("/api/v1/inventory/spools/import?dry_run=true", files=_csv_upload(csv_text))

        assert response.status_code == 200, response.text
        rows = response.json()["rows"]
        assert rows[0]["duplicate_of_existing"] is True
        assert rows[1]["duplicate_of_existing"] is False

        # Soft-warn only: a real import still creates the duplicate row.
        real = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))
        assert real.json()["created"] == 2
        all_spools = (await db_session.execute(select(Spool))).scalars().all()
        assert len(all_spools) == 3  # 1 pre-existing + 2 imported

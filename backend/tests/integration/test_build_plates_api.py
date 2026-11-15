"""Build plate catalog, printer plate swaps and queue plate constraints (#1306)."""

import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.build_plate import BuildPlate
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer


def _write_3mf(path, bed_types: list[str]):
    """Minimal sliced 3MF whose slice_info names one bed type per plate."""
    plates = "".join(
        f'<plate><metadata key="index" value="{i}"/><metadata key="curr_bed_type" value="{bed}"/></plate>'
        for i, bed in enumerate(bed_types, start=1)
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", f"<config>{plates}</config>")
        # A G-code per plate: the queue refuses a 3MF that was never sliced.
        for i in range(1, len(bed_types) + 1):
            zf.writestr(f"Metadata/plate_{i}.gcode", "; sliced\n")
    return path


@pytest.fixture
async def plates(db_session):
    rows = [
        BuildPlate(builtin_key="textured_pei", name="Textured PEI Plate", base_type="textured_pei"),
        BuildPlate(builtin_key="smooth_pei", name="Smooth PEI Plate", base_type="smooth_pei"),
        BuildPlate(
            builtin_key="3d_effect_carbon_fiber",
            name="3D Effect – Carbon Fiber",
            base_type="smooth_pei",
            pattern="Carbon Fiber",
            enabled=False,
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    return {r.builtin_key: r for r in rows}


class TestBuildPlateCatalog:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_includes_disabled_presets(self, async_client: AsyncClient, plates):
        response = await async_client.get("/api/v1/build-plates")
        assert response.status_code == 200
        body = response.json()
        cf = next(p for p in body if p["builtin_key"] == "3d_effect_carbon_fiber")
        assert cf["enabled"] is False
        assert cf["is_builtin"] is True
        assert cf["base_type"] == "smooth_pei"
        assert cf["base_type_label"] == "Smooth PEI / High Temp Plate"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bed_types(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/build-plates/bed-types")
        assert response.status_code == 200
        keys = [t["key"] for t in response.json()]
        assert "smooth_pei" in keys and "textured_pei" in keys

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tick_a_preset(self, async_client: AsyncClient, plates):
        plate_id = plates["3d_effect_carbon_fiber"].id
        response = await async_client.patch(f"/api/v1/build-plates/{plate_id}", json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_custom_plate(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/build-plates",
            json={"name": "Gold PEI", "base_type": "smooth_pei", "pattern": "Gold"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["is_builtin"] is False
        assert body["pattern"] == "Gold"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "Bad", "base_type": "carbon_fiber"},
            {"name": "Bad", "base_type": "smooth_pei", "image": "javascript:alert(1)"},
        ],
    )
    async def test_create_rejects_invalid(self, async_client: AsyncClient, payload):
        response = await async_client.post("/api/v1/build-plates", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_builtin_cannot_be_deleted(self, async_client: AsyncClient, plates):
        response = await async_client.delete(f"/api/v1/build-plates/{plates['smooth_pei'].id}")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_a_custom_plate_untracks_printers_and_jobs(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        plate = BuildPlate(name="Glacier", base_type="supertack")
        db_session.add(plate)
        await db_session.commit()
        printer = await printer_factory(installed_plate_id=plate.id)
        item = PrintQueueItem(printer_id=printer.id, status="pending", position=1, required_plate_id=plate.id)
        done = PrintQueueItem(printer_id=printer.id, status="completed", position=2, required_plate_id=plate.id)
        db_session.add_all([item, done])
        await db_session.commit()

        printer_id, item_id, done_id, plate_id = printer.id, item.id, done.id, plate.id

        response = await async_client.delete(f"/api/v1/build-plates/{plate_id}")

        assert response.status_code == 204
        db_session.expire_all()
        assert (await db_session.get(Printer, printer_id)).installed_plate_id is None
        assert (await db_session.get(PrintQueueItem, item_id)).required_plate_id is None
        # A job that already ran on the plate keeps it as history.
        assert (await db_session.get(PrintQueueItem, done_id)).required_plate_id == plate_id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_retyping_a_plate_releases_pending_jobs_that_asked_for_it(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        """Jobs asked for this plate for a file of its old type; they would wait forever."""
        plate = BuildPlate(name="Gold PEI", base_type="smooth_pei")
        db_session.add(plate)
        await db_session.commit()
        printer = await printer_factory()
        item = PrintQueueItem(
            printer_id=printer.id,
            status="pending",
            position=1,
            required_plate_type="smooth_pei",
            required_plate_id=plate.id,
        )
        db_session.add(item)
        await db_session.commit()
        item_id, plate_id = item.id, plate.id

        listed = await async_client.get("/api/v1/build-plates")
        assert next(p for p in listed.json() if p["id"] == plate_id)["required_by_pending"] == 1

        response = await async_client.patch(f"/api/v1/build-plates/{plate_id}", json={"base_type": "textured_pei"})

        assert response.status_code == 200
        assert response.json()["required_by_pending"] == 0
        db_session.expire_all()
        row = await db_session.get(PrintQueueItem, item_id)
        assert row.required_plate_id is None
        assert row.required_plate_type == "smooth_pei"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_renaming_or_unticking_keeps_pending_requests(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        plate = BuildPlate(name="Gold PEI", base_type="smooth_pei")
        db_session.add(plate)
        await db_session.commit()
        printer = await printer_factory()
        db_session.add(PrintQueueItem(printer_id=printer.id, status="pending", position=1, required_plate_id=plate.id))
        await db_session.commit()
        plate_id = plate.id

        response = await async_client.patch(
            f"/api/v1/build-plates/{plate_id}", json={"name": "Gold", "enabled": False, "base_type": "smooth_pei"}
        )

        assert response.status_code == 200
        assert response.json()["required_by_pending"] == 1


class TestInstalledPlate:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_swap_plate(self, async_client: AsyncClient, printer_factory, plates):
        printer = await printer_factory()
        response = await async_client.put(
            f"/api/v1/printers/{printer.id}/installed-plate", json={"plate_id": plates["smooth_pei"].id}
        )
        assert response.status_code == 200
        assert response.json()["installed_plate_id"] == plates["smooth_pei"].id

        response = await async_client.put(f"/api/v1/printers/{printer.id}/installed-plate", json={"plate_id": None})
        assert response.status_code == 200
        assert response.json()["installed_plate_id"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_swap_to_unknown_plate_is_404(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory()
        response = await async_client.put(f"/api/v1/printers/{printer.id}/installed-plate", json={"plate_id": 999})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_printer_patch_validates_plate(self, async_client: AsyncClient, printer_factory, plates):
        printer = await printer_factory()
        bad = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"installed_plate_id": 999})
        assert bad.status_code == 400
        ok = await async_client.patch(
            f"/api/v1/printers/{printer.id}", json={"installed_plate_id": plates["textured_pei"].id}
        )
        assert ok.status_code == 200
        assert ok.json()["installed_plate_id"] == plates["textured_pei"].id


class TestQueuePlateConstraint:
    @pytest.fixture
    async def archive(self, db_session, tmp_path):
        from backend.app.models.archive import PrintArchive

        path = _write_3mf(tmp_path / "two_plates.3mf", ["Textured PEI Plate", "High Temp Plate"])
        archive = PrintArchive(
            filename="two_plates.3mf",
            file_path=str(path),
            file_size=path.stat().st_size,
            status="completed",
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)
        return archive

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_plate_type_is_derived_per_plate(self, async_client: AsyncClient, printer_factory, archive):
        printer = await printer_factory()
        first = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 1}
        )
        second = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 2}
        )
        assert first.status_code == 200, first.text
        assert first.json()["required_plate_type"] == "textured_pei"
        assert second.json()["required_plate_type"] == "smooth_pei"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_any_opts_out(self, async_client: AsyncClient, printer_factory, archive):
        printer = await printer_factory()
        response = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 1, "required_plate_type": "any"},
        )
        assert response.status_code == 200
        assert response.json()["required_plate_type"] == "any"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_specific_plate_must_match_the_file(
        self, async_client: AsyncClient, printer_factory, archive, plates
    ):
        printer = await printer_factory()
        cf = plates["3d_effect_carbon_fiber"].id
        # Plate 2 is High Temp (Smooth PEI): Carbon Fiber is a Smooth PEI plate.
        ok = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 2, "required_plate_id": cf},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["required_plate_id"] == cf
        # Plate 1 is Textured PEI: Carbon Fiber can't print it.
        bad = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 1, "required_plate_id": cf},
        )
        assert bad.status_code == 400
        assert "sliced for Textured PEI Plate" in bad.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_changing_plate_rederives_type(self, async_client: AsyncClient, printer_factory, archive, db_session):
        printer = await printer_factory()
        created = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 1}
        )
        item_id = created.json()["id"]
        response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"plate_id": 2})
        assert response.status_code == 200, response.text
        assert response.json()["required_plate_type"] == "smooth_pei"
        row = (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()
        await db_session.refresh(row)
        assert row.required_plate_type == "smooth_pei"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_moving_to_another_plate_drops_an_incompatible_specific_plate(
        self, async_client: AsyncClient, printer_factory, archive, plates
    ):
        """Carbon Fiber fits plate 2 (Smooth PEI) but not plate 1 (Textured).
        Editing only the plate must not be refused over it."""
        printer = await printer_factory()
        cf = plates["3d_effect_carbon_fiber"].id
        created = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 2, "required_plate_id": cf},
        )
        item_id = created.json()["id"]
        response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"plate_id": 1})
        assert response.status_code == 200, response.text
        assert response.json()["required_plate_type"] == "textured_pei"
        assert response.json()["required_plate_id"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_moving_to_a_compatible_plate_keeps_the_specific_plate(
        self, async_client: AsyncClient, printer_factory, db_session, tmp_path, plates
    ):
        from backend.app.models.archive import PrintArchive

        path = _write_3mf(tmp_path / "two_smooth.3mf", ["High Temp Plate", "High Temp Plate"])
        archive = PrintArchive(filename="s.3mf", file_path=str(path), file_size=1, status="completed")
        db_session.add(archive)
        await db_session.commit()
        printer = await printer_factory()
        cf = plates["3d_effect_carbon_fiber"].id
        created = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "plate_id": 1, "required_plate_id": cf},
        )
        response = await async_client.patch(f"/api/v1/queue/{created.json()['id']}", json={"plate_id": 2})
        assert response.status_code == 200, response.text
        assert response.json()["required_plate_id"] == cf

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_plate_type_is_rejected(self, async_client: AsyncClient, printer_factory, archive):
        printer = await printer_factory()
        response = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "required_plate_type": "Glass Plate"},
        )
        assert response.status_code == 400


class TestSetting:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tracking_is_off_by_default_and_can_be_enabled(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["build_plate_tracking_enabled"] is False
        response = await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})
        assert response.status_code == 200
        assert response.json()["build_plate_tracking_enabled"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_re_enabling_clears_recorded_plates_but_keeps_job_requirements(
        self, async_client: AsyncClient, db_session, printer_factory, plates
    ):
        """Plates are swapped by hand while tracking is off, so what each printer
        had recorded may be wrong when it comes back on."""
        cf = plates["3d_effect_carbon_fiber"].id
        textured = plates["textured_pei"].id
        printer = await printer_factory(installed_plate_id=textured)
        item = PrintQueueItem(
            printer_id=printer.id,
            status="pending",
            position=1,
            required_plate_type="smooth_pei",
            required_plate_id=cf,
        )
        db_session.add(item)
        await db_session.commit()
        printer_id, item_id = printer.id, item.id

        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})
        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": False})
        # Turned off: nothing is touched.
        db_session.expire_all()
        await async_client.put(f"/api/v1/printers/{printer_id}/installed-plate", json={"plate_id": textured})
        assert (await db_session.get(Printer, printer_id)).installed_plate_id == textured

        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})

        db_session.expire_all()
        assert (await db_session.get(Printer, printer_id)).installed_plate_id is None
        row = await db_session.get(PrintQueueItem, item_id)
        assert row.required_plate_id == cf
        assert row.required_plate_type == "smooth_pei"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_saving_while_already_on_keeps_recorded_plates(
        self, async_client: AsyncClient, db_session, printer_factory, plates
    ):
        """The settings page re-sends every value on save; that must not wipe plates."""
        smooth = plates["smooth_pei"].id
        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})
        printer = await printer_factory(installed_plate_id=smooth)
        printer_id = printer.id

        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})
        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": False})

        db_session.expire_all()
        assert (await db_session.get(Printer, printer_id)).installed_plate_id == smooth

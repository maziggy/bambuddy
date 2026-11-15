"""Owned build plates, printer plate swaps and queue plate constraints (#1306)."""

import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer

ALL_TYPES = ["cool_plate", "textured_cool_plate", "supertack", "engineering", "smooth_pei", "textured_pei"]
CARBON = {"base_type": "smooth_pei", "name": "Carbon Fiber"}


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


async def _configure(client: AsyncClient, types=("smooth_pei", "textured_pei"), patterns=(CARBON,)):
    response = await client.put("/api/v1/build-plates", json={"types": list(types), "patterns": list(patterns)})
    assert response.status_code == 200, response.text
    return response.json()


def _owned(config) -> list[str]:
    return [t["key"] for t in config["types"] if t["owned"]]


class TestOwnedPlates:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_every_type_is_owned_without_history(self, async_client: AsyncClient):
        config = (await async_client.get("/api/v1/build-plates")).json()
        assert [t["key"] for t in config["types"]] == ALL_TYPES
        assert _owned(config) == ALL_TYPES
        assert config["patterns"] == []
        assert "Carbon Fiber" in config["pattern_suggestions"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_types_printed_on_start_ticked(self, async_client: AsyncClient, db_session):
        """Before the first save, the types in the print history count as owned."""
        for bed in ("Textured PEI Plate", "High Temp Plate", "High Temp Plate", "Default Plate"):
            db_session.add(
                PrintArchive(filename="a.3mf", file_path="a.3mf", file_size=1, status="completed", bed_type=bed)
            )
        await db_session.commit()

        config = (await async_client.get("/api/v1/build-plates")).json()
        assert _owned(config) == ["smooth_pei", "textured_pei"]

        # Once saved, the user's choice wins over the history.
        await _configure(async_client, types=["engineering"], patterns=[])
        config = (await async_client.get("/api/v1/build-plates")).json()
        assert _owned(config) == ["engineering"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_types_and_patterns(self, async_client: AsyncClient):
        saved = await _configure(
            async_client,
            types=["textured_pei", "smooth_pei", "smooth_pei"],
            patterns=[
                CARBON,
                {"base_type": "smooth_pei", "name": "  carbon   FIBER "},
                {"base_type": "smooth_pei", "name": "Starry"},
            ],
        )
        assert _owned(saved) == ["smooth_pei", "textured_pei"]
        assert [(p["name"], p["label"]) for p in saved["patterns"]] == [
            ("Carbon Fiber", "Smooth PEI Plate · Carbon Fiber"),
            ("Starry", "Smooth PEI Plate · Starry"),
        ]
        assert (await async_client.get("/api/v1/build-plates")).json() == saved

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "payload",
        [
            {"types": ["glass"], "patterns": []},
            {"types": [], "patterns": [{"base_type": "glass", "name": "X"}]},
            {"types": [], "patterns": [{"base_type": "smooth_pei", "name": "   "}]},
            {"types": [], "patterns": [{"base_type": "smooth_pei", "name": "x" * 65}]},
        ],
    )
    async def test_save_rejects_invalid(self, async_client: AsyncClient, payload):
        response = await async_client.put("/api/v1/build-plates", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_counts_where_plates_are_used(self, async_client: AsyncClient, db_session, printer_factory):
        """The settings page warns before unticking a plate that is in use."""
        await _configure(async_client)
        printer = await printer_factory(installed_plate_type="smooth_pei", installed_plate_pattern="Carbon Fiber")
        db_session.add_all(
            [
                PrintQueueItem(
                    status="pending",
                    position=1,
                    required_plate_type="smooth_pei",
                    required_plate_pattern="carbon fiber",
                ),
                PrintQueueItem(status="pending", position=2, required_plate_type="smooth_pei"),
                PrintQueueItem(status="completed", position=3, required_plate_type="smooth_pei"),
                PrintQueueItem(status="pending", position=4, required_plate_type="any"),
            ]
        )
        await db_session.commit()

        config = (await async_client.get("/api/v1/build-plates")).json()
        smooth = next(t for t in config["types"] if t["key"] == "smooth_pei")
        assert smooth["installed_on"] == [printer.id]
        assert smooth["waiting_jobs"] == 2
        assert config["patterns"][0]["installed_on"] == [printer.id]
        assert config["patterns"][0]["waiting_jobs"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_removing_a_pattern_forgets_it_on_printers_and_pending_jobs(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        await _configure(async_client)
        printer = await printer_factory(installed_plate_type="smooth_pei", installed_plate_pattern="Carbon Fiber")
        pending = PrintQueueItem(
            status="pending", position=1, required_plate_type="smooth_pei", required_plate_pattern="Carbon Fiber"
        )
        done = PrintQueueItem(
            status="completed", position=2, required_plate_type="smooth_pei", required_plate_pattern="Carbon Fiber"
        )
        db_session.add_all([pending, done])
        await db_session.commit()
        printer_id, pending_id, done_id = printer.id, pending.id, done.id

        saved = await _configure(async_client, patterns=[])
        assert saved["patterns"] == []

        db_session.expire_all()
        row = await db_session.get(Printer, printer_id)
        assert (row.installed_plate_type, row.installed_plate_pattern) == ("smooth_pei", None)
        job = await db_session.get(PrintQueueItem, pending_id)
        assert (job.required_plate_type, job.required_plate_pattern) == ("smooth_pei", None)
        # History keeps what it ran on.
        assert (await db_session.get(PrintQueueItem, done_id)).required_plate_pattern == "Carbon Fiber"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unticking_a_type_only_hides_it(self, async_client: AsyncClient, db_session, printer_factory):
        await _configure(async_client)
        printer = await printer_factory(installed_plate_type="textured_pei")
        job = PrintQueueItem(status="pending", position=1, required_plate_type="textured_pei")
        db_session.add(job)
        await db_session.commit()

        printer_id, job_id = printer.id, job.id

        await _configure(async_client, types=["smooth_pei"])

        db_session.expire_all()
        assert (await db_session.get(Printer, printer_id)).installed_plate_type == "textured_pei"
        assert (await db_session.get(PrintQueueItem, job_id)).required_plate_type == "textured_pei"


class TestInstalledPlate:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_set_plate(self, async_client: AsyncClient, printer_factory):
        await _configure(async_client)
        printer = await printer_factory()
        url = f"/api/v1/printers/{printer.id}/installed-plate"

        response = await async_client.put(url, json={"plate_type": "textured_pei"})
        assert response.status_code == 200, response.text
        assert (response.json()["installed_plate_type"], response.json()["installed_plate_pattern"]) == (
            "textured_pei",
            None,
        )

        # The pattern is stored as it was set up, whatever the spelling sent.
        response = await async_client.put(url, json={"plate_type": "smooth_pei", "pattern": "carbon fiber"})
        assert response.json()["installed_plate_pattern"] == "Carbon Fiber"

        response = await async_client.put(url, json={"plate_type": None})
        assert (response.json()["installed_plate_type"], response.json()["installed_plate_pattern"]) == (None, None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_type_need_not_be_ticked(self, async_client: AsyncClient, printer_factory):
        """Owning only narrows the pickers; any real plate type can be recorded."""
        await _configure(async_client, types=["smooth_pei"])
        printer = await printer_factory()
        response = await async_client.put(
            f"/api/v1/printers/{printer.id}/installed-plate", json={"plate_type": "engineering"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"plate_type": "glass"}, 422),
            ({"plate_type": "smooth_pei", "pattern": "Galaxy"}, 400),  # not set up
            ({"plate_type": "textured_pei", "pattern": "Carbon Fiber"}, 400),  # set up under another type
            ({"plate_type": None, "pattern": "Carbon Fiber"}, 400),
        ],
    )
    async def test_rejects(self, async_client: AsyncClient, printer_factory, body, status):
        await _configure(async_client)
        printer = await printer_factory()
        response = await async_client.put(f"/api/v1/printers/{printer.id}/installed-plate", json=body)
        assert response.status_code == status

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_printer_is_404(self, async_client: AsyncClient):
        response = await async_client.put("/api/v1/printers/999/installed-plate", json={"plate_type": "smooth_pei"})
        assert response.status_code == 404


class TestQueuePlateConstraint:
    @pytest.fixture
    async def archive(self, db_session, tmp_path):
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

    async def _queue(self, client, printer_id, archive_id, plate_id, **extra):
        return await client.post(
            "/api/v1/queue/",
            json={"printer_id": printer_id, "archive_id": archive_id, "plate_id": plate_id, **extra},
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_plate_type_is_derived_per_plate(self, async_client: AsyncClient, printer_factory, archive):
        printer = await printer_factory()
        first = await self._queue(async_client, printer.id, archive.id, 1)
        second = await self._queue(async_client, printer.id, archive.id, 2)
        assert first.status_code == 200, first.text
        assert first.json()["required_plate_type"] == "textured_pei"
        assert second.json()["required_plate_type"] == "smooth_pei"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_any_opts_out(self, async_client: AsyncClient, printer_factory, archive):
        printer = await printer_factory()
        response = await self._queue(async_client, printer.id, archive.id, 1, required_plate_type="any")
        assert response.status_code == 200
        assert response.json()["required_plate_type"] == "any"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_pattern_must_be_set_up_for_the_files_type(
        self, async_client: AsyncClient, printer_factory, archive
    ):
        await _configure(async_client)
        printer = await printer_factory()
        # Plate 2 is High Temp (Smooth PEI), where Carbon Fiber is set up.
        ok = await self._queue(async_client, printer.id, archive.id, 2, required_plate_pattern="carbon fiber")
        assert ok.status_code == 200, ok.text
        assert ok.json()["required_plate_pattern"] == "Carbon Fiber"
        # Plate 1 is Textured PEI: no Carbon Fiber plate of that type.
        bad = await self._queue(async_client, printer.id, archive.id, 1, required_plate_pattern="Carbon Fiber")
        assert bad.status_code == 400
        assert "Textured PEI Plate" in bad.json()["detail"]
        # Nor a pattern nobody set up, nor one with no type to narrow.
        assert (
            await self._queue(async_client, printer.id, archive.id, 2, required_plate_pattern="Galaxy")
        ).status_code == 400
        assert (
            await self._queue(
                async_client,
                printer.id,
                archive.id,
                2,
                required_plate_type="any",
                required_plate_pattern="Carbon Fiber",
            )
        ).status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_changing_plate_rederives_type(self, async_client: AsyncClient, printer_factory, archive, db_session):
        printer = await printer_factory()
        item_id = (await self._queue(async_client, printer.id, archive.id, 1)).json()["id"]
        response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"plate_id": 2})
        assert response.status_code == 200, response.text
        assert response.json()["required_plate_type"] == "smooth_pei"
        row = (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()
        await db_session.refresh(row)
        assert row.required_plate_type == "smooth_pei"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_moving_to_a_plate_of_another_type_drops_the_pattern(
        self, async_client: AsyncClient, printer_factory, archive
    ):
        """Editing only the plate must not be refused over a pattern the new type lacks."""
        await _configure(async_client)
        printer = await printer_factory()
        created = await self._queue(async_client, printer.id, archive.id, 2, required_plate_pattern="Carbon Fiber")
        response = await async_client.patch(f"/api/v1/queue/{created.json()['id']}", json={"plate_id": 1})
        assert response.status_code == 200, response.text
        assert response.json()["required_plate_type"] == "textured_pei"
        assert response.json()["required_plate_pattern"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_moving_to_a_plate_of_the_same_type_keeps_the_pattern(
        self, async_client: AsyncClient, printer_factory, db_session, tmp_path
    ):
        path = _write_3mf(tmp_path / "two_smooth.3mf", ["High Temp Plate", "High Temp Plate"])
        archive = PrintArchive(filename="s.3mf", file_path=str(path), file_size=1, status="completed")
        db_session.add(archive)
        await db_session.commit()
        await _configure(async_client)
        printer = await printer_factory()
        created = await self._queue(async_client, printer.id, archive.id, 1, required_plate_pattern="Carbon Fiber")
        response = await async_client.patch(f"/api/v1/queue/{created.json()['id']}", json={"plate_id": 2})
        assert response.status_code == 200, response.text
        assert response.json()["required_plate_pattern"] == "Carbon Fiber"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_job_set_to_any_plate_can_be_given_a_pattern(
        self, async_client: AsyncClient, printer_factory, archive
    ):
        await _configure(async_client)
        printer = await printer_factory()
        created = await self._queue(async_client, printer.id, archive.id, 2, required_plate_type="any")
        response = await async_client.patch(
            f"/api/v1/queue/{created.json()['id']}",
            json={"required_plate_type": "smooth_pei", "required_plate_pattern": "Carbon Fiber"},
        )
        assert response.status_code == 200, response.text
        assert (response.json()["required_plate_type"], response.json()["required_plate_pattern"]) == (
            "smooth_pei",
            "Carbon Fiber",
        )
        # And back to any plate, which takes the pattern with it.
        response = await async_client.patch(
            f"/api/v1/queue/{created.json()['id']}", json={"required_plate_type": "any"}
        )
        assert (response.json()["required_plate_type"], response.json()["required_plate_pattern"]) == ("any", None)

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
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        """Plates are swapped by hand while tracking is off, so what each printer
        had recorded may be wrong when it comes back on."""
        await _configure(async_client)
        printer = await printer_factory(installed_plate_type="smooth_pei", installed_plate_pattern="Carbon Fiber")
        item = PrintQueueItem(
            printer_id=printer.id,
            status="pending",
            position=1,
            required_plate_type="smooth_pei",
            required_plate_pattern="Carbon Fiber",
        )
        db_session.add(item)
        await db_session.commit()
        printer_id, item_id = printer.id, item.id

        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})
        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": False})
        # Turned off: nothing is touched.
        await async_client.put(f"/api/v1/printers/{printer_id}/installed-plate", json={"plate_type": "textured_pei"})
        db_session.expire_all()
        assert (await db_session.get(Printer, printer_id)).installed_plate_type == "textured_pei"

        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})

        db_session.expire_all()
        row = await db_session.get(Printer, printer_id)
        assert (row.installed_plate_type, row.installed_plate_pattern) == (None, None)
        job = await db_session.get(PrintQueueItem, item_id)
        assert (job.required_plate_type, job.required_plate_pattern) == ("smooth_pei", "Carbon Fiber")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_saving_while_already_on_keeps_recorded_plates(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        """The settings page re-sends every value on save; that must not wipe plates."""
        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})
        printer = await printer_factory(installed_plate_type="smooth_pei")
        printer_id = printer.id

        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": True})
        await async_client.patch("/api/v1/settings/", json={"build_plate_tracking_enabled": False})

        db_session.expire_all()
        assert (await db_session.get(Printer, printer_id)).installed_plate_type == "smooth_pei"

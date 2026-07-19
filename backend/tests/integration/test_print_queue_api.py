"""Integration tests for Print Queue API endpoints."""

import pytest
from httpx import AsyncClient


class TestPrintQueueAPI:
    """Integration tests for /api/v1/queue endpoints."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Printer {counter}",
                "ip_address": f"192.168.1.{100 + counter}",
                "serial_number": f"TESTSERIAL{counter:04d}",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""
        _counter = [0]

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"test_print_{counter}.3mf",
                "print_name": f"Test Print {counter}",
                "file_path": f"/tmp/test_print_{counter}.3mf",
                "file_size": 1024,
                "content_hash": f"testhash{counter:08d}",
                "status": "completed",
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items."""
        _counter = [0]

        async def _create_queue_item(**kwargs):
            from backend.app.models.print_queue import PrintQueueItem

            _counter[0] += 1
            counter = _counter[0]

            # Create printer and archive if not provided
            if "printer_id" not in kwargs:
                printer = await printer_factory()
                kwargs["printer_id"] = printer.id

            if "archive_id" not in kwargs:
                archive = await archive_factory()
                kwargs["archive_id"] = archive.id

            defaults = {
                "status": "pending",
                "position": counter,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_queue_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_queue_empty(self, async_client: AsyncClient):
        """Verify empty list when no queue items exist."""
        response = await async_client.get("/api/v1/queue/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue(self, async_client: AsyncClient, printer_factory, archive_factory, db_session):
        """Verify item can be added to queue."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["printer_id"] == printer.id
        assert result["archive_id"] == archive.id
        assert result["status"] == "pending"
        assert result["manual_start"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_manual_start(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify item can be added to queue with manual_start=True."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "manual_start": True,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["printer_id"] == printer.id
        assert result["archive_id"] == archive.id
        assert result["status"] == "pending"
        assert result["manual_start"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_skip_filament_check(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """PrintModal "Print Anyway" persists skip_filament_check on creation (#1698-followup)."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "skip_filament_check": True,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["skip_filament_check"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_skip_filament_check_defaults_false(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Default add-to-queue has skip_filament_check=False — no silent bypass."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {"printer_id": printer.id, "archive_id": archive.id}
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["skip_filament_check"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_project_id(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """#932: queue items created from the project view carry project_id forward."""
        from backend.app.models.project import Project

        printer = await printer_factory()
        archive = await archive_factory()
        project = Project(name="Queue Project")
        db_session.add(project)
        await db_session.commit()
        await db_session.refresh(project)

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "project_id": project.id,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        # The response schema may or may not echo project_id; the stored row is
        # what matters, so verify via DB.
        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        row = (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == result["id"]))).scalar_one()
        assert row.project_id == project.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_invalid_project_id_returns_404(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """#932: bogus project_id must be rejected before the FK constraint fires.

        Regression guard for the pre-check added to add_to_queue. Without the
        validation, a nonexistent project_id would reach db.commit() and raise
        an IntegrityError → 500. The pre-check must convert that to a 404 so
        the UI gets a clean error it can surface.
        """
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "project_id": 999999,  # nonexistent
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 404
        assert "project" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_ams_mapping(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify item can be added to queue with ams_mapping."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "ams_mapping": [5, -1, 2, -1],  # Slot 1 -> tray 5, slot 3 -> tray 2
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["printer_id"] == printer.id
        assert result["archive_id"] == archive.id
        assert result["ams_mapping"] == [5, -1, 2, -1]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_plate_id(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify item can be added to queue with plate_id for multi-plate 3MF."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "plate_id": 3,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["plate_id"] == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_print_options(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify item can be added to queue with print options."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "bed_levelling": False,
            "flow_cali": True,
            "vibration_cali": False,
            "layer_inspect": True,
            "timelapse": True,
            "use_ams": False,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["bed_levelling"] is False
        assert result["flow_cali"] is True
        assert result["vibration_cali"] is False
        assert result["layer_inspect"] is True
        assert result["timelapse"] is True
        assert result["use_ams"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_queue_item_plate_id(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify queue item plate_id can be updated."""
        item = await queue_item_factory()
        response = await async_client.patch(f"/api/v1/queue/{item.id}", json={"plate_id": 5})
        assert response.status_code == 200
        result = response.json()
        assert result["plate_id"] == 5

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_queue_item_print_options(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify queue item print options can be updated."""
        item = await queue_item_factory()
        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            json={
                "bed_levelling": False,
                "timelapse": True,
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["bed_levelling"] is False
        assert result["timelapse"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify single queue item can be retrieved."""
        item = await queue_item_factory()
        response = await async_client.get(f"/api/v1/queue/{item.id}")
        assert response.status_code == 200
        assert response.json()["id"] == item.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_queue_item_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent queue item."""
        response = await async_client.get("/api/v1/queue/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify queue item can be updated."""
        item = await queue_item_factory()
        response = await async_client.patch(f"/api/v1/queue/{item.id}", json={"auto_off_after": True})
        assert response.status_code == 200
        result = response.json()
        assert result["auto_off_after"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_queue_item_manual_start(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify queue item manual_start can be updated."""
        item = await queue_item_factory(manual_start=False)
        response = await async_client.patch(f"/api/v1/queue/{item.id}", json={"manual_start": True})
        assert response.status_code == 200
        result = response.json()
        assert result["manual_start"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify queue item can be deleted."""
        item = await queue_item_factory()
        response = await async_client.delete(f"/api/v1/queue/{item.id}")
        assert response.status_code == 200
        assert response.json()["message"] == "Queue item deleted"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_queue_item_not_found(self, async_client: AsyncClient):
        """Verify 404 for deleting non-existent queue item."""
        response = await async_client.delete("/api/v1/queue/9999")
        assert response.status_code == 404


class TestQueueStartEndpoint:
    """Tests for the /queue/{item_id}/start endpoint."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Printer {counter}",
                "ip_address": f"192.168.1.{100 + counter}",
                "serial_number": f"TESTSERIAL{counter:04d}",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""
        _counter = [0]

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"test_print_{counter}.3mf",
                "print_name": f"Test Print {counter}",
                "file_path": f"/tmp/test_print_{counter}.3mf",
                "file_size": 1024,
                "content_hash": f"testhash{counter:08d}",
                "status": "completed",
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items."""
        _counter = [0]

        async def _create_queue_item(**kwargs):
            from backend.app.models.print_queue import PrintQueueItem

            _counter[0] += 1
            counter = _counter[0]

            if "printer_id" not in kwargs:
                printer = await printer_factory()
                kwargs["printer_id"] = printer.id

            if "archive_id" not in kwargs:
                archive = await archive_factory()
                kwargs["archive_id"] = archive.id

            defaults = {
                "status": "pending",
                "position": counter,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_queue_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_staged_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify starting a staged (manual_start=True) queue item clears the flag."""
        item = await queue_item_factory(manual_start=True)
        assert item.manual_start is True

        response = await async_client.post(f"/api/v1/queue/{item.id}/start")
        assert response.status_code == 200
        result = response.json()
        assert result["manual_start"] is False
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_non_staged_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify starting a non-staged queue item still works (idempotent)."""
        item = await queue_item_factory(manual_start=False)
        assert item.manual_start is False

        response = await async_client.post(f"/api/v1/queue/{item.id}/start")
        assert response.status_code == 200
        result = response.json()
        assert result["manual_start"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_queue_item_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent queue item."""
        response = await async_client.post("/api/v1/queue/9999/start")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_non_pending_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify 400 error when trying to start a non-pending queue item."""
        item = await queue_item_factory(status="printing", manual_start=True)

        response = await async_client.post(f"/api/v1/queue/{item.id}/start")
        assert response.status_code == 400
        assert "pending" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_completed_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify 400 error when trying to start a completed queue item."""
        item = await queue_item_factory(status="completed", manual_start=True)

        response = await async_client.post(f"/api/v1/queue/{item.id}/start")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_returns_409_on_filament_deficit(
        self,
        async_client: AsyncClient,
        queue_item_factory,
        db_session,
        monkeypatch,
    ):
        """Filament deficit must surface as 409 + structured payload (#1496)."""
        from backend.app.services import filament_deficit as fd_module

        item = await queue_item_factory(manual_start=True)

        async def _fake_deficit(_db, _item):
            return [
                fd_module.FilamentDeficit(
                    slot_id=1,
                    ams_id=0,
                    tray_id=0,
                    filament_type="PLA",
                    required_grams=270.0,
                    remaining_grams=200.0,
                ),
            ]

        monkeypatch.setattr(
            "backend.app.api.routes.print_queue.compute_deficit_for_queue_item",
            _fake_deficit,
        )

        response = await async_client.post(f"/api/v1/queue/{item.id}/start")
        assert response.status_code == 409
        body = response.json()
        assert body["detail"]["code"] == "insufficient_filament"
        assert len(body["detail"]["deficit"]) == 1
        assert body["detail"]["deficit"][0]["slot_id"] == 1
        assert body["detail"]["deficit"][0]["required_grams"] == 270.0
        assert body["detail"]["deficit"][0]["remaining_grams"] == 200.0

        # Item still pending, manual_start unchanged.
        await db_session.refresh(item)
        assert item.status == "pending"
        assert item.manual_start is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_with_skip_flag_bypasses_deficit_check(
        self,
        async_client: AsyncClient,
        queue_item_factory,
        db_session,
        monkeypatch,
    ):
        """With skip_filament_check=true the route dispatches even when short (#1496)."""
        from backend.app.services import filament_deficit as fd_module

        item = await queue_item_factory(manual_start=True, filament_short=True)
        called_with = {}

        async def _fake_deficit(_db, _item):
            called_with["called"] = True
            return [
                fd_module.FilamentDeficit(
                    slot_id=1,
                    ams_id=0,
                    tray_id=0,
                    filament_type="PLA",
                    required_grams=270.0,
                    remaining_grams=200.0,
                ),
            ]

        monkeypatch.setattr(
            "backend.app.api.routes.print_queue.compute_deficit_for_queue_item",
            _fake_deficit,
        )

        response = await async_client.post(f"/api/v1/queue/{item.id}/start?skip_filament_check=true")
        assert response.status_code == 200
        body = response.json()
        assert body["manual_start"] is False
        assert body["filament_short"] is False
        # Helper not called on the bypass path — we trust the operator's
        # decision to print anyway.
        assert called_with == {}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_with_skip_flag_persists_acknowledgement(
        self,
        async_client: AsyncClient,
        queue_item_factory,
        db_session,
    ):
        """skip_filament_check=true sets the persistent flag on the queue item
        so the scheduler doesn't re-flag it on the next tick (#1698-followup).

        Without persistence the route's flag-clearing only survives until the
        next scheduler tick re-runs the deficit check on identical spool
        state and re-promotes the item — the user has to click Play+Confirm
        every single tick.
        """
        item = await queue_item_factory(manual_start=True, filament_short=True)
        assert item.skip_filament_check is False

        response = await async_client.post(f"/api/v1/queue/{item.id}/start?skip_filament_check=true")
        assert response.status_code == 200
        body = response.json()
        assert body["skip_filament_check"] is True

        await db_session.refresh(item)
        assert item.skip_filament_check is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_without_skip_flag_does_not_set_acknowledgement(
        self,
        async_client: AsyncClient,
        queue_item_factory,
        db_session,
    ):
        """A successful Play click with no deficit must NOT silently set the
        acknowledgement flag — only an explicit Print Anyway should.
        """
        item = await queue_item_factory(manual_start=False, filament_short=False)
        assert item.skip_filament_check is False

        response = await async_client.post(f"/api/v1/queue/{item.id}/start")
        assert response.status_code == 200

        await db_session.refresh(item)
        assert item.skip_filament_check is False


class TestQueueCancelEndpoint:
    """Tests for the /queue/{item_id}/cancel endpoint."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            defaults = {
                "name": "Cancel Test Printer",
                "ip_address": "192.168.1.200",
                "serial_number": "TESTCANCEL001",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            defaults = {
                "filename": "cancel_test.3mf",
                "print_name": "Cancel Test Print",
                "file_path": "/tmp/cancel_test.3mf",
                "file_size": 1024,
                "content_hash": "cancelhash001",
                "status": "completed",
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items."""

        async def _create_queue_item(**kwargs):
            from backend.app.models.print_queue import PrintQueueItem

            if "printer_id" not in kwargs:
                printer = await printer_factory()
                kwargs["printer_id"] = printer.id

            if "archive_id" not in kwargs:
                archive = await archive_factory()
                kwargs["archive_id"] = archive.id

            defaults = {
                "status": "pending",
                "position": 1,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_queue_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_pending_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify cancelling a pending queue item."""
        item = await queue_item_factory(status="pending")

        response = await async_client.post(f"/api/v1/queue/{item.id}/cancel")
        assert response.status_code == 200
        assert response.json()["message"] == "Queue item cancelled"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_non_pending_queue_item(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify 400 error when trying to cancel a non-pending queue item."""
        item = await queue_item_factory(status="printing")

        response = await async_client.post(f"/api/v1/queue/{item.id}/cancel")
        assert response.status_code == 400


class TestQueueLibraryFileSupport:
    """Tests for queue items with library_file_id (instead of archive_id)."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Library Test Printer {counter}",
                "ip_address": f"192.168.1.{150 + counter}",
                "serial_number": f"TESTLIB{counter:04d}",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def library_file_factory(self, db_session):
        """Factory to create test library files."""
        _counter = [0]

        async def _create_library_file(**kwargs):
            from backend.app.models.library import LibraryFile

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"library_test_{counter}.3mf",
                "file_path": f"/test/library/library_test_{counter}.3mf",
                "file_size": 2048,
                "file_type": "3mf",
                "file_metadata": {"print_name": f"Library Print {counter}", "print_time_seconds": 3600},
            }
            defaults.update(kwargs)

            lib_file = LibraryFile(**defaults)
            db_session.add(lib_file)
            await db_session.commit()
            await db_session.refresh(lib_file)
            return lib_file

        return _create_library_file

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_library_file(
        self, async_client: AsyncClient, printer_factory, library_file_factory, db_session
    ):
        """Verify item can be added to queue using library_file_id instead of archive_id."""
        printer = await printer_factory()
        lib_file = await library_file_factory()

        data = {
            "printer_id": printer.id,
            "library_file_id": lib_file.id,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["printer_id"] == printer.id
        assert result["library_file_id"] == lib_file.id
        assert result["archive_id"] is None
        assert result["status"] == "pending"
        assert result["library_file_name"] == "Library Print 1"
        assert result["print_time_seconds"] == 3600

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_library_file_rejects_cross_model_mismatch(
        self, async_client: AsyncClient, printer_factory, library_file_factory, db_session
    ):
        """Cross-model gate (#2578) also reads sliced_for_model from library file metadata."""
        await printer_factory(model="H2D")
        lib_file = await library_file_factory(file_metadata={"print_name": "Mismatch", "sliced_for_model": "X1C"})

        response = await async_client.post(
            "/api/v1/queue/",
            json={"target_model": "H2D", "library_file_id": lib_file.id},
        )
        assert response.status_code == 400
        assert "sliced for X1C" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_library_file_with_options(
        self, async_client: AsyncClient, printer_factory, library_file_factory, db_session
    ):
        """Verify library file queue item can have all options set."""
        printer = await printer_factory()
        lib_file = await library_file_factory()

        data = {
            "printer_id": printer.id,
            "library_file_id": lib_file.id,
            "ams_mapping": [1, 2, -1, -1],
            "plate_id": 2,
            "bed_levelling": False,
            "timelapse": True,
            "manual_start": True,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["library_file_id"] == lib_file.id
        assert result["ams_mapping"] == [1, 2, -1, -1]
        assert result["plate_id"] == 2
        assert result["bed_levelling"] is False
        assert result["timelapse"] is True
        assert result["manual_start"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_requires_archive_or_library_file(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        """Verify 400 error when neither archive_id nor library_file_id provided."""
        printer = await printer_factory()

        data = {
            "printer_id": printer.id,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 400
        assert (
            "archive_id" in response.json()["detail"].lower() or "library_file_id" in response.json()["detail"].lower()
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_queue_item_with_library_file(
        self, async_client: AsyncClient, printer_factory, library_file_factory, db_session
    ):
        """Verify queue item with library_file_id can be updated."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        lib_file = await library_file_factory()

        # Create queue item directly
        item = PrintQueueItem(
            printer_id=printer.id,
            library_file_id=lib_file.id,
            status="pending",
            position=1,
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)

        # Update the item
        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            json={"auto_off_after": True, "plate_id": 3},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["auto_off_after"] is True
        assert result["plate_id"] == 3
        assert result["library_file_id"] == lib_file.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_queue_includes_library_file_info(
        self, async_client: AsyncClient, printer_factory, library_file_factory, db_session
    ):
        """Verify queue list includes library file metadata."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        lib_file = await library_file_factory(
            file_metadata={"print_name": "Custom Print Name", "print_time_seconds": 7200}
        )

        item = PrintQueueItem(
            printer_id=printer.id,
            library_file_id=lib_file.id,
            status="pending",
            position=1,
        )
        db_session.add(item)
        await db_session.commit()

        response = await async_client.get("/api/v1/queue/")
        assert response.status_code == 200
        items = response.json()
        assert len(items) >= 1

        # Find our item
        our_item = next((i for i in items if i["library_file_id"] == lib_file.id), None)
        assert our_item is not None
        assert our_item["library_file_name"] == "Custom Print Name"
        assert our_item["print_time_seconds"] == 7200


class TestBulkUpdateEndpoint:
    """Tests for the /queue/bulk endpoint."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Bulk Test Printer {counter}",
                "ip_address": f"192.168.1.{150 + counter}",
                "serial_number": f"TESTBULK{counter:04d}",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""
        _counter = [0]

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"bulk_test_{counter}.3mf",
                "print_name": f"Bulk Test Print {counter}",
                "file_path": f"/tmp/bulk_test_{counter}.3mf",
                "file_size": 1024,
                "content_hash": f"bulkhash{counter:04d}",
                "status": "completed",
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items."""

        async def _create_item(**kwargs):
            from backend.app.models.print_queue import PrintQueueItem

            if "printer_id" not in kwargs:
                printer = await printer_factory()
                kwargs["printer_id"] = printer.id

            if "archive_id" not in kwargs:
                archive = await archive_factory()
                kwargs["archive_id"] = archive.id

            defaults = {
                "status": "pending",
                "position": 1,
                "bed_levelling": True,
                "flow_cali": False,
                "vibration_cali": True,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_single_field(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify bulk update can change a single field on multiple items."""
        item1 = await queue_item_factory(bed_levelling=True)
        item2 = await queue_item_factory(bed_levelling=True)

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={"item_ids": [item1.id, item2.id], "bed_levelling": False},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["updated_count"] == 2
        assert result["skipped_count"] == 0

        # Verify items were updated
        await db_session.refresh(item1)
        await db_session.refresh(item2)
        assert item1.bed_levelling is False
        assert item2.bed_levelling is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_multiple_fields(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify bulk update can change multiple fields at once."""
        item1 = await queue_item_factory(bed_levelling=True, flow_cali=False, manual_start=False)
        item2 = await queue_item_factory(bed_levelling=True, flow_cali=False, manual_start=False)

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={
                "item_ids": [item1.id, item2.id],
                "bed_levelling": False,
                "flow_cali": True,
                "manual_start": True,
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["updated_count"] == 2

        await db_session.refresh(item1)
        assert item1.bed_levelling is False
        assert item1.flow_cali is True
        assert item1.manual_start is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_skips_non_pending(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify bulk update skips non-pending items."""
        pending_item = await queue_item_factory(status="pending", bed_levelling=True)
        printing_item = await queue_item_factory(status="printing", bed_levelling=True)
        completed_item = await queue_item_factory(status="completed", bed_levelling=True)

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={
                "item_ids": [pending_item.id, printing_item.id, completed_item.id],
                "bed_levelling": False,
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["updated_count"] == 1
        assert result["skipped_count"] == 2

        # Only pending item should be updated
        await db_session.refresh(pending_item)
        await db_session.refresh(printing_item)
        await db_session.refresh(completed_item)
        assert pending_item.bed_levelling is False
        assert printing_item.bed_levelling is True
        assert completed_item.bed_levelling is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_change_printer(
        self, async_client: AsyncClient, queue_item_factory, printer_factory, db_session
    ):
        """Verify bulk update can reassign items to a different printer."""
        new_printer = await printer_factory(name="New Target Printer")
        item1 = await queue_item_factory()
        item2 = await queue_item_factory()

        original_printer_id = item1.printer_id

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={"item_ids": [item1.id, item2.id], "printer_id": new_printer.id},
        )
        assert response.status_code == 200

        await db_session.refresh(item1)
        await db_session.refresh(item2)
        assert item1.printer_id == new_printer.id
        assert item2.printer_id == new_printer.id
        assert item1.printer_id != original_printer_id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_empty_item_ids(self, async_client: AsyncClient):
        """Verify 400 error when item_ids is empty."""
        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={"item_ids": [], "bed_levelling": False},
        )
        assert response.status_code == 400
        assert "no item" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_no_fields(self, async_client: AsyncClient, queue_item_factory):
        """Verify 400 error when no fields to update."""
        item = await queue_item_factory()

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={"item_ids": [item.id]},
        )
        assert response.status_code == 400
        assert "no fields" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_update_invalid_printer(self, async_client: AsyncClient, queue_item_factory):
        """Verify 400 error when printer_id doesn't exist."""
        item = await queue_item_factory()

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={"item_ids": [item.id], "printer_id": 99999},
        )
        assert response.status_code == 400
        assert "printer not found" in response.json()["detail"].lower()


class TestTargetLocationFeature:
    """Tests for queue items with target_location (Issue #220)."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Location Test Printer {counter}",
                "ip_address": f"192.168.1.{50 + counter}",
                "serial_number": f"TESTLOC{counter:04d}",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""
        _counter = [0]

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"location_test_{counter}.3mf",
                "print_name": f"Location Test Print {counter}",
                "file_path": f"/tmp/location_test_{counter}.3mf",
                "file_size": 1024,
                "content_hash": f"lochash{counter:08d}",
                "status": "completed",
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items."""
        _counter = [0]

        async def _create_queue_item(**kwargs):
            from backend.app.models.print_queue import PrintQueueItem

            _counter[0] += 1
            counter = _counter[0]

            if "printer_id" not in kwargs and "target_model" not in kwargs:
                printer = await printer_factory()
                kwargs["printer_id"] = printer.id

            if "archive_id" not in kwargs:
                archive = await archive_factory()
                kwargs["archive_id"] = archive.id

            defaults = {
                "status": "pending",
                "position": counter,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_queue_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_target_location(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify item can be added with target_model and target_location."""
        # Create a printer with model X1C so the API can validate
        await printer_factory(model="X1C", location="Office")
        archive = await archive_factory()

        data = {
            "target_model": "X1C",
            "target_location": "Workbench",
            "archive_id": archive.id,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["target_model"] == "X1C"
        assert result["target_location"] == "Workbench"
        assert result["printer_id"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_location_without_model_ignored(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify target_location without target_model is allowed (location is just ignored)."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "target_location": "Workbench",  # This gets ignored since printer_id is set
            "archive_id": archive.id,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        # The API accepts this but the location is only used with target_model
        assert response.status_code == 200
        result = response.json()
        assert result["printer_id"] == printer.id
        # Location may or may not be stored since it's meaningless without target_model
        # The important thing is the request succeeds

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_item_target_location_in_response(
        self, async_client: AsyncClient, queue_item_factory, db_session
    ):
        """Verify target_location is returned in queue item response."""
        item = await queue_item_factory(
            printer_id=None,
            target_model="X1C",
            target_location="Workshop",
        )

        response = await async_client.get(f"/api/v1/queue/{item.id}")
        assert response.status_code == 200
        result = response.json()
        assert result["target_model"] == "X1C"
        assert result["target_location"] == "Workshop"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_list_includes_target_location(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify target_location is included in queue list."""
        await queue_item_factory(
            printer_id=None,
            target_model="P1S",
            target_location="Garage",
        )

        response = await async_client.get("/api/v1/queue/")
        assert response.status_code == 200
        items = response.json()
        assert len(items) >= 1

        # Find our item
        our_item = next((i for i in items if i["target_location"] == "Garage"), None)
        assert our_item is not None
        assert our_item["target_model"] == "P1S"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_queue_item_target_location(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify target_location can be updated on existing queue item."""
        item = await queue_item_factory(
            printer_id=None,
            target_model="X1C",
            target_location="Office",
        )

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            json={"target_location": "Basement"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["target_location"] == "Basement"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clear_target_location(self, async_client: AsyncClient, queue_item_factory, db_session):
        """Verify target_location can be cleared (set to None)."""
        item = await queue_item_factory(
            printer_id=None,
            target_model="X1C",
            target_location="Office",
        )

        # Note: Setting to empty string should clear it
        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            json={"target_location": None},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["target_location"] is None

    # ------------------------------------------------------------------
    # Cross-model dispatch gate (#2578): a G-code 3MF sliced for one model
    # must not be queued for model-based dispatch to an incompatible model.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_rejects_cross_model_mismatch(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """X1C-sliced archive + target_model=H2D must be rejected (#2578)."""
        await printer_factory(model="H2D")
        archive = await archive_factory(sliced_for_model="X1C")

        response = await async_client.post(
            "/api/v1/queue/",
            json={"target_model": "H2D", "archive_id": archive.id},
        )
        assert response.status_code == 400
        assert "sliced for X1C" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_allows_gcode_family_target(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """X1C-sliced G-code on a P1S is an intentional mixed-farm workflow —
        same kinematics/volume family, must stay allowed."""
        await printer_factory(model="P1S")
        archive = await archive_factory(sliced_for_model="X1C")

        response = await async_client.post(
            "/api/v1/queue/",
            json={"target_model": "P1S", "archive_id": archive.id},
        )
        assert response.status_code == 200
        assert response.json()["target_model"] == "P1S"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_without_sliced_metadata_not_blocked(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Legacy archives without sliced_for_model can't be validated — must keep working."""
        await printer_factory(model="H2D")
        archive = await archive_factory()  # no sliced_for_model

        response = await async_client.post(
            "/api/v1/queue/",
            json={"target_model": "H2D", "archive_id": archive.id},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_cross_model_mismatch(
        self, async_client: AsyncClient, printer_factory, archive_factory, queue_item_factory, db_session
    ):
        """Editing an item must not be able to introduce an incompatible target either."""
        await printer_factory(model="X1C")
        await printer_factory(model="H2D")
        archive = await archive_factory(sliced_for_model="X1C")
        item = await queue_item_factory(printer_id=None, target_model="X1C", archive_id=archive.id)

        response = await async_client.patch(f"/api/v1/queue/{item.id}", json={"target_model": "H2D"})
        assert response.status_code == 400
        assert "sliced for X1C" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_can_fix_stale_mismatched_target(
        self, async_client: AsyncClient, printer_factory, archive_factory, queue_item_factory, db_session
    ):
        """A pre-fix DB row with a wrong target (the reporter's rows 78-82) must be
        repairable by editing the target back to the sliced-for model."""
        await printer_factory(model="X1C")
        archive = await archive_factory(sliced_for_model="X1C")
        # Stale mismatched row written directly to the DB (bypasses the API gate)
        item = await queue_item_factory(printer_id=None, target_model="H2D", archive_id=archive.id)

        response = await async_client.patch(f"/api/v1/queue/{item.id}", json={"target_model": "X1C"})
        assert response.status_code == 200
        assert response.json()["target_model"] == "X1C"


class TestAbortedStatusNormalisation:
    """Tests for issue #558: 'aborted' queue status causes 500 error."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Abort Test Printer {counter}",
                "ip_address": f"192.168.1.{60 + counter}",
                "serial_number": f"TESTABORT{counter:04d}",
                "access_code": "12345678",
                "model": "P1S",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""
        _counter = [0]

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"abort_test_{counter}.3mf",
                "print_name": f"Abort Test Print {counter}",
                "file_path": f"/tmp/abort_test_{counter}.3mf",
                "file_size": 1024,
                "content_hash": f"aborthash{counter:06d}",
                "status": "completed",
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items."""
        _counter = [0]

        async def _create_queue_item(**kwargs):
            from backend.app.models.print_queue import PrintQueueItem

            _counter[0] += 1
            counter = _counter[0]

            if "printer_id" not in kwargs:
                printer = await printer_factory()
                kwargs["printer_id"] = printer.id
            if "archive_id" not in kwargs:
                archive = await archive_factory()
                kwargs["archive_id"] = archive.id

            defaults = {
                "status": "pending",
                "position": counter,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_queue_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_on_print_complete_normalises_aborted_to_cancelled(self, queue_item_factory, db_session):
        """Verify the completion handler maps 'aborted' → 'cancelled' for queue items."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        item = await queue_item_factory(status="printing")

        # Build a mock session whose execute returns our item
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [item]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        tasks_before = set(asyncio.all_tasks())

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.core.database.async_session", return_value=mock_session),
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.mqtt_relay") as mock_relay,
            patch("backend.app.main.notification_service") as mock_notif,
            patch("backend.app.main.smart_plug_manager") as mock_plug,
            patch("backend.app.main.printer_manager") as mock_pm,
        ):
            mock_ws.send_print_complete = AsyncMock()
            mock_ws.broadcast = AsyncMock()
            mock_relay.on_print_complete = AsyncMock()
            mock_relay.on_queue_job_completed = AsyncMock()
            mock_notif.on_print_complete = AsyncMock()
            mock_plug.on_print_complete = AsyncMock()
            mock_pm.get_printer.return_value = None

            from backend.app.main import on_print_complete

            await on_print_complete(
                item.printer_id,
                {
                    "status": "aborted",
                    "filename": "test.gcode",
                    "subtask_name": "Test",
                    "timelapse_was_active": False,
                },
            )

            # Cancel background tasks before leaving mock context
            for task in asyncio.all_tasks() - tasks_before:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # The item status should be normalised to 'cancelled', not 'aborted'
        assert item.status == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_startup_fixup_converts_aborted_to_cancelled(self, queue_item_factory, db_session):
        """Verify the startup fixup converts existing 'aborted' rows to 'cancelled'."""
        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        # Create items with various statuses including 'aborted'
        item_aborted = await queue_item_factory(status="pending")
        item_pending = await queue_item_factory(status="pending")

        # Manually set the invalid status
        item_aborted.status = "aborted"
        db_session.add(item_aborted)
        await db_session.commit()

        # Run the fixup query (same logic as lifespan)
        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.status == "aborted"))
        aborted_items = result.scalars().all()
        for i in aborted_items:
            i.status = "cancelled"
        await db_session.commit()

        # Verify: no more 'aborted' items
        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.status == "aborted"))
        assert len(result.scalars().all()) == 0

        # The previously aborted item should now be 'cancelled'
        await db_session.refresh(item_aborted)
        assert item_aborted.status == "cancelled"

        # The pending item should be unchanged
        await db_session.refresh(item_pending)
        assert item_pending.status == "pending"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_completed_status_passes_through_unchanged(self, queue_item_factory, db_session):
        """Verify normal statuses like 'completed' are not affected by normalisation."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        item = await queue_item_factory(status="printing")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [item]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        tasks_before = set(asyncio.all_tasks())

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.core.database.async_session", return_value=mock_session),
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.mqtt_relay") as mock_relay,
            patch("backend.app.main.notification_service") as mock_notif,
            patch("backend.app.main.smart_plug_manager") as mock_plug,
            patch("backend.app.main.printer_manager") as mock_pm,
        ):
            mock_ws.send_print_complete = AsyncMock()
            mock_ws.broadcast = AsyncMock()
            mock_relay.on_print_complete = AsyncMock()
            mock_relay.on_queue_job_completed = AsyncMock()
            mock_notif.on_print_complete = AsyncMock()
            mock_plug.on_print_complete = AsyncMock()
            mock_pm.get_printer.return_value = None

            from backend.app.main import on_print_complete

            await on_print_complete(
                item.printer_id,
                {
                    "status": "completed",
                    "filename": "test.gcode",
                    "subtask_name": "Test",
                    "timelapse_was_active": False,
                },
            )

            # Cancel background tasks before leaving mock context
            for task in asyncio.all_tasks() - tasks_before:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        assert item.status == "completed"

    # ========================================================================
    # Library file usage tracking on print completion (#1008)
    #
    # These exercise the _bump_library_file_usage_if_completed helper directly
    # rather than invoking the whole on_print_complete handler — that path
    # spawns background asyncio tasks (notifications, MQTT relay, smart-plug)
    # that are expensive to mock and have nothing to do with the bump logic.
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bump_library_file_usage_on_completed(self, printer_factory, db_session):
        """Successful completion increments print_count and stamps last_printed_at."""
        from datetime import datetime, timezone

        from backend.app.main import _bump_library_file_usage_if_completed
        from backend.app.models.library import LibraryFile
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        lib_file = LibraryFile(
            filename="benchy.gcode.3mf",
            file_path="/data/library/benchy.gcode.3mf",
            file_type="gcode.3mf",
            file_size=1024,
            print_count=0,
            last_printed_at=None,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        item = PrintQueueItem(
            printer_id=printer.id,
            library_file_id=lib_file.id,
            status="printing",
            position=1,
        )

        before = datetime.now(timezone.utc).replace(tzinfo=None)
        await _bump_library_file_usage_if_completed(db_session, item, "completed")
        await db_session.commit()
        await db_session.refresh(lib_file)

        assert lib_file.print_count == 1
        assert lib_file.last_printed_at is not None
        assert lib_file.last_printed_at >= before

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bump_library_file_usage_repeated_prints_increment_count(self, printer_factory, db_session):
        """Each successful completion bumps print_count cumulatively."""
        from backend.app.main import _bump_library_file_usage_if_completed
        from backend.app.models.library import LibraryFile
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        lib_file = LibraryFile(
            filename="repeat.gcode.3mf",
            file_path="/data/library/repeat.gcode.3mf",
            file_type="gcode.3mf",
            file_size=1024,
            print_count=0,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        item = PrintQueueItem(
            printer_id=printer.id,
            library_file_id=lib_file.id,
            status="printing",
            position=1,
        )

        for _ in range(3):
            await _bump_library_file_usage_if_completed(db_session, item, "completed")

        await db_session.commit()
        await db_session.refresh(lib_file)
        assert lib_file.print_count == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
    async def test_bump_library_file_usage_skips_non_completed(self, printer_factory, db_session, terminal_status):
        """Failed and cancelled prints must NOT count as usage."""
        from backend.app.main import _bump_library_file_usage_if_completed
        from backend.app.models.library import LibraryFile
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        lib_file = LibraryFile(
            filename="broken.gcode.3mf",
            file_path="/data/library/broken.gcode.3mf",
            file_type="gcode.3mf",
            file_size=1024,
            print_count=0,
            last_printed_at=None,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        item = PrintQueueItem(
            printer_id=printer.id,
            library_file_id=lib_file.id,
            status="printing",
            position=1,
        )

        await _bump_library_file_usage_if_completed(db_session, item, terminal_status)
        await db_session.commit()
        await db_session.refresh(lib_file)

        assert lib_file.print_count == 0
        assert lib_file.last_printed_at is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bump_library_file_usage_skips_when_no_library_file_id(
        self, printer_factory, archive_factory, db_session
    ):
        """Queue items without library_file_id (e.g. archive reprints) are a no-op."""
        from backend.app.main import _bump_library_file_usage_if_completed
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        item = PrintQueueItem(
            printer_id=printer.id,
            library_file_id=None,
            archive_id=archive.id,
            status="printing",
            position=1,
        )

        # Must not raise.
        await _bump_library_file_usage_if_completed(db_session, item, "completed")

    # ========================================================================
    # Batch quantity tests
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_quantity_default(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify quantity=1 (default) creates a single item with no batch."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["batch_id"] is None
        assert result["batch_name"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_quantity_one_explicit(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify quantity=1 explicitly creates a single item with no batch."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "quantity": 1,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["batch_id"] is None
        assert result["batch_name"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_quantity_creates_batch(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify quantity > 1 creates a batch and multiple queue items."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "quantity": 3,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        result = response.json()
        # First item is returned, linked to a batch
        assert result["batch_id"] is not None
        assert result["batch_name"] is not None
        assert "×3" in result["batch_name"]

        # Verify all 3 items were created
        list_response = await async_client.get("/api/v1/queue/")
        items = list_response.json()
        batch_items = [i for i in items if i["batch_id"] == result["batch_id"]]
        assert len(batch_items) == 3
        # All items should have the same settings
        for item in batch_items:
            assert item["printer_id"] == printer.id
            assert item["archive_id"] == archive.id
            assert item["status"] == "pending"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_quantity_sequential_positions(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify batch items get sequential positions."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "quantity": 3,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        batch_id = response.json()["batch_id"]

        list_response = await async_client.get("/api/v1/queue/")
        items = list_response.json()
        batch_items = sorted(
            [i for i in items if i["batch_id"] == batch_id],
            key=lambda i: i["position"],
        )
        positions = [i["position"] for i in batch_items]
        assert positions == [positions[0], positions[0] + 1, positions[0] + 2]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_insert_position_shifts_existing_items(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify priority insertion shifts existing pending items in the same printer queue."""
        printer = await printer_factory()
        first = await archive_factory(print_name="First")
        second = await archive_factory(print_name="Second")
        priority = await archive_factory(print_name="Priority")

        assert (
            await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": first.id})
        ).status_code == 200
        assert (
            await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": second.id})
        ).status_code == 200

        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": priority.id,
                "insert_position": 1,
            },
        )
        assert response.status_code == 200

        list_response = await async_client.get(f"/api/v1/queue/?printer_id={printer.id}")
        items = sorted(list_response.json(), key=lambda item: item["position"])
        assert [item["archive_id"] for item in items[:3]] == [priority.id, first.id, second.id]
        assert [item["position"] for item in items[:3]] == [1, 2, 3]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_insert_position_quantity_shifts_existing_by_quantity(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """ASAP batch insertion shifts existing pending items by the inserted quantity."""
        printer = await printer_factory()
        first = await archive_factory(print_name="First")
        second = await archive_factory(print_name="Second")
        priority = await archive_factory(print_name="Priority")

        assert (
            await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": first.id})
        ).status_code == 200
        assert (
            await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": second.id})
        ).status_code == 200

        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": priority.id,
                "quantity": 3,
                "insert_position": 1,
            },
        )
        assert response.status_code == 200
        batch_id = response.json()["batch_id"]

        list_response = await async_client.get(f"/api/v1/queue/?printer_id={printer.id}")
        items = sorted(list_response.json(), key=lambda item: item["position"])
        assert [item["archive_id"] for item in items] == [
            priority.id,
            priority.id,
            priority.id,
            first.id,
            second.id,
        ]
        assert [item["position"] for item in items] == [1, 2, 3, 4, 5]
        assert [item["batch_id"] for item in items[:3]] == [batch_id, batch_id, batch_id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_insert_position_scopes_unassigned_items(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Unassigned inserts shift only the unassigned queue scope."""
        printer = await printer_factory()
        unassigned_first = await archive_factory(print_name="Unassigned First")
        unassigned_second = await archive_factory(print_name="Unassigned Second")
        assigned = await archive_factory(print_name="Assigned")
        priority = await archive_factory(print_name="Unassigned Priority")

        assert (await async_client.post("/api/v1/queue/", json={"archive_id": unassigned_first.id})).status_code == 200
        assert (await async_client.post("/api/v1/queue/", json={"archive_id": unassigned_second.id})).status_code == 200
        assigned_response = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": assigned.id},
        )
        assert assigned_response.status_code == 200
        assert assigned_response.json()["position"] == 1

        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "archive_id": priority.id,
                "insert_position": 1,
            },
        )
        assert response.status_code == 200

        unassigned_response = await async_client.get("/api/v1/queue/?printer_id=-1")
        unassigned_items = sorted(unassigned_response.json(), key=lambda item: item["position"])
        assert [item["archive_id"] for item in unassigned_items] == [
            priority.id,
            unassigned_first.id,
            unassigned_second.id,
        ]
        assert [item["position"] for item in unassigned_items] == [1, 2, 3]

        assigned_scope_response = await async_client.get(f"/api/v1/queue/?printer_id={printer.id}&target_model=NONE")
        assigned_items = sorted(assigned_scope_response.json(), key=lambda item: item["position"])
        assert [item["archive_id"] for item in assigned_items] == [assigned.id]
        assert [item["position"] for item in assigned_items] == [1]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_insert_position_greater_than_max_appends_without_gap(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Oversized explicit insert_position appends at max+1 instead of creating sparse positions."""
        printer = await printer_factory()
        first = await archive_factory(print_name="First")
        second = await archive_factory(print_name="Second")
        appended = await archive_factory(print_name="Append")

        assert (
            await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": first.id})
        ).status_code == 200
        assert (
            await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": second.id})
        ).status_code == 200

        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": appended.id,
                "insert_position": 99,
            },
        )
        assert response.status_code == 200
        assert response.json()["position"] == 3

        list_response = await async_client.get(f"/api/v1/queue/?printer_id={printer.id}")
        items = sorted(list_response.json(), key=lambda item: item["position"])
        assert [item["archive_id"] for item in items] == [first.id, second.id, appended.id]
        assert [item["position"] for item in items] == [1, 2, 3]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_consecutive_asap_inserts_stack_in_submission_order(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Consecutive ASAP inserts to the same printer preserve the client submission order."""
        printer = await printer_factory()
        existing = await archive_factory(print_name="Existing")
        first_asap = await archive_factory(print_name="First ASAP")
        second_asap = await archive_factory(print_name="Second ASAP")

        assert (
            await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": existing.id})
        ).status_code == 200

        first_response = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": first_asap.id,
                "insert_position": 1,
            },
        )
        assert first_response.status_code == 200

        second_response = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": second_asap.id,
                "insert_position": 2,
            },
        )
        assert second_response.status_code == 200

        list_response = await async_client.get(f"/api/v1/queue/?printer_id={printer.id}")
        items = sorted(list_response.json(), key=lambda item: item["position"])
        assert [item["archive_id"] for item in items] == [first_asap.id, second_asap.id, existing.id]
        assert [item["position"] for item in items] == [1, 2, 3]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_quantity_with_print_options(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Verify print options are applied to all batch items."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "quantity": 2,
            "bed_levelling": False,
            "timelapse": True,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        assert response.status_code == 200
        batch_id = response.json()["batch_id"]

        list_response = await async_client.get("/api/v1/queue/")
        batch_items = [i for i in list_response.json() if i["batch_id"] == batch_id]
        assert len(batch_items) == 2
        for item in batch_items:
            assert item["bed_levelling"] is False
            assert item["timelapse"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_batch(self, async_client: AsyncClient, printer_factory, archive_factory, db_session):
        """Verify batch can be retrieved with progress stats."""
        printer = await printer_factory()
        archive = await archive_factory()

        # Create a batch of 3
        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "quantity": 3,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        batch_id = response.json()["batch_id"]

        # Get batch
        response = await async_client.get(f"/api/v1/queue/batches/{batch_id}")
        assert response.status_code == 200
        result = response.json()
        assert result["id"] == batch_id
        assert result["quantity"] == 3
        assert result["status"] == "active"
        assert result["pending_count"] == 3
        assert result["printing_count"] == 0
        assert result["completed_count"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_batches(self, async_client: AsyncClient, printer_factory, archive_factory, db_session):
        """Verify batches can be listed."""
        printer = await printer_factory()
        archive = await archive_factory()

        # Create two batches
        for qty in [2, 3]:
            await async_client.post(
                "/api/v1/queue/",
                json={"printer_id": printer.id, "archive_id": archive.id, "quantity": qty},
            )

        response = await async_client.get("/api/v1/queue/batches")
        assert response.status_code == 200
        batches = response.json()
        assert len(batches) >= 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_batch(self, async_client: AsyncClient, printer_factory, archive_factory, db_session):
        """Verify cancelling a batch cancels all pending items."""
        printer = await printer_factory()
        archive = await archive_factory()

        data = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "quantity": 3,
        }
        response = await async_client.post("/api/v1/queue/", json=data)
        batch_id = response.json()["batch_id"]

        # Cancel the batch
        response = await async_client.delete(f"/api/v1/queue/batches/{batch_id}")
        assert response.status_code == 200

        # Verify all items are cancelled
        list_response = await async_client.get("/api/v1/queue/")
        batch_items = [i for i in list_response.json() if i["batch_id"] == batch_id]
        for item in batch_items:
            assert item["status"] == "cancelled"

        # Verify batch status
        batch_response = await async_client.get(f"/api/v1/queue/batches/{batch_id}")
        assert batch_response.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_batch_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent batch."""
        response = await async_client.get("/api/v1/queue/batches/9999")
        assert response.status_code == 404

    # ========================================================================
    # Queue redesign: create-empty + group-existing + ungroup
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_empty_batch_for_client_side_grouping(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """Verify POST /queue/batches without item_ids creates an empty batch
        whose id can be passed on subsequent /queue/ POSTs (the multi-plate
        auto-batch flow). Subsequent items must end up with the same batch_id."""
        printer = await printer_factory()
        archive = await archive_factory()

        # 1. Pre-create batch
        batch_resp = await async_client.post(
            "/api/v1/queue/batches",
            json={"name": "Plates · 2 plates", "archive_id": archive.id},
        )
        assert batch_resp.status_code == 200
        batch = batch_resp.json()
        assert batch["status"] == "active"
        batch_id = batch["id"]

        # 2. Add two items referencing that batch
        for plate_id in (1, 2):
            item_resp = await async_client.post(
                "/api/v1/queue/",
                json={
                    "printer_id": printer.id,
                    "archive_id": archive.id,
                    "plate_id": plate_id,
                    "batch_id": batch_id,
                },
            )
            assert item_resp.status_code == 200
            assert item_resp.json()["batch_id"] == batch_id

        # 3. Verify batch now has 2 pending children
        list_resp = await async_client.get("/api/v1/queue/")
        siblings = [i for i in list_resp.json() if i["batch_id"] == batch_id]
        assert len(siblings) == 2
        assert {i["plate_id"] for i in siblings} == {1, 2}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_group_existing_items_as_batch(
        self, async_client: AsyncClient, printer_factory, archive_factory, queue_item_factory
    ):
        """Verify POST /queue/batches with item_ids assigns batch_id to
        existing pending items (the 'Group as batch' UI action)."""
        printer = await printer_factory()
        archive = await archive_factory()
        item_a = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="pending")
        item_b = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="pending")

        resp = await async_client.post(
            "/api/v1/queue/batches",
            json={"name": "Manual group", "item_ids": [item_a.id, item_b.id]},
        )
        assert resp.status_code == 200
        batch_id = resp.json()["id"]

        list_resp = await async_client.get("/api/v1/queue/")
        grouped = [i for i in list_resp.json() if i["batch_id"] == batch_id]
        assert {i["id"] for i in grouped} == {item_a.id, item_b.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_group_skips_non_pending_items(
        self, async_client: AsyncClient, printer_factory, archive_factory, queue_item_factory
    ):
        """Verify grouping doesn't pull in already-completed/cancelled items."""
        printer = await printer_factory()
        archive = await archive_factory()
        pending = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="pending")
        completed = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="completed")

        resp = await async_client.post(
            "/api/v1/queue/batches",
            json={"name": "Mixed", "item_ids": [pending.id, completed.id]},
        )
        assert resp.status_code == 200
        batch_id = resp.json()["id"]

        list_resp = await async_client.get("/api/v1/queue/")
        grouped = [i for i in list_resp.json() if i["batch_id"] == batch_id]
        assert {i["id"] for i in grouped} == {pending.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_batch_requires_name(self, async_client: AsyncClient):
        """Verify empty / whitespace-only name is rejected with 400."""
        resp = await async_client.post("/api/v1/queue/batches", json={"name": "   "})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ungroup_batch_clears_batch_id_and_deletes_row(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """Verify POST /queue/batches/{id}/ungroup clears batch_id from all
        members and deletes the batch row when nothing remains assigned."""
        printer = await printer_factory()
        archive = await archive_factory()

        # Create batch with two items via the existing quantity flow
        add_resp = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 2},
        )
        batch_id = add_resp.json()["batch_id"]

        # Ungroup
        ungroup_resp = await async_client.post(f"/api/v1/queue/batches/{batch_id}/ungroup")
        assert ungroup_resp.status_code == 200
        assert ungroup_resp.json()["ungrouped_count"] == 2

        # Verify items still exist but no longer batched
        list_resp = await async_client.get("/api/v1/queue/")
        ex_members = [i for i in list_resp.json() if i["batch_id"] == batch_id]
        assert ex_members == []

        # Batch row was deleted
        get_resp = await async_client.get(f"/api/v1/queue/batches/{batch_id}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_with_unknown_batch_id_404(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """Verify addToQueue with a non-existent batch_id is rejected."""
        printer = await printer_factory()
        archive = await archive_factory()
        resp = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": archive.id,
                "batch_id": 99999,
            },
        )
        assert resp.status_code == 404

    # ========================================================================
    # Soft-deleted archive handling (#1348 follow-up)
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_soft_delete_archive_deletes_all_related_queue_items(
        self, async_client: AsyncClient, printer_factory, archive_factory, queue_item_factory, db_session
    ):
        """Soft-deleting an archive removes every related queue item, regardless
        of status (#1734). Pre-#1734 only ``pending`` rows were flipped to
        ``cancelled`` and stayed in the DB, surprising users who expected the
        queue lines to disappear with the archive — especially on multi-plate
        Send All uploads (#1733), where ONE archive backed N queue items and
        soft-deleting the archive left N "cancelled" rows behind. The change
        keeps the printing guard (a row with ``status='printing'`` blocks the
        delete one layer up at the API route), so we never delete the row of
        an actively-running print here.

        Print history lives in ``PrintLogEntry`` (FK ``ON DELETE SET NULL``) —
        the audit trail survives independently of the queue rows.
        """
        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem
        from backend.app.services.archive import ArchiveService

        printer = await printer_factory()
        archive = await archive_factory(thumbnail_path="archives/test/test/thumbnail.png")
        pending = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="pending")
        completed = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="completed")

        service = ArchiveService(db_session)
        assert await service.soft_delete_archive(archive.id) is True

        # Every queue row that referenced this archive is gone — both the
        # pending and the completed rows. Print history (PrintLogEntry) is
        # the authoritative record and is preserved by the FK SET NULL.
        remaining = (
            (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id.in_([pending.id, completed.id]))))
            .scalars()
            .all()
        )
        assert remaining == [], (
            "Soft-deleting the archive must delete every related queue row, "
            f"got {[(r.id, r.status) for r in remaining]} still present"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_api_hides_archive_surface_when_soft_deleted(
        self, async_client: AsyncClient, printer_factory, archive_factory, queue_item_factory, db_session
    ):
        """Queue serializer must NOT populate archive_thumbnail / archive_name
        when the archive is soft-deleted — otherwise the frontend renders a
        broken <img> and 404-storms the thumbnail / plates / plate-thumbnail
        endpoints. archive_deleted=True signals the soft-deleted state so
        the UI can render a 'source deleted' badge."""
        from datetime import datetime, timezone

        printer = await printer_factory()
        archive = await archive_factory(
            print_name="Test Print",
            thumbnail_path="archives/test/test/thumbnail.png",
            deleted_at=datetime.now(timezone.utc),  # Pre-soft-deleted
        )
        item = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="cancelled")

        resp = await async_client.get("/api/v1/queue/")
        assert resp.status_code == 200
        body = resp.json()
        row = next((r for r in body if r["id"] == item.id), None)
        assert row is not None
        assert row["archive_deleted"] is True
        assert row["archive_thumbnail"] is None, "must not expose stale thumbnail path for soft-deleted archive"
        assert row["archive_name"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_api_still_exposes_archive_surface_when_live(
        self, async_client: AsyncClient, printer_factory, archive_factory, queue_item_factory, db_session
    ):
        """Sanity guard: the soft-delete suppression must not affect live
        archives. archive_name / archive_thumbnail still flow through and
        archive_deleted stays False."""
        printer = await printer_factory()
        archive = await archive_factory(
            print_name="Live Archive",
            thumbnail_path="archives/test/live/thumbnail.png",
        )
        item = await queue_item_factory(printer_id=printer.id, archive_id=archive.id, status="pending")

        resp = await async_client.get("/api/v1/queue/")
        assert resp.status_code == 200
        row = next((r for r in resp.json() if r["id"] == item.id), None)
        assert row is not None
        assert row["archive_deleted"] is False
        assert row["archive_name"] == "Live Archive"
        assert row["archive_thumbnail"] == "archives/test/live/thumbnail.png"


class TestResumeQueueAfterFailure:
    """Integration tests for POST /api/v1/queue/printer/{id}/resume (#1818)."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]
            defaults = {
                "name": f"Resume Printer {counter}",
                "ip_address": f"192.168.42.{100 + counter}",
                "serial_number": f"RESUMESERIAL{counter:04d}",
                "access_code": "12345678",
                "model": "P1S",
            }
            defaults.update(kwargs)
            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        _counter = [0]

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            counter = _counter[0]
            defaults = {
                "filename": f"resume_print_{counter}.3mf",
                "print_name": f"Resume Print {counter}",
                "file_path": f"/tmp/resume_print_{counter}.3mf",  # nosec B108
                "file_size": 1024,
                "content_hash": f"resumehash{counter:08d}",
                "status": "completed",
            }
            defaults.update(kwargs)
            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    async def _add_item(self, db_session, printer, archive_factory, **kwargs):
        from backend.app.models.print_queue import PrintQueueItem

        archive = await archive_factory()
        defaults = {
            "printer_id": printer.id,
            "archive_id": archive.id,
            "status": "pending",
            "require_previous_success": True,
        }
        defaults.update(kwargs)
        item = PrintQueueItem(**defaults)
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_unknown_printer_returns_404(self, async_client: AsyncClient):
        resp = await async_client.post("/api/v1/queue/printer/999999/resume")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_no_op_on_clean_queue(self, async_client: AsyncClient, printer_factory):
        """Calling resume on a printer with no failures and no skipped items
        returns zero counts — endpoint is idempotent and safe to spam."""
        printer = await printer_factory()
        resp = await async_client.post(f"/api/v1/queue/printer/{printer.id}/resume")
        assert resp.status_code == 200
        assert resp.json() == {"acknowledged": 0, "restored": 0}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_acknowledges_failed_and_restores_skipped(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Reporter's scenario: failed predecessor + N skipped downstream items.
        Resume sets gate_acknowledged on the failure and flips skipped → pending."""
        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        failed = await self._add_item(db_session, printer, archive_factory, status="failed")
        skipped_1 = await self._add_item(
            db_session,
            printer,
            archive_factory,
            status="skipped",
            error_message="Previous print failed or was aborted",
        )
        skipped_2 = await self._add_item(
            db_session,
            printer,
            archive_factory,
            status="skipped",
            error_message="Previous print failed or was aborted",
        )

        resp = await async_client.post(f"/api/v1/queue/printer/{printer.id}/resume")
        assert resp.status_code == 200
        assert resp.json() == {"acknowledged": 1, "restored": 2}

        failed_id = failed.id
        skipped_ids = [skipped_1.id, skipped_2.id]
        db_session.expire_all()

        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == failed_id))
        assert result.scalar_one().gate_acknowledged is True

        for sid in skipped_ids:
            result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == sid))
            row = result.scalar_one()
            assert row.status == "pending"
            assert row.error_message is None
            assert row.completed_at is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_preserves_skipped_items_with_other_reasons(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Skipped items whose error_message is something OTHER than the
        gate string (e.g. filament-deficit promotion, future skip reasons)
        must not be touched — they encode different user intent."""
        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        gate_skip = await self._add_item(
            db_session,
            printer,
            archive_factory,
            status="skipped",
            error_message="Previous print failed or was aborted",
        )
        other_skip = await self._add_item(
            db_session,
            printer,
            archive_factory,
            status="skipped",
            error_message="User skipped via UI",
        )

        gate_id = gate_skip.id
        other_id = other_skip.id
        resp = await async_client.post(f"/api/v1/queue/printer/{printer.id}/resume")
        assert resp.json() == {"acknowledged": 0, "restored": 1}

        db_session.expire_all()
        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == gate_id))
        assert result.scalar_one().status == "pending"
        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == other_id))
        assert result.scalar_one().status == "skipped"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_scoped_to_printer(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """A resume on printer A must not clear printer B's gate — farms run
        each printer's queue independently."""
        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        p1 = await printer_factory()
        p2 = await printer_factory()
        failed_p1 = await self._add_item(db_session, p1, archive_factory, status="failed")
        failed_p2 = await self._add_item(db_session, p2, archive_factory, status="failed")

        failed_p1_id = failed_p1.id
        failed_p2_id = failed_p2.id
        resp = await async_client.post(f"/api/v1/queue/printer/{p1.id}/resume")
        assert resp.json() == {"acknowledged": 1, "restored": 0}

        db_session.expire_all()
        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == failed_p1_id))
        assert result.scalar_one().gate_acknowledged is True
        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == failed_p2_id))
        assert result.scalar_one().gate_acknowledged is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_handles_aborted_status(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Aborted prints (printer-detected mid-print failure) gate the same
        way failed prints do and must also be acknowledgeable."""
        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        aborted = await self._add_item(db_session, printer, archive_factory, status="aborted")
        aborted_id = aborted.id
        resp = await async_client.post(f"/api/v1/queue/printer/{printer.id}/resume")
        assert resp.json() == {"acknowledged": 1, "restored": 0}

        db_session.expire_all()
        result = await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == aborted_id))
        assert result.scalar_one().gate_acknowledged is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_idempotent_second_call_is_no_op(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Calling resume twice on the same printer doesn't re-acknowledge
        the same failure — the second call sees acknowledged=0, restored=0."""
        printer = await printer_factory()
        await self._add_item(db_session, printer, archive_factory, status="failed")
        await self._add_item(
            db_session,
            printer,
            archive_factory,
            status="skipped",
            error_message="Previous print failed or was aborted",
        )

        first = await async_client.post(f"/api/v1/queue/printer/{printer.id}/resume")
        assert first.json() == {"acknowledged": 1, "restored": 1}

        second = await async_client.post(f"/api/v1/queue/printer/{printer.id}/resume")
        assert second.json() == {"acknowledged": 0, "restored": 0}


class TestReorderEndpoint:
    """Tests for the /queue/reorder endpoint (#1625-followup duplicate-position validator)."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        async def _create(**kwargs):
            from backend.app.models.printer import Printer

            defaults = {
                "name": "Reorder Test Printer",
                "ip_address": "192.168.1.220",
                "serial_number": "TESTREORDER001",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)
            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create

    @pytest.fixture
    async def archive_factory(self, db_session):
        _counter = [0]

        async def _create(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            defaults = {
                "filename": f"reorder_{_counter[0]}.3mf",
                "print_name": f"Reorder {_counter[0]}",
                "file_path": f"/tmp/reorder_{_counter[0]}.3mf",  # nosec B108
                "file_size": 1024,
                "content_hash": f"reorderhash{_counter[0]:06d}",
                "status": "completed",
            }
            defaults.update(kwargs)
            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reorder_rejects_duplicate_positions(
        self, async_client: AsyncClient, db_session, printer_factory, archive_factory
    ):
        """Reorder payload with duplicate positions → 422 at schema layer.

        Regression guard: pre-fix, a buggy client sending two items at the
        same position would leave the queue in an inconsistent state (the
        scheduler's ORDER BY (printer_id, position) tie would be broken by
        physical row order — non-deterministic dispatch order).
        """
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        a1 = await archive_factory()
        a2 = await archive_factory()
        item1 = PrintQueueItem(printer_id=printer.id, archive_id=a1.id, status="pending", position=1)
        item2 = PrintQueueItem(printer_id=printer.id, archive_id=a2.id, status="pending", position=2)
        db_session.add_all([item1, item2])
        await db_session.commit()
        await db_session.refresh(item1)
        await db_session.refresh(item2)

        response = await async_client.post(
            "/api/v1/queue/reorder",
            json={
                "items": [
                    {"id": item1.id, "position": 1},
                    {"id": item2.id, "position": 1},  # duplicate
                ]
            },
        )
        assert response.status_code == 422
        body = response.json()
        # Pydantic v2 wraps custom validator errors; the message must mention "Duplicate"
        # so the FE can surface the actionable detail.
        assert any("duplicate" in str(err).lower() for err in body.get("detail", []))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reorder_accepts_unique_positions(
        self, async_client: AsyncClient, db_session, printer_factory, archive_factory
    ):
        """Reorder with unique positions succeeds and updates them in DB."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        a1 = await archive_factory()
        a2 = await archive_factory()
        item1 = PrintQueueItem(printer_id=printer.id, archive_id=a1.id, status="pending", position=1)
        item2 = PrintQueueItem(printer_id=printer.id, archive_id=a2.id, status="pending", position=2)
        db_session.add_all([item1, item2])
        await db_session.commit()
        await db_session.refresh(item1)
        await db_session.refresh(item2)

        response = await async_client.post(
            "/api/v1/queue/reorder",
            json={
                "items": [
                    {"id": item1.id, "position": 2},
                    {"id": item2.id, "position": 1},
                ]
            },
        )
        assert response.status_code == 200

        await db_session.refresh(item1)
        await db_session.refresh(item2)
        assert item1.position == 2
        assert item2.position == 1


class TestForceColorOverridesAreScopedToThePlate:
    """Queueing several plates of one 3MF must not make each plate wait on the
    colours of its siblings (#2551).

    The print dialog builds one override list from every selected plate and posts
    that same list with each plate's item, so the API is what has to keep only the
    slots the plate prints -- a ``force_color_match`` entry blocks dispatch until
    the printer has that exact colour loaded.
    """

    THREE_PLATES = """<?xml version="1.0" encoding="UTF-8"?>
    <config>
        <plate>
            <metadata key="index" value="1"/>
            <filament id="1" used_g="50.0" type="PLA" color="#0B2C7A"/>
        </plate>
        <plate>
            <metadata key="index" value="2"/>
            <filament id="2" used_g="40.0" type="PLA" color="#9B9EA0"/>
        </plate>
        <plate>
            <metadata key="index" value="3"/>
            <filament id="3" used_g="30.0" type="PLA" color="#F4EE2A"/>
        </plate>
    </config>
    """

    # What the dialog posts for every plate: the union of all three plates'
    # filaments, each one force-matched.
    ALL_THREE_COLORS = [
        {"slot_id": 1, "type": "PLA", "color": "#0B2C7A", "color_name": "Army Blue", "force_color_match": True},
        {"slot_id": 2, "type": "PLA", "color": "#9B9EA0", "color_name": "Ash Grey", "force_color_match": True},
        {"slot_id": 3, "type": "PLA", "color": "#F4EE2A", "color_name": "Sunshine Yellow", "force_color_match": True},
    ]

    @pytest.fixture
    async def multi_plate_archive(self, db_session, tmp_path):
        """An archive whose 3MF really exists on disk, one colour per plate."""
        import zipfile

        from backend.app.models.archive import PrintArchive

        file_path = tmp_path / "three_plates.gcode.3mf"
        with zipfile.ZipFile(file_path, "w") as zf:
            zf.writestr("Metadata/slice_info.config", self.THREE_PLATES)

        archive = PrintArchive(
            filename="three_plates.gcode.3mf",
            print_name="Three Plates",
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
            content_hash="platehash0001",
            status="completed",
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)
        return archive

    @pytest.fixture
    async def x1c(self, db_session):
        from backend.app.models.printer import Printer

        printer = Printer(
            name="Force Color X1C",
            ip_address="192.168.1.210",
            serial_number="FORCECOLOR01",
            access_code="12345678",
            model="X1C",
        )
        db_session.add(printer)
        await db_session.commit()
        await db_session.refresh(printer)
        return printer

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_each_plate_keeps_only_the_colour_it_prints(
        self, async_client: AsyncClient, multi_plate_archive, x1c
    ):
        """The bug: plate 1 prints Army Blue only, but was stored demanding all three."""
        stored = {}
        for plate_id in (1, 2, 3):
            response = await async_client.post(
                "/api/v1/queue/",
                json={
                    "target_model": "X1C",
                    "archive_id": multi_plate_archive.id,
                    "plate_id": plate_id,
                    "filament_overrides": self.ALL_THREE_COLORS,
                },
            )
            assert response.status_code == 200
            stored[plate_id] = response.json()["filament_overrides"]

        assert [o["color_name"] for o in stored[1]] == ["Army Blue"]
        assert [o["color_name"] for o in stored[2]] == ["Ash Grey"]
        assert [o["color_name"] for o in stored[3]] == ["Sunshine Yellow"]
        # The slot each entry maps to has to survive narrowing untouched, or the
        # dispatch-time AMS mapping would key the override onto the wrong slot.
        assert [o["slot_id"] for o in stored[2]] == [2]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_whole_file_queue_keeps_every_colour(self, async_client: AsyncClient, multi_plate_archive, x1c):
        """No plate_id means the job prints the whole file, so every colour is needed."""
        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "target_model": "X1C",
                "archive_id": multi_plate_archive.id,
                "filament_overrides": self.ALL_THREE_COLORS,
            },
        )
        assert response.status_code == 200
        assert len(response.json()["filament_overrides"]) == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unreadable_3mf_keeps_every_colour(self, async_client: AsyncClient, db_session, tmp_path, x1c):
        """When the plate's slots can't be read, keep the overrides rather than drop them.

        An item waiting on a colour it doesn't need is visible and fixable; one that
        silently lost its forced colour would dispatch in the wrong filament.
        """
        from backend.app.models.archive import PrintArchive

        file_path = tmp_path / "not_a_zip.gcode.3mf"
        file_path.write_text("this is not a 3mf")
        archive = PrintArchive(
            filename="not_a_zip.gcode.3mf",
            print_name="Corrupt",
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
            content_hash="platehash0002",
            status="completed",
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "target_model": "X1C",
                "archive_id": archive.id,
                "plate_id": 1,
                "filament_overrides": self.ALL_THREE_COLORS,
            },
        )
        assert response.status_code == 200
        assert len(response.json()["filament_overrides"]) == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_required_types_stay_scoped_to_the_plate(self, async_client: AsyncClient, db_session, tmp_path, x1c):
        """Override types are merged into required_filament_types, so a shared list
        also widened the type gate -- a PLA-only plate demanded PETG as well."""
        import zipfile

        from backend.app.models.archive import PrintArchive

        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="1"/>
                <filament id="1" used_g="50.0" type="PLA" color="#0B2C7A"/>
            </plate>
            <plate>
                <metadata key="index" value="2"/>
                <filament id="2" used_g="40.0" type="PETG" color="#9B9EA0"/>
            </plate>
        </config>
        """
        file_path = tmp_path / "mixed_types.gcode.3mf"
        with zipfile.ZipFile(file_path, "w") as zf:
            zf.writestr("Metadata/slice_info.config", xml)
        archive = PrintArchive(
            filename="mixed_types.gcode.3mf",
            print_name="Mixed",
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
            content_hash="platehash0003",
            status="completed",
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "target_model": "X1C",
                "archive_id": archive.id,
                "plate_id": 1,
                "filament_overrides": [
                    {"slot_id": 1, "type": "PLA", "color": "#0B2C7A", "force_color_match": True},
                    {"slot_id": 2, "type": "PETG", "color": "#9B9EA0", "force_color_match": True},
                ],
            },
        )
        assert response.status_code == 200
        assert response.json()["required_filament_types"] == ["PLA"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_editing_an_item_narrows_the_overrides_too(
        self, async_client: AsyncClient, db_session, multi_plate_archive, x1c
    ):
        """The edit dialog posts the same shared list, so PATCH narrows it as well."""
        from backend.app.models.print_queue import PrintQueueItem

        item = PrintQueueItem(
            target_model="X1C",
            archive_id=multi_plate_archive.id,
            plate_id=2,
            status="pending",
            position=1,
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            json={"filament_overrides": self.ALL_THREE_COLORS},
        )
        assert response.status_code == 200
        assert [o["color_name"] for o in response.json()["filament_overrides"]] == ["Ash Grey"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_editing_the_plate_renarrows_against_the_new_plate(
        self, async_client: AsyncClient, db_session, multi_plate_archive, x1c
    ):
        """Moving an item to another plate must re-scope its colours to that plate."""
        from backend.app.models.print_queue import PrintQueueItem

        item = PrintQueueItem(
            target_model="X1C",
            archive_id=multi_plate_archive.id,
            plate_id=1,
            status="pending",
            position=1,
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            json={"plate_id": 3, "filament_overrides": self.ALL_THREE_COLORS},
        )
        assert response.status_code == 200
        assert [o["color_name"] for o in response.json()["filament_overrides"]] == ["Sunshine Yellow"]


@pytest.mark.asyncio
async def test_stop_offline_reconciles_linked_archive_status_2603(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    """Stopping a printing item while the printer is offline must also close out its
    archive (#2603).

    When the stop command reaches the printer, the later MQTT completion event flips
    the archive to cancelled. When the printer is offline no such event ever arrives,
    so without this the archive stays "printing" forever while the queue row is
    already cancelled — the reporter's archive 436. The offline branch reconciles the
    archive directly.
    """
    from unittest.mock import MagicMock, patch

    from backend.app.models.print_queue import PrintQueueItem

    printer = await printer_factory(name="Offline printer")
    archive = await archive_factory(
        printer.id, status="printing", plate_id=22, filename="heart 3.gcode.3mf", with_run=False
    )
    item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="printing")
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    # stop_print returns False => printer offline / not connected.
    with patch(
        "backend.app.services.printer_manager.printer_manager.stop_print",
        MagicMock(return_value=False),
    ):
        resp = await async_client.post(f"/api/v1/queue/{item.id}/stop")

    assert resp.status_code == 200
    await db_session.refresh(item)
    await db_session.refresh(archive)
    assert item.status == "cancelled"
    assert archive.status == "cancelled", "an offline stop must reconcile the archive, not leave it 'printing'"
    assert archive.completed_at is not None
    assert archive.failure_reason == "Stopped by user (printer was offline)"


@pytest.mark.asyncio
async def test_stop_online_leaves_archive_for_mqtt_to_reconcile_2603(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    """When the stop command reaches the printer, the archive is left to the MQTT
    completion path — the offline reconcile must NOT fire and pre-empt it."""
    from unittest.mock import MagicMock, patch

    from backend.app.models.print_queue import PrintQueueItem

    printer = await printer_factory(name="Online printer")
    archive = await archive_factory(printer.id, status="printing", filename="heart 3.gcode.3mf", with_run=False)
    item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="printing")
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    with patch(
        "backend.app.services.printer_manager.printer_manager.stop_print",
        MagicMock(return_value=True),
    ):
        resp = await async_client.post(f"/api/v1/queue/{item.id}/stop")

    assert resp.status_code == 200
    await db_session.refresh(item)
    await db_session.refresh(archive)
    assert item.status == "cancelled"
    assert archive.status == "printing", "an online stop must leave the archive for the MQTT completion path"

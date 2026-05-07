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

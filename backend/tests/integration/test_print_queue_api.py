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

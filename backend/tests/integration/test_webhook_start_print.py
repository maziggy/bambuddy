"""Regression tests for the webhook `/printer/{id}/start` route.

The previous implementation called `printer_manager.start_print()` directly
with `queue_item.archive_id` (an int) as the filename arg and no print
options, and used `await` on a non-async function. That route 500'd on
every invocation. The fix mirrors `POST /print-queue/{item_id}/start`:
clear the next pending item's `manual_start` so the scheduler picks it up
with the queue's stored options (timelapse, bed_levelling, etc.) intact.
"""

import pytest
from httpx import AsyncClient


@pytest.fixture
async def api_key_data(async_client: AsyncClient, db_session):
    """Create an API key with control_printer permission."""
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    full_key, key_hash, key_prefix = generate_api_key()
    api_key = APIKey(
        name="webhook-test-key",
        key_hash=key_hash,
        key_prefix=key_prefix,
        can_queue=True,
        can_control_printer=True,
        can_read_status=True,
        enabled=True,
    )
    db_session.add(api_key)
    await db_session.commit()
    return full_key


@pytest.fixture
async def printer_with_queue(db_session):
    """Create a printer and a pending queue item with manual_start=True."""
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.models.printer import Printer

    printer = Printer(
        name="WebhookTest",
        ip_address="192.168.1.42",
        access_code="12345678",
        serial_number="00M00A000000000",
        model="P1S",
    )
    db_session.add(printer)
    await db_session.commit()

    item = PrintQueueItem(
        printer_id=printer.id,
        position=1,
        status="pending",
        manual_start=True,
        timelapse=True,
        bed_levelling="on",
        flow_cali="off",
        vibration_cali=True,
        layer_inspect=False,
        use_ams=True,
    )
    db_session.add(item)
    await db_session.commit()
    return printer, item


class TestWebhookStartPrint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clears_manual_start_on_next_pending_item(
        self, async_client: AsyncClient, db_session, api_key_data, printer_with_queue
    ):
        """The webhook flips manual_start to False so the scheduler picks it up.

        Pre-fix the route called `printer_manager.start_print()` directly
        with no options and `archive_id` (int) as the filename — 500'd on
        every invocation. Now it mirrors the regular `/print-queue/{id}/start`
        affordance: scheduler dispatch handles FTP upload and all print
        options via the queue's stored fields.
        """
        printer, item = printer_with_queue

        resp = await async_client.post(
            f"/api/v1/webhook/printer/{printer.id}/start",
            headers={"X-API-Key": api_key_data},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["queue_item_id"] == item.id

        await db_session.refresh(item)
        assert item.manual_start is False, "manual_start must be cleared so scheduler dispatches"
        # Stored options must be untouched so the scheduler picks the user's choice.
        assert item.timelapse is True
        assert item.bed_levelling == "on"
        assert item.vibration_cali is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_404_when_no_pending_items(self, async_client: AsyncClient, db_session, api_key_data):
        from backend.app.models.printer import Printer

        printer = Printer(
            name="EmptyQueue",
            ip_address="192.168.1.43",
            access_code="12345678",
            serial_number="00M00A000000001",
            model="P1S",
        )
        db_session.add(printer)
        await db_session.commit()

        resp = await async_client.post(
            f"/api/v1/webhook/printer/{printer.id}/start",
            headers={"X-API-Key": api_key_data},
        )

        assert resp.status_code == 404
        assert "No pending prints" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_404_when_printer_does_not_exist(self, async_client: AsyncClient, api_key_data):
        resp = await async_client.post(
            "/api/v1/webhook/printer/99999/start",
            headers={"X-API-Key": api_key_data},
        )
        assert resp.status_code == 404

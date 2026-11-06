"""Regression tests for the webhook printer-status / stop / cancel routes.

Pre-fix the routes treated ``printer_manager.get_status(...)``'s return value
as a dict and called ``.get(...)`` on it. The return is a ``PrinterState``
dataclass (``backend/app/services/bambu_mqtt.py``), so the call raised
``AttributeError`` and surfaced as a generic 500 for any printer that
actually had a status row. See #1584.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import HMSError, PrinterState


@pytest.fixture
async def api_key_data(async_client: AsyncClient, db_session):
    """API key with read_status + control_printer scopes — covers status,
    stop, and cancel in a single fixture."""
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    full_key, key_hash, key_prefix = generate_api_key()
    api_key = APIKey(
        name="webhook-status-test-key",
        key_hash=key_hash,
        key_prefix=key_prefix,
        can_read_status=True,
        can_control_printer=True,
        enabled=True,
    )
    db_session.add(api_key)
    await db_session.commit()
    return full_key


@pytest.fixture
async def printer_row(db_session):
    from backend.app.models.printer import Printer

    printer = Printer(
        name="StatusTest",
        ip_address="192.168.1.44",
        access_code="12345678",
        serial_number="00M00A000000010",
        model="P1S",
    )
    db_session.add(printer)
    await db_session.commit()
    return printer


class TestWebhookGetPrinterStatus:
    """``GET /api/v1/webhook/printer/{id}/status`` — the route reads the
    dataclass via attribute access, not ``.get(...)``. Pre-fix the call
    raised AttributeError → 500 for every printer with a status row.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_200_with_connected_dataclass_status(
        self,
        async_client: AsyncClient,
        api_key_data,
        printer_row,
    ):
        """A live PrinterState dataclass must yield a 200 with the
        attributes mapped into the response — this is the exact regression
        from #1584 where the dataclass crashed the ``.get(...)`` calls."""
        state = PrinterState(
            connected=True,
            state="RUNNING",
            current_print="bench.3mf",
            progress=42.0,
            remaining_time=1234,
        )
        with patch(
            "backend.app.api.routes.webhook.printer_manager.get_status",
            MagicMock(return_value=state),
        ):
            resp = await async_client.get(
                f"/api/v1/webhook/printer/{printer_row.id}/status",
                headers={"X-API-Key": api_key_data},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == printer_row.id
        assert body["name"] == "StatusTest"
        assert body["connected"] is True
        assert body["state"] == "RUNNING"
        assert body["current_print"] == "bench.3mf"
        assert body["progress"] == 42.0
        assert body["remaining_time"] == 1234
        assert body["remaining_seconds"] == 1234 * 60

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_200_when_status_is_none(
        self,
        async_client: AsyncClient,
        api_key_data,
        printer_row,
    ):
        """A registered printer the manager hasn't seen yet returns None from
        ``get_status``; the response must still be 200 with sensible
        defaults rather than 500."""
        with patch(
            "backend.app.api.routes.webhook.printer_manager.get_status",
            MagicMock(return_value=None),
        ):
            resp = await async_client.get(
                f"/api/v1/webhook/printer/{printer_row.id}/status",
                headers={"X-API-Key": api_key_data},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == printer_row.id
        assert body["connected"] is False
        assert body["state"] is None
        assert body["current_print"] is None
        assert body["progress"] is None
        assert body["remaining_time"] is None
        assert body["serial_number"] == "00M00A000000010"
        assert body["remaining_seconds"] is None
        assert body["layer_num"] is None
        assert body["total_layers"] is None
        assert body["subtask_id"] is None
        assert body["hms_errors"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_404_when_printer_does_not_exist(
        self,
        async_client: AsyncClient,
        api_key_data,
    ):
        resp = await async_client.get(
            "/api/v1/webhook/printer/99999/status",
            headers={"X-API-Key": api_key_data},
        )
        assert resp.status_code == 404


async def _status(async_client: AsyncClient, key: str, printer_id: int, state: PrinterState | None):
    with patch(
        "backend.app.api.routes.webhook.printer_manager.get_status",
        MagicMock(return_value=state),
    ):
        return await async_client.get(
            f"/api/v1/webhook/printer/{printer_id}/status",
            headers={"X-API-Key": key},
        )


class TestWebhookPrinterStatusFields:
    """What an external client such as a phone Live Activity needs from the
    status route, all of it already on ``PrinterState`` (#2919)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_running_print_reports_layers_job_and_seconds(
        self, async_client: AsyncClient, api_key_data, printer_row
    ):
        state = PrinterState(
            connected=True,
            state="RUNNING",
            current_print="bench.3mf",
            progress=62.0,
            remaining_time=107,
            layer_num=88,
            total_layers=240,
            subtask_id="512345678",
        )
        body = (await _status(async_client, api_key_data, printer_row.id, state)).json()

        assert body["serial_number"] == "00M00A000000010"
        assert body["layer_num"] == 88
        assert body["total_layers"] == 240
        assert body["subtask_id"] == "512345678"
        # remaining_time stays in minutes for existing clients.
        assert body["remaining_time"] == 107
        assert body["remaining_seconds"] == 6420
        assert body["hms_errors"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_paused_print_says_why(self, async_client: AsyncClient, api_key_data, printer_row):
        """A runout pause carries its HMS fault; a user pause carries none."""
        runout = HMSError(
            code="0x20008",
            attr=0x07008000,
            module=0x07,
            severity=2,
            description="Filament has run out.",
            actions=["RESUME_PRINTING"],
            job_id="512345678",
            full_code="0700800000020008",
        )
        state = PrinterState(connected=True, state="PAUSE", subtask_id="512345678", hms_errors=[runout])
        body = (await _status(async_client, api_key_data, printer_row.id, state)).json()

        assert body["hms_errors"] == [
            {
                "code": "0x20008",
                "attr": 0x07008000,
                "module": 0x07,
                "severity": 2,
                "actions": ["RESUME_PRINTING"],
                "job_id": "512345678",
                "full_code": "0700800000020008",
                "description": "Filament has run out.",
            }
        ]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hms_errors_match_the_printer_status_route(
        self, async_client: AsyncClient, api_key_data, printer_row
    ):
        """One shape for a fault, whether the UI or an API key asks."""
        from backend.app.api.routes.printers import hms_error_responses as used_by_printers_route

        fault = HMSError(code="0x1", attr=0x0300_0100, module=0x03, severity=1, full_code="0300010000000001")
        state = PrinterState(connected=True, state="FAILED", hms_errors=[fault])
        body = (await _status(async_client, api_key_data, printer_row.id, state)).json()

        assert body["hms_errors"] == [e.model_dump() for e in used_by_printers_route([fault])]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_numeric_subtask_id_is_returned_as_text(self, async_client: AsyncClient, api_key_data, printer_row):
        """Stored as the printer sent it; a number must not 500 the poll."""
        state = PrinterState(connected=True, state="RUNNING", subtask_id=512345678)
        resp = await _status(async_client, api_key_data, printer_row.id, state)

        assert resp.status_code == 200, resp.text
        assert resp.json()["subtask_id"] == "512345678"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("raw", ["0", "", " ", 0])
    async def test_a_job_without_an_id_reports_null(self, async_client: AsyncClient, api_key_data, printer_row, raw):
        """Bambu reports "0" or "" for local prints. Every such print would
        share the same "id", so it must not be handed out as one."""
        state = PrinterState(connected=True, state="RUNNING", subtask_id=raw)
        resp = await _status(async_client, api_key_data, printer_row.id, state)

        assert resp.status_code == 200, resp.text
        assert resp.json()["subtask_id"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_idle_printer_reports_zero_not_null(self, async_client: AsyncClient, api_key_data, printer_row):
        """A connected idle printer has real zeros; null is kept for "no status yet"."""
        state = PrinterState(connected=True, state="IDLE")
        body = (await _status(async_client, api_key_data, printer_row.id, state)).json()

        assert body["remaining_time"] == 0
        assert body["remaining_seconds"] == 0
        assert body["layer_num"] == 0
        assert body["total_layers"] == 0
        assert body["subtask_id"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_needs_the_read_status_scope(self, async_client: AsyncClient, db_session, printer_row):
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(
                name="no-status",
                key_hash=key_hash,
                key_prefix=key_prefix,
                can_read_status=False,
                can_queue=True,
                enabled=True,
            )
        )
        await db_session.commit()

        resp = await _status(async_client, full_key, printer_row.id, PrinterState(connected=True))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_key_for_another_printer_sees_nothing(self, async_client: AsyncClient, db_session, printer_row):
        """The serial and faults are only for keys allowed on this printer."""
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(
                name="other-printer",
                key_hash=key_hash,
                key_prefix=key_prefix,
                can_read_status=True,
                printer_ids=[printer_row.id + 1000],
                enabled=True,
            )
        )
        await db_session.commit()

        resp = await _status(async_client, full_key, printer_row.id, PrinterState(connected=True))
        # Out of scope reads as missing, so the id isn't confirmed (#1727)
        assert resp.status_code == 404
        assert "00M00A000000010" not in resp.text


class TestWebhookStopPrint:
    """``POST /api/v1/webhook/printer/{id}/stop`` — same dataclass-shape
    fix applies to the connection / state precondition checks (#1584)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_503_when_disconnected(
        self,
        async_client: AsyncClient,
        api_key_data,
        printer_row,
    ):
        state = PrinterState(connected=False, state="unknown")
        with patch(
            "backend.app.api.routes.webhook.printer_manager.get_status",
            MagicMock(return_value=state),
        ):
            resp = await async_client.post(
                f"/api/v1/webhook/printer/{printer_row.id}/stop",
                headers={"X-API-Key": api_key_data},
            )
        # Pre-fix this would have 500'd on `status.get(...)`. Now it
        # cleanly returns the documented 503.
        assert resp.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_409_when_not_running(
        self,
        async_client: AsyncClient,
        api_key_data,
        printer_row,
    ):
        state = PrinterState(connected=True, state="FINISH")
        with patch(
            "backend.app.api.routes.webhook.printer_manager.get_status",
            MagicMock(return_value=state),
        ):
            resp = await async_client.post(
                f"/api/v1/webhook/printer/{printer_row.id}/stop",
                headers={"X-API-Key": api_key_data},
            )
        assert resp.status_code == 409


class TestWebhookCancelPrint:
    """``POST /api/v1/webhook/printer/{id}/cancel`` — same fix shape."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_503_when_disconnected(
        self,
        async_client: AsyncClient,
        api_key_data,
        printer_row,
    ):
        state = PrinterState(connected=False, state="unknown")
        with patch(
            "backend.app.api.routes.webhook.printer_manager.get_status",
            MagicMock(return_value=state),
        ):
            resp = await async_client.post(
                f"/api/v1/webhook/printer/{printer_row.id}/cancel",
                headers={"X-API-Key": api_key_data},
            )
        assert resp.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_409_when_not_running_or_paused(
        self,
        async_client: AsyncClient,
        api_key_data,
        printer_row,
    ):
        state = PrinterState(connected=True, state="IDLE")
        with patch(
            "backend.app.api.routes.webhook.printer_manager.get_status",
            MagicMock(return_value=state),
        ):
            resp = await async_client.post(
                f"/api/v1/webhook/printer/{printer_row.id}/cancel",
                headers={"X-API-Key": api_key_data},
            )
        assert resp.status_code == 409


async def _send(async_client: AsyncClient, key: str, printer_id: int, action: str, state: str, stop_result: bool):
    """POST stop/cancel with a live-looking printer; returns (response, stop_print mock)."""
    stop_print = MagicMock(return_value=stop_result)
    with (
        patch(
            "backend.app.api.routes.webhook.printer_manager.get_status",
            MagicMock(return_value=PrinterState(connected=True, state=state)),
        ),
        patch("backend.app.api.routes.webhook.printer_manager.stop_print", stop_print),
    ):
        resp = await async_client.post(
            f"/api/v1/webhook/printer/{printer_id}/{action}",
            headers={"X-API-Key": key},
        )
    return resp, stop_print


class TestWebhookStopAndCancelSendTheStop:
    """Both routes send the stop and mark the printer as stopped by the user.

    Pre-fix, /stop awaited the synchronous ``stop_print`` (the stop went out,
    then ``await True`` raised and the route answered 500) and /cancel called a
    ``cancel_print`` that does not exist (500, print kept running). Neither
    marked the printer, so the print ended up classified as failed instead of
    stopped, like a stop from the printer card does.
    """

    @pytest.fixture(autouse=True)
    def _clear_user_stopped(self):
        from backend.app.main import _user_stopped_printers

        _user_stopped_printers.clear()
        yield
        _user_stopped_printers.clear()

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("action", "state"),
        [(action, state) for action in ("stop", "cancel") for state in ("RUNNING", "PAUSE")],
    )
    async def test_sends_the_stop_and_marks_the_printer(self, async_client, api_key_data, printer_row, action, state):
        from backend.app.main import _user_stopped_printers

        resp, stop_print = await _send(async_client, api_key_data, printer_row.id, action, state, stop_result=True)

        assert resp.status_code == 200, resp.text
        stop_print.assert_called_once_with(printer_row.id)
        assert printer_row.id in _user_stopped_printers

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("action", ["stop", "cancel"])
    async def test_reports_503_when_the_stop_cannot_be_sent(self, async_client, api_key_data, printer_row, action):
        from backend.app.main import _user_stopped_printers

        resp, stop_print = await _send(async_client, api_key_data, printer_row.id, action, "RUNNING", stop_result=False)

        assert resp.status_code == 503, resp.text
        stop_print.assert_called_once_with(printer_row.id)
        assert printer_row.id not in _user_stopped_printers

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("action", ["stop", "cancel"])
    async def test_a_print_still_preparing_is_not_stopped(self, async_client, api_key_data, printer_row, action):
        """As on the printer card, whose Stop works only while printing or paused."""
        resp, stop_print = await _send(async_client, api_key_data, printer_row.id, action, "PREPARE", stop_result=True)

        assert resp.status_code == 409
        stop_print.assert_not_called()

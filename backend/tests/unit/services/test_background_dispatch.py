"""Unit tests for background dispatch service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.background_dispatch import (
    ActiveDispatchState,
    BackgroundDispatchService,
    DispatchEnqueueRejected,
    PrintDispatchJob,
)


@pytest.mark.asyncio
async def test_dispatch_rejects_when_printer_busy_printing():
    """Reject enqueue when target printer is already printing."""
    service = BackgroundDispatchService()

    with (
        patch(
            "backend.app.services.background_dispatch.printer_manager.get_status",
            return_value=SimpleNamespace(state="RUNNING", gcode_file="active.gcode.3mf"),
        ),
        pytest.raises(DispatchEnqueueRejected, match="currently busy printing"),
    ):
        await service.dispatch_reprint_archive(
            archive_id=1,
            archive_name="Test Archive",
            printer_id=10,
            printer_name="Printer A",
            options={},
            requested_by_user_id=None,
            requested_by_username=None,
        )


@pytest.mark.asyncio
async def test_dispatch_enqueues_job_and_broadcasts_state():
    """Enqueue succeeds and emits websocket queue update."""
    service = BackgroundDispatchService()

    with (
        patch("backend.app.services.background_dispatch.printer_manager.get_status", return_value=None),
        patch(
            "backend.app.services.background_dispatch.ws_manager.broadcast", new_callable=AsyncMock
        ) as mock_broadcast,
    ):
        result = await service.dispatch_print_library_file(
            file_id=22,
            filename="cube.gcode.3mf",
            printer_id=7,
            printer_name="Printer B",
            options={"plate_id": 2},
            requested_by_user_id=5,
            requested_by_username="tester",
        )

    assert result["status"] == "dispatched"
    assert result["dispatch_job_id"] == 1
    assert result["dispatch_position"] == 1
    assert len(service._queued_jobs) == 1

    mock_broadcast.assert_awaited_once()
    payload = mock_broadcast.await_args.args[0]
    assert payload["type"] == "background_dispatch"
    assert payload["data"]["recent_event"]["status"] == "dispatched"


@pytest.mark.asyncio
async def test_cancel_queued_job_removes_it_and_broadcasts():
    """Cancelling queued job removes it immediately."""
    service = BackgroundDispatchService()

    with (
        patch("backend.app.services.background_dispatch.printer_manager.get_status", return_value=None),
        patch(
            "backend.app.services.background_dispatch.ws_manager.broadcast", new_callable=AsyncMock
        ) as mock_broadcast,
    ):
        result = await service.dispatch_reprint_archive(
            archive_id=1,
            archive_name="benchy.gcode.3mf",
            printer_id=1,
            printer_name="Printer 1",
            options={},
            requested_by_user_id=None,
            requested_by_username=None,
        )
        mock_broadcast.reset_mock()

        cancel_result = await service.cancel_job(result["dispatch_job_id"])

    assert cancel_result["cancelled"] is True
    assert cancel_result["pending"] is False
    assert len(service._queued_jobs) == 0
    assert service._batch_total == 0

    mock_broadcast.assert_awaited_once()
    payload = mock_broadcast.await_args.args[0]
    assert payload["data"]["recent_event"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_active_job_marks_pending_and_sets_cancel_flag():
    """Cancelling active job marks it as pending cancellation."""
    service = BackgroundDispatchService()
    job = PrintDispatchJob(
        id=42,
        kind="reprint_archive",
        source_id=100,
        source_name="gearbox.gcode.3mf",
        printer_id=3,
        printer_name="Printer C",
    )
    service._active_jobs[job.id] = ActiveDispatchState(job=job, message="Uploading...")

    with patch(
        "backend.app.services.background_dispatch.ws_manager.broadcast", new_callable=AsyncMock
    ) as mock_broadcast:
        result = await service.cancel_job(job.id)

    assert result["cancelled"] is True
    assert result["pending"] is True
    assert job.id in service._cancel_requested_job_ids

    mock_broadcast.assert_awaited_once()
    payload = mock_broadcast.await_args.args[0]
    assert payload["data"]["recent_event"]["status"] == "cancelling"


def test_resolve_plate_id_uses_request_value_when_provided(tmp_path):
    """Explicit plate_id wins over auto-detection."""
    file_path = tmp_path / "dummy.3mf"
    file_path.write_text("not-a-zip")

    plate_id = BackgroundDispatchService._resolve_plate_id(file_path, requested_plate_id=9)
    assert plate_id == 9


def test_resolve_plate_id_auto_detects_from_3mf(tmp_path):
    """Auto-detect plate from Metadata/plate_X.gcode entry."""
    import zipfile

    file_path = tmp_path / "multi.3mf"
    with zipfile.ZipFile(file_path, "w") as zf:
        zf.writestr("Metadata/plate_7.gcode", b"G1 X0 Y0")

    plate_id = BackgroundDispatchService._resolve_plate_id(file_path, requested_plate_id=None)
    assert plate_id == 7


def test_is_sliced_file_recognizes_supported_extensions():
    """Only .gcode and .gcode.3mf should be accepted."""
    assert BackgroundDispatchService._is_sliced_file("part.gcode") is True
    assert BackgroundDispatchService._is_sliced_file("part.gcode.3mf") is True
    assert BackgroundDispatchService._is_sliced_file("part.3mf") is False

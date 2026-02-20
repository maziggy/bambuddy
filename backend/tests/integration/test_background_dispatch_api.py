"""Integration tests for background dispatch API behavior."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.background_dispatch import DispatchEnqueueRejected


class TestBackgroundDispatchArchivesAPI:
    """Tests for archive reprint dispatch endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reprint_returns_dispatched_payload(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session, tmp_path
    ):
        """Reprint endpoint returns background dispatch metadata."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            filename="widget.gcode.3mf",
            file_path="archives/test/widget.gcode.3mf",
        )

        archive_file = tmp_path / archive.file_path
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_file.write_bytes(b"3mf-data")

        with (
            patch("backend.app.api.routes.archives.settings.base_dir", tmp_path),
            patch("backend.app.services.printer_manager.printer_manager.is_connected", return_value=True),
            patch(
                "backend.app.services.background_dispatch.background_dispatch.dispatch_reprint_archive",
                new=AsyncMock(return_value={"dispatch_job_id": 15, "dispatch_position": 1}),
            ) as mock_dispatch,
        ):
            response = await async_client.post(
                f"/api/v1/archives/{archive.id}/reprint?printer_id={printer.id}",
                json={"plate_id": 2},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dispatched"
        assert data["dispatch_job_id"] == 15
        assert data["dispatch_position"] == 1
        assert data["filename"] == "widget.gcode.3mf"

        mock_dispatch.assert_awaited_once()
        kwargs = mock_dispatch.await_args.kwargs
        assert kwargs["archive_name"].endswith("• Plate 2")
        assert kwargs["options"]["plate_id"] == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reprint_returns_409_when_enqueue_rejected(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session, tmp_path
    ):
        """Reprint endpoint maps enqueue rejection to HTTP 409."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            filename="widget2.gcode.3mf",
            file_path="archives/test/widget2.gcode.3mf",
        )

        archive_file = tmp_path / archive.file_path
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_file.write_bytes(b"3mf-data")

        with (
            patch("backend.app.api.routes.archives.settings.base_dir", tmp_path),
            patch("backend.app.services.printer_manager.printer_manager.is_connected", return_value=True),
            patch(
                "backend.app.services.background_dispatch.background_dispatch.dispatch_reprint_archive",
                new=AsyncMock(side_effect=DispatchEnqueueRejected("already has a background dispatch")),
            ),
        ):
            response = await async_client.post(
                f"/api/v1/archives/{archive.id}/reprint?printer_id={printer.id}",
                json={"plate_id": 1},
            )

        assert response.status_code == 409
        assert "already has a background dispatch" in response.json()["detail"]


class TestBackgroundDispatchLibraryAPI:
    """Tests for library print dispatch endpoint."""

    @pytest.fixture
    async def library_file_factory(self, db_session):
        """Factory to create library files."""

        async def _create_file(**kwargs):
            from backend.app.models.library import LibraryFile

            defaults = {
                "filename": "library_part.gcode.3mf",
                "file_path": "library/files/library_part.gcode.3mf",
                "file_type": "gcode",
                "file_size": 1024,
            }
            defaults.update(kwargs)
            lib_file = LibraryFile(**defaults)
            db_session.add(lib_file)
            await db_session.commit()
            await db_session.refresh(lib_file)
            return lib_file

        return _create_file

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_print_returns_dispatched_payload(
        self, async_client: AsyncClient, library_file_factory, printer_factory, db_session, tmp_path
    ):
        """Library print endpoint returns dispatch job metadata."""
        printer = await printer_factory()
        lib_file = await library_file_factory()

        disk_path = tmp_path / lib_file.file_path
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_bytes(b"library data")

        with (
            patch("backend.app.api.routes.library.app_settings.base_dir", tmp_path),
            patch("backend.app.services.printer_manager.printer_manager.is_connected", return_value=True),
            patch(
                "backend.app.services.background_dispatch.background_dispatch.dispatch_print_library_file",
                new=AsyncMock(return_value={"dispatch_job_id": 21, "dispatch_position": 2}),
            ) as mock_dispatch,
        ):
            response = await async_client.post(
                f"/api/v1/library/files/{lib_file.id}/print?printer_id={printer.id}",
                json={"plate_id": 4},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dispatched"
        assert data["dispatch_job_id"] == 21
        assert data["dispatch_position"] == 2
        assert data["archive_id"] is None

        mock_dispatch.assert_awaited_once()
        kwargs = mock_dispatch.await_args.kwargs
        assert kwargs["filename"].endswith("• Plate 4")
        assert kwargs["options"]["plate_id"] == 4

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_print_returns_409_when_enqueue_rejected(
        self, async_client: AsyncClient, library_file_factory, printer_factory, db_session, tmp_path
    ):
        """Library print endpoint maps enqueue rejection to HTTP 409."""
        printer = await printer_factory()
        lib_file = await library_file_factory(filename="another_part.gcode")

        disk_path = tmp_path / lib_file.file_path
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_bytes(b"library data")

        with (
            patch("backend.app.api.routes.library.app_settings.base_dir", tmp_path),
            patch("backend.app.services.printer_manager.printer_manager.is_connected", return_value=True),
            patch(
                "backend.app.services.background_dispatch.background_dispatch.dispatch_print_library_file",
                new=AsyncMock(side_effect=DispatchEnqueueRejected("queue conflict")),
            ),
        ):
            response = await async_client.post(
                f"/api/v1/library/files/{lib_file.id}/print?printer_id={printer.id}",
                json={"plate_id": 1},
            )

        assert response.status_code == 409
        assert "queue conflict" in response.json()["detail"]


class TestBackgroundDispatchCancelAPI:
    """Tests for /background-dispatch cancel endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_job_returns_cancelled(self, async_client: AsyncClient):
        """Cancel endpoint returns cancelled for queued job."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.cancel_job",
            new=AsyncMock(
                return_value={
                    "cancelled": True,
                    "pending": False,
                    "job_id": 9,
                    "source_name": "cube.gcode.3mf",
                    "printer_id": 1,
                    "printer_name": "Printer A",
                }
            ),
        ):
            response = await async_client.delete("/api/v1/background-dispatch/9")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"
        assert data["job_id"] == 9

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_job_returns_cancelling_for_active_job(self, async_client: AsyncClient):
        """Cancel endpoint returns cancelling while active upload is being interrupted."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.cancel_job",
            new=AsyncMock(
                return_value={
                    "cancelled": True,
                    "pending": True,
                    "job_id": 10,
                    "source_name": "cube.gcode.3mf",
                    "printer_id": 1,
                    "printer_name": "Printer A",
                }
            ),
        ):
            response = await async_client.delete("/api/v1/background-dispatch/10")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelling"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_job_returns_404_when_not_found(self, async_client: AsyncClient):
        """Cancel endpoint returns 404 for unknown job id."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.cancel_job",
            new=AsyncMock(return_value={"cancelled": False, "reason": "not_found"}),
        ):
            response = await async_client.delete("/api/v1/background-dispatch/999")

        assert response.status_code == 404
        assert response.json()["detail"] == "Dispatch job not found"

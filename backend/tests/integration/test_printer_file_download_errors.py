"""A printer file that could not be fetched is not reported as missing (TO-C2, TO-C3, TO-C1).

The file browser's previews (G-code, plates, plate thumbnails) and its single
file download answered every failure with 404 "File not found", including a
timeout on a file the browser had just listed. They now answer 404 only when
the printer says the path is not there, 502 when the download failed, and 504
when it ran out of the time its size allows. The timelapse scan answered a
failed download with 500; it now gives 502, and 504 for a deadline.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_ftp import DownloadDeadlineExceeded, FileNotOnPrinterError
from backend.app.services.printer_media import PrinterFileDownloadError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PREVIEWS = [
    ("/files/gcode", {"path": "/cache/part.gcode"}),
    ("/files/plates", {"path": "/cache/part.3mf"}),
    ("/files/plate-thumbnail/1", {"path": "/cache/part.3mf"}),
]


@pytest.mark.parametrize(("route", "params"), PREVIEWS)
@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (FileNotOnPrinterError("/cache/part"), 404),
        (None, 502),
        (DownloadDeadlineExceeded("/cache/part"), 504),
    ],
)
async def test_a_preview_says_what_went_wrong(
    async_client: AsyncClient, printer_factory, route, params, outcome, status
):
    printer = await printer_factory()
    download = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)

    with patch("backend.app.api.routes.printers.download_file_bytes_async", download):
        resp = await async_client.get(f"/api/v1/printers/{printer.id}{route}", params=params)

    assert resp.status_code == status, resp.text
    # One FTP session per preview: the plate picker asks once per plate.
    assert download.await_count == 1
    assert download.await_args.kwargs["report_missing"] is True
    # The file browser's budget: with print start's 300 s ceiling a slow file
    # would get no more than the flat base deadline.
    from backend.app.services.printer_media import MAX_PRINTER_ZIP_PREPARE_SECONDS

    assert download.await_args.kwargs["max_total_timeout"] == MAX_PRINTER_ZIP_PREPARE_SECONDS


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (PrinterFileDownloadError("could not be downloaded"), 502),
        (TimeoutError("took longer than its size allows"), 504),
        (FileNotFoundError("Invalid printer file path"), 404),
    ],
)
async def test_the_file_download_says_what_went_wrong(async_client: AsyncClient, printer_factory, raised, status):
    printer = await printer_factory()

    with patch("backend.app.api.routes.printers.build_printer_file", AsyncMock(side_effect=raised)):
        resp = await async_client.get(f"/api/v1/printers/{printer.id}/files/download", params={"path": "/ipcam/a.mp4"})

    assert resp.status_code == status, resp.text


@pytest.mark.parametrize(
    ("outcome", "status"),
    [(None, 502), (DownloadDeadlineExceeded("/timelapse/t.mp4"), 504)],
)
async def test_a_timelapse_scan_says_what_went_wrong(
    async_client: AsyncClient, archive_factory, printer_factory, outcome, status
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, filename="t.gcode.3mf")
    listing = [
        {"name": "t.gcode.mp4", "path": "/timelapse/t.gcode.mp4", "is_directory": False, "size": 10, "mtime": None}
    ]
    download = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)

    with (
        patch("backend.app.services.bambu_ftp.list_files_async", AsyncMock(return_value=listing)),
        patch("backend.app.services.bambu_ftp.get_ftp_retry_settings", AsyncMock(return_value=(True, 3, 0.0, 30.0))),
        patch("backend.app.services.bambu_ftp.download_file_bytes_async", download),
    ):
        resp = await async_client.post(f"/api/v1/archives/{archive.id}/timelapse/scan")

    assert resp.status_code == status, resp.text
    if status == 504:
        assert download.await_count == 1  # a size-fitted deadline is not retried


async def test_an_automatic_timelapse_attach_survives_a_deadline():
    """The background attach treats it as a failed round: the printer keeps its copy."""
    import logging

    from backend.app import main

    printer = MagicMock(ip_address="10.0.0.5", access_code="c", model="P1S")
    download = AsyncMock(side_effect=DownloadDeadlineExceeded("/timelapse/new.mp4"))
    with patch("backend.app.services.bambu_ftp.download_file_bytes_async", download):
        result = await main._attach_first_unclaimed_timelapse(
            1,
            printer,
            [{"name": "new.mp4", "path": "/timelapse/new.mp4", "size": 10}],
            set(),
            set(),
            1,
            logging.getLogger(__name__),
        )

    assert result is False
    download.assert_awaited_once()


async def test_the_plate_picker_downloads_the_file_once(async_client: AsyncClient, printer_factory):
    """The plate list and every plate thumbnail share one download (TO-C2)."""
    import io
    import zipfile

    printer = await printer_factory()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in (1, 2, 3):
            zf.writestr(f"Metadata/plate_{n}.gcode", "; gcode")
            zf.writestr(f"Metadata/plate_{n}.png", b"\x89PNG fake")
    download = AsyncMock(return_value=buf.getvalue())
    params = {"path": "/cache/multi.3mf"}

    with patch("backend.app.api.routes.printers.download_file_bytes_async", download):
        plates = await async_client.get(f"/api/v1/printers/{printer.id}/files/plates", params=params)
        thumbs = [
            await async_client.get(f"/api/v1/printers/{printer.id}/files/plate-thumbnail/{n}", params=params)
            for n in (1, 2, 3)
        ]

    assert plates.status_code == 200, plates.text
    assert [t.status_code for t in thumbs] == [200, 200, 200]
    assert download.await_count == 1


async def test_a_deleted_printer_file_is_downloaded_again(async_client: AsyncClient, printer_factory):
    from backend.app.services.bambu_ftp import DeleteResult

    printer = await printer_factory()
    download = AsyncMock(return_value=b"; gcode")
    params = {"path": "/cache/part.gcode"}

    with (
        patch("backend.app.api.routes.printers.download_file_bytes_async", download),
        patch("backend.app.api.routes.printers.delete_file_async", AsyncMock(return_value=DeleteResult.DELETED)),
    ):
        await async_client.get(f"/api/v1/printers/{printer.id}/files/gcode", params=params)
        deleted = await async_client.delete(f"/api/v1/printers/{printer.id}/files", params=params)
        await async_client.get(f"/api/v1/printers/{printer.id}/files/gcode", params=params)

    assert deleted.status_code == 200, deleted.text
    assert download.await_count == 2

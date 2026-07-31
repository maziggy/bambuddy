from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.services import camera, qsv, qsv_diagnostic


@pytest.fixture(autouse=True)
def reset_ffmpeg_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera, "_ffmpeg_path", None)


def _codec_query(*args: str) -> tuple[int, str, str]:
    if "-decoders" in args:
        return 0, " V..... h264_qsv Intel Quick Sync Video H.264 decoder", ""
    if "-encoders" in args:
        return 0, " V..... mjpeg_qsv MJPEG (Intel Quick Sync Video acceleration)", ""
    raise AssertionError(args)


@pytest.mark.asyncio
async def test_diagnose_qsv_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    device = Path("/dev/dri/renderD129")
    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/custom/ffmpeg")
    monkeypatch.setattr(qsv_diagnostic, "_run_command", AsyncMock(side_effect=_codec_query))
    find_device = AsyncMock(return_value=(device, [qsv.QsvDeviceProbe(device=device, available=True, duration_ms=7)]))
    monkeypatch.setattr(qsv, "find_qsv_render_device", find_device)

    result = await qsv_diagnostic.diagnose_qsv()

    assert result.available is True
    assert result.device == str(device)
    assert [stage.name for stage in result.stages] == [
        "ffmpeg",
        "qsv_codecs",
        "render_device",
        "qsv_initialization",
    ]
    find_device.assert_awaited_once_with("/custom/ffmpeg", refresh=True)


@pytest.mark.asyncio
async def test_diagnose_qsv_reports_ffmpeg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: None)

    result = await qsv_diagnostic.diagnose_qsv()

    assert result.summary_code == "ffmpeg_not_found"
    assert result.stages[0].status == "failed"
    assert all(stage.status == "skipped" for stage in result.stages[1:])


@pytest.mark.asyncio
async def test_diagnose_qsv_checks_codecs_before_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/custom/ffmpeg")
    monkeypatch.setattr(
        qsv_diagnostic,
        "_run_command",
        AsyncMock(
            side_effect=[
                (0, " V..... h264_qsv", ""),
                (0, " V..... libx264", ""),
            ]
        ),
    )
    find_device = AsyncMock()
    monkeypatch.setattr(qsv, "find_qsv_render_device", find_device)

    result = await qsv_diagnostic.diagnose_qsv()

    assert result.summary_code == "mjpeg_qsv_missing"
    assert result.stages[1].name == "qsv_codecs"
    assert result.stages[1].status == "failed"
    assert result.stages[2].name == "render_device"
    assert result.stages[2].status == "skipped"
    find_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_diagnose_qsv_reports_no_render_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/custom/ffmpeg")
    monkeypatch.setattr(qsv_diagnostic, "_run_command", AsyncMock(side_effect=_codec_query))
    monkeypatch.setattr(qsv, "find_qsv_render_device", AsyncMock(return_value=(None, [])))

    result = await qsv_diagnostic.diagnose_qsv()

    assert result.summary_code == "render_device_missing"
    assert result.stages[2].code == "render_device_missing"


@pytest.mark.asyncio
async def test_diagnose_qsv_reports_all_probes_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]
    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/custom/ffmpeg")
    monkeypatch.setattr(qsv_diagnostic, "_run_command", AsyncMock(side_effect=_codec_query))
    monkeypatch.setattr(
        qsv,
        "find_qsv_render_device",
        AsyncMock(
            return_value=(
                None,
                [
                    qsv.QsvDeviceProbe(
                        device=device,
                        available=False,
                        code="qsv_initialization_failed",
                    )
                    for device in devices
                ],
            )
        ),
    )

    result = await qsv_diagnostic.diagnose_qsv()

    assert result.summary_code == "qsv_initialization_failed"
    assert str(devices[0]) in (result.stages[2].detail or "")
    assert str(devices[1]) in (result.stages[2].detail or "")

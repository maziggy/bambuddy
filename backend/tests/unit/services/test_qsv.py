from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.services import qsv


@pytest.fixture(autouse=True)
def reset_qsv_device_cache() -> None:
    qsv.clear_qsv_device_cache()
    yield
    qsv.clear_qsv_device_cache()


def test_list_render_devices_returns_sorted_render_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = qsv._RENDER_DEVICE_DIRECTORY

    monkeypatch.setattr(Path, "is_dir", lambda self: self == directory)
    monkeypatch.setattr(
        Path,
        "glob",
        lambda self, pattern: iter(
            [
                directory / "renderD130",
                directory / "renderD128",
                directory / "renderD129",
            ]
        ),
    )

    assert qsv.list_render_devices() == [
        directory / "renderD128",
        directory / "renderD129",
        directory / "renderD130",
    ]


@pytest.mark.asyncio
async def test_find_qsv_render_device_uses_first_working_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [
        Path("/dev/dri/renderD128"),
        Path("/dev/dri/renderD129"),
        Path("/dev/dri/renderD130"),
    ]

    monkeypatch.setattr(qsv, "list_render_devices", lambda: devices)

    probe = AsyncMock(
        side_effect=[
            qsv.QsvDeviceProbe(
                device=devices[0],
                available=False,
                code="qsv_initialization_failed",
            ),
            qsv.QsvDeviceProbe(device=devices[1], available=True),
        ]
    )
    monkeypatch.setattr(qsv, "probe_qsv_device", probe)

    selected, probes = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")

    assert selected == devices[1]
    assert [result.device for result in probes] == devices[:2]
    assert probe.await_count == 2


@pytest.mark.asyncio
async def test_find_qsv_render_device_returns_none_when_no_device_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [
        Path("/dev/dri/renderD128"),
        Path("/dev/dri/renderD129"),
    ]

    monkeypatch.setattr(qsv, "list_render_devices", lambda: devices)
    monkeypatch.setattr(
        qsv,
        "probe_qsv_device",
        AsyncMock(
            side_effect=[
                qsv.QsvDeviceProbe(
                    device=device,
                    available=False,
                    code="qsv_initialization_failed",
                )
                for device in devices
            ]
        ),
    )

    selected, probes = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")

    assert selected is None
    assert len(probes) == 2


@pytest.mark.asyncio
async def test_find_qsv_render_device_reuses_cached_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD128")
    probe = AsyncMock(
        return_value=qsv.QsvDeviceProbe(
            device=device,
            available=False,
            code="qsv_initialization_failed",
        )
    )
    monkeypatch.setattr(qsv, "list_render_devices", lambda: [device])
    monkeypatch.setattr(qsv, "probe_qsv_device", probe)

    selected, probes = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")
    assert selected is None
    assert len(probes) == 1

    selected, probes = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")
    assert selected is None
    assert len(probes) == 1
    assert probe.await_count == 1


@pytest.mark.asyncio
async def test_find_qsv_render_device_refresh_retries_cached_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD128")
    probe = AsyncMock(
        side_effect=[
            qsv.QsvDeviceProbe(
                device=device,
                available=False,
                code="qsv_initialization_failed",
            ),
            qsv.QsvDeviceProbe(device=device, available=True),
        ]
    )
    monkeypatch.setattr(qsv, "list_render_devices", lambda: [device])
    monkeypatch.setattr(qsv, "probe_qsv_device", probe)

    selected, _ = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")
    assert selected is None

    selected, probes = await qsv.find_qsv_render_device(
        "/usr/bin/ffmpeg",
        refresh=True,
    )
    assert selected == device
    assert [result.device for result in probes] == [device]
    assert probe.await_count == 2


@pytest.mark.asyncio
async def test_find_qsv_render_device_reuses_cached_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD129")

    qsv.clear_qsv_device_cache()
    monkeypatch.setattr(qsv.os, "access", lambda path, mode: path == device)

    probe = AsyncMock(return_value=qsv.QsvDeviceProbe(device=device, available=True))
    monkeypatch.setattr(qsv, "list_render_devices", lambda: [device])
    monkeypatch.setattr(qsv, "probe_qsv_device", probe)

    selected, probes = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")
    assert selected == device
    assert len(probes) == 1

    selected, probes = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")
    assert selected == device
    assert probes == []
    assert probe.await_count == 1

    qsv.clear_qsv_device_cache()


@pytest.mark.asyncio
async def test_find_qsv_render_device_refreshes_cached_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Path("/dev/dri/renderD128")
    second = Path("/dev/dri/renderD129")

    qsv.clear_qsv_device_cache()
    monkeypatch.setattr(qsv.os, "access", lambda path, mode: True)

    devices = [first]
    monkeypatch.setattr(qsv, "list_render_devices", lambda: devices)

    probe = AsyncMock(
        side_effect=[
            qsv.QsvDeviceProbe(device=first, available=True),
            qsv.QsvDeviceProbe(device=second, available=True),
        ]
    )
    monkeypatch.setattr(qsv, "probe_qsv_device", probe)

    selected, _ = await qsv.find_qsv_render_device("/usr/bin/ffmpeg")
    assert selected == first

    devices[:] = [second]
    selected, probes = await qsv.find_qsv_render_device(
        "/usr/bin/ffmpeg",
        refresh=True,
    )

    assert selected == second
    assert [result.device for result in probes] == [second]
    assert probe.await_count == 2

    qsv.clear_qsv_device_cache()


def test_clear_qsv_device_cache() -> None:
    device = Path("/dev/dri/renderD128")

    qsv._qsv_device_cache = device
    qsv._qsv_probe_failure_cache = (qsv.QsvDeviceProbe(device=device, available=False),)
    qsv.clear_qsv_device_cache()

    assert qsv._qsv_device_cache is None
    assert qsv._qsv_probe_failure_cache is None


@pytest.mark.asyncio
async def test_probe_qsv_device_runs_real_pipeline_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD129")
    monkeypatch.setattr(Path, "exists", lambda self: self == device)
    monkeypatch.setattr(qsv.os, "access", lambda path, mode: path == device)

    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (b"", b"")
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(qsv.asyncio, "create_subprocess_exec", create_process)

    result = await qsv.probe_qsv_device("/custom/ffmpeg", device)

    assert result.available is True
    create_process.assert_awaited_once()
    command = create_process.await_args.args
    assert command == qsv.build_qsv_probe_command("/custom/ffmpeg", device)


@pytest.mark.asyncio
async def test_probe_qsv_device_reports_ffmpeg_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD129")
    monkeypatch.setattr(Path, "exists", lambda self: self == device)
    monkeypatch.setattr(qsv.os, "access", lambda path, mode: path == device)

    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (b"", b"MFX session failed")
    monkeypatch.setattr(
        qsv.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )

    result = await qsv.probe_qsv_device("/custom/ffmpeg", device)

    assert result.available is False
    assert result.code == "qsv_initialization_failed"
    assert result.detail == "MFX session failed"


@pytest.mark.asyncio
async def test_probe_qsv_device_reports_missing_render_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD999")
    monkeypatch.setattr(Path, "exists", lambda self: False)

    create_process = AsyncMock()
    monkeypatch.setattr(
        qsv.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    result = await qsv.probe_qsv_device("/custom/ffmpeg", device)

    assert result.available is False
    assert result.code == "render_device_missing"
    assert result.detail == str(device)
    create_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_qsv_device_reports_render_device_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD128")
    monkeypatch.setattr(Path, "exists", lambda self: self == device)
    monkeypatch.setattr(qsv.os, "access", lambda path, mode: False)

    create_process = AsyncMock()
    monkeypatch.setattr(
        qsv.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    result = await qsv.probe_qsv_device("/custom/ffmpeg", device)

    assert result.available is False
    assert result.code == "render_device_permission_denied"
    assert result.detail == str(device)
    create_process.assert_not_awaited()


def test_classify_qsv_failure_reports_missing_runtime() -> None:
    stderr = b"Error creating a MFX session: -9."

    assert qsv._classify_qsv_failure(stderr) == "qsv_runtime_missing"


def test_classify_qsv_failure_keeps_generic_initialization_error() -> None:
    stderr = b"MFX session failed"

    assert qsv._classify_qsv_failure(stderr) == "qsv_initialization_failed"


@pytest.mark.asyncio
async def test_probe_qsv_device_reports_missing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Path("/dev/dri/renderD128")
    monkeypatch.setattr(Path, "exists", lambda self: self == device)
    monkeypatch.setattr(qsv.os, "access", lambda path, mode: path == device)

    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (
        b"",
        b"Error creating a MFX session: -9.",
    )
    monkeypatch.setattr(
        qsv.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )

    result = await qsv.probe_qsv_device("/custom/ffmpeg", device)

    assert result.available is False
    assert result.code == "qsv_runtime_missing"
    assert result.detail == "Error creating a MFX session: -9."

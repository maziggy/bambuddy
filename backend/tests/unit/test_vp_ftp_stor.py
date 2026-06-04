"""Tests for the FTPSession.cmd_STOR streaming + size-cap behaviour.

The original cmd_STOR buffered the entire upload in a ``list[bytes]`` and
called ``write_bytes`` at the end. For multi-GB ``.gcode.3mf`` files this
peaked at ~2× the file size in RSS (chunks held + the ``b''.join`` of
them) and could OOM low-memory hosts. The streaming rewrite writes each
chunk to disk inline (memory bounded at one chunk) and enforces
``MAX_UPLOAD_BYTES``. These tests pin both behaviours without standing
up a real TLS/FTP server.
"""

import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.virtual_printer.ftp_server import MAX_UPLOAD_BYTES, FTPSession


def _make_session(tmp_path, *, data_chunks: list[bytes]) -> FTPSession:
    """Build an FTPSession primed with a pre-fed StreamReader so cmd_STOR
    can iterate through the chunks without a real TCP connection.
    """
    control_writer = MagicMock()
    control_writer.write = MagicMock()
    control_writer.drain = AsyncMock()
    control_writer.get_extra_info = MagicMock(return_value=("192.168.1.99", 12345))

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    session = FTPSession(
        reader=asyncio.StreamReader(),
        writer=control_writer,
        upload_dir=upload_dir,
        access_code="deadbeef",
        ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER),
        on_file_received=None,
        bind_address="127.0.0.1",
        vp_name="stor-test",
    )
    session.authenticated = True

    data_reader = asyncio.StreamReader()
    for chunk in data_chunks:
        data_reader.feed_data(chunk)
    data_reader.feed_eof()
    session._data_reader = data_reader

    data_writer = MagicMock()
    data_writer.close = MagicMock()
    data_writer.wait_closed = AsyncMock()
    session._data_writer = data_writer

    session._data_connected.set()
    session.data_server = None

    return session


@pytest.mark.asyncio
async def test_stor_writes_payload_to_disk(tmp_path):
    """Happy path: chunks fed to the data reader land in the upload_dir
    with the right content + the slicer gets 226."""
    payload = b"X" * (3 * 64 * 1024 + 123)  # 3 chunks + a partial one
    chunks = [payload[i : i + 65536] for i in range(0, len(payload), 65536)]
    session = _make_session(tmp_path, data_chunks=chunks)
    session.send = AsyncMock()

    await session.cmd_STOR("Untitled.gcode.3mf")

    saved = session.upload_dir / "Untitled.gcode.3mf"
    assert saved.exists()
    assert saved.stat().st_size == len(payload)
    assert saved.read_bytes() == payload

    sent_codes = [args[0][0] for args in session.send.call_args_list]
    assert 150 in sent_codes  # "Opening data connection"
    assert 226 in sent_codes  # "Transfer complete"


@pytest.mark.asyncio
async def test_stor_rejects_upload_over_max_upload_bytes(tmp_path, monkeypatch):
    """A single chunk taking us over the cap must abort with 426 and
    drop the partially-written file so it doesn't masquerade as a
    successful upload."""
    # Lower the cap to 100 KiB so the test doesn't need to allocate
    # 4 GiB to trigger it. The same logic governs the production cap.
    monkeypatch.setattr(
        "backend.app.services.virtual_printer.ftp_server.MAX_UPLOAD_BYTES",
        100 * 1024,
    )

    over_cap = b"X" * (200 * 1024)  # 200 KiB > 100 KiB cap
    session = _make_session(tmp_path, data_chunks=[over_cap])
    session.send = AsyncMock()

    await session.cmd_STOR("toobig.gcode.3mf")

    # Partial file must be unlinked.
    assert not (session.upload_dir / "toobig.gcode.3mf").exists()
    # 426 (transfer failed) sent — not 226.
    sent_codes = [args[0][0] for args in session.send.call_args_list]
    assert 426 in sent_codes
    assert 226 not in sent_codes


@pytest.mark.asyncio
async def test_stor_cleans_up_partial_file_on_read_error(tmp_path):
    """If the data channel raises mid-transfer (slicer RST, TLS error,
    timeout, …), the partial file on disk must be removed so the next
    upload of the same name starts clean and the user doesn't see a
    truncated file in the upload_dir."""
    payload = b"X" * 65536  # one full chunk
    session = _make_session(tmp_path, data_chunks=[payload])
    session.send = AsyncMock()

    # Inject an OSError on the NEXT read after the first chunk.
    orig_read = session._data_reader.read
    state = {"calls": 0}

    async def read_then_error(n):
        state["calls"] += 1
        if state["calls"] == 1:
            return await orig_read(n)
        raise OSError("simulated connection reset")

    session._data_reader.read = read_then_error  # type: ignore[assignment]

    await session.cmd_STOR("aborted.gcode.3mf")

    # Partial file removed.
    assert not (session.upload_dir / "aborted.gcode.3mf").exists()
    sent_codes = [args[0][0] for args in session.send.call_args_list]
    assert 426 in sent_codes


def test_max_upload_bytes_is_at_least_4_gib():
    """The cap exists to prevent OOM, but should be high enough that
    legitimate multi-plate .gcode.3mf uploads (~hundreds of MB) succeed
    without bumping up against it. 4 GiB is the documented floor."""
    assert MAX_UPLOAD_BYTES >= 4 * 1024 * 1024 * 1024

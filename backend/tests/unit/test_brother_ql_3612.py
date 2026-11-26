"""Brother QL raster protocol, status parsing and TCP send (#3612). No real printer."""

from __future__ import annotations

import gc
import inspect
import random
import socket
import threading
import time
import weakref
from unittest.mock import patch

import pytest
from PIL import Image

from backend.app.services import label_renderer
from backend.app.services.label_printers.base import (
    LabelPrinterConfig,
    PrinterBusy,
    PrinterDidNotFinish,
    PrinterReportedError,
    PrinterUnreachable,
    WrongMedia,
    parse_printer_address,
)
from backend.app.services.label_printers.brother_ql import (
    INITIALIZE,
    INVALIDATE,
    LOCK_TIMEOUT_S,
    BrotherQLDriver,
    _printer_lock,
    _StatusReader,
    build_job,
    fit_to_tape,
    job_timeout_s,
    max_job_timeout_s,
    packbits,
    parse_status,
    prepare_job,
    rasterize,
    send,
)
from backend.app.services.label_printers.brother_ql_media import TAPES
from backend.app.services.label_renderer import (
    LabelData,
    RollLayout,
    iter_pdf_images,
    render_labels,
)


def packbits_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        n = data[i]
        i += 1
        if n < 128:
            out += data[i : i + n + 1]
            i += n + 1
        elif n > 128:
            out += bytes([data[i]]) * (257 - n)
            i += 1
    return bytes(out)


def page_ends(job: bytes) -> list[int]:
    """Offsets just after each print command (0x1A), walking the command stream.
    A form feed (0x0C) as a command is a failure: brother_ql never sends one."""
    i, ends = len(INVALIDATE), []
    sizes = {b"ia": 4, b"iz": 13, b"iM": 4, b"iA": 4, b"iK": 4, b"id": 5}
    while i < len(job):
        if job[i] == 0x67:  # g 00 n data
            i += 3 + job[i + 2]
        elif job[i] == 0x1A:
            i += 1
            ends.append(i)
        elif job[i : i + 2] == b"M\x02" or job[i : i + 2] == b"\x1b@":
            i += 2
        elif job[i] == 0x1B:
            i += sizes[job[i + 1 : i + 3]]
        else:
            raise AssertionError(f"unexpected command byte {job[i]:#04x} at {i}")
    return ends


def make_image(tape: str, height: int = 4) -> Image.Image:
    return Image.new("L", (TAPES[tape].printable_dots, height), 255)


def pin_is_black(line: bytes, pin: int) -> bool:
    return bool(line[pin // 8] & (0x80 >> (pin % 8)))


class TestRaster:
    @pytest.mark.parametrize("tape", list(TAPES))
    def test_lines_are_90_bytes_one_per_row(self, tape):
        lines = rasterize(make_image(tape, 7), tape)
        assert len(lines) == 7
        assert all(len(line) == 90 for line in lines)

    def test_wrong_width_is_refused(self):
        with pytest.raises(ValueError):
            rasterize(Image.new("L", (500, 3), 255), "50")

    def test_fit_to_tape_pads_a_pixel_and_refuses_more(self):
        assert fit_to_tape(Image.new("L", (553, 2), 255), "50").width == 554
        assert fit_to_tape(Image.new("L", (556, 2), 255), "50").width == 554
        with pytest.raises(ValueError):
            fit_to_tape(Image.new("L", (400, 2), 255), "50")

    @pytest.mark.parametrize(("tape", "margin"), [("50", 12), ("62", 12), ("54", 0), ("12", 29)])
    def test_margin_and_mirroring(self, tape, margin):
        dots = TAPES[tape].printable_dots
        image = make_image(tape, 1)
        image.putpixel((0, 0), 0)  # leftmost column
        line = rasterize(image, tape)[0]
        # Mirrored: image column 0 is the LAST printable pin of the line.
        assert pin_is_black(line, margin + dots - 1)
        assert sum(bin(b).count("1") for b in line) == 1
        image = make_image(tape, 1)
        image.putpixel((dots - 1, 0), 0)  # rightmost column -> first printable pin
        line = rasterize(image, tape)[0]
        assert pin_is_black(line, margin)
        assert not any(pin_is_black(line, p) for p in range(margin))

    def test_threshold_black_is_a_set_bit(self):
        image = make_image("62", 1)
        image.putpixel((10, 0), 127)
        image.putpixel((11, 0), 128)
        line = rasterize(image, "62")[0]
        assert sum(bin(b).count("1") for b in line) == 1


class TestPackBits:
    def test_round_trip_random_rows(self):
        rnd = random.Random(1)
        for _ in range(500):
            row = bytes(rnd.choice((0, 0, 255, rnd.randrange(256))) for _ in range(90))
            assert packbits_decode(packbits(row)) == row

    def test_known_encodings(self):
        assert packbits(b"\x00" * 90) == bytes([257 - 90, 0])
        assert packbits(b"\x01\x02\x03") == b"\x02\x01\x02\x03"
        assert packbits(b"\xaa\xaa\xaa\xaa\x01") == b"\xfd\xaa\x00\x01"
        assert packbits(b"") == b""

    def test_long_runs_and_literals_split_at_limits(self):
        assert packbits_decode(packbits(b"\x07" * 300)) == b"\x07" * 300
        literal = bytes(range(200))
        assert packbits_decode(packbits(literal)) == literal


class TestBuildJob:
    def test_command_sequence_compressed_50mm(self):
        job = build_job([make_image("50", 10)], "50", cut=True, compress=True)
        assert job.startswith(b"\x00" * 400 + b"\x1b@" + b"\x1bia\x01")
        body = job[400 + 2 + 4 :]
        # ESC i z: flags, continuous (0x0A), 50 mm, length 0, 10 lines LE, first page.
        assert body.startswith(b"\x1biz\xce\x0a\x32\x00\x0a\x00\x00\x00\x00\x00")
        assert body[13:].startswith(b"\x1biM\x40\x1biA\x01\x1biK\x08\x1bid\x23\x00M\x02")
        assert job.endswith(b"\x1a")

    def test_raster_lines_compressed_blank(self):
        job = build_job([make_image("50", 3)], "50")
        # A blank line: "g" 00, length 2, packbits(90 x 00) = A7 00.
        assert job.count(b"g\x00\x02\xa7\x00") == 3

    def test_uncompressed_has_no_compression_command_and_full_lines(self):
        job = build_job([make_image("50", 3)], "50", compress=False)
        assert b"M\x02" not in job
        assert job.count(b"g\x00\x5a" + b"\x00" * 90) == 3

    def test_no_cut(self):
        job = build_job([make_image("50", 3)], "50", cut=False)
        assert b"\x1biM\x00" in job
        assert b"\x1biA" not in job
        assert b"\x1biK\x00" in job

    def test_every_page_is_a_complete_job_like_brother_ql(self):
        job = build_job([make_image("50", 3), make_image("50", 5)], "50", compress=False)
        assert job.count(b"\x1biz") == 2
        pages = job.split(b"\x1biz")[1:]
        for page, lines in zip(pages, (3, 5), strict=True):
            # Page byte 0 on every page, the full header, 0x1A at the end.
            assert page[:10] == b"\xce\x0a\x32\x00" + lines.to_bytes(4, "little") + b"\x00\x00"
            assert page[10:].startswith(b"\x1biM\x40\x1biA\x01\x1biK\x08\x1bid\x23\x00")
            assert page.endswith(b"\x1a")
        assert page_ends(job)[-1] == len(job)
        assert len(page_ends(job)) == 2

    def test_62mm_media_width(self):
        job = build_job([make_image("62", 2)], "62")
        assert b"\x1biz\xce\x0a\x3e\x00\x02\x00\x00\x00\x00\x00" in job

    def test_nothing_to_print(self):
        with pytest.raises(ValueError):
            build_job([], "50")

    def test_decoded_lines_match_rasterize(self):
        image = make_image("50", 4)
        image.putpixel((5, 2), 0)
        expected = rasterize(image, "50")
        job = build_job([image], "50", compress=True)
        lines, i = [], job.index(b"M\x02") + 2
        while job[i : i + 2] == b"g\x00":
            n = job[i + 2]
            lines.append(packbits_decode(job[i + 3 : i + 3 + n]))
            i += 3 + n
        assert lines == expected


def status_block(*, width=50, media=0x0A, err1=0, err2=0, status_type=0x00, phase=0x00, notification=0) -> bytes:
    data = bytearray(32)
    data[0:3] = b"\x80\x20\x42"
    data[3] = 0x34
    data[4] = 0x41  # QL-820NWB
    data[5] = data[6] = 0x30  # fixed "0"s
    data[8], data[9] = err1, err2
    data[10], data[11] = width, media
    data[18], data[19], data[22] = status_type, phase, notification
    return bytes(data)


class TestParseStatus:
    def test_50mm_continuous_no_error(self):
        status = parse_status(status_block())
        assert (status.media_width_mm, status.media_type, status.errors) == (50, "continuous", [])

    def test_cover_open(self):
        assert "cover open" in parse_status(status_block(err2=0x10)).errors

    def test_no_media(self):
        status = parse_status(status_block(width=0, media=0, err1=0x01))
        assert status.errors == ["no tape loaded"]
        assert status.media_type == "none"

    def test_several_errors(self):
        status = parse_status(status_block(err1=0x04, err2=0x40))
        assert status.errors == ["cutter jam", "tape cannot be fed"]

    def test_wrong_media_width_is_reported_as_loaded(self):
        assert parse_status(status_block(width=62)).media_width_mm == 62

    def test_completed_and_cooling(self):
        assert parse_status(status_block(status_type=0x01)).status_type == "completed"
        assert parse_status(status_block(status_type=0x05, notification=0x03)).cooling

    def test_garbage_is_refused(self):
        with pytest.raises(ValueError):
            parse_status(b"\x00" * 32)
        with pytest.raises(ValueError):
            parse_status(b"\x80\x20\x42")


class FakePrinter:
    """TCP server on a free port: records what it receives, optionally answers."""

    def __init__(self, replies=None, after_job=None):
        self.replies = replies or []  # sent once ESC i S arrives
        self.after_job = after_job or []  # sent once the job's final 0x1A arrives
        self.received = b""
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        conn, _ = self.server.accept()
        conn.settimeout(5)
        answered = finished = False
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                self.received += chunk
                if not answered and b"\x1biS" in self.received:
                    answered = True
                    for reply in self.replies:
                        conn.sendall(reply)
                if not finished and self.received.endswith(b"\x1a"):
                    finished = True
                    for reply in self.after_job:
                        conn.sendall(reply)
        except OSError:
            pass
        finally:
            conn.close()
            self.server.close()

    def close(self):
        self.thread.join(timeout=5)


def config(port: int, tape: str = "50") -> LabelPrinterConfig:
    return LabelPrinterConfig(type="brother_ql", host="127.0.0.1", port=port, model="QL-820NWB", tape=tape)


JOB = build_job([make_image("50", 3)], "50")


class TestSend:
    def test_sends_the_job_after_a_status_request(self):
        printer = FakePrinter(replies=[status_block()], after_job=[status_block(status_type=0x01)])
        status = send(config(printer.port), JOB)
        printer.close()
        assert status is not None and status.media_width_mm == 50
        assert printer.received == INVALIDATE + INITIALIZE + b"\x1biS" + JOB

    def test_printer_without_status_still_prints(self):
        printer = FakePrinter()
        started = time.monotonic()
        status = send(config(printer.port), JOB, status_timeout=0.3, silent_grace=0.3)
        printer.close()
        assert status is None
        assert printer.received.endswith(JOB)
        assert time.monotonic() - started < 3

    def test_error_status_refuses_to_print(self):
        printer = FakePrinter(replies=[status_block(err2=0x10)])
        with pytest.raises(PrinterReportedError) as exc:
            send(config(printer.port), JOB)
        printer.close()
        assert exc.value.reasons == ["cover open"]
        assert JOB not in printer.received

    def test_wrong_tape(self):
        printer = FakePrinter(replies=[status_block(width=62)])
        with pytest.raises(WrongMedia) as exc:
            send(config(printer.port), JOB)
        printer.close()
        assert (exc.value.expected, exc.value.loaded) == ("50 mm", "62 mm")
        assert JOB not in printer.received

    def test_error_reported_after_the_job(self):
        printer = FakePrinter(replies=[status_block()], after_job=[status_block(err1=0x01, status_type=0x02)])
        with pytest.raises(PrinterReportedError):
            send(config(printer.port), JOB)
        printer.close()

    def test_unreachable(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        started = time.monotonic()
        with pytest.raises(PrinterUnreachable):
            send(config(port), JOB, connect_timeout=1)
        assert time.monotonic() - started < 5

    def test_driver_prepares_and_delivers_over_tcp(self):
        printer = FakePrinter(replies=[status_block()], after_job=[status_block(status_type=0x01)])
        driver = BrotherQLDriver()
        driver.deliver(driver.prepare([make_image("50", 3)], config(printer.port)), config(printer.port))
        printer.close()
        assert printer.received.endswith(b"\x1a")
        assert b"\x1biz\xce\x0a\x32" in printer.received

    def test_driver_test_page(self):
        printer = FakePrinter(replies=[status_block()], after_job=[status_block(status_type=0x01)])
        driver = BrotherQLDriver()
        driver.deliver(driver.test_job(config(printer.port)), config(printer.port))
        printer.close()
        assert printer.received.count(b"\x1biz") == 1

    def test_test_page_without_the_bundled_fonts(self, tmp_path):
        # Builds without backend/app/data/fonts must still print a test label.
        from backend.app.services.label_printers import brother_ql

        missing = tmp_path / "missing.ttf"
        with patch.object(brother_ql, "_FONT", missing), patch.object(brother_ql, "_FONT_BOLD", missing):
            job = BrotherQLDriver().test_job(config(9100))
        assert job.pages == 1 and job.data


class TestAddress:
    def test_host_and_port(self):
        assert parse_printer_address("192.168.1.50") == ("192.168.1.50", 9100)
        assert parse_printer_address("label.local:9101") == ("label.local", 9101)

    @pytest.mark.parametrize("value", ["169.254.169.254", "metadata.google.internal", "a:0", "a:70000", "a b", ""])
    def test_refused(self, value):
        with pytest.raises(ValueError):
            parse_printer_address(value)


# ── Multi-label jobs, slow and silent printers (#3612) ───────────────────────


def phase(kind=0x06, ph=0x01):
    return status_block(status_type=kind, phase=ph)


class ScriptedPrinter:
    """A printer that accepts several connections (each in its own thread). It
    reads the job at ``rate`` bytes/s, answers the status request, and after each
    label it has received calls ``on_page(conn, index)`` (default: phase change,
    completed, phase change, like the QL). Records what it got and whether the
    client closed cleanly (FIN) or reset."""

    def __init__(
        self, job: bytes, *, rate=None, reply=True, on_page=None, stop_after=None, stall_after=None, rcvbuf=None
    ):
        self.job, self.rate, self.reply = job, rate, reply
        self.on_page = self._default_page if on_page is None else on_page
        self.stop_after, self.stall_after = stop_after, stall_after
        self.ends = page_ends(job)
        self.got = bytearray()
        self.pages_sent = 0
        self.reset = False
        self.clean_close = False
        self.active = self.max_active = self.connections = 0
        self._lock = threading.Lock()
        self.server = socket.socket()
        if rcvbuf:
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        self.threads: list[threading.Thread] = []
        self.acceptor = threading.Thread(target=self._accept, daemon=True)
        self.acceptor.start()

    @staticmethod
    def _default_page(conn, index):
        conn.sendall(phase(0x06, 0x01) + status_block(status_type=0x01) + phase(0x06, 0x00))

    def _accept(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            thread = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _serve(self, conn):
        with self._lock:
            self.active += 1
            self.connections += 1
            self.max_active = max(self.max_active, self.active)
        conn.settimeout(10)
        try:
            pre = b""
            while len(pre) < len(INVALIDATE) + 2 + 3:
                chunk = conn.recv(len(INVALIDATE) + 5 - len(pre))
                if not chunk:
                    return
                pre += chunk
            if self.reply:
                conn.sendall(status_block())
            got = sent = 0
            while got < len(self.job):
                if self.stall_after is not None and got >= self.stall_after:
                    time.sleep(1.5)  # stops reading, keeps the connection open
                    return
                chunk = conn.recv(min(4096, len(self.job) - got))
                if not chunk:
                    return
                self.got += chunk
                got += len(chunk)
                if self.stop_after is not None and got >= self.stop_after:
                    return  # hangs up mid-job
                while sent < len(self.ends) and got >= self.ends[sent]:
                    sent += 1
                    self.pages_sent = max(self.pages_sent, sent)
                    self.on_page(conn, sent - 1)
                if self.rate:
                    time.sleep(len(chunk) / self.rate)
            try:
                self.clean_close = conn.recv(4096) == b""
            except ConnectionResetError:
                self.reset = True
        except OSError:
            self.reset = True
        finally:
            with self._lock:
                self.active -= 1
            conn.close()

    def close(self):
        self.server.close()
        for thread in self.threads:
            thread.join(timeout=15)


def textured(length: int) -> Image.Image:
    image = Image.new("L", (TAPES["50"].printable_dots, length), 255)
    for y in range(0, length, 7):
        for x in range(0, image.width, 3):
            image.putpixel((x, y), 0)
    return image


@pytest.fixture
def small_send_buffer():
    """Client sockets with a tiny send buffer, so sendall really waits for a slow printer."""
    real = socket.create_connection

    def create(*args, **kwargs):
        sock = real(*args, **kwargs)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        return sock

    with patch("backend.app.services.label_printers.brother_ql.socket.create_connection", create):
        yield


class TestMultiLabelJobs:
    @pytest.mark.parametrize("pages", [1, 2, 3])
    def test_every_byte_and_every_page_arrive_without_a_reset(self, pages):
        job = prepare_job([textured(60 + 20 * i) for i in range(pages)], "50")
        printer = ScriptedPrinter(job.data)
        status = send(config(printer.port), job.data, pages=job.pages, lines=job.lines)
        printer.close()
        assert status is not None
        assert bytes(printer.got) == job.data
        assert printer.pages_sent == pages
        assert printer.clean_close and not printer.reset

    def test_returns_only_after_the_last_label_is_confirmed(self):
        job = prepare_job([textured(40)] * 3, "50")
        seen = []

        def on_page(conn, index):
            time.sleep(0.2)  # the printer takes its time per label
            seen.append(index)
            conn.sendall(status_block(status_type=0x01))

        printer = ScriptedPrinter(job.data, on_page=on_page)
        send(config(printer.port), job.data, pages=3)
        assert seen == [0, 1, 2]  # not cut off at the first "completed"
        printer.close()
        assert bytes(printer.got) == job.data

    def test_slow_reading_printer_with_a_large_job_succeeds(self, small_send_buffer):
        # 490 KB at ~250 KB/s: sending takes seconds, far longer than the status timeout.
        job = prepare_job([textured(900)] * 6, "50", compress=False)
        assert len(job.data) > 290_000
        printer = ScriptedPrinter(job.data, rate=250_000, rcvbuf=4096)
        started = time.monotonic()
        send(config(printer.port), job.data, pages=job.pages, lines=job.lines, status_timeout=0.3)
        assert time.monotonic() - started > 1.0
        printer.close()
        assert bytes(printer.got) == job.data and printer.pages_sent == 6

    def test_silent_printer_succeeds_once_the_job_is_sent(self):
        job = prepare_job([textured(60)] * 2, "50")
        printer = ScriptedPrinter(job.data, reply=False, on_page=lambda c, i: None)
        started = time.monotonic()
        assert send(config(printer.port), job.data, pages=2, status_timeout=0.3, silent_grace=0.3) is None
        assert time.monotonic() - started < 3
        printer.close()
        assert bytes(printer.got) == job.data
        assert printer.clean_close and not printer.reset

    def test_printer_without_status_pushes_succeeds(self):
        # It answers the status request, but sends nothing about the labels.
        job = prepare_job([textured(60)] * 2, "50")
        printer = ScriptedPrinter(job.data, on_page=lambda c, i: None)
        assert send(config(printer.port), job.data, pages=2, silent_grace=0.3) is not None
        printer.close()

    def test_printer_that_hangs_up_mid_job_is_an_error(self):
        job = prepare_job([textured(900)] * 4, "50", compress=False)
        printer = ScriptedPrinter(job.data, stop_after=len(job.data) // 2)
        with pytest.raises(PrinterDidNotFinish):
            send(config(printer.port), job.data, pages=4)
        printer.close()

    def test_hang_up_during_the_send_is_not_unreachable(self, small_send_buffer):
        job = prepare_job([textured(900)] * 6, "50", compress=False)
        printer = ScriptedPrinter(job.data, stop_after=60_000, rcvbuf=4096)
        with pytest.raises(PrinterDidNotFinish):
            send(config(printer.port), job.data, pages=6, status_timeout=0.3)
        printer.close()

    def test_send_timeout_after_a_partial_send_is_did_not_finish(self, small_send_buffer):
        job = prepare_job([textured(900)] * 6, "50", compress=False)
        printer = ScriptedPrinter(job.data, stall_after=30_000, rcvbuf=4096)
        started = time.monotonic()
        with pytest.raises(PrinterDidNotFinish):
            send(config(printer.port), job.data, pages=6, status_timeout=0.3, send_timeout=0.4, finish_timeout=0.3)
        assert time.monotonic() - started < 3
        printer.close()

    def test_printer_that_stops_confirming_is_did_not_finish(self):
        job = prepare_job([textured(60)] * 3, "50")

        def first_only(conn, index):
            if index == 0:
                conn.sendall(phase() + status_block(status_type=0x01))

        printer = ScriptedPrinter(job.data, on_page=first_only)
        with pytest.raises(PrinterDidNotFinish, match="1 of 3"):
            send(config(printer.port), job.data, pages=3, send_timeout=0.3, finish_timeout=0.5)
        printer.close()

    def test_cooling_notification_extends_the_deadline(self):
        job = prepare_job([textured(60)], "50")

        def cool_then_finish(conn, index):
            conn.sendall(status_block(status_type=0x05, notification=0x03))
            time.sleep(0.8)
            conn.sendall(status_block(status_type=0x01))

        printer = ScriptedPrinter(job.data, on_page=cool_then_finish)
        assert send(config(printer.port), job.data, pages=1, send_timeout=0.2, finish_timeout=0.2) is not None
        printer.close()

    @pytest.mark.parametrize(
        ("block", "reasons"),
        [
            ({"status_type": 0x02}, ["printer reported an error"]),
            ({"status_type": 0x04}, ["printer turned off"]),
            ({"status_type": 0x02, "err1": 0x80}, ["fan motor error"]),
            ({"status_type": 0x02, "err1": 0x04}, ["cutter jam"]),
            ({"status_type": 0x02, "err2": 0x40}, ["tape cannot be fed"]),
            ({"status_type": 0x01, "err2": 0x10}, ["cover open"]),
        ],
    )
    def test_error_reports_after_the_job(self, block, reasons):
        job = prepare_job([textured(60)] * 2, "50")
        printer = ScriptedPrinter(job.data, on_page=lambda c, i: c.sendall(status_block(**block)))
        with pytest.raises(PrinterReportedError) as exc:
            send(config(printer.port), job.data, pages=2)
        printer.close()
        assert exc.value.reasons == reasons

    def test_error_type_before_the_job_refuses_it(self):
        job = prepare_job([textured(60)], "50")
        for block in (status_block(status_type=0x02), status_block(status_type=0x04)):
            printer = FakePrinter(replies=[block])
            with pytest.raises(PrinterReportedError):
                send(config(printer.port), job.data, pages=1)
            printer.close()
            assert job.data not in printer.received

    def test_prints_one_at_a_time_per_printer(self):
        job = prepare_job([textured(60)], "50")

        def slow(conn, index):
            time.sleep(0.3)
            conn.sendall(status_block(status_type=0x01))

        printer = ScriptedPrinter(job.data, on_page=slow)
        threads = [
            threading.Thread(target=send, args=(config(printer.port), job.data), kwargs={"pages": 1}) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        printer.close()
        assert printer.connections == 3
        assert printer.max_active == 1


class TestStatusBits:
    def test_fan_motor_and_system_error_bits(self):
        assert parse_status(status_block(err1=0x80)).errors == ["fan motor error"]
        assert parse_status(status_block(err2=0x80)).errors == ["system error"]

    def test_turned_off_status_type(self):
        assert parse_status(status_block(status_type=0x04)).status_type == "turned off"


class TestLazyPipeline:
    def test_prepare_job_holds_one_page_image_at_a_time(self):
        refs: list[weakref.ref] = []
        most = 0

        def pages():
            nonlocal most
            for _ in range(6):
                image = textured(80)
                refs.append(weakref.ref(image))
                gc.collect()
                most = max(most, sum(r() is not None for r in refs))
                yield image
                del image

        job = prepare_job(pages(), "50")
        assert job.pages == 6
        assert most == 1  # the previous page was gone before the next one existed

    def test_pdf_pages_come_one_at_a_time(self):
        data = [
            LabelData(spool_id=i, name=f"S{i}", material="PLA", brand="B", deeplink_url=f"http://x/{i}")
            for i in (1, 2, 3)
        ]
        pdf = render_labels(RollLayout(tape="50", length_mm=40), data, monochrome=True)
        pages = iter_pdf_images(pdf, 300, grayscale=True)
        assert inspect.isgenerator(pages)
        first = next(pages)
        assert first.mode == "L"
        assert len(list(pages)) == 2


class TestSingleDeadline:
    def test_big_slow_job_succeeds_on_the_one_deadline(self, small_send_buffer):
        # ~2 s to send: far over the send budget alone, but the printer keeps up and
        # confirms every label, so the shared send-and-print deadline holds.
        job = prepare_job([textured(900)] * 6, "50", compress=False)
        printer = ScriptedPrinter(job.data, rate=250_000, rcvbuf=4096)
        started = time.monotonic()
        send(config(printer.port), job.data, pages=6, send_timeout=0.4, finish_timeout=4.0, status_timeout=0.3)
        assert time.monotonic() - started > 1.0
        printer.close()
        assert bytes(printer.got) == job.data and printer.pages_sent == 6

    def test_the_deadline_is_the_sum_not_each_part_again(self, small_send_buffer):
        job = prepare_job([textured(900)] * 6, "50", compress=False)
        printer = ScriptedPrinter(job.data, rate=100_000, rcvbuf=4096)  # ~5 s to take it all
        started = time.monotonic()
        with pytest.raises(PrinterDidNotFinish):
            send(config(printer.port), job.data, pages=6, send_timeout=0.5, finish_timeout=0.5, status_timeout=0.3)
        assert time.monotonic() - started < 3  # about 1 s, not send + a fresh finish allowance
        printer.close()

    def test_cooling_is_not_in_the_normal_bound(self):
        job = prepare_job([textured(60)] * 2, "50")
        assert max_job_timeout_s(job) - job_timeout_s(job) == pytest.approx(3 * 120.0 + LOCK_TIMEOUT_S)
        assert job_timeout_s(job) < 60

    def test_nothing_sent_is_busy_not_did_not_finish(self):
        job = prepare_job([textured(60)], "50")
        printer = ScriptedPrinter(job.data)
        with (
            patch.object(socket.socket, "send", side_effect=TimeoutError),
            pytest.raises(PrinterBusy, match="took no data"),
        ):
            send(config(printer.port), job.data, send_timeout=0.2, finish_timeout=0.1, status_timeout=0.2)
        printer.close()

    def test_busy_when_the_lock_is_held(self):
        job = prepare_job([textured(60)], "50")
        lock = _printer_lock("127.0.0.1", 1)
        cfg = LabelPrinterConfig(type="brother_ql", host="127.0.0.1", port=1)
        with lock:
            started = time.monotonic()
            with pytest.raises(PrinterBusy):
                send(cfg, job.data, lock_timeout=0.3)
        assert time.monotonic() - started < 2

    def test_zero_lock_timeout_fails_at_once_when_held_and_works_when_free(self):
        job = prepare_job([textured(60)], "50")
        cfg = LabelPrinterConfig(type="brother_ql", host="127.0.0.1", port=1)
        with _printer_lock("127.0.0.1", 1):
            started = time.monotonic()
            with pytest.raises(PrinterBusy):
                send(cfg, job.data, lock_timeout=0)
        assert time.monotonic() - started < 0.4
        printer = ScriptedPrinter(job.data)
        assert send(config(printer.port), job.data, lock_timeout=0) is not None  # free: no waiting needed
        printer.close()

    def test_progress_callback_reports_each_confirmed_label(self):
        job = prepare_job([textured(40)] * 3, "50")
        printer = ScriptedPrinter(job.data, on_page=lambda c, i: c.sendall(status_block(status_type=0x01)))
        seen: list[int] = []
        send(config(printer.port), job.data, pages=3, on_progress=seen.append)
        printer.close()
        assert seen == [1, 2, 3]

    def test_progress_callback_keeps_the_count_when_the_printer_then_fails(self):
        job = prepare_job([textured(40)] * 3, "50")

        def on_page(conn, index):
            conn.sendall(status_block(status_type=0x01) if index < 2 else status_block(err1=0x04))

        printer = ScriptedPrinter(job.data, on_page=on_page)
        seen: list[int] = []
        with pytest.raises(PrinterReportedError):
            send(config(printer.port), job.data, pages=3, on_progress=seen.append)
        printer.close()
        assert seen == [1, 2]

    def test_abandon_stops_a_stalled_send(self, small_send_buffer):
        job = prepare_job([textured(900)] * 6, "50", compress=False)
        printer = ScriptedPrinter(job.data, stall_after=30_000, rcvbuf=4096)
        abandon = threading.Event()
        threading.Timer(0.5, abandon.set).start()
        started = time.monotonic()
        with pytest.raises(PrinterDidNotFinish):
            send(config(printer.port), job.data, pages=6, status_timeout=0.3, abandon=abandon)  # default budget 40 s
        assert time.monotonic() - started < 3
        printer.close()

    def test_abandon_while_waiting_for_confirmations(self):
        job = prepare_job([textured(60)] * 3, "50")
        printer = ScriptedPrinter(job.data, on_page=lambda c, i: c.sendall(phase()) if i == 0 else None)
        abandon = threading.Event()
        threading.Timer(0.5, abandon.set).start()
        started = time.monotonic()
        with pytest.raises(PrinterDidNotFinish, match="abandoned"):
            send(config(printer.port), job.data, pages=3, abandon=abandon)
        assert time.monotonic() - started < 3
        printer.close()


class TestStatusResync:
    @staticmethod
    def reader_with(data: bytes) -> _StatusReader:
        a, b = socket.socketpair()
        b.sendall(data)
        b.close()
        return _StatusReader(a)

    def test_magic_inside_other_data_is_skipped_one_byte(self):
        junk = b"\x80\x20\x42" + b"\x11" * 10  # looks like a block start, but the fixed bytes are wrong
        reader = self.reader_with(junk + status_block(err2=0x10))
        status = reader.next(time.monotonic() + 1)
        assert status is not None and status.errors == ["cover open"]

    @pytest.mark.parametrize("offset", [3, 7])
    def test_a_block_with_bad_fixed_bytes_is_not_taken(self, offset):
        block = bytearray(status_block())
        block[offset] = 0x55
        reader = self.reader_with(bytes(block))
        with pytest.raises(EOFError):
            reader.next(time.monotonic() + 1)

    def test_zero_and_ascii_zero_in_bytes_5_6_are_both_fine(self):
        for fill in (0x00, 0x30):
            block = bytearray(status_block())
            block[5] = block[6] = fill
            assert self.reader_with(bytes(block)).next(time.monotonic() + 1) is not None


class TestCloseKind:
    def test_error_resets_the_connection_success_closes_gracefully(self):
        job = prepare_job([textured(60)] * 2, "50")
        bad = ScriptedPrinter(job.data, on_page=lambda c, i: c.sendall(status_block(status_type=0x02, err2=0x10)))
        with pytest.raises(PrinterReportedError):
            send(config(bad.port), job.data, pages=2)
        bad.close()
        assert bad.reset and not bad.clean_close  # the peer saw an RST

        good = ScriptedPrinter(job.data)
        send(config(good.port), job.data, pages=2)
        good.close()
        assert good.clean_close and not good.reset  # a FIN


class TestPngDownloadIsLazy:
    def test_pdf_pages_are_lazy_in_pdf_to_pngs(self):
        refs: list[weakref.ref] = []
        alive_with_new: list[int] = []
        real = label_renderer.iter_pdf_images

        def spy(pdf, dpi, **kwargs):
            iterator = real(pdf, dpi, **kwargs)
            while (image := next(iterator, None)) is not None:
                refs.append(weakref.ref(image))
                gc.collect()
                alive_with_new.append(sum(r() is not None for r in refs))
                yield image
                del image

        data = [
            LabelData(spool_id=i, name=f"S{i}", material="PLA", brand="B", deeplink_url=f"http://x/{i}")
            for i in range(4)
        ]
        pdf = render_labels(RollLayout(tape="50", length_mm=40), data, monochrome=True)
        with patch.object(label_renderer, "iter_pdf_images", spy):
            pngs = label_renderer.pdf_to_pngs(pdf, 100)
        assert len(pngs) == 4 and all(p.startswith(b"\x89PNG") for p in pngs)
        # Each page was encoded and dropped before the next one existed: only the new one is alive.
        assert alive_with_new == [1, 1, 1, 1]

"""Log lines written before a redaction existed are masked when read.

The filters only mask lines written from now on; ``bambuddy.log`` can still
hold older ones with a camera login (an access line with the camera URL in
its query, a URL in a shape an older pattern missed). Lines are masked as
they are read, before the search filter, so a search cannot confirm a secret
either.
"""

from backend.app.core.config import settings
from backend.app.services.log_reader import read_log_entries

ACCESS = (
    "2026-10-01 10:00:00,000 INFO [uvicorn.access] [-] 127.0.0.1:1 - "
    '"POST /api/v1/printers/1/camera/external/test'
    '?url=rtsp%3A%2F%2Fadmin%3Aold-pw-1%40cam%2Fs&camera_type=rtsp HTTP/1.1" 200'
)
APP = (
    "2026-10-01 10:00:01,000 INFO [backend.app.services.external_camera] [x] "
    "Testing camera connection: type=rtsp, url=rtsp://:old-pw-2@10.0.0.5/s..."
)
TRACE = [
    "2026-10-01 10:00:02,000 ERROR [backend.app.services.external_camera] [x] Stream failed",
    "  Input #0, rtsp, from 'http://old-token-3@10.0.0.5/snap.jpg':",
]


def _write(tmp_path, monkeypatch):
    (tmp_path / "bambuddy.log").write_text("\n".join([ACCESS, APP, *TRACE]) + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "log_dir", tmp_path)


def test_old_lines_come_back_masked(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)

    entries, _total = read_log_entries(limit=50)

    text = "\n".join(e.message for e in entries)
    assert len(entries) == 3
    for secret in ("old-pw-1", "old-pw-2", "old-token-3"):
        assert secret not in text
    assert "camera_type=rtsp" in text


def test_a_search_cannot_confirm_a_secret(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)

    for secret in ("old-pw-1", "old-pw-2", "old-token-3"):
        entries, _total = read_log_entries(limit=50, search=secret)
        assert entries == [], secret

"""Regression test for #1353: layer timelapse must start for queue/VP-dispatched prints.

Reporter @Andlar94 ran the external-camera flow on an A1 dispatched via the
print queue (so each print landed in the on_print_start "expected archive"
branch). Frames were never captured, no MP4 was produced, yet the post-print
log line said "Stitching layer timelapse for printer 1" — because
`tl_complete()` ran, found no active session, and silently returned None.

Root cause: only the two new-archive code paths in on_print_start
(`fallback_archive` + `archive_print`) called `layer_timelapse.start_session`.
The expected-archive branch — where reprints and queue dispatch land —
updated the existing archive's status to "printing" but never started a
timelapse session.

Fix: the three start_session call sites in on_print_start were unified
behind `_maybe_start_layer_timelapse(printer, printer_id, archive_id)`,
which gates on the same `external_camera_enabled and external_camera_url`
check. Testing the helper directly (instead of driving the whole
on_print_start flow) keeps this regression locked in without dragging in
unrelated side effects (plate detection, DB queries, MQTT relay, etc.).
"""

from types import SimpleNamespace
from unittest.mock import patch

from backend.app.main import _maybe_start_layer_timelapse


def _make_printer(*, external_camera_enabled: bool, external_camera_url: str | None):
    """Construct a minimal printer-shaped object with exactly the attributes
    the helper reads. SimpleNamespace is used over MagicMock so attribute
    access raises AttributeError on anything unexpected — keeps the test
    honest about which fields the helper actually depends on.
    """
    return SimpleNamespace(
        external_camera_enabled=external_camera_enabled,
        external_camera_url=external_camera_url,
        external_camera_type="snapshot",
        external_camera_snapshot_url=external_camera_url,
    )


def test_starts_timelapse_when_external_camera_enabled():
    """Queue/VP-dispatched prints land in the expected-archive branch and must
    start the timelapse session there (the #1353 root cause). The helper is
    called from all three on_print_start paths (expected-archive promotion,
    fallback archive, fresh archive) so testing it once covers all three."""
    printer = _make_printer(
        external_camera_enabled=True,
        external_camera_url="http://camera.local:5000/snapshot.jpg",
    )

    with patch("backend.app.services.layer_timelapse.start_session") as mock_start_session:
        started = _maybe_start_layer_timelapse(printer, printer_id=1, archive_id=42)

    assert started is True
    mock_start_session.assert_called_once_with(
        1,
        42,
        "http://camera.local:5000/snapshot.jpg",
        "snapshot",
        snapshot_url="http://camera.local:5000/snapshot.jpg",
    )


def test_skips_timelapse_when_external_camera_disabled():
    """The same guard that the new-archive paths use must hold here: no
    external camera → no timelapse session. Otherwise we'd try to capture
    from a None URL and crash the print-start flow."""
    printer = _make_printer(external_camera_enabled=False, external_camera_url=None)

    with patch("backend.app.services.layer_timelapse.start_session") as mock_start_session:
        started = _maybe_start_layer_timelapse(printer, printer_id=1, archive_id=99)

    assert started is False
    mock_start_session.assert_not_called()


def test_skips_timelapse_when_url_missing_even_if_flag_set():
    """If the toggle is on but the URL field is empty (legacy / half-configured
    install), the guard must still hold — calling start_session with an empty
    URL would crash downstream when the capture thread tries to fetch frames."""
    printer = _make_printer(external_camera_enabled=True, external_camera_url=None)

    with patch("backend.app.services.layer_timelapse.start_session") as mock_start_session:
        started = _maybe_start_layer_timelapse(printer, printer_id=1, archive_id=7)

    assert started is False
    mock_start_session.assert_not_called()


def test_camera_type_defaults_to_mjpeg_when_unset():
    """external_camera_type defaults to 'mjpeg' in start_session when the
    printer column is None — pre-existing contract preserved by the helper."""
    printer = SimpleNamespace(
        external_camera_enabled=True,
        external_camera_url="http://cam/feed",
        external_camera_type=None,
        external_camera_snapshot_url=None,
    )

    with patch("backend.app.services.layer_timelapse.start_session") as mock_start_session:
        _maybe_start_layer_timelapse(printer, printer_id=2, archive_id=11)

    assert mock_start_session.called
    call_kwargs = mock_start_session.call_args.kwargs
    call_args = mock_start_session.call_args.args
    assert call_args[3] == "mjpeg"
    assert call_kwargs["snapshot_url"] is None

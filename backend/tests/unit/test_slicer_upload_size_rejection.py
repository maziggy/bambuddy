"""The sidecar's upload cap, reported as something the user can act on (#2802).

The slicer sidecar bounds the size of the model it will accept. multer raises
that rejection as a ``MulterError``, which is not the sidecar's ``AppError`` —
so on every image built before the cap became configurable, the sidecar's error
handler fell through to its default status and answered:

    HTTP 500 {"message": "File too large"}

A 500 reads as "the slicer crashed". Bambuddy's one good message about request
size lived behind ``if response.status_code == 413``, so it never fired, and the
reporter of #2802 spent an evening setting ``MAX_FILE_SIZE``,
``BODY_PARSER_LIMIT`` and ``EXPRESS_PAYLOAD_LIMIT`` and stopping nginx — none of
which the sidecar reads, on a proxy that was never in the path.

Two things follow, and both are pinned here:

- The rejection is recognised by its *text*, not its status, so it is handled
  the same whether the sidecar is old (500) or current (413).
- It raises ``SlicerInputError`` rather than ``SlicerApiServerError``. That is
  what stops ``POST /library/files/{id}/slice`` retrying the identical
  oversized upload "with embedded settings" — a second 25-second 3MF
  conversion for a guaranteed-identical answer, which the reporter's log shows
  happening on every attempt.
"""

import httpx
import pytest

from backend.app.services.slicer_api import (
    SlicerApiServerError,
    SlicerApiService,
    SlicerApiUnavailableError,
    SlicerInputError,
    _transport_error_reason,
)

SLICE_ARGS = {
    "model_bytes": b"x" * (3 * 1024 * 1024),
    "model_filename": "0399 Bidoof.3mf",
    "printer_profile_json": "{}",
    "process_profile_json": "{}",
    "filament_profile_jsons": ["{}"],
}


def _service(handler) -> SlicerApiService:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SlicerApiService("http://sidecar:3001", client=client)


def _responder(status_code: int, payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return handler


class TestOversizeUploadIsRecognised:
    @pytest.mark.asyncio
    async def test_a_500_file_too_large_is_treated_as_bad_input(self):
        """The exact shape an un-updated sidecar returns."""
        svc = _service(_responder(500, {"message": "File too large"}))

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        assert "too large" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_a_413_from_a_current_sidecar_is_handled_the_same(self):
        """Once the sidecar maps MulterError properly it sends 413 instead."""
        svc = _service(
            _responder(
                413,
                {
                    "message": "The model file exceeds this slicer's 512 MB upload limit.",
                    "details": "Raise it by setting MAX_MODEL_UPLOAD_MB.",
                },
            )
        )

        with pytest.raises(SlicerInputError):
            await svc.slice_with_profiles(**SLICE_ARGS)

    @pytest.mark.asyncio
    async def test_it_is_not_a_server_error(self):
        """The distinction the retry logic in library.py branches on.

        ``SlicerApiServerError`` is the "the CLI fell over, try the other
        request shape" signal. An upload the sidecar never accepted is not
        that, and retrying it uploads the same too-big file again.
        """
        svc = _service(_responder(500, {"message": "File too large"}))

        with pytest.raises(SlicerInputError):
            await svc.slice_with_profiles(**SLICE_ARGS)

        # Belt and braces: SlicerInputError must not be a subclass of the type
        # the fallback catches, or the branch above is decorative.
        assert not issubclass(SlicerInputError, SlicerApiServerError)


class TestTheMessageIsActionable:
    @pytest.mark.asyncio
    async def test_it_names_the_model_size(self):
        """Support packages carried no size at all; #2802 had to be probed."""
        svc = _service(_responder(500, {"message": "File too large"}))

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        assert "3 MB" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_it_rules_out_the_layers_the_reporter_tried(self):
        """Naming the wrong knobs is the point: they were tried first."""
        svc = _service(_responder(500, {"message": "File too large"}))

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        message = str(excinfo.value)
        assert "reverse-proxy" in message
        assert "client_max_body_size" in message

    @pytest.mark.asyncio
    async def test_an_old_sidecar_is_told_to_update_not_to_set_a_variable(self):
        """There is no env var to set on an image that predates the cap.

        Telling that user to set MAX_MODEL_UPLOAD_MB would send them round the
        loop the reporter already did: change a setting, restart, no effect.
        """
        svc = _service(_responder(500, {"message": "File too large"}))

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        message = str(excinfo.value)
        assert "docker compose pull" in message
        assert "100 MB" in message

    @pytest.mark.asyncio
    async def test_the_update_command_names_the_service(self):
        """A bare ``docker compose pull`` does not update the Bambu sidecar.

        ``bambu-studio-api`` is declared with ``profiles: [bambu]``, and compose
        skips profile-gated services unless the profile is enabled or the
        service is named. The advice this message used to give was therefore a
        no-op for Bambu Studio users -- they pulled, saw "up to date", restarted
        into the same 100 MB image and came back to the issue (#2802).

        Both commands are checked: pulling the right image is useless if the
        ``up -d`` that follows leaves the old container running.
        """
        svc = _service(_responder(500, {"message": "File too large"}))

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        message = str(excinfo.value)
        assert "docker compose pull orca-slicer-api" in message
        assert "docker compose up -d orca-slicer-api" in message
        assert "bambu-studio-api" in message
        # The bare forms must not appear at all -- a reader who copies the first
        # command they see must not get the one that silently does nothing.
        assert "docker compose pull &&" not in message
        assert "docker compose up -d'" not in message

    @pytest.mark.asyncio
    async def test_a_current_sidecar_is_told_which_variable_to_set(self):
        """Once the image is current, the fix is one env var, not another pull."""
        svc = _service(
            _responder(
                413,
                {"message": "The model file exceeds this slicer's 512 MB upload limit."},
            )
        )

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        message = str(excinfo.value)
        assert "MAX_MODEL_UPLOAD_MB" in message
        assert "docker compose pull" not in message

    @pytest.mark.asyncio
    async def test_it_keeps_what_the_sidecar_said(self):
        """Never swallow the upstream text — it identifies the sidecar version."""
        svc = _service(_responder(413, {"message": "The model file exceeds this slicer's 256 MB upload limit."}))

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        assert "256 MB" in str(excinfo.value)


class TestOtherFailuresAreUnaffected:
    @pytest.mark.asyncio
    async def test_an_ordinary_cli_failure_is_still_a_server_error(self):
        """The embedded-settings fallback must keep working for real crashes."""
        svc = _service(
            _responder(
                500,
                {
                    "message": "Slicing failed with error from slicer",
                    "details": "Slicer process failed (exit code 250)",
                },
            )
        )

        with pytest.raises(SlicerApiServerError):
            await svc.slice_with_profiles(**SLICE_ARGS)

    @pytest.mark.asyncio
    async def test_a_cli_error_that_merely_mentions_a_large_file_is_not_hijacked(self):
        """A 500 only counts as an upload rejection if that is all it says.

        The slicer's own diagnostics land in ``details``, and treating one of
        those as a size rejection would rob it of the embedded-settings retry
        that exists to recover from CLI failures.
        """
        svc = _service(
            _responder(
                500,
                {
                    "message": "Slicing failed with error from slicer",
                    "details": "stderr: output file too large to write",
                },
            )
        )

        with pytest.raises(SlicerApiServerError):
            await svc.slice_with_profiles(**SLICE_ARGS)

    @pytest.mark.asyncio
    async def test_a_proxy_413_still_names_the_proxy(self):
        """A 413 that is *not* the sidecar's own cap is a proxy body limit.

        Those really are fixed with ``client_max_body_size``, so that advice
        has to survive — the new branch must not swallow every 413.
        """
        svc = _service(_responder(413, {"message": "<html>413 Request Entity Too Large</html>"}))

        with pytest.raises(SlicerInputError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        assert "client_max_body_size" in str(excinfo.value)


class TestTransportErrorsAlwaysNameSomething:
    """Three lines of the #2802 support package read "unreachable: " and stop."""

    def test_an_exception_with_no_message_falls_back_to_its_type(self):
        assert _transport_error_reason(httpx.ConnectError("")) == "ConnectError"

    def test_a_real_message_is_preferred(self):
        assert _transport_error_reason(httpx.ConnectError("All connection attempts failed")) == (
            "All connection attempts failed"
        )

    @pytest.mark.asyncio
    async def test_the_slice_path_never_reports_an_empty_reason(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("")

        svc = _service(handler)

        with pytest.raises(SlicerApiUnavailableError) as excinfo:
            await svc.slice_with_profiles(**SLICE_ARGS)

        assert str(excinfo.value).strip().endswith("ReadError")

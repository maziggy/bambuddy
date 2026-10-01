"""The Bambu Cloud fallback for /printers/{id}/cover (#2910).

The FTP path searches for the active print's 3MF by NAME, built from `subtask_name`. Two things
defeat that. On H2-series printers the file is on internal eMMC and FTPS on :990 only exposes
external storage. And `subtask_name` is not always a file name at all — a print started from a
MakerWorld profile reports the PROFILE TITLE, so there is nothing in it to match.

`subtask_id` identifies the JOB rather than the file, and the cloud task it keys carries the sliced
plate render — the same `Metadata/plate_<n>.png` the FTP path was trying to unzip.

Everything here is best-effort by design: each failure returns None so the caller's existing 404
stands. These tests pin *which* failures return None, because a fallback that raised would turn a
missing decoration into a broken endpoint.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.api.routes.printers import _cloud_task_cover
from backend.app.services.bambu_cloud import BambuCloudAuthError, BambuCloudError

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

# Captured BEFORE any patching. `patch("...printers.httpx.AsyncClient")` replaces the attribute
# on the shared httpx module, so a factory that called `httpx.AsyncClient` itself would recurse
# into its own patch — which it did, as a RecursionError, on the first run.
_RealAsyncClient = httpx.AsyncClient


def _status(subtask_id):
    return SimpleNamespace(subtask_id=subtask_id)


class _Session:
    """A stand-in for `database.async_session()` that yields a session whose execute() is scripted."""

    def __init__(self, execute_result):
        self._execute_result = execute_result

    async def __aenter__(self):
        session = MagicMock()
        session.execute = AsyncMock(return_value=self._execute_result)
        return session

    async def __aexit__(self, *exc):
        return False


def _no_users():
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    return result


def _user(token="tok", region="global"):
    result = MagicMock()
    result.scalars.return_value.first.return_value = SimpleNamespace(cloud_token=token, cloud_region=region)
    return result


@pytest.mark.asyncio
async def test_no_subtask_id_means_no_lookup():
    """The identifier the whole fallback rests on. Absent, "" and the firmware's "0" all mean the
    printer has not told us which job this is — and "0" is the one that would slip through a
    truthiness test."""
    for value in (None, "", "0", 0):
        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_status.return_value = _status(value)
            assert await _cloud_task_cover(2, "anything") is None


@pytest.mark.asyncio
async def test_no_stored_session_means_no_lookup():
    """A deployment that has never signed in to Bambu Cloud gets the old 404, not an exception."""
    with (
        patch("backend.app.api.routes.printers.printer_manager") as pm,
        patch("backend.app.api.routes.printers.get_stored_token", AsyncMock(return_value=(None, None, "global"))),
        patch("backend.app.api.routes.printers.database.async_session", lambda: _Session(_no_users())),
    ):
        pm.get_status.return_value = _status("123")
        assert await _cloud_task_cover(2, "job") is None


@pytest.mark.asyncio
async def test_a_per_user_session_is_used_when_there_is_no_global_one():
    """Auth-enabled deployments key the session to a user, and this route is authenticated by a
    camera stream token so it has no user context. The lowest user id holding a session is used."""
    cloud = MagicMock()
    cloud.get_task = AsyncMock(return_value={"cover": "https://cdn.example.com/plate_1.png"})
    cloud.close = AsyncMock()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=PNG))

    with (
        patch("backend.app.api.routes.printers.printer_manager") as pm,
        patch("backend.app.api.routes.printers.get_stored_token", AsyncMock(return_value=(None, None, "global"))),
        patch("backend.app.api.routes.printers.database.async_session", lambda: _Session(_user())),
        patch("backend.app.api.routes.printers.BambuCloudService", return_value=cloud),
        patch(
            "backend.app.api.routes.printers.httpx.AsyncClient",
            lambda *a, t=transport, **kw: _RealAsyncClient(transport=t),
        ),
    ):
        pm.get_status.return_value = _status("123")
        assert await _cloud_task_cover(2, "job") == PNG
    cloud.set_token.assert_called_once_with("tok")
    cloud.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_rejected_or_unreachable_cloud_is_not_an_error():
    """Both cloud failure modes end the same way. The session may have expired, or Bambu may be
    down; neither should turn a missing picture into a failed request."""
    for boom in (BambuCloudAuthError("expired"), BambuCloudError("502")):
        cloud = MagicMock()
        cloud.get_task = AsyncMock(side_effect=boom)
        cloud.close = AsyncMock()
        with (
            patch("backend.app.api.routes.printers.printer_manager") as pm,
            patch("backend.app.api.routes.printers.get_stored_token", AsyncMock(return_value=("tok", None, "global"))),
            patch("backend.app.api.routes.printers.database.async_session", lambda: _Session(_no_users())),
            patch("backend.app.api.routes.printers.BambuCloudService", return_value=cloud),
        ):
            pm.get_status.return_value = _status("123")
            assert await _cloud_task_cover(2, "job") is None
        # The client is closed even on the failing path.
        cloud.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_task_without_a_usable_cover_is_not_fetched():
    """A task can exist and carry no cover, or carry something that is not an https URL. Neither is
    worth a request."""
    for task in (
        {},
        {"cover": ""},
        {"cover": None},
        {"cover": 12345},
        {"cover": "http://cdn.example.com/x.png"},
        {"cover": "file:///etc/passwd"},
    ):
        cloud = MagicMock()
        cloud.get_task = AsyncMock(return_value=task)
        cloud.close = AsyncMock()
        with (
            patch("backend.app.api.routes.printers.printer_manager") as pm,
            patch("backend.app.api.routes.printers.get_stored_token", AsyncMock(return_value=("tok", None, "global"))),
            patch("backend.app.api.routes.printers.database.async_session", lambda: _Session(_no_users())),
            patch("backend.app.api.routes.printers.BambuCloudService", return_value=cloud),
        ):
            pm.get_status.return_value = _status("123")
            assert await _cloud_task_cover(2, "job") is None, task


@pytest.mark.asyncio
async def test_a_body_that_is_not_a_png_is_refused():
    """The CDN needs no credential, so a non-200 means the object is gone. And a 200 is not enough:
    an error page is a 200 somewhere, and handing non-image bytes back as `image/png` would put a
    broken picture on a card instead of no picture."""
    for response in (
        httpx.Response(404, content=b""),
        httpx.Response(200, content=b"<html>nope</html>"),
        httpx.Response(200, content=b""),
    ):
        cloud = MagicMock()
        cloud.get_task = AsyncMock(return_value={"cover": "https://cdn.example.com/p.png"})
        cloud.close = AsyncMock()
        transport = httpx.MockTransport(lambda request, r=response: r)
        with (
            patch("backend.app.api.routes.printers.printer_manager") as pm,
            patch("backend.app.api.routes.printers.get_stored_token", AsyncMock(return_value=("tok", None, "global"))),
            patch("backend.app.api.routes.printers.database.async_session", lambda: _Session(_no_users())),
            patch("backend.app.api.routes.printers.BambuCloudService", return_value=cloud),
            patch(
                "backend.app.api.routes.printers.httpx.AsyncClient",
                lambda *a, t=transport, **kw: _RealAsyncClient(transport=t),
            ),
        ):
            pm.get_status.return_value = _status("123")
            assert await _cloud_task_cover(2, "job") is None


@pytest.mark.asyncio
async def test_a_download_that_never_connects_is_not_an_error():
    cloud = MagicMock()
    cloud.get_task = AsyncMock(return_value={"cover": "https://cdn.example.com/p.png"})
    cloud.close = AsyncMock()

    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    transport = httpx.MockTransport(boom)
    with (
        patch("backend.app.api.routes.printers.printer_manager") as pm,
        patch("backend.app.api.routes.printers.get_stored_token", AsyncMock(return_value=("tok", None, "global"))),
        patch("backend.app.api.routes.printers.database.async_session", lambda: _Session(_no_users())),
        patch("backend.app.api.routes.printers.BambuCloudService", return_value=cloud),
        patch(
            "backend.app.api.routes.printers.httpx.AsyncClient",
            lambda *a, t=transport, **kw: _RealAsyncClient(transport=t),
        ),
    ):
        pm.get_status.return_value = _status("123")
        assert await _cloud_task_cover(2, "job") is None


@pytest.mark.asyncio
async def test_an_unknown_printer_has_no_status_and_no_cover():
    with patch("backend.app.api.routes.printers.printer_manager") as pm:
        pm.get_status.return_value = None
        assert await _cloud_task_cover(99, "job") is None


class TestGetTask:
    """`BambuCloudService.get_task` itself.

    The tests above mock the whole service, so without these the new method would ship with its
    error branches never executed — the exact shape the cover fallback relies on to degrade
    quietly rather than raise into a request.
    """

    def _service(self, response=None, raises=None):
        from backend.app.services.bambu_cloud import BambuCloudService

        svc = BambuCloudService(region="global")
        # The public loader, which is what production uses — poking a private attribute would
        # pass here and diverge the moment the class changed how a token is held.
        svc.set_token("tok")
        client = MagicMock()
        client.get = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=response)
        svc._client = client  # noqa: SLF001
        return svc

    @pytest.mark.asyncio
    async def test_an_unauthenticated_service_refuses_before_the_network(self):
        from backend.app.services.bambu_cloud import BambuCloudService

        svc = BambuCloudService(region="global")
        svc._client = MagicMock()  # noqa: SLF001
        svc._client.get = AsyncMock()  # noqa: SLF001
        with pytest.raises(BambuCloudAuthError):
            await svc.get_task("123")
        svc._client.get.assert_not_awaited()  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_a_200_returns_the_task_body(self):
        body = {"cover": "https://cdn.example.com/plate_1.png", "designTitle": "Bracket"}
        svc = self._service(httpx.Response(200, json=body))
        assert await svc.get_task("123") == body
        url = svc._client.get.await_args.args[0]  # noqa: SLF001
        assert url.endswith("/v1/user-service/my/task/123")

    @pytest.mark.asyncio
    async def test_a_non_200_raises_rather_than_returning_an_empty_task(self):
        """Returning {} would look like "this print has no cover" when the truth is that the
        session was rejected — the caller could not tell them apart."""
        for status in (401, 403, 404, 500):
            svc = self._service(httpx.Response(status, json={}))
            with pytest.raises(BambuCloudError):
                await svc.get_task("123")

    @pytest.mark.asyncio
    async def test_a_transport_failure_becomes_a_cloud_error(self):
        svc = self._service(raises=httpx.ConnectError("no route"))
        with pytest.raises(BambuCloudError):
            await svc.get_task("123")


class TestArchiveThumbnailFill:
    """Step 3 of #2910: the no-3MF archive ROW, not just the live card.

    The card gets the cloud cover at request time, but `thumbnail_path` stayed NULL, so print
    history was blank for exactly the prints the fallback exists for — and nothing later filled
    it, because a storage verdict schedules no 3MF retry.
    """

    @pytest.mark.asyncio
    async def test_writes_the_cover_and_records_the_path(self, tmp_path):
        from backend.app.main import _fill_cloud_cover_thumbnail

        archive = SimpleNamespace(id=7, subtask_id="12345", file_path="", thumbnail_path=None)
        db = MagicMock()
        db.commit = AsyncMock()

        with (
            patch("backend.app.api.routes.printers.cloud_cover_bytes", AsyncMock(return_value=PNG)),
            patch("backend.app.core.config.settings.base_dir", tmp_path),
            patch("backend.app.core.config.settings.archive_dir", tmp_path / "archive"),
        ):
            assert await _fill_cloud_cover_thumbnail(db, archive) is True

        written = tmp_path / "archive" / "7" / "thumbnail.png"
        assert written.read_bytes() == PNG
        assert archive.thumbnail_path == "archive/7/thumbnail.png"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("subtask_id", ["", "0", "  ", None])
    async def test_declines_without_a_usable_subtask_id(self, subtask_id):
        """ "0" is the firmware's "no job" and passes both `!= 0` and truthiness."""
        from backend.app.main import _fill_cloud_cover_thumbnail

        archive = SimpleNamespace(id=7, subtask_id=subtask_id, file_path="", thumbnail_path=None)
        fetch = AsyncMock(return_value=PNG)

        with patch("backend.app.api.routes.printers.cloud_cover_bytes", fetch):
            assert await _fill_cloud_cover_thumbnail(MagicMock(), archive) is False

        fetch.assert_not_awaited()
        assert archive.thumbnail_path is None

    @pytest.mark.asyncio
    async def test_no_cover_leaves_the_row_alone(self):
        from backend.app.main import _fill_cloud_cover_thumbnail

        archive = SimpleNamespace(id=7, subtask_id="12345", file_path="", thumbnail_path=None)
        db = MagicMock()
        db.commit = AsyncMock()

        with patch("backend.app.api.routes.printers.cloud_cover_bytes", AsyncMock(return_value=None)):
            assert await _fill_cloud_cover_thumbnail(db, archive) is False

        assert archive.thumbnail_path is None
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_raising_fetch_never_reaches_the_caller(self):
        """Print start must not fail because a decoration could not be fetched."""
        from backend.app.main import _fill_cloud_cover_thumbnail

        archive = SimpleNamespace(id=7, subtask_id="12345", file_path="", thumbnail_path=None)

        with patch(
            "backend.app.api.routes.printers.cloud_cover_bytes",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            assert await _fill_cloud_cover_thumbnail(MagicMock(), archive) is False

        assert archive.thumbnail_path is None

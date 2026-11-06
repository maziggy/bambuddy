"""An archive's started_at is when the print started, not when it was written (#3327).

The archive waits for the 3MF, which the printer serves slowly while it starts:
the reporter's H2C archived its print a minute after the start. Completion takes
the print time from started_at, so that minute was missing from it. The fallback
archive, written only after every download attempt failed, waited longer still.
"""

from datetime import datetime, timezone

import pytest

from backend.app.core.config import settings
from backend.app.services.archive import ArchiveService
from backend.tests.unit.test_archive_print_plate import _three_mf, printer_id  # noqa: F401
from backend.tests.unit.test_print_start_no_second_download_3308 import (  # noqa: F401
    FILE,
    _isolated,
    _listed,
    _print_start,
    _served_on_second_try,
    _times_out_at_root,
)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """The data directory; the archive directory is the print-start harness's
    (``_isolated``), so it is patched once and put back once."""
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    return tmp_path


def _timed(transfer):
    """*transfer*, noting when the first download attempt began."""
    began: list[datetime] = []

    async def timed(remote_path, local_path):
        began.append(datetime.now(timezone.utc))
        return await transfer(remote_path, local_path)

    return timed, began


class TestOnPrintStart:
    @pytest.mark.asyncio
    async def test_the_archive_starts_when_the_print_was_seen(self):
        before = datetime.now(timezone.utc)
        transfer, began = _timed(_served_on_second_try(f"/cache/{FILE}"))

        run = await _print_start(transfer=transfer, listing=_listed(FILE))

        started_at = run.service.archive_print.await_args.kwargs["started_at"]
        assert before <= started_at <= began[0]

    @pytest.mark.asyncio
    async def test_so_does_the_fallback_archive(self):
        before = datetime.now(timezone.utc)
        transfer, began = _timed(_times_out_at_root)

        run = await _print_start(transfer=transfer, listing=_listed(FILE))

        assert before <= run.fallback.started_at <= began[0]


class TestArchivePrint:
    @pytest.mark.asyncio
    async def test_a_given_start_is_kept(self, db_session, dirs, printer_id):  # noqa: F811
        start = datetime(2026, 10, 15, 2, 48, 54, tzinfo=timezone.utc)

        archive = await ArchiveService(db_session).archive_print(
            printer_id, _three_mf(dirs / "p.3mf", [1]), print_data={"status": "printing"}, started_at=start
        )

        assert archive.started_at.replace(tzinfo=timezone.utc) == start

    @pytest.mark.asyncio
    async def test_without_one_it_is_now(self, db_session, dirs, printer_id):  # noqa: F811
        before = datetime.now(timezone.utc)

        archive = await ArchiveService(db_session).archive_print(
            printer_id, _three_mf(dirs / "p.3mf", [1]), print_data={"status": "printing"}
        )

        assert archive.started_at.replace(tzinfo=timezone.utc) >= before.replace(microsecond=0)

    @pytest.mark.asyncio
    async def test_a_finished_archive_has_none(self, db_session, dirs, printer_id):  # noqa: F811
        archive = await ArchiveService(db_session).archive_print(
            printer_id,
            _three_mf(dirs / "p.3mf", [1]),
            print_data={"status": "completed"},
            started_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
        )

        assert archive.started_at is None

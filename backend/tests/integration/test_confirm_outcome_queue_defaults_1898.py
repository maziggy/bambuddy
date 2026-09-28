"""Every queue-creation path has to be able to ask for the outcome (#1898).

``confirm_outcome`` rides from a queue item onto the archive at dispatch, and
the only place that ever set it was the print dialog. Jobs created anywhere
else -- a plate sent from Bambu Studio to a virtual printer, the Library's bulk
"Add to queue", the webhook, a pipeline run -- carried the column default and
were never asked about, however the install's settings were configured.

That mattered most for the virtual printer: a Bambu Studio plate is one of the
prints ``confirm_outcome_external_prints`` names in its own description, but it
arrives with a queue item, so ``on_print_start`` resumes its archive instead of
treating it as external and the setting could not reach it at all.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import settings as app_settings
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.services.print_confirmation import confirm_outcome_for_new_queue_item

pytestmark = pytest.mark.integration


async def _set_setting(db_session, key: str, value: str) -> None:
    db_session.add(Settings(key=key, value=value))
    await db_session.commit()


class TestTheDefaultForAQueueItemNobodyToggled:
    @pytest.mark.asyncio
    async def test_off_when_nothing_is_configured(self, db_session):
        assert await confirm_outcome_for_new_queue_item(db_session) is False
        assert await confirm_outcome_for_new_queue_item(db_session, started_outside_bambuddy=True) is False

    @pytest.mark.asyncio
    async def test_the_per_job_default_covers_every_path(self, db_session):
        """Same setting the print dialog seeds its own toggle from."""
        await _set_setting(db_session, "default_confirm_outcome", "true")

        assert await confirm_outcome_for_new_queue_item(db_session) is True
        assert await confirm_outcome_for_new_queue_item(db_session, started_outside_bambuddy=True) is True

    @pytest.mark.asyncio
    async def test_the_external_setting_only_covers_external_origins(self, db_session):
        """A Bambu Studio plate counts; a pipeline run Bambuddy started itself
        does not -- that one follows the per-job default like any queued job."""
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")

        assert await confirm_outcome_for_new_queue_item(db_session, started_outside_bambuddy=True) is True
        assert await confirm_outcome_for_new_queue_item(db_session) is False

    @pytest.mark.asyncio
    async def test_an_odd_stored_value_counts_as_off(self, db_session):
        await _set_setting(db_session, "default_confirm_outcome", "None")

        assert await confirm_outcome_for_new_queue_item(db_session) is False


@pytest.fixture
async def sliced_library_file(db_session):
    """A library file that passes add-to-queue's gates: the filename has to look
    sliced and the bytes have to exist under ``base_dir``."""
    from backend.app.models.library import LibraryFile

    # Unique per test: the suite runs with xdist and the teardown below would
    # otherwise delete the bytes a sibling worker is still relying on, which the
    # route answers with a 400 for a bulk add that queued nothing (#3112).
    name = f"confirm_outcome_probe_{uuid4().hex}.gcode.3mf"
    rel_path = f"archive/library/files/{name}"
    abs_path = Path(app_settings.base_dir) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"probe")

    lib_file = LibraryFile(
        filename=name,
        file_path=rel_path,
        file_size=5,
        file_type="3mf",
    )
    db_session.add(lib_file)
    await db_session.commit()
    await db_session.refresh(lib_file)

    yield lib_file

    abs_path.unlink(missing_ok=True)


async def _read_item(test_engine, item_id: int) -> PrintQueueItem:
    """Fresh-session read: the route ran on its own ``Depends(get_db)`` session."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        return (await fresh.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


class TestTheLibraryBulkAdd:
    """The route has no per-job toggle at all, so the install-wide default is
    the only thing that can decide."""

    async def _add(self, async_client: AsyncClient, file_id: int) -> int:
        response = await async_client.post("/api/v1/library/files/add-to-queue", json={"file_ids": [file_id]})
        assert response.status_code == 200
        added = response.json()["added"]
        assert len(added) == 1
        return added[0]["queue_item_id"]

    @pytest.mark.asyncio
    async def test_follows_the_default(self, async_client, db_session, test_engine, sliced_library_file):
        await _set_setting(db_session, "default_confirm_outcome", "true")

        item_id = await self._add(async_client, sliced_library_file.id)

        assert (await _read_item(test_engine, item_id)).confirm_outcome is True

    @pytest.mark.asyncio
    async def test_stays_off_for_an_install_that_never_asked_for_it(
        self, async_client, db_session, test_engine, sliced_library_file
    ):
        item_id = await self._add(async_client, sliced_library_file.id)

        assert (await _read_item(test_engine, item_id)).confirm_outcome is False

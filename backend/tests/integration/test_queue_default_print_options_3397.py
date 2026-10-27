"""Queue items created without the print dialog take the Default Print Options (#3397).

The print dialog seeds its timelapse / calibration toggles from the
``default_*`` settings. The Library's bulk "Add to queue", the webhook and a
pipeline run have no toggles, and their rows kept the column defaults -- so an
install with timelapse on by default got no timelapse for any of them.
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
from backend.app.services.print_options import default_print_options_for_new_queue_item

pytestmark = pytest.mark.integration

COLUMN_DEFAULTS = {
    "bed_levelling": "auto",
    "flow_cali": "auto",
    "nozzle_offset_cali": "auto",
    "vibration_cali": True,
    "layer_inspect": False,
    "timelapse": False,
}


async def _set_settings(db_session, **values: str) -> None:
    for key, value in values.items():
        db_session.add(Settings(key=key, value=value))
    await db_session.commit()


class TestTheDefaults:
    @pytest.mark.asyncio
    async def test_nothing_configured_gives_the_column_defaults(self, db_session):
        assert await default_print_options_for_new_queue_item(db_session) == COLUMN_DEFAULTS

    @pytest.mark.asyncio
    async def test_every_setting_is_read(self, db_session):
        await _set_settings(
            db_session,
            default_bed_levelling="off",
            default_flow_cali="on",
            default_nozzle_offset_cali="off",
            default_vibration_cali="false",
            default_layer_inspect="true",
            default_timelapse="true",
        )

        assert await default_print_options_for_new_queue_item(db_session) == {
            "bed_levelling": "off",
            "flow_cali": "on",
            "nozzle_offset_cali": "off",
            "vibration_cali": False,
            "layer_inspect": True,
            "timelapse": True,
        }

    @pytest.mark.asyncio
    async def test_legacy_and_odd_tristate_rows(self, db_session):
        """Rows from before the tri-state change hold true/false; anything else
        falls back to auto rather than reaching the printer."""
        await _set_settings(
            db_session, default_bed_levelling="true", default_flow_cali="false", default_nozzle_offset_cali="None"
        )

        options = await default_print_options_for_new_queue_item(db_session)

        assert (options["bed_levelling"], options["flow_cali"], options["nozzle_offset_cali"]) == ("on", "off", "auto")


@pytest.fixture
async def sliced_library_file(db_session):
    """A library file that passes add-to-queue's gates (see #3112 for why the
    name is unique per test)."""
    from backend.app.models.library import LibraryFile

    name = f"print_options_probe_{uuid4().hex}.gcode.3mf"
    rel_path = f"archive/library/files/{name}"
    abs_path = Path(app_settings.base_dir) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"probe")

    lib_file = LibraryFile(filename=name, file_path=rel_path, file_size=5, file_type="3mf")
    db_session.add(lib_file)
    await db_session.commit()
    await db_session.refresh(lib_file)

    yield lib_file

    abs_path.unlink(missing_ok=True)


async def _read_item(test_engine, item_id: int) -> PrintQueueItem:
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        return (await fresh.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


class TestTheLibraryBulkAdd:
    async def _add(self, async_client: AsyncClient, file_id: int) -> int:
        response = await async_client.post("/api/v1/library/files/add-to-queue", json={"file_ids": [file_id]})
        assert response.status_code == 200
        added = response.json()["added"]
        assert len(added) == 1
        return added[0]["queue_item_id"]

    @pytest.mark.asyncio
    async def test_follows_the_defaults(self, async_client, db_session, test_engine, sliced_library_file):
        await _set_settings(
            db_session, default_timelapse="true", default_flow_cali="off", default_vibration_cali="false"
        )

        item = await _read_item(test_engine, await self._add(async_client, sliced_library_file.id))

        assert (item.timelapse, item.flow_cali, item.vibration_cali) == (True, "off", False)

    @pytest.mark.asyncio
    async def test_unchanged_for_an_install_with_no_defaults(
        self, async_client, db_session, test_engine, sliced_library_file
    ):
        item = await _read_item(test_engine, await self._add(async_client, sliced_library_file.id))

        assert {key: getattr(item, key) for key in COLUMN_DEFAULTS} == COLUMN_DEFAULTS

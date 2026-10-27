"""Queue items created without the print dialog take the Default Print Options (#3397).

The print dialog seeds its timelapse / calibration toggles from the
``default_*`` settings. The Library's bulk "Add to queue", the webhook and a
pipeline run have no toggles, and their rows kept the column defaults -- so an
install with timelapse on by default got no timelapse for any of them.
"""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.auth import generate_api_key
from backend.app.core.config import settings as app_settings
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
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


DEFAULTS_SET = {"default_timelapse": "true", "default_flow_cali": "off", "default_vibration_cali": "false"}


def _picked(item: PrintQueueItem) -> tuple:
    return (item.timelapse, item.flow_cali, item.vibration_cali)


@pytest.fixture
async def printer(db_session):
    row = Printer(
        name="Defaults Probe",
        serial_number=f"SERIAL{uuid4().hex[:8]}",
        ip_address="192.0.2.1",
        access_code="ABCD1234",
        model="Bambu Lab X1 Carbon",
        is_active=True,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


class TestTheWebhook:
    @pytest.mark.asyncio
    async def test_follows_the_defaults(self, async_client, db_session, test_engine, printer):
        await _set_settings(db_session, **DEFAULTS_SET)
        archive = PrintArchive(
            filename="Plate_1.gcode.3mf",
            print_name="Plate 1",
            file_path="/tmp/print_options_probe.3mf",  # nosec B108
            file_size=1024,
            content_hash=f"printoptions{uuid4().hex}",
            status="completed",
        )
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add_all(
            [archive, APIKey(name="defaults probe", key_hash=key_hash, key_prefix=key_prefix, can_queue=True)]
        )
        await db_session.commit()
        await db_session.refresh(archive)

        response = await async_client.post(
            "/api/v1/webhook/queue/add",
            json={"printer_id": printer.id, "archive_id": archive.id},
            headers={"X-API-Key": full_key},
        )
        assert response.status_code == 200, response.text

        assert _picked(await _read_item(test_engine, response.json()["id"])) == (True, "off", False)


@dataclass
class _FakeSliceJob:
    id: int = 9001


@dataclass
class _FakeSliceResponse:
    library_file_id: int

    def model_dump(self) -> dict:
        return {"library_file_id": self.library_file_id}


class TestThePipelineRun:
    """Slicing is stubbed: the run's background callable is captured from
    ``slice_dispatch.enqueue`` and run against the test database."""

    @pytest.mark.asyncio
    async def test_follows_the_defaults(self, async_client, db_session, test_engine, printer, sliced_library_file):
        await _set_settings(db_session, **DEFAULTS_SET)
        created = await async_client.post(
            "/api/v1/slicer-pipelines/",
            json={
                "name": "Defaults probe",
                "printer_preset": {"source": "local", "id": "1"},
                "process_preset": {"source": "local", "id": "2"},
                "filament_presets": [{"source": "local", "id": "3"}],
            },
        )
        assert created.status_code == 201, created.text
        pipeline_id = created.json()["id"]
        targeted = await async_client.put(
            f"/api/v1/slicer-pipelines/{pipeline_id}",
            json={"target_kind": "specific_printer", "target_printer_id": printer.id},
        )
        assert targeted.status_code == 200, targeted.text

        enqueue = AsyncMock(return_value=_FakeSliceJob())
        with (
            patch(
                "backend.app.api.routes.pipeline_runs._load_printer_status",
                new=AsyncMock(return_value={"connected": True, "raw_data": {"ams": []}}),
            ),
            patch("backend.app.services.slice_dispatch.slice_dispatch.enqueue", new=enqueue),
        ):
            response = await async_client.post(
                f"/api/v1/slicer-pipelines/{pipeline_id}/run",
                json={"source_library_file_id": sliced_library_file.id, "force": True},
            )
        assert response.status_code == 202, response.text
        orchestrate = enqueue.call_args.kwargs["run"]

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with (
            patch("backend.app.api.routes.pipeline_runs.async_session", maker),
            patch("backend.app.api.routes.pipeline_runs._publish_run_event", new=AsyncMock()),
            patch(
                "backend.app.api.routes.library.slice_and_persist",
                new=AsyncMock(return_value=_FakeSliceResponse(library_file_id=sliced_library_file.id)),
            ),
        ):
            await orchestrate(9001)

        async with maker() as fresh:
            item = (
                await fresh.execute(
                    select(PrintQueueItem).where(PrintQueueItem.library_file_id == sliced_library_file.id)
                )
            ).scalar_one()
        assert _picked(item) == (True, "off", False)

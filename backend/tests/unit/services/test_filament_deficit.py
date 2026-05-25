"""Unit tests for the filament-deficit pre-dispatch check (#1496).

The check is the single source of truth that both ``POST /queue/{id}/start``
and the dispatch scheduler call before sending a print to the printer. Pin
the contract for the cases that matter:

* Internal-inventory mode: shortfall + sufficient + no assignment.
* AMS-mapping gating: a missing mapping means "not yet decided, skip".
* Disabled-warnings setting + missing printer (model-based item) + no
  source 3MF all short-circuit to "no deficit".
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.filament_deficit import (
    FilamentDeficit,
    compute_deficit_for_queue_item,
)


def _write_3mf(file_path: Path, filaments: list[dict]) -> None:
    """Minimal 3MF that ``extract_filament_requirements`` can parse (flat shape)."""
    body = "".join(
        f'<filament id="{f["id"]}" type="{f["type"]}" color="{f["color"]}" '
        f'used_g="{f["used_g"]}" tray_info_idx="{f.get("tray_info_idx", "")}"/>'
        for f in filaments
    )
    config = f'<?xml version="1.0" encoding="utf-8"?><config>{body}</config>'
    with zipfile.ZipFile(file_path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", config)


async def _setup_archive_3mf(db_session, tmp_path: Path, filaments: list[dict]) -> PrintArchive:
    """Create a 3MF on disk and a PrintArchive row pointing at it."""
    file_name = "model.3mf"
    file_path = tmp_path / file_name
    _write_3mf(file_path, filaments)
    archive = PrintArchive(
        filename=file_name,
        print_name="Test",
        # The helper resolves via app_settings.base_dir / file_path, but
        # storing the absolute path on the model also works because
        # ``Path / abs`` collapses to the absolute side.
        file_path=str(file_path),
        file_size=file_path.stat().st_size,
        status="completed",
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)
    return archive


async def _spool(db_session, *, label_weight: int, weight_used: float, color: str = "#000000") -> Spool:
    spool = Spool(
        material="PLA",
        label_weight=label_weight,
        weight_used=weight_used,
        rgba=color,
    )
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)
    return spool


async def _assign(db_session, *, printer_id: int, spool_id: int, ams_id: int = 0, tray_id: int = 0) -> None:
    db_session.add(
        SpoolAssignment(
            spool_id=spool_id,
            printer_id=printer_id,
            ams_id=ams_id,
            tray_id=tray_id,
        )
    )
    await db_session.commit()


async def _queue_item(
    db_session,
    *,
    printer_id: int | None,
    archive: PrintArchive | None,
    ams_mapping: list[int] | None,
    plate_id: int | None = None,
) -> PrintQueueItem:
    item = PrintQueueItem(
        printer_id=printer_id,
        archive_id=archive.id if archive else None,
        ams_mapping=json.dumps(ams_mapping) if ams_mapping is not None else None,
        plate_id=plate_id,
        status="pending",
        manual_start=True,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item, ["archive", "library_file"])
    return item


class TestFilamentDeficit:
    @pytest.mark.asyncio
    async def test_returns_deficit_when_spool_too_light(self, db_session, printer_factory, tmp_path):
        """Spool with 30g remaining for a 100g print → one deficit row."""
        printer = await printer_factory()
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "100.0"}],
        )
        spool = await _spool(db_session, label_weight=1000, weight_used=970.0)  # 30g left
        await _assign(db_session, printer_id=printer.id, spool_id=spool.id, ams_id=0, tray_id=0)
        item = await _queue_item(db_session, printer_id=printer.id, archive=archive, ams_mapping=[0])

        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            deficit = await compute_deficit_for_queue_item(db_session, item)

        assert len(deficit) == 1
        assert isinstance(deficit[0], FilamentDeficit)
        assert deficit[0].slot_id == 1
        assert deficit[0].required_grams == 100.0
        assert deficit[0].remaining_grams == 30.0
        assert deficit[0].filament_type == "PLA"

    @pytest.mark.asyncio
    async def test_returns_empty_when_spool_has_enough(self, db_session, printer_factory, tmp_path):
        printer = await printer_factory()
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "100.0"}],
        )
        spool = await _spool(db_session, label_weight=1000, weight_used=200.0)  # 800g left
        await _assign(db_session, printer_id=printer.id, spool_id=spool.id, ams_id=0, tray_id=0)
        item = await _queue_item(db_session, printer_id=printer.id, archive=archive, ams_mapping=[0])

        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            deficit = await compute_deficit_for_queue_item(db_session, item)

        assert deficit == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_ams_mapping_missing(self, db_session, printer_factory, tmp_path):
        """No mapping yet = scheduler hasn't decided which slot maps where."""
        printer = await printer_factory()
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "100.0"}],
        )
        item = await _queue_item(db_session, printer_id=printer.id, archive=archive, ams_mapping=None)

        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            deficit = await compute_deficit_for_queue_item(db_session, item)

        assert deficit == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_printer_assigned(self, db_session, tmp_path):
        """Model-based queue items with no resolved printer_id can't be checked."""
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "100.0"}],
        )
        item = await _queue_item(db_session, printer_id=None, archive=archive, ams_mapping=[0])

        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            deficit = await compute_deficit_for_queue_item(db_session, item)

        assert deficit == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_warnings_disabled(self, db_session, printer_factory, tmp_path):
        """Honour the disable_filament_warnings setting (#720 toggle)."""
        printer = await printer_factory()
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "100.0"}],
        )
        spool = await _spool(db_session, label_weight=1000, weight_used=970.0)
        await _assign(db_session, printer_id=printer.id, spool_id=spool.id)
        item = await _queue_item(db_session, printer_id=printer.id, archive=archive, ams_mapping=[0])
        db_session.add(Settings(key="disable_filament_warnings", value="true"))
        await db_session.commit()

        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            deficit = await compute_deficit_for_queue_item(db_session, item)

        assert deficit == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_assignment(self, db_session, printer_factory, tmp_path):
        """Mapping points at a slot with no spool assigned → silent, not blocked."""
        printer = await printer_factory()
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "100.0"}],
        )
        item = await _queue_item(db_session, printer_id=printer.id, archive=archive, ams_mapping=[0])

        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            deficit = await compute_deficit_for_queue_item(db_session, item)

        assert deficit == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_3mf_missing(self, db_session, printer_factory):
        printer = await printer_factory()
        archive = PrintArchive(
            filename="ghost.3mf",
            file_path="/nonexistent/ghost.3mf",
            file_size=0,
            status="completed",
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)
        item = await _queue_item(db_session, printer_id=printer.id, archive=archive, ams_mapping=[0])

        deficit = await compute_deficit_for_queue_item(db_session, item)

        assert deficit == []

    @pytest.mark.asyncio
    async def test_multi_slot_only_shorted_slot_returned(self, db_session, printer_factory, tmp_path):
        """One slot fine, one short — only the short slot is in the result."""
        printer = await printer_factory()
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [
                {"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "100.0"},
                {"id": "2", "type": "PETG", "color": "#000000", "used_g": "80.0"},
            ],
        )
        plenty = await _spool(db_session, label_weight=1000, weight_used=100.0)  # 900g
        shorted = await _spool(db_session, label_weight=1000, weight_used=950.0)  # 50g
        await _assign(db_session, printer_id=printer.id, spool_id=plenty.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=shorted.id, ams_id=0, tray_id=1)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0, 1],  # slot 1 -> tray 0, slot 2 -> tray 1
        )

        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            deficit = await compute_deficit_for_queue_item(db_session, item)

        assert [d.slot_id for d in deficit] == [2]
        assert deficit[0].remaining_grams == 50.0
        assert deficit[0].required_grams == 80.0

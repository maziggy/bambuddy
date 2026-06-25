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


async def _spool(
    db_session,
    *,
    label_weight: int,
    weight_used: float,
    color: str = "#000000",
    slicer_filament: str | None = None,
) -> Spool:
    spool = Spool(
        material="PLA",
        label_weight=label_weight,
        weight_used=weight_used,
        rgba=color,
        slicer_filament=slicer_filament,
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


class TestFilamentDeficitBackupAware:
    """#1762 — when AMS Filament Backup is ON, pool remaining grams across
    same-material spools on the printer (within the same extruder side on
    dual-nozzle models) before declaring a slot deficit.

    Reporter scenario: PLA Basic in AMS-1 slot 1 with 10 g left, same PLA
    Basic in AMS-2 slot 1 with 500 g left. Today's per-slot accounting
    blocks the print because slot 1 of AMS-1 is short. With backup ON,
    firmware switches mid-print, so the deficit shouldn't fire.
    """

    @staticmethod
    def _patch_status(
        *,
        printer_id: int,
        backup_on: bool,
        ams_extruder_map: dict | None = None,
        model: str | None = None,
    ):
        """Patch ``printer_manager.get_status`` + ``get_model`` for the test."""
        from types import SimpleNamespace
        from unittest.mock import patch as _patch

        fake_state = SimpleNamespace(
            ams_filament_backup=backup_on if backup_on is not None else None,
            ams_extruder_map=ams_extruder_map or {},
        )

        return [
            _patch(
                "backend.app.services.printer_manager.printer_manager.get_status",
                lambda pid: fake_state if pid == printer_id else None,
            ),
            _patch(
                "backend.app.services.printer_manager.printer_manager.get_model",
                lambda pid: model if pid == printer_id else None,
            ),
        ]

    @pytest.mark.asyncio
    async def test_backup_on_pool_covers_short_slot(self, db_session, printer_factory, tmp_path):
        """The reporter scenario: assigned slot is short, but the same
        material on a peer slot covers the print. With backup ON, no deficit."""
        printer = await printer_factory(model="X1C")
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#000000", "used_g": "200.0"}],
        )
        # Mapped slot: 10 g remaining, same Bambu preset as peer.
        short = await _spool(db_session, label_weight=1000, weight_used=990.0, slicer_filament="GFA00")
        # Peer slot on AMS-2: same preset, 500 g remaining.
        peer = await _spool(db_session, label_weight=1000, weight_used=500.0, slicer_filament="GFA00")
        await _assign(db_session, printer_id=printer.id, spool_id=short.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=peer.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(printer_id=printer.id, backup_on=True, model="X1C")
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # Pool (10 + 500 = 510 g) covers the 200 g print → no deficit.
        assert deficit == []

    @pytest.mark.asyncio
    async def test_backup_on_pool_insufficient_emits_deficit(self, db_session, printer_factory, tmp_path):
        """Backup ON but the same-material pool across all slots is still
        too small for the print → deficit emitted (real shortfall)."""
        printer = await printer_factory(model="X1C")
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#000000", "used_g": "1500.0"}],
        )
        a = await _spool(db_session, label_weight=1000, weight_used=900.0, slicer_filament="GFA00")  # 100g
        b = await _spool(db_session, label_weight=1000, weight_used=700.0, slicer_filament="GFA00")  # 300g
        await _assign(db_session, printer_id=printer.id, spool_id=a.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=b.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(printer_id=printer.id, backup_on=True, model="X1C")
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # Pool 400 g < required 1500 g → deficit fires.
        assert len(deficit) == 1
        assert deficit[0].slot_id == 1

    @pytest.mark.asyncio
    async def test_backup_on_different_materials_no_pool(self, db_session, printer_factory, tmp_path):
        """Backup ON, but the peer slot holds a DIFFERENT material — pool
        doesn't include it, deficit fires for the original short slot."""
        printer = await printer_factory(model="X1C")
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "200.0"}],
        )
        # Assigned slot: PLA White preset GFA01, 10 g.
        short = await _spool(db_session, label_weight=1000, weight_used=990.0, color="#FFFFFF", slicer_filament="GFA01")
        # Peer: PLA Black, different preset (GFA00) — NOT a backup peer under the strict rule.
        peer = await _spool(db_session, label_weight=1000, weight_used=500.0, color="#000000", slicer_filament="GFA00")
        await _assign(db_session, printer_id=printer.id, spool_id=short.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=peer.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(printer_id=printer.id, backup_on=True, model="X1C")
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # Pool for white = 10 g, required = 200 g → deficit.
        assert len(deficit) == 1
        assert deficit[0].slot_id == 1
        assert deficit[0].remaining_grams == 10.0

    @pytest.mark.asyncio
    async def test_backup_off_falls_back_to_per_slot_accounting(self, db_session, printer_factory, tmp_path):
        """When backup is OFF the new code path must be a strict no-op vs.
        the pre-#1762 per-slot accounting. Identical inputs to the
        ``pool_covers_short_slot`` case but with backup OFF — deficit fires."""
        printer = await printer_factory(model="X1C")
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#000000", "used_g": "200.0"}],
        )
        short = await _spool(db_session, label_weight=1000, weight_used=990.0, slicer_filament="GFA00")
        peer = await _spool(db_session, label_weight=1000, weight_used=500.0, slicer_filament="GFA00")
        await _assign(db_session, printer_id=printer.id, spool_id=short.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=peer.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(printer_id=printer.id, backup_on=False, model="X1C")
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # Backup OFF → per-slot accounting → slot 1 has 10 g, needs 200 g.
        assert len(deficit) == 1
        assert deficit[0].remaining_grams == 10.0

    @pytest.mark.asyncio
    async def test_backup_on_dual_extruder_scopes_pool_per_side(self, db_session, printer_factory, tmp_path):
        """Dual-extruder printer (H2D): peer slot on the OPPOSITE extruder
        does NOT count toward the pool — firmware can't cross. Deficit fires."""
        printer = await printer_factory(model="O1D")  # H2D internal code
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#000000", "used_g": "200.0"}],
        )
        short = await _spool(db_session, label_weight=1000, weight_used=990.0, slicer_filament="GFA00")
        peer_other_side = await _spool(db_session, label_weight=1000, weight_used=500.0, slicer_filament="GFA00")
        # AMS 0 is on extruder 0 (right). AMS 1 is on extruder 1 (left).
        await _assign(db_session, printer_id=printer.id, spool_id=short.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=peer_other_side.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(
            printer_id=printer.id,
            backup_on=True,
            ams_extruder_map={"0": 0, "1": 1},
            model="O1D",
        )
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # Pool for extruder 0 = 10 g (peer on extruder 1 is unreachable) <
        # required 200 g → deficit.
        assert len(deficit) == 1
        assert deficit[0].slot_id == 1

    @pytest.mark.asyncio
    async def test_backup_on_no_preset_never_pairs(self, db_session, printer_factory, tmp_path):
        """Strict rule: two user-tagged spools with no slicer_filament preset
        must NEVER pair, even when material + colour match. Mirrors Bambu
        firmware: the backup decision relies on the Bambu Lab preset ID, so
        generic spools without one can't be trusted to switch."""
        printer = await printer_factory(model="X1C")
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#000000", "used_g": "200.0"}],
        )
        # Both spools: material PLA, colour black, NO preset → unique keys.
        short = await _spool(db_session, label_weight=1000, weight_used=990.0)
        peer_no_preset = await _spool(db_session, label_weight=1000, weight_used=500.0)
        await _assign(db_session, printer_id=printer.id, spool_id=short.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=peer_no_preset.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(printer_id=printer.id, backup_on=True, model="X1C")
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # No preset means no pool — slot 1's 10 g vs 200 g required → deficit.
        assert len(deficit) == 1
        assert deficit[0].slot_id == 1
        assert deficit[0].remaining_grams == 10.0

    @pytest.mark.asyncio
    async def test_backup_on_same_preset_different_colors_does_not_pair(self, db_session, printer_factory, tmp_path):
        """STRICT colour rule: two spools sharing the same Bambu preset ID
        but DIFFERENT colours must NOT pool. Three PETG HF spools in
        different colours can't back each other up — the firmware would
        switch material correctly but the print would change colour
        mid-run. Pool is per-(preset, colour)."""
        printer = await printer_factory(model="X1C")
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#000000", "used_g": "200.0"}],
        )
        # Assigned slot: PLA Basic + GFA00 + BLACK, only 10 g left.
        short = await _spool(db_session, label_weight=1000, weight_used=990.0, color="#000000", slicer_filament="GFA00")
        # Peer slot: same GFA00 profile but WHITE — must not pool.
        peer_diff_color = await _spool(
            db_session, label_weight=1000, weight_used=500.0, color="#FFFFFF", slicer_filament="GFA00"
        )
        await _assign(db_session, printer_id=printer.id, spool_id=short.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=peer_diff_color.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(printer_id=printer.id, backup_on=True, model="X1C")
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # Pool for (GFA00, black) = 10 g; required = 200 g → deficit.
        assert len(deficit) == 1
        assert deficit[0].slot_id == 1
        assert deficit[0].remaining_grams == 10.0

    @pytest.mark.asyncio
    async def test_backup_on_color_alpha_normalized(self, db_session, printer_factory, tmp_path):
        """Colour normalisation: 6-char hex matches 8-char hex of the same
        RGB. ``000000`` and ``000000FF`` should both resolve to BLACK."""
        printer = await printer_factory(model="X1C")
        archive = await _setup_archive_3mf(
            db_session,
            tmp_path,
            [{"id": "1", "type": "PLA", "color": "#000000", "used_g": "200.0"}],
        )
        short = await _spool(db_session, label_weight=1000, weight_used=990.0, color="#000000", slicer_filament="GFA00")
        # Same colour but expressed with explicit alpha.
        peer = await _spool(
            db_session, label_weight=1000, weight_used=500.0, color="#000000FF", slicer_filament="GFA00"
        )
        await _assign(db_session, printer_id=printer.id, spool_id=short.id, ams_id=0, tray_id=0)
        await _assign(db_session, printer_id=printer.id, spool_id=peer.id, ams_id=1, tray_id=0)
        item = await _queue_item(
            db_session,
            printer_id=printer.id,
            archive=archive,
            ams_mapping=[0],
        )

        patches = TestFilamentDeficitBackupAware._patch_status(printer_id=printer.id, backup_on=True, model="X1C")
        with patch("backend.app.services.filament_deficit.app_settings.base_dir", Path("/")):
            for p in patches:
                p.start()
            try:
                deficit = await compute_deficit_for_queue_item(db_session, item)
            finally:
                for p in patches:
                    p.stop()

        # Pool (10 + 500 = 510 g) covers 200 g → no deficit.
        assert deficit == []

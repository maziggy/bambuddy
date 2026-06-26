"""Unit tests for `_scope_notification_archive_data_to_plate` (#1785).

The 3MF parser at services/archive.py:200-264 sums per-plate `prediction` and
`weight` into archive-level totals (#1593) — correct for the archive card's
"whole project" headline, wrong for the completion notification of a single
plate. The helper under test mirrors what the queue UI does at
print_queue.py:272-285: re-read the 3MF and substitute the plate's actual
values for filament grams, time estimate, and per-slot breakdown.
"""

import io
import zipfile

from backend.app.main import _scope_notification_archive_data_to_plate


def _write_multi_plate_3mf(tmp_path, name="multi.3mf") -> "tuple":
    """Create a 3-plate 3MF with distinct prediction + weight per plate.

    Plate 1: 30 min, 50g PLA
    Plate 2: 60 min, 120g PETG
    Plate 3: 90 min, 200g PLA
    """
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <config>
        <plate>
            <metadata key="index" value="1"/>
            <metadata key="prediction" value="1800"/>
            <metadata key="weight" value="50"/>
            <filament id="1" used_g="50.0" type="PLA" color="#FF0000"/>
        </plate>
        <plate>
            <metadata key="index" value="2"/>
            <metadata key="prediction" value="3600"/>
            <metadata key="weight" value="120"/>
            <filament id="1" used_g="80.0" type="PETG" color="#00FF00"/>
            <filament id="2" used_g="40.0" type="PETG" color="#0000FF"/>
        </plate>
        <plate>
            <metadata key="index" value="3"/>
            <metadata key="prediction" value="5400"/>
            <metadata key="weight" value="200"/>
            <filament id="1" used_g="200.0" type="PLA" color="#FF0000"/>
        </plate>
    </config>
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Metadata/slice_info.config", xml_content)
    buffer.seek(0)

    file_path = tmp_path / name
    file_path.write_bytes(buffer.read())
    return file_path, "multi.3mf"


def _project_totals_archive_data() -> dict:
    """Pre-fix archive_data as `_background_notifications` constructs it: the
    summed-across-plates totals from PrintArchive's columns and extra_data."""
    return {
        # Summed: 30 + 60 + 90 min = 180 min = 10800s
        "print_time_seconds": 10800,
        "actual_time_seconds": None,
        # Summed: 50 + 120 + 200 = 370g
        "actual_filament_grams": 370.0,
        # Summed across all 3 plates' filament rows
        "filament_slots": [
            {"slot_id": 1, "used_g": 330.0, "type": "PLA", "color": "#FF0000"},
            {"slot_id": 2, "used_g": 40.0, "type": "PETG", "color": "#0000FF"},
        ],
    }


class TestScopeNotificationArchiveDataToPlate:
    def test_completed_plate_replaces_summed_totals(self, tmp_path):
        # The bug: notification shows project totals (370g, 3h) when only
        # plate 2 was printed. Expected after fix: plate 2's 120g and 60 min.
        file_path, rel = _write_multi_plate_3mf(tmp_path)
        archive_data = _project_totals_archive_data()

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            rel,
            plate_id=2,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == 120.0
        assert result["print_time_seconds"] == 3600
        assert result["filament_slots"] == [
            {"slot_id": 1, "used_g": 80.0, "type": "PETG", "color": "#00FF00"},
            {"slot_id": 2, "used_g": 40.0, "type": "PETG", "color": "#0000FF"},
        ]

    def test_plate_1_scoping_works(self, tmp_path):
        file_path, rel = _write_multi_plate_3mf(tmp_path)
        archive_data = _project_totals_archive_data()

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            rel,
            plate_id=1,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == 50.0
        assert result["print_time_seconds"] == 1800

    def test_plate_3_scoping_works(self, tmp_path):
        file_path, rel = _write_multi_plate_3mf(tmp_path)
        archive_data = _project_totals_archive_data()

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            rel,
            plate_id=3,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == 200.0
        assert result["print_time_seconds"] == 5400

    def test_partial_print_scales_plate_values(self, tmp_path):
        # Plate 2 cancelled at 50%: expect half the plate's grams + per-slot
        # values scaled, but full slicer estimate kept (callers display this
        # alongside the partial actual_filament_grams).
        file_path, rel = _write_multi_plate_3mf(tmp_path)
        archive_data = _project_totals_archive_data()

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            rel,
            plate_id=2,
            print_status="cancelled",
            progress=50,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == 60.0
        assert result["print_time_seconds"] == 3600
        assert result["filament_slots"] == [
            {"slot_id": 1, "used_g": 40.0, "type": "PETG", "color": "#00FF00"},
            {"slot_id": 2, "used_g": 20.0, "type": "PETG", "color": "#0000FF"},
        ]

    def test_no_plate_id_returns_unchanged(self, tmp_path):
        # Single-plate prints or non-plate-scoped completions take the
        # project-level archive values as-is.
        file_path, rel = _write_multi_plate_3mf(tmp_path)
        archive_data = _project_totals_archive_data()
        before = {**archive_data, "filament_slots": list(archive_data["filament_slots"])}

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            rel,
            plate_id=None,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == before["actual_filament_grams"]
        assert result["print_time_seconds"] == before["print_time_seconds"]
        assert result["filament_slots"] == before["filament_slots"]

    def test_no_file_path_returns_unchanged(self, tmp_path):
        archive_data = _project_totals_archive_data()
        before = {**archive_data}

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            None,
            plate_id=2,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == before["actual_filament_grams"]
        assert result["print_time_seconds"] == before["print_time_seconds"]

    def test_missing_3mf_returns_unchanged(self, tmp_path):
        # Archive's file may have been deleted (manual cleanup) between print
        # completion and the notification firing — must not blow up the
        # notification, just send the project-level numbers we already have.
        archive_data = _project_totals_archive_data()
        before = {**archive_data}

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            "missing.3mf",
            plate_id=2,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == before["actual_filament_grams"]
        assert result["print_time_seconds"] == before["print_time_seconds"]

    def test_corrupt_3mf_returns_unchanged(self, tmp_path):
        # Invalid file at the right path: helper falls back gracefully.
        bad_path = tmp_path / "bad.3mf"
        bad_path.write_text("not a zip file")

        archive_data = _project_totals_archive_data()
        before = {**archive_data}

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            "bad.3mf",
            plate_id=2,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == before["actual_filament_grams"]
        assert result["print_time_seconds"] == before["print_time_seconds"]

    def test_plate_id_outside_range_returns_unchanged(self, tmp_path):
        # Defensive: if plate_id doesn't match any plate in the 3MF, leave the
        # project-level numbers alone rather than emitting zeros.
        file_path, rel = _write_multi_plate_3mf(tmp_path)
        archive_data = _project_totals_archive_data()
        before = {**archive_data}

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            rel,
            plate_id=99,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == before["actual_filament_grams"]
        assert result["print_time_seconds"] == before["print_time_seconds"]

    def test_zero_grams_plate_keeps_project_level_breakdown(self, tmp_path):
        # Defensive: a 3MF that emits per-plate filament rows summing to zero
        # (slicer bug / re-slice without estimate) must NOT clobber the
        # project-level grams + per-slot breakdown the archive columns already
        # provide — otherwise the notification would headline "370 g" next to
        # an all-zero per-slot breakdown.
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="1"/>
                <metadata key="prediction" value="1800"/>
                <metadata key="weight" value="0"/>
                <filament id="1" used_g="0" type="PLA" color="#FF0000"/>
            </plate>
        </config>
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("Metadata/slice_info.config", xml_content)
        buffer.seek(0)
        file_path = tmp_path / "zero.3mf"
        file_path.write_bytes(buffer.read())

        archive_data = _project_totals_archive_data()
        before_slots = list(archive_data["filament_slots"])
        before_grams = archive_data["actual_filament_grams"]

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            "zero.3mf",
            plate_id=1,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        # Time still scopes (prediction parsed cleanly).
        assert result["print_time_seconds"] == 1800
        # Grams + per-slot breakdown stay on project-level so the notification
        # doesn't ship an inconsistent headline.
        assert result["actual_filament_grams"] == before_grams
        assert result["filament_slots"] == before_slots

    def test_single_plate_file_with_plate_id_1(self, tmp_path):
        # Single-plate 3MF where queue still has plate_id=1 set: the parser's
        # "sum across plates" already collapses to plate 1's values, so the
        # helper just confirms (no double-scaling, no field clobber).
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="1"/>
                <metadata key="prediction" value="2400"/>
                <metadata key="weight" value="75"/>
                <filament id="1" used_g="75.0" type="PLA" color="#0000FF"/>
            </plate>
        </config>
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("Metadata/slice_info.config", xml_content)
        buffer.seek(0)
        file_path = tmp_path / "single.3mf"
        file_path.write_bytes(buffer.read())

        archive_data = {
            "print_time_seconds": 2400,
            "actual_time_seconds": None,
            "actual_filament_grams": 75.0,
        }

        result = _scope_notification_archive_data_to_plate(
            archive_data,
            "single.3mf",
            plate_id=1,
            print_status="completed",
            progress=100,
            base_dir=tmp_path,
        )

        assert result["actual_filament_grams"] == 75.0
        assert result["print_time_seconds"] == 2400

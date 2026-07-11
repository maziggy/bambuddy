"""Skip-object extraction must follow the plate that is actually printing (#2522).

An "all plates" sliced export lists every plate in ``slice_info.config`` and
ships a ``plate_N.json`` per plate. Bambuddy read the *first* plate whatever
the printer was running, so a reporter printing plate 2 (one object) was
offered plate 1's four objects, with plate 1's marker positions drawn over
plate 2's thumbnail.

Two defects fed that: no call site passed ``plate_number``, and the lookup it
would have used (``.//plate[@plate_idx='N']``) tested an attribute Bambu and
Orca never write — the index lives in a ``<metadata key="index">`` child, so
the selector silently fell back to plate 1 regardless.

The fixture below mirrors the reporter's real file: plate 1 holds four copies
of ``stand_pillow_01.stl`` (identify_ids 2040/2062/2084/2106), plate 2 holds
one (2168).
"""

import json
import logging
import zipfile
from io import BytesIO
from types import SimpleNamespace

import pytest

from backend.app import main as main_module
from backend.app.services.archive import (
    extract_printable_objects_from_3mf,
    peek_plate_index_in_3mf,
)

PLATE_1_IDS = [2040, 2062, 2084, 2106]
PLATE_2_ID = 2168


def _plate_xml(index: int, ids: list[int]) -> str:
    objects = "".join(f'<object identify_id="{i}" name="stand_pillow_01.stl" skipped="false" />' for i in ids)
    return f'<plate><metadata key="index" value="{index}"/><metadata key="weight" value="10"/>{objects}</plate>'


def _plate_json(boxes: list[list[float]]) -> str:
    """A plate_N.json with one bbox_objects entry per box, plus their union."""
    return json.dumps(
        {
            "bbox_all": [
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ],
            "bbox_objects": [
                # The ids here are the slicer's own bbox ids, which do NOT equal
                # identify_id in real files — matching is by name, as before.
                {"id": 9000 + n, "name": "stand_pillow_01.stl", "bbox": box}
                for n, box in enumerate(boxes)
            ],
        }
    )


# Four copies in a square (plate 1) vs. a single copy elsewhere (plate 2).
PLATE_1_BOXES = [[0, 0, 10, 10], [90, 0, 100, 10], [0, 90, 10, 100], [90, 90, 100, 100]]
PLATE_2_BOXES = [[40, 40, 60, 60]]


def _multi_plate_3mf() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            f"<config><header/>{_plate_xml(1, PLATE_1_IDS)}{_plate_xml(2, [PLATE_2_ID])}</config>",
        )
        zf.writestr("Metadata/plate_1.json", _plate_json(PLATE_1_BOXES))
        zf.writestr("Metadata/plate_2.json", _plate_json(PLATE_2_BOXES))
    return buf.getvalue()


def _single_plate_3mf(index: int) -> bytes:
    """A per-plate export: one <plate>, but its index is the original plate number."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            f"<config><header/>{_plate_xml(index, [PLATE_2_ID])}</config>",
        )
        zf.writestr(f"Metadata/plate_{index}.json", _plate_json(PLATE_2_BOXES))
    return buf.getvalue()


class TestExtractPrintableObjectsPlateScope:
    def test_returns_only_the_requested_plates_objects(self):
        objects, bbox_all = extract_printable_objects_from_3mf(
            _multi_plate_3mf(), plate_number=2, include_positions=True
        )
        assert list(objects) == [PLATE_2_ID]
        # Positions come from plate_2.json, not plate_1.json.
        assert objects[PLATE_2_ID]["x"] == 50
        assert objects[PLATE_2_ID]["y"] == 50
        assert bbox_all == [40, 40, 60, 60]

    def test_other_plate_of_the_same_file_resolves_independently(self):
        objects, bbox_all = extract_printable_objects_from_3mf(
            _multi_plate_3mf(), plate_number=1, include_positions=True
        )
        assert sorted(objects) == PLATE_1_IDS
        assert bbox_all == [0, 0, 100, 100]

    def test_no_plate_given_falls_back_to_the_first(self):
        objects = extract_printable_objects_from_3mf(_multi_plate_3mf())
        assert sorted(objects) == PLATE_1_IDS

    def test_unknown_plate_falls_back_without_mixing_plates(self):
        # Plate 9 doesn't exist. We fall back to the first plate — and must read
        # ITS plate_1.json, not a plate_9.json that isn't there. Getting this
        # wrong would return plate 1's objects with no positions at all.
        objects, bbox_all = extract_printable_objects_from_3mf(
            _multi_plate_3mf(), plate_number=9, include_positions=True
        )
        assert sorted(objects) == PLATE_1_IDS
        assert bbox_all == [0, 0, 100, 100]
        assert all(o["x"] is not None for o in objects.values())

    def test_single_plate_export_keeps_its_own_index(self):
        # Bambu Studio's "current plate" export carries one <plate> whose index
        # is still the original plate number, and its positions live in
        # plate_3.json. Asking for plate 3 must match it.
        objects, bbox_all = extract_printable_objects_from_3mf(
            _single_plate_3mf(3), plate_number=3, include_positions=True
        )
        assert list(objects) == [PLATE_2_ID]
        assert bbox_all == [40, 40, 60, 60]


class TestPeekPlateIndexMultiPlate:
    def test_multi_plate_file_has_no_single_plate_index(self, tmp_path):
        # The #1204 guard compares this against the plate parsed from gcode_file
        # and throws the 3MF away on mismatch. An all-plates upload has no one
        # answer, and reporting plate 1 made the guard discard a correct file.
        path = tmp_path / "all-plates.3mf"
        path.write_bytes(_multi_plate_3mf())
        assert peek_plate_index_in_3mf(path) is None

    def test_single_plate_file_still_reports_its_index(self, tmp_path):
        path = tmp_path / "one-plate.3mf"
        path.write_bytes(_single_plate_3mf(2))
        assert peek_plate_index_in_3mf(path) == 2


class TestLoadObjectsFromArchiveWiring:
    """The extractor took a plate_number all along — no caller ever passed one."""

    @pytest.fixture
    def archive_3mf(self, tmp_path, monkeypatch):
        path = tmp_path / "job.3mf"
        path.write_bytes(_multi_plate_3mf())
        monkeypatch.setattr(main_module.app_settings, "base_dir", tmp_path)
        return SimpleNamespace(file_path="job.3mf")

    def _client(self, **state):
        fields = {
            "printable_objects": {},
            "printable_objects_bbox_all": None,
            "skipped_objects": [1],
            "gcode_file": None,
            "subtask_name": None,
            "dispatched_plate_id": None,
            "dispatched_subtask": None,
        }
        fields.update(state)
        return SimpleNamespace(state=SimpleNamespace(**fields))

    def _load(self, monkeypatch, client, archive):
        monkeypatch.setattr(main_module.printer_manager, "get_client", lambda pid: client)
        main_module._load_objects_from_archive(archive, 1, logging.getLogger(__name__))

    def test_uses_the_plate_bambuddy_dispatched(self, monkeypatch, archive_3mf):
        # Bambuddy-dispatched print: the plate is known from the dispatch itself,
        # which is what the reporter's P1S does (its gcode_file echo carries no
        # plate path — #1166).
        client = self._client(dispatched_plate_id=2, dispatched_subtask="job", subtask_name="job")
        self._load(monkeypatch, client, archive_3mf)

        assert list(client.state.printable_objects) == [PLATE_2_ID]
        assert client.state.printable_objects_bbox_all == [40, 40, 60, 60]
        assert client.state.skipped_objects == []

    def test_uses_the_plate_parsed_from_gcode_file(self, monkeypatch, archive_3mf):
        # Print started outside Bambuddy: the plate comes from the gcode path.
        client = self._client(gcode_file="/data/Metadata/plate_2.gcode")
        self._load(monkeypatch, client, archive_3mf)

        assert list(client.state.printable_objects) == [PLATE_2_ID]

    def test_unknown_plate_still_loads_the_first(self, monkeypatch, archive_3mf):
        client = self._client()
        self._load(monkeypatch, client, archive_3mf)

        assert sorted(client.state.printable_objects) == PLATE_1_IDS

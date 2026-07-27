"""Tests for the per-slice 3MF input normalisation helpers."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

from backend.app.services.slicer_3mf_convert import (
    count_plates_in_3mf,
    extract_source_printer_model,
    merge_plate_3mfs,
    substitute_unused_plate_filaments,
)


def _make_3mf(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buf.getvalue()


class TestExtractSourcePrinterModel:
    def test_returns_canonical_short_code_for_x1c(self):
        # Raw field is the long display name; we need the short code so
        # is_dual_nozzle_model() matches against the model registry.
        cfg = json.dumps({"printer_model": "Bambu Lab X1 Carbon", "other": "field"}).encode()
        zip_bytes = _make_3mf({"Metadata/project_settings.config": cfg})
        assert extract_source_printer_model(zip_bytes) == "X1C"

    def test_returns_canonical_short_code_for_h2d(self):
        cfg = json.dumps({"printer_model": "Bambu Lab H2D"}).encode()
        zip_bytes = _make_3mf({"Metadata/project_settings.config": cfg})
        assert extract_source_printer_model(zip_bytes) == "H2D"

    def test_dual_nozzle_check_works_on_extracted_code(self):
        """The whole point of canonicalising in this helper: the result
        must feed straight into is_dual_nozzle_model() without further
        normalisation."""
        from backend.app.utils.printer_models import is_dual_nozzle_model

        h2d = _make_3mf({"Metadata/project_settings.config": json.dumps({"printer_model": "Bambu Lab H2D"}).encode()})
        x1c = _make_3mf(
            {"Metadata/project_settings.config": json.dumps({"printer_model": "Bambu Lab X1 Carbon"}).encode()}
        )
        assert is_dual_nozzle_model(extract_source_printer_model(h2d)) is True
        assert is_dual_nozzle_model(extract_source_printer_model(x1c)) is False

    def test_returns_none_when_field_missing(self):
        cfg = json.dumps({"other": "field"}).encode()
        zip_bytes = _make_3mf({"Metadata/project_settings.config": cfg})
        assert extract_source_printer_model(zip_bytes) is None

    def test_returns_none_when_field_empty(self):
        cfg = json.dumps({"printer_model": ""}).encode()
        zip_bytes = _make_3mf({"Metadata/project_settings.config": cfg})
        assert extract_source_printer_model(zip_bytes) is None

    def test_returns_none_when_no_embedded_config(self):
        zip_bytes = _make_3mf({"Metadata/other.txt": b"hello"})
        assert extract_source_printer_model(zip_bytes) is None

    def test_returns_none_for_non_zip_bytes(self):
        assert extract_source_printer_model(b"not a zip") is None

    def test_returns_none_for_malformed_json(self):
        zip_bytes = _make_3mf({"Metadata/project_settings.config": b"{not json"})
        assert extract_source_printer_model(zip_bytes) is None

    def test_returns_none_when_config_is_list_not_dict(self):
        cfg = json.dumps(["not", "a", "dict"]).encode()
        zip_bytes = _make_3mf({"Metadata/project_settings.config": cfg})
        assert extract_source_printer_model(zip_bytes) is None


class TestCountPlatesIn3mf:
    def test_counts_plater_id_entries(self):
        xml = (
            b'<?xml version="1.0"?>\n<config>\n'
            b'<plate><metadata key="plater_id" value="1"/></plate>\n'
            b'<plate><metadata key="plater_id" value="2"/></plate>\n'
            b'<plate><metadata key="plater_id" value="3"/></plate>\n'
            b"</config>\n"
        )
        zip_bytes = _make_3mf({"Metadata/model_settings.config": xml})
        assert count_plates_in_3mf(zip_bytes) == 3

    def test_returns_zero_for_no_model_settings(self):
        zip_bytes = _make_3mf({"3D/3dmodel.model": b"<model/>"})
        assert count_plates_in_3mf(zip_bytes) == 0

    def test_returns_zero_for_non_zip(self):
        assert count_plates_in_3mf(b"not a zip") == 0

    def test_returns_zero_when_no_plate_ids(self):
        zip_bytes = _make_3mf({"Metadata/model_settings.config": b"<config/>"})
        assert count_plates_in_3mf(zip_bytes) == 0


class TestMergePlate3mfs:
    """Per-plate cross-class loop output → merged multi-plate 3MF. The
    merge needs to: (1) carry forward the first plate's base metadata
    (project_settings, model_settings, 3dmodel), (2) overlay each
    plate's gcode + thumbnails, (3) re-assemble slice_info.config to
    list every plate."""

    @staticmethod
    def _single_plate_3mf(plate_num: int, gcode_bytes: bytes, slice_info_block: str | None = None) -> bytes:
        slice_info = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
            '<header><header_item key="X-BBL-Client-Type" value="slicer"/></header>\n'
            + (slice_info_block or f'<plate><metadata key="index" value="{plate_num}"/></plate>')
            + "\n</config>\n"
        ).encode("utf-8")
        return _make_3mf(
            {
                "3D/3dmodel.model": f"<model plate={plate_num}/>".encode(),
                "Metadata/project_settings.config": b'{"printer_model": "Bambu Lab H2D"}',
                "Metadata/model_settings.config": b"<config/>",
                "Metadata/slice_info.config": slice_info,
                f"Metadata/plate_{plate_num}.gcode": gcode_bytes,
                f"Metadata/plate_{plate_num}.gcode.md5": b"d41d8cd98f00b204e9800998ecf8427e",
                f"Metadata/plate_{plate_num}.json": b"{}",
                f"Metadata/plate_{plate_num}.png": b"PLATE_PNG",
                f"Metadata/plate_{plate_num}_small.png": b"SMALL",
                f"Metadata/top_{plate_num}.png": b"TOP",
                f"Metadata/pick_{plate_num}.png": b"PICK",
            }
        )

    def test_empty_input_raises(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            merge_plate_3mfs([])

    def test_single_plate_is_passthrough(self):
        only = self._single_plate_3mf(1, b"GCODE-1")
        assert merge_plate_3mfs([(1, only)]) == only

    def test_overlays_per_plate_artifacts(self):
        p1 = self._single_plate_3mf(1, b"GCODE-PLATE-1")
        p2 = self._single_plate_3mf(2, b"GCODE-PLATE-2")
        p3 = self._single_plate_3mf(3, b"GCODE-PLATE-3")
        merged = merge_plate_3mfs([(1, p1), (2, p2), (3, p3)])

        with zipfile.ZipFile(BytesIO(merged), "r") as zf:
            assert zf.read("Metadata/plate_1.gcode") == b"GCODE-PLATE-1"
            assert zf.read("Metadata/plate_2.gcode") == b"GCODE-PLATE-2"
            assert zf.read("Metadata/plate_3.gcode") == b"GCODE-PLATE-3"
            # Per-plate thumbnails and json overlaid too.
            assert zf.read("Metadata/plate_2.png") == b"PLATE_PNG"
            assert zf.read("Metadata/plate_3_small.png") == b"SMALL"
            # Base 3MF's project_settings.config carried forward unchanged.
            assert zf.read("Metadata/project_settings.config") == p1_project(p1)

    def test_combined_slice_info_lists_every_plate(self):
        p1 = self._single_plate_3mf(1, b"G1", slice_info_block='<plate><metadata key="index" value="1"/></plate>')
        p2 = self._single_plate_3mf(2, b"G2", slice_info_block='<plate><metadata key="index" value="2"/></plate>')
        merged = merge_plate_3mfs([(1, p1), (2, p2)])

        with zipfile.ZipFile(BytesIO(merged), "r") as zf:
            info = zf.read("Metadata/slice_info.config").decode("utf-8")
        # Both plate blocks present.
        assert 'value="1"' in info
        assert 'value="2"' in info
        # Two <plate> blocks total (we don't include the source's stale
        # one from before slicing).
        assert info.count("<plate>") == 2

    def test_falls_back_to_source_thumbnails_when_sliced_outputs_lack_them(self):
        """BS CLI with --arrange generates fresh per-plate gcode but
        doesn't always write a fresh ``plate_N.png``. The merger's
        ``source_3mf_bytes`` fallback should fill the gap from the
        source 3MF's original per-plate render so the archive's per-
        plate previews aren't blank."""

        # Sliced outputs that lack plate_N.png entries entirely (only
        # gcode + json + md5 — the thumbnail slot is empty).
        def _no_thumb_3mf(plate_num: int) -> bytes:
            return _make_3mf(
                {
                    "3D/3dmodel.model": b"<model/>",
                    "Metadata/project_settings.config": b"{}",
                    "Metadata/model_settings.config": b"<config/>",
                    "Metadata/slice_info.config": (
                        '<?xml version="1.0"?>\n<config>'
                        f'<plate><metadata key="index" value="{plate_num}"/></plate>'
                        "</config>"
                    ).encode(),
                    f"Metadata/plate_{plate_num}.gcode": f"G{plate_num}".encode(),
                }
            )

        source = _make_3mf(
            {
                "3D/3dmodel.model": b"<model/>",
                "Metadata/plate_1.png": b"SRC_PNG_1",
                "Metadata/plate_1_small.png": b"SRC_SMALL_1",
                "Metadata/plate_2.png": b"SRC_PNG_2",
                "Metadata/plate_2_small.png": b"SRC_SMALL_2",
            }
        )

        merged = merge_plate_3mfs(
            [(1, _no_thumb_3mf(1)), (2, _no_thumb_3mf(2))],
            source_3mf_bytes=source,
        )
        with zipfile.ZipFile(BytesIO(merged), "r") as zf:
            assert zf.read("Metadata/plate_1.png") == b"SRC_PNG_1"
            assert zf.read("Metadata/plate_1_small.png") == b"SRC_SMALL_1"
            assert zf.read("Metadata/plate_2.png") == b"SRC_PNG_2"
            assert zf.read("Metadata/plate_2_small.png") == b"SRC_SMALL_2"

    def test_source_fallback_does_not_overwrite_fresh_sliced_thumbnails(self):
        """If a sliced output DID write its own ``plate_N.png`` (same-class
        slice / older BS that renders even with arrange), keep it — the
        sliced render reflects the actual H2D layout; the source fallback
        only fills gaps."""
        p1 = self._single_plate_3mf(1, b"G1")  # has plate_1.png = PLATE_PNG
        p2 = self._single_plate_3mf(2, b"G2")  # has plate_2.png = PLATE_PNG
        source = _make_3mf(
            {
                "Metadata/plate_1.png": b"SRC_PNG_1",
                "Metadata/plate_2.png": b"SRC_PNG_2",
            }
        )
        merged = merge_plate_3mfs([(1, p1), (2, p2)], source_3mf_bytes=source)
        with zipfile.ZipFile(BytesIO(merged), "r") as zf:
            # Sliced output's thumbnails win.
            assert zf.read("Metadata/plate_1.png") == b"PLATE_PNG"
            assert zf.read("Metadata/plate_2.png") == b"PLATE_PNG"

    def test_unsorted_input_is_sorted_by_plate_number(self):
        p1 = self._single_plate_3mf(1, b"G1")
        p2 = self._single_plate_3mf(2, b"G2")
        # Pass them out of order; the merger should still place plate 2's
        # artifacts at plate_2.* and plate 1's at plate_1.*.
        merged = merge_plate_3mfs([(2, p2), (1, p1)])
        with zipfile.ZipFile(BytesIO(merged), "r") as zf:
            assert zf.read("Metadata/plate_1.gcode") == b"G1"
            assert zf.read("Metadata/plate_2.gcode") == b"G2"


def p1_project(zip_bytes: bytes) -> bytes:
    """Helper for the merge test — pulls plate-1's project_settings.config out
    of a fixture so the test's assertion shows the actual reference value
    rather than hard-coding the literal."""
    with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
        return zf.read("Metadata/project_settings.config")


class TestSubstituteUnusedPlateFilaments:
    """Unused-slot entries are overwritten with the plate's lowest *used*
    slot so BambuStudio's validators don't trip on loaded filaments the
    plate's G-code never actually touches — neither the filament-temp
    spread nor the preset-vs-printer compatibility check."""

    @staticmethod
    def _model_settings_xml(per_plate_extruders: list[tuple[int, list[int]]]) -> bytes:
        """Build a minimal model_settings.config mapping each plate to a set
        of extruder/slot numbers via per-object extruder metadata. Mirrors
        the schema ``extract_plate_extruder_set_from_3mf`` parses:
        - top-level ``<object id=N>`` with ``<metadata key="extruder" .../>``
        - per-plate ``<plate>`` listing the object ids it contains.
        ``per_plate_extruders`` is a list of (plate_id, [extruder_ids]).
        Object ids are auto-numbered globally so plates can reference them.
        """
        objects = []
        plates = []
        oid = 1
        for plate_id, exts in per_plate_extruders:
            plate_obj_refs = []
            for ext in exts:
                objects.append(f'<object id="{oid}"><metadata key="extruder" value="{ext}"/></object>')
                plate_obj_refs.append(
                    f'<model_instance><metadata key="object_id" value="{oid}"/>'
                    f'<metadata key="instance_id" value="0"/>'
                    f'<metadata key="identify_id" value="{oid}"/></model_instance>'
                )
                oid += 1
            plates.append(
                f'<plate><metadata key="plater_id" value="{plate_id}"/>' + "".join(plate_obj_refs) + "</plate>"
            )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<config>\n" + "\n".join(objects) + "\n" + "\n".join(plates) + "\n" + "</config>"
        )
        return xml.encode("utf-8")

    def test_substitutes_unused_slot_with_slot_1(self):
        # Plate 1 uses slot 1 only; slots 2 and 3 are loaded but unused.
        zip_bytes = _make_3mf({"Metadata/model_settings.config": self._model_settings_xml([(1, [1])])})
        items = ["pla_basic.json", "abs_loaded_but_unused.json", "abs_again_unused.json"]
        result = substitute_unused_plate_filaments(zip_bytes, plate_id=1, items=items)
        assert result == ["pla_basic.json", "pla_basic.json", "pla_basic.json"]

    def test_no_substitution_when_all_used(self):
        # Multi-colour plate where every slot is used: leave the user's selections alone.
        zip_bytes = _make_3mf({"Metadata/model_settings.config": self._model_settings_xml([(1, [1, 2, 3])])})
        items = ["pla_white.json", "pla_red.json", "pla_blue.json"]
        result = substitute_unused_plate_filaments(zip_bytes, plate_id=1, items=items)
        assert result == ["pla_white.json", "pla_red.json", "pla_blue.json"]

    def test_no_op_when_plate_id_is_none(self):
        items = ["a.json", "b.json", "c.json"]
        result = substitute_unused_plate_filaments(b"any bytes", plate_id=None, items=items)
        assert result == items

    def test_no_op_when_single_filament(self):
        result = substitute_unused_plate_filaments(b"any bytes", plate_id=1, items=["only.json"])
        assert result == ["only.json"]
        result = substitute_unused_plate_filaments(b"any bytes", plate_id=1, items=[])
        assert result == []

    def test_no_op_when_source_not_zip(self):
        items = ["a.json", "b.json"]
        result = substitute_unused_plate_filaments(b"not a zip", plate_id=1, items=items)
        assert result == items

    def test_no_op_when_no_model_settings(self):
        # Empty parse result is treated as "every slot used" (fail-open default).
        zip_bytes = _make_3mf({"3D/3dmodel.model": b"<model/>"})
        items = ["a.json", "b.json", "c.json"]
        result = substitute_unused_plate_filaments(zip_bytes, plate_id=1, items=items)
        assert result == items

    def test_support_material_slot_preserved(self):
        # #1881 regression: object geometry references only slot 1 (PLA),
        # but slot 2 (PVA) is configured as the support material in
        # project_settings.config. Without the support-slot union, slot 2's
        # user-picked PVA profile would be overwritten with slot 1's PLA
        # and the print would come out single-material with PLA supports.
        model_settings = self._model_settings_xml([(1, [1])])
        project_settings = json.dumps(
            {
                "enable_support": "1",
                "support_filament": "2",
                "support_interface_filament": "2",
                "filament_type": ["PLA", "PVA"],
            }
        ).encode()
        zip_bytes = _make_3mf(
            {
                "Metadata/model_settings.config": model_settings,
                "Metadata/project_settings.config": project_settings,
            }
        )
        items = ["pla.json", "pva_support.json"]
        result = substitute_unused_plate_filaments(zip_bytes, plate_id=1, items=items)
        assert result == ["pla.json", "pva_support.json"]

    def test_support_disabled_still_substitutes_unused(self):
        # When supports are off, slot 2 is genuinely unused — the temp-spread
        # validator still needs the substitution to succeed.
        model_settings = self._model_settings_xml([(1, [1])])
        project_settings = json.dumps(
            {
                "enable_support": "0",
                "support_filament": "2",
            }
        ).encode()
        zip_bytes = _make_3mf(
            {
                "Metadata/model_settings.config": model_settings,
                "Metadata/project_settings.config": project_settings,
            }
        )
        items = ["pla.json", "abs_never_used.json"]
        result = substitute_unused_plate_filaments(zip_bytes, plate_id=1, items=items)
        assert result == ["pla.json", "pla.json"]

    # ---- #2628: the anchor is the lowest USED slot, not slot 1 ----------

    def test_substitutes_from_first_used_slot_when_slot_1_is_unused(self):
        """michaelklos's report: plate 2 of a multi-plate project uses only
        slot 2, while slot 1 carries an ``@Bambu Lab H2D`` preset baked into
        the source 3MF. Anchoring on slot 1 made the substitution a no-op for
        the one slot that needed it, and the CLI rejected the A1 slice with
        "filament preset (slot 1) is not compatible with printer
        Bambu Lab A1 0.4 nozzle"."""
        zip_bytes = _make_3mf({"Metadata/model_settings.config": self._model_settings_xml([(1, [1]), (2, [2])])})
        items = ["tpu_at_h2d.json", "pla_at_a1.json"]

        result = substitute_unused_plate_filaments(zip_bytes, plate_id=2, items=items)

        assert result == ["pla_at_a1.json", "pla_at_a1.json"]

    def test_unused_slot_1_does_not_poison_the_other_unused_slots(self):
        """With more than one unused slot, the old anchor propagated slot 1's
        own (foreign-printer) preset into every other unused slot — the exact
        poisoning #1851 removed from the picker."""
        zip_bytes = _make_3mf({"Metadata/model_settings.config": self._model_settings_xml([(1, [1]), (2, [3])])})
        items = ["tpu_at_h2d.json", "abs.json", "pla_at_a1.json"]

        result = substitute_unused_plate_filaments(zip_bytes, plate_id=2, items=items)

        assert result == ["pla_at_a1.json", "pla_at_a1.json", "pla_at_a1.json"]

    def test_anchor_is_the_lowest_used_slot_not_merely_a_used_one(self):
        """Deterministic pick so the same project always slices identically."""
        zip_bytes = _make_3mf({"Metadata/model_settings.config": self._model_settings_xml([(1, [1]), (2, [2, 4])])})
        items = ["slot1.json", "slot2.json", "slot3.json", "slot4.json"]

        result = substitute_unused_plate_filaments(zip_bytes, plate_id=2, items=items)

        assert result == ["slot2.json", "slot2.json", "slot2.json", "slot4.json"]

    def test_no_op_when_every_used_slot_is_outside_the_submitted_list(self):
        """A plate referencing only slots beyond the picked list leaves nothing
        trustworthy to copy from — keep the user's picks rather than invent a
        substitution from a slot the plate doesn't use."""
        zip_bytes = _make_3mf({"Metadata/model_settings.config": self._model_settings_xml([(1, [1]), (2, [5])])})
        items = ["a.json", "b.json"]

        result = substitute_unused_plate_filaments(zip_bytes, plate_id=2, items=items)

        assert result == ["a.json", "b.json"]

    def test_support_slot_can_be_the_anchor(self):
        """The support-filament union (#1881) feeds the same used-slot set, so
        a plate whose only geometry slot is 2 with PVA supports in 3 anchors on
        2 — never on the unused slot 1."""
        model_settings = self._model_settings_xml([(1, [1]), (2, [2])])
        project_settings = json.dumps(
            {
                "enable_support": "1",
                "support_filament": "3",
                "support_interface_filament": "3",
                "filament_type": ["PLA", "PLA", "PVA"],
            }
        ).encode()
        zip_bytes = _make_3mf(
            {
                "Metadata/model_settings.config": model_settings,
                "Metadata/project_settings.config": project_settings,
            }
        )
        items = ["tpu_at_h2d.json", "pla.json", "pva.json"]

        result = substitute_unused_plate_filaments(zip_bytes, plate_id=2, items=items)

        assert result == ["pla.json", "pla.json", "pva.json"]

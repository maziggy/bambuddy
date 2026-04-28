"""Tests for `SliceRequest` validator — covers both the legacy bare-int
shape and the new source-aware shape, plus the backwards-compat
normalisation that lets the route handler ignore the difference.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.slicer import PresetRef, SliceRequest


class TestLegacyBareIntegerShape:
    """Existing clients (and stale browser tabs after upgrade) keep
    sending bare integer ids. They must continue working unchanged."""

    def test_bare_int_ids_normalise_to_local_preset_ref(self):
        req = SliceRequest(printer_preset_id=1, process_preset_id=2, filament_preset_id=3)
        assert req.printer_preset == PresetRef(source="local", id="1")
        assert req.process_preset == PresetRef(source="local", id="2")
        assert req.filament_preset == PresetRef(source="local", id="3")

    def test_legacy_ids_unchanged_in_payload(self):
        """The legacy fields stay populated — no behaviour change for
        clients that read them back from the model."""
        req = SliceRequest(printer_preset_id=10, process_preset_id=20, filament_preset_id=30)
        assert req.printer_preset_id == 10
        assert req.process_preset_id == 20
        assert req.filament_preset_id == 30


class TestNewSourceAwareShape:
    """The new modal sends source-aware refs explicitly."""

    def test_cloud_refs_pass_through(self):
        req = SliceRequest(
            printer_preset=PresetRef(source="cloud", id="PFUprinter"),
            process_preset=PresetRef(source="cloud", id="PFUprocess"),
            filament_preset=PresetRef(source="cloud", id="PFUfilament"),
        )
        assert req.printer_preset.source == "cloud"
        assert req.printer_preset.id == "PFUprinter"

    def test_mixed_sources_per_slot(self):
        """A user may pick cloud for printer, local for process, standard
        for filament — the modal is per-slot."""
        req = SliceRequest(
            printer_preset=PresetRef(source="cloud", id="PFU123"),
            process_preset=PresetRef(source="local", id="42"),
            filament_preset=PresetRef(source="standard", id="Bambu PLA Basic"),
        )
        assert req.printer_preset.source == "cloud"
        assert req.process_preset.source == "local"
        assert req.filament_preset.source == "standard"


class TestValidationErrors:
    def test_missing_printer_slot_raises(self):
        with pytest.raises(ValidationError) as exc:
            SliceRequest(process_preset_id=2, filament_preset_id=3)
        assert "printer" in str(exc.value)

    def test_invalid_source_rejected(self):
        with pytest.raises(ValidationError):
            SliceRequest(
                printer_preset={"source": "made_up", "id": "x"},
                process_preset_id=2,
                filament_preset_id=3,
            )


class TestPriorityWhenBothSet:
    """If a client sends BOTH the legacy id AND the new ref for the same
    slot (unlikely in practice, but ambiguous), the new ref wins. Tests
    pin the resolution order so a future schema change can't silently
    flip it."""

    def test_explicit_ref_wins_over_legacy_id(self):
        req = SliceRequest(
            printer_preset_id=999,  # would resolve to local:999
            printer_preset=PresetRef(source="cloud", id="PFU"),
            process_preset_id=2,
            filament_preset_id=3,
        )
        # Validator only fills the ref when it's None — the explicit cloud
        # ref stays untouched.
        assert req.printer_preset == PresetRef(source="cloud", id="PFU")


class TestFilamentPresetsList:
    """Multi-color: the new array shape carries one filament profile per
    plate slot in plate order. Backwards-compat: legacy clients still
    submit a singular `filament_preset` and the validator promotes it into
    a one-element list so the route handler only deals with one shape."""

    def test_explicit_list_passes_through(self):
        refs = [
            PresetRef(source="cloud", id="A"),
            PresetRef(source="local", id="2"),
            PresetRef(source="standard", id="Bambu PLA Basic"),
        ]
        req = SliceRequest(
            printer_preset_id=1,
            process_preset_id=2,
            filament_preset_id=99,  # explicit legacy id — should be ignored
            filament_presets=refs,
        )
        assert req.filament_presets == refs
        # Precedence pin: when caller sends both shapes, the array wins and
        # the singular gets backfilled from the array's first entry — NOT
        # from the legacy id 99. Documents the migration ordering for a
        # future change that might quietly mix them.
        assert req.filament_preset == refs[0]

    def test_empty_list_is_backfilled_from_singular(self):
        req = SliceRequest(printer_preset_id=1, process_preset_id=2, filament_preset_id=3)
        # Legacy single-color path: validator promotes the singular into a
        # one-element list so route handlers can iterate uniformly.
        assert req.filament_presets == [PresetRef(source="local", id="3")]

    def test_explicit_empty_list_with_singular_set_uses_singular(self):
        # User of the new schema can leave `filament_presets` as the empty
        # default and rely on the legacy `filament_preset_id` — same path
        # as `test_empty_list_is_backfilled_from_singular`.
        req = SliceRequest(
            printer_preset_id=1,
            process_preset_id=2,
            filament_preset=PresetRef(source="cloud", id="PFU"),
            filament_presets=[],
        )
        assert req.filament_presets == [PresetRef(source="cloud", id="PFU")]

    def test_list_preserves_order(self):
        refs = [
            PresetRef(source="cloud", id="slot1"),
            PresetRef(source="cloud", id="slot2"),
            PresetRef(source="cloud", id="slot3"),
        ]
        req = SliceRequest(
            printer_preset_id=1,
            process_preset_id=2,
            filament_preset_id=3,
            filament_presets=refs,
        )
        assert [r.id for r in req.filament_presets] == ["slot1", "slot2", "slot3"]

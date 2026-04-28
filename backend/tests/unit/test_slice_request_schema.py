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

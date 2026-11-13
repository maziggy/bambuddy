"""Nozzle-aware printer pick for "Any <model>" queue items (#3536).

Before #3536 the model-based pick looked at model, filament and state but not
at the nozzle: a file sliced for 0.6 mm could be assigned to a printer with a
0.4 mm nozzle and then fail in ``_start_print``. These tests cover the pieces
of the fix in order: the sliced diameter carried on each candidate, then the
printer filter built on it.
"""

from types import SimpleNamespace

import pytest

from backend.app.services.print_scheduler import _candidates_for, _sliced_nozzle, _wrong_nozzle_label

# --- Task 1: sliced diameter per candidate ---------------------------------


def _item(*, variants=None, archive=None, library_file=None):
    return SimpleNamespace(
        variants=variants or [],
        archive=archive,
        archive_id=1 if archive is not None else None,
        library_file=library_file,
        library_file_id=2 if library_file is not None else None,
        target_model="A1",
        required_filament_types=None,
        filament_overrides=None,
    )


def _variant(vid, nozzle):
    return SimpleNamespace(
        id=vid,
        position=vid,
        attempt_count=0,
        target_model="A1",
        required_filament_types=None,
        filament_overrides=None,
        library_file=SimpleNamespace(deleted_at=None, file_metadata={"nozzle_diameter": nozzle}),
    )


def test_sliced_nozzle_from_archive():
    assert _sliced_nozzle(SimpleNamespace(nozzle_diameter=0.6), None) == 0.6


def test_sliced_nozzle_from_library_metadata():
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": 0.6})) == 0.6


def test_sliced_nozzle_unknown():
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata=None)) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={})) is None
    assert _sliced_nozzle(None, None) is None


def test_sliced_nozzle_rejects_garbage_and_non_positive():
    assert _sliced_nozzle(SimpleNamespace(nozzle_diameter=0), None) is None
    assert _sliced_nozzle(SimpleNamespace(nozzle_diameter=-0.4), None) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": "abc"})) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": [0.4]})) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": "0.6"})) == 0.6


def test_sliced_nozzle_is_read_without_rounding():
    # The tolerance lives in _nozzle_mismatch_message, not here.
    meta = SimpleNamespace(file_metadata={"nozzle_diameter": "0.6000001"})
    assert _sliced_nozzle(None, meta) == 0.6000001


def test_candidates_carry_their_own_nozzle():
    single = _item(library_file=SimpleNamespace(file_metadata={"nozzle_diameter": 0.6}))
    assert [c.sliced_nozzle for c in _candidates_for(single)] == [0.6]

    multi = _item(variants=[_variant(1, 0.4), _variant(2, 0.6)])
    assert sorted(c.sliced_nozzle for c in _candidates_for(multi)) == [0.4, 0.6]


# --- Task 2: does this nozzle fit? ------------------------------------------


def _state(*diameters: str):
    """PrinterState-shaped namespace with the given nozzle diameter strings."""
    return SimpleNamespace(nozzles=[SimpleNamespace(nozzle_diameter=d) for d in diameters])


def _h2c(*rack: tuple[int, str], mounted: str = "0.4"):
    """H2C-shaped state: one mounted hotend plus docked nozzles (#2885 shape)."""
    info = [{"id": 0, "diameter": mounted, "wear": 0, "max_temp": 350, "serial_number": "SN0"}]
    info += [
        {"id": nozzle_id, "diameter": d, "wear": 0, "max_temp": 350, "serial_number": f"SN{nozzle_id}"}
        for nozzle_id, d in rack
    ]
    return SimpleNamespace(nozzles=[SimpleNamespace(nozzle_diameter=mounted)], nozzle_rack=info)


def test_wrong_nozzle_label_single_and_dual():
    assert _wrong_nozzle_label("A1-1", _state("0.4"), 0.6) == "A1-1 (0.4mm)"
    assert _wrong_nozzle_label("A1-1", _state("0.6"), 0.6) is None
    assert _wrong_nozzle_label("H2D-1", _state("0.4", "0.4"), 0.6) == "H2D-1 (0.4mm)"
    assert _wrong_nozzle_label("H2D-1", _state("0.4", "0.6"), 0.6) is None


def test_wrong_nozzle_label_unknown_fits():
    assert _wrong_nozzle_label("A1-1", _state("0.4"), None) is None
    assert _wrong_nozzle_label("A1-1", _state("0.4"), 0) is None
    assert _wrong_nozzle_label("A1-1", None, 0.6) is None
    assert _wrong_nozzle_label("A1-1", _state(""), 0.6) is None  # nothing reported


def test_wrong_nozzle_label_rack():
    assert _wrong_nozzle_label("H2C-1", _h2c((16, "0.6")), 0.6) is None
    assert _wrong_nozzle_label("H2C-1", _h2c((16, "0.2"), (18, "0.8")), 0.6) == "H2C-1 (0.4mm, rack 0.2mm / 0.8mm)"


def test_wrong_nozzle_label_rack_without_mounted_nozzle():
    status = SimpleNamespace(
        nozzles=[SimpleNamespace(nozzle_diameter="")],
        nozzle_rack=[{"id": 16, "diameter": "0.2", "max_temp": 350, "serial_number": "SN16"}],
    )
    assert _wrong_nozzle_label("H2C-1", status, 0.6) == "H2C-1 (no nozzle mounted, rack 0.2mm)"


SIZES = ["0.2", "0.4", "0.6", "0.8"]


@pytest.mark.parametrize("sliced", SIZES)
@pytest.mark.parametrize("installed", SIZES)
def test_only_the_same_size_fits(sliced, installed):
    # Same size fits, every other (also the neighbouring one) never does.
    label = _wrong_nozzle_label("A1-1", _state(installed), float(sliced))
    if sliced == installed:
        assert label is None
    else:
        assert label == f"A1-1 ({installed}mm)"


def test_float_noise_from_metadata_still_fits():
    assert _wrong_nozzle_label("A1-1", _state("0.6"), 0.6000001) is None
    assert _wrong_nozzle_label("A1-1", _state("0.4"), 0.6000001) == "A1-1 (0.4mm)"

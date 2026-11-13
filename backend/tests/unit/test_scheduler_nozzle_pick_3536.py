"""Nozzle-aware printer pick for "Any <model>" queue items (#3536).

Before #3536 the model-based pick looked at model, filament and state but not
at the nozzle: a file sliced for 0.6 mm could be assigned to a printer with a
0.4 mm nozzle and then fail in ``_start_print``. These tests cover the pieces
of the fix in order: the sliced diameter carried on each candidate, then the
printer filter built on it.
"""

from types import SimpleNamespace

from backend.app.services.print_scheduler import _candidates_for, _sliced_nozzle

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


def test_candidates_carry_their_own_nozzle():
    single = _item(library_file=SimpleNamespace(file_metadata={"nozzle_diameter": 0.6}))
    assert [c.sliced_nozzle for c in _candidates_for(single)] == [0.6]

    multi = _item(variants=[_variant(1, 0.4), _variant(2, 0.6)])
    assert sorted(c.sliced_nozzle for c in _candidates_for(multi)) == [0.4, 0.6]

"""Validator tests for the user-configurable temperature / fan presets."""

import pytest
from pydantic import ValidationError

from backend.app.schemas.settings import AppSettingsUpdate

# (field_name, valid_payload, range_lo, range_hi)
PRESET_FIELDS = [
    ("nozzle_temp_presets", "[120, 220, 260]", 0, 320),
    ("bed_temp_presets", "[55, 75, 90]", 0, 140),
    ("chamber_temp_presets", "[35, 45, 60]", 0, 60),
    ("fan_speed_presets", "[50, 75, 100]", 0, 100),
]


@pytest.mark.parametrize("field,valid,lo,hi", PRESET_FIELDS)
def test_valid_triple_round_trips(field, valid, lo, hi):
    update = AppSettingsUpdate(**{field: valid})
    assert getattr(update, field) == valid


@pytest.mark.parametrize("field,_valid,_lo,_hi", PRESET_FIELDS)
def test_empty_string_means_use_defaults(field, _valid, _lo, _hi):
    update = AppSettingsUpdate(**{field: ""})
    assert getattr(update, field) == ""


@pytest.mark.parametrize("field,_valid,_lo,_hi", PRESET_FIELDS)
def test_missing_field_is_optional(field, _valid, _lo, _hi):
    # Updates are PATCH-style; omitting the field shouldn't trigger the validator.
    update = AppSettingsUpdate()
    assert getattr(update, field) is None


@pytest.mark.parametrize("field,_valid,_lo,_hi", PRESET_FIELDS)
def test_malformed_json_rejected(field, _valid, _lo, _hi):
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(**{field: "not json"})
    assert field in str(exc.value)


@pytest.mark.parametrize("field,_valid,_lo,_hi", PRESET_FIELDS)
def test_non_array_rejected(field, _valid, _lo, _hi):
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(**{field: '{"a": 1}'})
    assert "array of exactly 3 integers" in str(exc.value)


@pytest.mark.parametrize("field,_valid,_lo,_hi", PRESET_FIELDS)
@pytest.mark.parametrize("bad_payload", ["[]", "[100]", "[100, 200]", "[100, 200, 300, 400]"])
def test_wrong_length_rejected(field, _valid, _lo, _hi, bad_payload):
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(**{field: bad_payload})
    assert "array of exactly 3 integers" in str(exc.value)


@pytest.mark.parametrize("field,_valid,_lo,_hi", PRESET_FIELDS)
def test_float_entries_rejected(field, _valid, _lo, _hi):
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(**{field: "[1.5, 2.0, 3.0]"})
    assert "must all be integers" in str(exc.value)


@pytest.mark.parametrize("field,_valid,_lo,_hi", PRESET_FIELDS)
def test_string_entries_rejected(field, _valid, _lo, _hi):
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(**{field: '["120", "220", "260"]'})
    assert "must all be integers" in str(exc.value)


@pytest.mark.parametrize("field,_valid,lo,hi", PRESET_FIELDS)
def test_below_range_rejected(field, _valid, lo, hi):
    bad = f"[{lo - 1}, {lo}, {lo}]"
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(**{field: bad})
    assert f"[{lo}, {hi}]" in str(exc.value)


@pytest.mark.parametrize("field,_valid,lo,hi", PRESET_FIELDS)
def test_above_range_rejected(field, _valid, lo, hi):
    bad = f"[{hi}, {hi}, {hi + 1}]"
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(**{field: bad})
    assert f"[{lo}, {hi}]" in str(exc.value)


@pytest.mark.parametrize("field,_valid,lo,hi", PRESET_FIELDS)
def test_range_bounds_inclusive(field, _valid, lo, hi):
    """Both endpoints lo and hi must be accepted (inclusive bounds)."""
    update = AppSettingsUpdate(**{field: f"[{lo}, {hi}, {hi}]"})
    assert getattr(update, field) == f"[{lo}, {hi}, {hi}]"


def test_booleans_rejected_even_though_python_treats_them_as_ints():
    """`isinstance(True, int)` is True in Python — explicit guard required."""
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(nozzle_temp_presets="[true, 220, 260]")
    assert "must all be integers" in str(exc.value)

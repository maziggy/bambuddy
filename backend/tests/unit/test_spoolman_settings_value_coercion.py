"""PUT /settings/spoolman must not 500 on a JSON boolean.

The endpoint takes a free-form ``dict`` body, and settings are persisted in a
VARCHAR column that every reader compares as a string. Sending the natural JSON
form — ``{"spoolman_enabled": true}`` — used to fail twice over:

- ``bool.lower()`` raised AttributeError while deciding whether the mode had
  changed, surfacing as an opaque 500;
- the raw bool was handed to ``upsert_setting``, which SQLite silently coerces
  to 1/0 while asyncpg rejects it — so the stored representation depended on
  the deployment's database.

The shipped UI sends strings, so this was reachable only through the REST API
(scripts, Home Assistant ``rest_command``) — which is exactly where a JSON
boolean is the obvious thing to send.

These tests cover the normalisers directly. They are pure functions, so the
matrix stays readable and the endpoint keeps a single code path per field.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.api.routes.settings import (
    normalize_bool_setting,
    normalize_str_setting,
    setting_is_true,
)

# ---------------------------------------------------------------------------
# The reported crash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [(True, "true"), (False, "false")])
def test_json_booleans_are_accepted_and_canonicalised(value: bool, expected: str):
    """The exact input that used to 500."""
    assert normalize_bool_setting("spoolman_enabled", value) == expected


@pytest.mark.parametrize(("value", "expected"), [(1, "true"), (0, "false")])
def test_json_numbers_one_and_zero_are_accepted(value: int, expected: str):
    assert normalize_bool_setting("spoolman_enabled", value) == expected


# ---------------------------------------------------------------------------
# String spellings — generous on purpose, this is a documented REST surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "TRUE", "True", " true ", "1", "yes", "on", "ON"])
def test_truthy_spellings(value: str):
    assert normalize_bool_setting("auto_add_unknown_rfid", value) == "true"


@pytest.mark.parametrize("value", ["false", "FALSE", "False", " false ", "0", "no", "off"])
def test_falsy_spellings(value: str):
    assert normalize_bool_setting("auto_add_unknown_rfid", value) == "false"


def test_python_style_capitalised_true_is_normalised_lowercase():
    """The frontend compares with a case-sensitive ``=== 'true'``.

    A client sending "True" previously had it stored verbatim, so the UI
    rendered the setting as OFF while every backend reader (which all use
    ``.lower()``) treated it as ON.
    """
    assert normalize_bool_setting("spoolman_enabled", "True") == "true"


# ---------------------------------------------------------------------------
# Empty means "use the default" — deliberately NOT normalised to "false"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_is_preserved_not_turned_into_false(value: str):
    """get_spoolman_settings reads these with ``or "<default>"``.

    spoolman_report_partial_usage and auto_add_unknown_rfid default to ON, so
    coercing a blank submission to "false" would silently switch them off.
    Whitespace-only collapses to "" so it takes the same path rather than
    being stored as a truthy-but-meaningless "   ".
    """
    assert normalize_bool_setting("spoolman_report_partial_usage", value) == ""


# ---------------------------------------------------------------------------
# Values with no sensible reading get a 400 naming the field, not a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["banana", "maybe", "2", "-1", None, [], {}, 3.5, 7])
def test_uninterpretable_values_raise_400_naming_the_field(value: object):
    with pytest.raises(HTTPException) as exc:
        normalize_bool_setting("spoolman_enabled", value)

    assert exc.value.status_code == 400
    assert "spoolman_enabled" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# String settings
# ---------------------------------------------------------------------------


def test_str_setting_passes_strings_through_untouched():
    assert normalize_str_setting("spoolman_url", "http://192.168.1.5:7912/") == "http://192.168.1.5:7912/"


def test_str_setting_stringifies_numbers():
    """An unquoted host or port is a plausible client slip, not a hard error."""
    assert normalize_str_setting("spoolman_url", 7912) == "7912"


def test_str_setting_maps_null_to_empty():
    assert normalize_str_setting("spoolman_url", None) == ""


@pytest.mark.parametrize("value", [{"a": 1}, ["x"]])
def test_str_setting_refuses_containers_rather_than_storing_a_repr(value: object):
    with pytest.raises(HTTPException) as exc:
        normalize_str_setting("spoolman_url", value)

    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# setting_is_true — used for the mode-switch comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        (" true ", True),
        ("false", False),
        ("", False),
        ("banana", False),
        (None, False),  # setting absent from the table
        (True, True),  # legacy row: SQLite coerced a raw bool into the column
        (False, False),
    ],
)
def test_setting_is_true(stored: object, expected: bool):
    assert setting_is_true(stored) is expected


@pytest.mark.parametrize("stored", ["1", "on", "yes"])
def test_setting_is_true_stays_narrower_than_the_write_path(stored: str):
    """Reading must agree with the rest of the codebase, which only accepts "true".

    normalize_bool_setting is generous about what clients may *send*; every
    reader (spoolman_tracking, filament_deficit, inventory, spoolbuddy, labels,
    main) compares ``.lower() == "true"``. Accepting more here would make the
    mode-switch check disagree with them about a legacy row.
    """
    assert setting_is_true(stored) is False

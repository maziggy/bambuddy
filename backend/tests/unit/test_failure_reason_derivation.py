"""Regression tests for derive_failure_reason in backend.app.main.

Ensures user-cancelled prints don't get archived as "layerShift" — the bug
seen on H2D where the firmware's cancel-sequence module-0x0C HMS was being
matched by the old broad heuristic (`module == 0x0C → Layer shift`).
"""

from __future__ import annotations

import pytest

from backend.app.main import derive_failure_reason

# ---------------------------------------------------------------------------
# Status-based reasons (no HMS lookup needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["aborted", "cancelled"])
def test_user_cancel_status_yields_user_cancelled(status: str) -> None:
    assert derive_failure_reason(status, None) == "userCancelled"
    assert derive_failure_reason(status, []) == "userCancelled"


def test_completed_status_returns_none() -> None:
    assert derive_failure_reason("completed", None) is None


# ---------------------------------------------------------------------------
# H2D regression: cancel-sequence HMS must not be labelled "layerShift"
# ---------------------------------------------------------------------------


def test_h2d_cancel_module_0x0c_is_not_layer_shift() -> None:
    """0C00_001B is the H2D cancel-sequence echo, not a real layer-shift code.

    The old `module == 0x0C → Layer shift` heuristic mislabeled every user-cancel
    on H2D as a layer-shift failure. This pins that code to None.
    """
    h2d_cancel_hms = [
        {"code": "0x2001b", "attr": 0x0C000C00, "module": 0x0C, "severity": 1},
        {"code": "0x400c", "attr": 0x03002C0C, "module": 0x03, "severity": 3},
    ]
    assert derive_failure_reason("failed", h2d_cancel_hms) is None


def test_unknown_module_0x0c_code_returns_none() -> None:
    """Any module-0x0C code we don't have an explicit short-code mapping for must
    leave failure_reason=None — being honest beats guessing."""
    unknown_hms = [{"code": "0x4099", "attr": 0x0C00_0000, "module": 0x0C, "severity": 2}]
    assert derive_failure_reason("failed", unknown_hms) is None


# ---------------------------------------------------------------------------
# Genuine failure modes still classified correctly
# ---------------------------------------------------------------------------


def test_real_layer_shift_short_code_detected() -> None:
    """0300_4057 ("Z-axis step loss") is a real layer-shift code from the wiki."""
    hms = [{"code": "0x4057", "attr": 0x0300_0000, "module": 0x03, "severity": 1}]
    assert derive_failure_reason("failed", hms) == "layerShift"


def test_real_filament_runout_short_code_detected() -> None:
    """07FF_8011 = external filament runout."""
    hms = [{"code": "0x8011", "attr": 0x07FF_0000, "module": 0x07, "severity": 2}]
    assert derive_failure_reason("failed", hms) == "filamentRunout"


def test_real_clogged_nozzle_short_code_detected() -> None:
    """0300_4006 = "The nozzle is clogged"."""
    hms = [{"code": "0x4006", "attr": 0x0300_0000, "module": 0x03, "severity": 1}]
    assert derive_failure_reason("failed", hms) == "cloggedNozzle"


def test_first_matching_code_wins() -> None:
    """When multiple known codes are present, the first one in the list wins."""
    hms = [
        {"code": "0x4057", "attr": 0x0300_0000, "module": 0x03, "severity": 1},  # layer shift
        {"code": "0x8011", "attr": 0x07FF_0000, "module": 0x07, "severity": 2},  # filament runout
    ]
    assert derive_failure_reason("failed", hms) == "layerShift"


def test_failed_with_no_hms_returns_none() -> None:
    assert derive_failure_reason("failed", None) is None
    assert derive_failure_reason("failed", []) is None


# ---------------------------------------------------------------------------
# Code-format tolerance (MQTT may send int or hex string)
# ---------------------------------------------------------------------------


def test_int_code_field_accepted() -> None:
    """The MQTT parser sometimes leaves `code` as an int rather than a hex string."""
    hms = [{"code": 0x4057, "attr": 0x0300_0000, "module": 0x03, "severity": 1}]
    assert derive_failure_reason("failed", hms) == "layerShift"


# ---------------------------------------------------------------------------
# One vocabulary in storage (issue #2974)
# ---------------------------------------------------------------------------


def test_every_derived_reason_is_a_canonical_key() -> None:
    """The map may only hold values the rest of the stack agrees are reasons.

    Three writers used to put three spellings of one cause into
    ``failure_reason``. The whole point of #2974 is that there is now exactly
    one, so a display label sneaking back into the map -- which is what shipped
    for months -- has to fail here rather than in a user's Statistics panel.
    """
    from backend.app.api.routes.print_log import _FAILURE_REASON_KEYS
    from backend.app.main import _HMS_FAILURE_REASONS

    offenders = sorted(set(_HMS_FAILURE_REASONS.values()) - _FAILURE_REASON_KEYS)
    assert not offenders, f"not canonical failure-reason keys: {offenders}"


@pytest.mark.parametrize("status", ["aborted", "cancelled", "failed"])
def test_derived_reason_is_always_a_canonical_key(status: str) -> None:
    """Covers the status branch too, not just the HMS table."""
    from backend.app.api.routes.print_log import _FAILURE_REASON_KEYS
    from backend.app.main import _HMS_FAILURE_REASONS

    for code in _HMS_FAILURE_REASONS:
        attr = int(code.split("_")[0], 16) << 16
        reason = derive_failure_reason(status, [{"attr": attr, "code": int(code.split("_")[1], 16)}])
        assert reason is None or reason in _FAILURE_REASON_KEYS, reason


def test_the_stale_paths_write_a_key_the_editor_will_not_discard() -> None:
    """Both stale writers in main.py store ``noStatusUpdate``.

    Read from the source rather than by calling them: they sit deep inside the
    MQTT archive paths and need a printer, a session and a live status. What
    matters is the value, and that the archive editor recognises it -- an
    unrecognised value opens the dropdown empty and the next save clears the
    classification outright.
    """
    from pathlib import Path

    from backend.app.api.routes.print_log import _FAILURE_REASON_KEYS

    source = Path(__file__).resolve().parents[3] / "backend" / "app" / "main.py"
    text = source.read_text(encoding="utf-8")

    assert "noStatusUpdate" in _FAILURE_REASON_KEYS
    assert text.count('failure_reason = "noStatusUpdate"') == 2
    assert "Stale - print likely cancelled" not in text
    assert "Stale - reconciled after reconnect" not in text

"""Regression tests for derive_failure_reason in backend.app.main.

Ensures user-cancelled prints don't get archived as "Layer shift" — the bug
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
    assert derive_failure_reason(status, None) == "User cancelled"
    assert derive_failure_reason(status, []) == "User cancelled"


def test_completed_status_returns_none() -> None:
    assert derive_failure_reason("completed", None) is None


# ---------------------------------------------------------------------------
# H2D regression: cancel-sequence HMS must not be labelled "Layer shift"
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
    assert derive_failure_reason("failed", hms) == "Layer shift"


def test_real_filament_runout_short_code_detected() -> None:
    """07FF_8011 = external filament runout."""
    hms = [{"code": "0x8011", "attr": 0x07FF_0000, "module": 0x07, "severity": 2}]
    assert derive_failure_reason("failed", hms) == "Filament runout"


def test_real_clogged_nozzle_short_code_detected() -> None:
    """0300_4006 = "The nozzle is clogged"."""
    hms = [{"code": "0x4006", "attr": 0x0300_0000, "module": 0x03, "severity": 1}]
    assert derive_failure_reason("failed", hms) == "Clogged nozzle"


def test_first_matching_code_wins() -> None:
    """When multiple known codes are present, the first one in the list wins."""
    hms = [
        {"code": "0x4057", "attr": 0x0300_0000, "module": 0x03, "severity": 1},  # layer shift
        {"code": "0x8011", "attr": 0x07FF_0000, "module": 0x07, "severity": 2},  # filament runout
    ]
    assert derive_failure_reason("failed", hms) == "Layer shift"


def test_failed_with_no_hms_returns_none() -> None:
    assert derive_failure_reason("failed", None) is None
    assert derive_failure_reason("failed", []) is None


# ---------------------------------------------------------------------------
# Code-format tolerance (MQTT may send int or hex string)
# ---------------------------------------------------------------------------


def test_int_code_field_accepted() -> None:
    """The MQTT parser sometimes leaves `code` as an int rather than a hex string."""
    hms = [{"code": 0x4057, "attr": 0x0300_0000, "module": 0x03, "severity": 1}]
    assert derive_failure_reason("failed", hms) == "Layer shift"


# ---------------------------------------------------------------------------
# AI print monitoring (issue #2946)
# ---------------------------------------------------------------------------


def test_ai_spaghetti_detection_is_classified() -> None:
    """0300_8003 is what the onboard AI raises when it halts a print for spaghetti.

    Taken from the archive that reported this: the printer sent
    ``attr=50364419, code='0x8003'``, which is 0x0300_8003, and the archive was
    written with failure_reason=None because the map had no row for it. The text
    for the code was already in the tree twice — hms_errors.py and
    HMSErrorModal.tsx — so this was a missing key, not a missing meaning.
    """
    hms = [{"code": "0x8003", "attr": 50364419, "module": 0x03, "severity": 2}]
    assert derive_failure_reason("failed", hms) == "Spaghetti / Detached"


def test_ai_detection_label_is_the_editors_own_vocabulary() -> None:
    """The label has to be the archive editor's existing option, character for
    character.

    EditArchiveModal stores a camelCase key and reverse-looks-up any older
    translated label against the current locale to pre-select the dropdown. A
    fresh phrase here ("Spaghetti detected", say) matches no key, so the reason
    shows on the archive but the editor opens with nothing selected. This pins
    the string to `editArchive.failureReasons.spaghettiDetached` in en.ts.
    """
    hms = [{"code": "0x8003", "attr": 0x0300_0000, "module": 0x03, "severity": 2}]
    assert derive_failure_reason("failed", hms) == "Spaghetti / Detached"


def test_ai_detection_and_its_runout_neighbour_are_distinct() -> None:
    """0300_8003 and 0300_8004 are one hex digit apart and arrive by the same
    path. The runout side was already mapped; this keeps them from drifting into
    each other."""
    ai = [{"code": "0x8003", "attr": 0x0300_0000, "module": 0x03, "severity": 2}]
    runout = [{"code": "0x8004", "attr": 0x0300_0000, "module": 0x03, "severity": 2}]
    assert derive_failure_reason("failed", ai) == "Spaghetti / Detached"
    assert derive_failure_reason("failed", runout) == "Filament runout"

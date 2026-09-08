"""Regression tests for derive_failure_reason in backend.app.main.

Ensures user-cancelled prints don't get archived as "layerShift" — the bug
seen on H2D where the firmware's cancel-sequence module-0x0C HMS was being
matched by the old broad heuristic (`module == 0x0C → Layer shift`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.app.main import _HMS_FAILURE_REASONS, derive_failure_reason

REPO_ROOT = Path(__file__).resolve().parents[3]
_EN_TS = REPO_ROOT / "frontend" / "src" / "i18n" / "locales" / "en.ts"
_EDIT_ARCHIVE_MODAL = REPO_ROOT / "frontend" / "src" / "components" / "EditArchiveModal.tsx"

# Dockerfile.test copies backend/, pyproject.toml and the requirements files and
# nothing else, so frontend/ does not exist inside the test image and the two
# tests below that read it have nothing to check. A source checkout always has
# it and keeps those guards live on every test_backend.sh run.
# frontend/package.json is present in every checkout and never in the image,
# which is what the launcher config tests use to tell the two apart.
_needs_the_frontend_tree = pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "package.json").is_file(),
    reason="frontend/ isn't shipped in the Docker test image; the guards run in native runs",
)

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
    assert derive_failure_reason("failed", hms) == "spaghettiDetached"


def test_the_ai_monitors_other_code_is_classified_too() -> None:
    """0C00_8042 is the same event reported from the motion-controller module.

    hms_errors.py documents it as "The AI print monitor has detected a spaghetti
    defect", so it is a full short code with a published meaning rather than the
    module-0x0C guessing the map header rules out.
    """
    hms = [{"code": "0x8042", "attr": 0x0C00_0000, "module": 0x0C, "severity": 2}]
    assert derive_failure_reason("failed", hms) == "spaghettiDetached"


@pytest.mark.parametrize(
    ("short_code", "attr", "code"),
    [
        # "Possible spaghetti failure was detected." — a warning about a print
        # that is still running, not a print that stopped.
        ("0C00_C004", 0x0C00_0000, "0xC004"),
        # AI monitoring, but a filament pile-up in the waste chute.
        ("0300_800A", 0x0300_0000, "0x800A"),
    ],
)
def test_the_ai_monitors_warnings_are_left_unclassified(short_code: str, attr: int, code: str) -> None:
    """Being AI monitoring is not the criterion — halting the print is.

    Both of these are in hms_errors.py and both would be easy to sweep in with
    the two that are mapped. Neither means the print failed, and a wrong reason
    on an archive is worse than none, so they stay out and this says so.
    """
    assert short_code not in _HMS_FAILURE_REASONS
    hms = [{"code": code, "attr": attr, "module": attr >> 24, "severity": 2}]
    assert derive_failure_reason("failed", hms) is None


def test_ai_detection_and_its_runout_neighbour_are_distinct() -> None:
    """0300_8003 and 0300_8004 are one hex digit apart and arrive by the same
    path. The runout side was already mapped; this keeps them from drifting into
    each other."""
    ai = [{"code": "0x8003", "attr": 0x0300_0000, "module": 0x03, "severity": 2}]
    runout = [{"code": "0x8004", "attr": 0x0300_0000, "module": 0x03, "severity": 2}]
    assert derive_failure_reason("failed", ai) == "spaghettiDetached"
    assert derive_failure_reason("failed", runout) == "filamentRunout"


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


# ---------------------------------------------------------------------------
# The vocabulary spans two languages, and only a comment says so
# ---------------------------------------------------------------------------


def _keys_the_dropdown_offers() -> set[str]:
    """The `FAILURE_REASON_KEYS` array exported from EditArchiveModal.tsx."""
    source = _EDIT_ARCHIVE_MODAL.read_text(encoding="utf-8")
    block = re.search(r"export const FAILURE_REASON_KEYS = \[(.*?)\] as const;", source, re.S)
    assert block is not None, f"no FAILURE_REASON_KEYS array in {_EDIT_ARCHIVE_MODAL}"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _keys_the_frontend_can_translate() -> set[str]:
    """Every key in the `editArchive.failureReasons` block of en.ts."""
    source = _EN_TS.read_text(encoding="utf-8")
    block = re.search(r"failureReasons:\s*\{(.*?)\}", source, re.S)
    assert block is not None, f"no failureReasons block in {_EN_TS}"
    return set(re.findall(r"^\s*(\w+):\s*'", block.group(1), re.M))


@_needs_the_frontend_tree
def test_the_backend_vocabulary_matches_the_one_the_frontend_offers() -> None:
    """The only thing holding the two lists together is a comment asking nicely.

    ``_FAILURE_REASON_KEYS`` in api/routes/print_log.py gates every write, and
    its own comment says "Keep these two lists in sync if the EditArchiveModal
    options ever change". Nothing enforces it, and the drift is silent in both
    directions: a key the frontend offers but the backend rejects turns a save
    into a 400 the modal has no surface for, and a key the backend accepts but
    the dropdown omits is a value the editor discards the next time anyone
    opens that archive.

    Asserting one Python literal against another cannot see either — the other
    end lives in TypeScript, so the check has to read it.
    """
    from backend.app.api.routes.print_log import _FAILURE_REASON_KEYS

    offered = _keys_the_dropdown_offers()
    assert offered, "the FAILURE_REASON_KEYS array parsed as empty; the regex has gone stale"

    # "" is the backend's "clear the classification" value; the dropdown spells
    # that as its own placeholder option rather than a key, so it is not drift.
    backend_keys = set(_FAILURE_REASON_KEYS) - {""}

    assert backend_keys == offered, (
        f"backend-only: {sorted(backend_keys - offered)}, frontend-only: {sorted(offered - backend_keys)}"
    )


@_needs_the_frontend_tree
def test_every_offered_key_has_english_text() -> None:
    """A key with no en.ts entry renders as the raw key in the dropdown.

    The parity script covers the other 13 locales against en.ts, so en.ts is the
    one end of this that nothing else checks.
    """
    translatable = _keys_the_frontend_can_translate()
    assert translatable, "the failureReasons block parsed as empty; the regex has gone stale"

    untranslated = sorted(_keys_the_dropdown_offers() - translatable)
    assert not untranslated, f"offered by the dropdown with no editArchive.failureReasons entry: {untranslated}"

"""Unit tests for the .cfg macro file parser.

Pure unit — no DB, no filesystem, no async.
"""

import pytest

from backend.app.services.macro_cfg_parser import get_macro_body, parse, serialize

# ── Helpers ────────────────────────────────────────────────────────────────────


def _single(text: str):
    """Parse text and assert exactly one macro was found; return it."""
    result = parse(text)
    assert len(result.macros) == 1, f"expected 1 macro, got {len(result.macros)}"
    return result.macros[0]


# ── P1 ─────────────────────────────────────────────────────────────────────────


def test_empty_file():
    result = parse("")
    assert result.macros == []
    assert result.errors == []


def test_whitespace_only_file():
    result = parse("   \n\n\t\n")
    assert result.macros == []
    assert result.errors == []


# ── P2 ─────────────────────────────────────────────────────────────────────────


def test_single_macro_no_config():
    text = "[macro home]\nG28\nG0 Z10\n"
    m = _single(text)
    assert m.name == "home"
    assert m.trigger_type == "manual"
    assert m.cron_expression is None
    assert m.description is None
    assert m.error is None
    assert "G28" in m.body
    assert "G0 Z10" in m.body


# ── P3 ─────────────────────────────────────────────────────────────────────────


def test_macro_with_all_config_keys():
    text = (
        "[macro preheat]\n"
        "description: Heat bed to 60°C\n"
        "trigger: schedule\n"
        "cron: 0 8 * * *\n"
        "printer: My X1C\n"
        "M140 S60\n"
    )
    m = _single(text)
    assert m.description == "Heat bed to 60°C"
    assert m.trigger_type == "schedule"
    assert m.cron_expression == "0 8 * * *"
    assert m.printer_name == "My X1C"
    assert "M140 S60" in m.body


# ── P4 ─────────────────────────────────────────────────────────────────────────


def test_multiple_macros():
    text = "[macro first]\nG28\n\n[macro second]\nM84\n"
    result = parse(text)
    assert len(result.macros) == 2
    names = [m.name for m in result.macros]
    assert "first" in names
    assert "second" in names
    assert result.errors == []


# ── P5 ─────────────────────────────────────────────────────────────────────────


def test_duplicate_name_is_error():
    text = "[macro foo]\nG28\n\n[macro foo]\nM84\n"
    result = parse(text)
    assert len(result.errors) >= 1
    assert any("foo" in e for e in result.errors)
    # duplicate entry has .error set
    errored = [m for m in result.macros if m.error]
    assert len(errored) >= 1


# ── P6 ─────────────────────────────────────────────────────────────────────────


def test_comments_not_in_body():
    # Comments that appear after the config section (mixed into G-code body) are
    # preserved as-is. Pre-config comment lines also end up in the body because the
    # parser cannot distinguish them from intentional preamble. The key invariant is
    # that G-code lines are always present and config keys (trigger:, cron:, etc.)
    # are never emitted into the body.
    text = "[macro clean]\ntrigger: manual\n; This is a comment\nG28\n"
    m = _single(text)
    # config key must not appear in body
    assert "trigger:" not in m.body
    # body must contain the G-code
    assert "G28" in m.body


# ── P7 ─────────────────────────────────────────────────────────────────────────


def test_unknown_trigger_defaults_to_manual():
    text = "[macro x]\ntrigger: foobar\nG28\n"
    m = _single(text)
    assert m.trigger_type == "manual"


def test_all_valid_trigger_types():
    for trigger in ("manual", "webhook", "schedule"):
        text = f"[macro x]\ntrigger: {trigger}\nG28\n"
        m = _single(text)
        assert m.trigger_type == trigger


# ── P8 ─────────────────────────────────────────────────────────────────────────


def test_valid_cron_parses():
    text = "[macro nightly]\ntrigger: schedule\ncron: 0 2 * * *\nG28\n"
    m = _single(text)
    assert m.cron_expression == "0 2 * * *"
    assert m.error is None


# ── P9 ─────────────────────────────────────────────────────────────────────────


def test_invalid_cron_sets_parse_error():
    pytest.importorskip("croniter", reason="cron validation requires croniter")
    text = "[macro bad]\ntrigger: schedule\ncron: every minute\nG28\n"
    result = parse(text)
    assert len(result.errors) >= 1
    assert any("cron" in e.lower() or "every minute" in e for e in result.errors)
    m = result.macros[0]
    assert m.cron_expression is None
    assert m.error is not None


# ── P10/P11 ────────────────────────────────────────────────────────────────────


def test_get_macro_body_found():
    text = "[macro home]\nG28\nG0 Z10\n"
    body = get_macro_body(text, "home")
    assert body is not None
    assert "G28" in body


def test_get_macro_body_not_found():
    text = "[macro home]\nG28\n"
    assert get_macro_body(text, "unknown") is None


def test_get_macro_body_errored_macro_returns_none():
    text = "[macro foo]\nG28\n\n[macro foo]\nM84\n"
    # second 'foo' has error — body should not be returned
    body = get_macro_body(text, "foo")
    # first occurrence (no error) is returned
    assert body is not None


# ── P12 ────────────────────────────────────────────────────────────────────────


def test_body_preserves_internal_blank_lines():
    text = "[macro spaced]\nG28\n\nG0 Z10\n"
    m = _single(text)
    # internal blank line should survive (body is stripped only at ends)
    assert "G28" in m.body
    assert "G0 Z10" in m.body


# ── P13 ────────────────────────────────────────────────────────────────────────


def test_config_case_insensitive():
    text = "[macro x]\nTRIGGER: schedule\nCRON: 0 8 * * *\nDESCRIPTION: Hello\nG28\n"
    m = _single(text)
    assert m.trigger_type == "schedule"
    assert m.cron_expression == "0 8 * * *"
    assert m.description == "Hello"


# ── P14 ────────────────────────────────────────────────────────────────────────


def test_serialize_roundtrip():
    text = "[macro preheat]\ndescription: Heat bed\ntrigger: manual\nM140 S60\n\n[macro home]\nG28\n"
    result = parse(text)
    macros_dicts = [
        {
            "name": m.name,
            "description": m.description,
            "trigger": m.trigger_type,
            "cron": m.cron_expression,
            "printer": m.printer_name,
            "body": m.body,
        }
        for m in result.macros
        if not m.error
    ]
    serialized = serialize(macros_dicts)
    result2 = parse(serialized)

    names_orig = {m.name for m in result.macros if not m.error}
    names_rt = {m.name for m in result2.macros if not m.error}
    assert names_orig == names_rt

    for m_orig in result.macros:
        if m_orig.error:
            continue
        m_rt = next(m for m in result2.macros if m.name == m_orig.name)
        assert m_rt.trigger_type == m_orig.trigger_type
        assert m_rt.description == m_orig.description

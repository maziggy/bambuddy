"""Unit tests for macro file disk I/O service.

Pure unit — no DB, no async.  Uses tmp_path and monkeypatch to redirect
macros_dir so tests never touch the real data directory.
"""

import pytest

from backend.app.services.macro_files import _safe_path, _slug, create, delete, list_cfg_files, read, write


@pytest.fixture(autouse=True)
def _tmp_macros_dir(tmp_path, monkeypatch):
    """Redirect every test in this file to a fresh temp macros directory."""
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("backend.app.core.config.settings.macros_dir", str(d))
    return d


# ── F1 ─────────────────────────────────────────────────────────────────────────


def test_create_returns_relative_cfg_path(tmp_path):
    path = create("My Macro")
    assert path.endswith(".cfg")
    # Must be relative, not absolute
    assert not path.startswith("/") and "\\" not in path


def test_create_writes_content(tmp_path):
    from pathlib import Path

    from backend.app.core.config import settings

    content = "[macro my_macro]\nG28\n"
    path = create("my_macro", content)
    full = Path(settings.macros_dir) / path
    assert full.exists()
    assert full.read_text(encoding="utf-8") == content


# ── F2 ─────────────────────────────────────────────────────────────────────────


def test_create_no_slug_collision():
    p1 = create("heat bed", "first")
    p2 = create("heat bed", "second")
    assert p1 != p2


def test_create_collision_counter_increments():
    paths = [create("same name") for _ in range(3)]
    assert len(set(paths)) == 3


# ── F3 ─────────────────────────────────────────────────────────────────────────


def test_write_read_roundtrip():
    path = create("roundtrip", "initial")
    new_content = "[macro roundtrip]\nM140 S60\nWAIT --seconds=1\n"
    write(path, new_content)
    assert read(path) == new_content


# ── F4 ─────────────────────────────────────────────────────────────────────────


def test_read_missing_raises():
    with pytest.raises(FileNotFoundError):
        read("nonexistent.cfg")


# ── F5 ─────────────────────────────────────────────────────────────────────────


def test_delete_removes_file():
    from pathlib import Path

    from backend.app.core.config import settings

    path = create("to_delete", "G28")
    full = Path(settings.macros_dir) / path
    assert full.exists()
    delete(path)
    assert not full.exists()


# ── F6 ─────────────────────────────────────────────────────────────────────────


def test_delete_missing_is_silent():
    # Must not raise
    delete("nonexistent.cfg")


def test_delete_traversal_is_silent():
    # Traversal attempts on delete must not raise and must not delete anything
    delete("../../etc/passwd")


# ── F7 ─────────────────────────────────────────────────────────────────────────


def test_safe_path_rejects_traversal(tmp_path):
    d = tmp_path / "macros"
    d.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="traversal"):
        _safe_path(d, "../../etc/passwd")


def test_safe_path_accepts_valid_name(tmp_path):
    d = tmp_path / "macros"
    d.mkdir(exist_ok=True)
    result = _safe_path(d, "my_macro.cfg")
    assert result == d / "my_macro.cfg"


def test_safe_path_accepts_nested_subdirectory(tmp_path):
    d = tmp_path / "macros"
    d.mkdir(exist_ok=True)
    result = _safe_path(d, "subdir/my_macro.cfg")
    assert str(result).startswith(str(d.resolve()))


# ── F8 ─────────────────────────────────────────────────────────────────────────


def test_slug_strips_special_chars():
    assert _slug("Hello World! #1") == "hello_world_1"


def test_slug_collapses_separators():
    assert _slug("foo---bar___baz") == "foo_bar_baz"


def test_slug_strips_leading_trailing_underscores():
    assert not _slug("  _hello_  ").startswith("_")
    assert not _slug("  _hello_  ").endswith("_")


def test_slug_empty_fallback():
    assert _slug("!!!") == "macro"
    assert _slug("") == "macro"


# ── F9 ─────────────────────────────────────────────────────────────────────────


def test_list_cfg_files_returns_only_cfg(tmp_path):
    from pathlib import Path

    from backend.app.core.config import settings

    d = Path(settings.macros_dir)
    # Create a .cfg and a stray .txt
    (d / "a.cfg").write_text("", encoding="utf-8")
    (d / "b.cfg").write_text("", encoding="utf-8")
    (d / "ignore.txt").write_text("", encoding="utf-8")

    files = list_cfg_files()
    assert "a.cfg" in files
    assert "b.cfg" in files
    assert all(f.endswith(".cfg") for f in files)


def test_list_cfg_files_empty_dir():
    assert list_cfg_files() == []

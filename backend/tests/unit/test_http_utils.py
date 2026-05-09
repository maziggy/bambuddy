"""Unit tests for backend.app.utils.http."""

from urllib.parse import unquote

import pytest

from backend.app.utils.http import build_content_disposition


@pytest.mark.parametrize(
    ("filename", "expected_ascii_fallback"),
    [
        ("hello.gcode.3mf", "hello.gcode.3mf"),
        ("龙泡泡石墩子_p2s_ok.gcode.3mf", "p2s_ok.gcode.3mf"),
        ("こんにちは.gcode.3mf", "gcode.3mf"),
        ("résumé.gcode.3mf", "rsum.gcode.3mf"),
        ("مرحبا.gcode.3mf", "gcode.3mf"),
        ("文件.3mf", "3mf"),
        ("模型.gcode.3mf", "gcode.3mf"),
        ("project_2026-05-08.zip", "project_2026-05-08.zip"),
        ("___.zip", "zip"),
        ("", "download"),
    ],
)
def test_ascii_fallback_strips_non_ascii(filename: str, expected_ascii_fallback: str) -> None:
    header = build_content_disposition(filename)
    assert f'filename="{expected_ascii_fallback}"' in header


@pytest.mark.parametrize(
    "filename",
    [
        "龙泡泡石墩子_p2s_ok.gcode.3mf",
        "こんにちは.gcode.3mf",
        "résumé.gcode.3mf",
        "مرحبا.gcode.3mf",
        "文件.3mf",
        "hello world (final).pdf",
        "你好/世界.pdf",
    ],
)
def test_filename_star_round_trips_to_original(filename: str) -> None:
    header = build_content_disposition(filename)
    assert "filename*=UTF-8''" in header
    encoded = header.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded) == filename


def test_header_is_latin1_encodable() -> None:
    """Starlette/uvicorn encodes response headers as latin-1 — the helper's
    output MUST round-trip through latin-1 without raising."""
    for filename in [
        "龙泡泡石墩子_p2s_ok.gcode.3mf",
        "こんにちは.gcode.3mf",
        "résumé.gcode.3mf",
        "مرحبا.gcode.3mf",
        '"quoted"name.zip',
        "back\\slash.zip",
    ]:
        header = build_content_disposition(filename)
        header.encode("latin-1")


def test_disposition_param_is_respected() -> None:
    assert build_content_disposition("foo.pdf", disposition="inline").startswith("inline; ")
    assert build_content_disposition("foo.pdf").startswith("attachment; ")


def test_quotes_and_backslashes_stripped_from_ascii_fallback() -> None:
    header = build_content_disposition('a"b\\c.pdf')
    assert 'filename="abc.pdf"' in header

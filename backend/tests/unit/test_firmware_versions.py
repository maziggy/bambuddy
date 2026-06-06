"""
Unit tests for firmware version listing.

Covers:
- Wiki-page version extraction is restricted to section-heading anchors
  (incidental version-like strings in release-note prose must be ignored).
- Merging wiki + download-page versions produces a single list where
  wiki-only versions are flagged as unavailable (no download URL).
- buildId disk-persistence + 403 fallback for #1350.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.firmware_check import FirmwareCheckService, FirmwareVersion

WIKI_SAMPLE = """
<h2 id="h-01030000-20260303" class="toc-header">01.03.00.00 (20260303)</h2>
<p>Released 20260303</p>
<ul><li>Optimized AMS 2 Pro (requires AMS firmware OTA v02.00.19.47 or newer).</li></ul>
<h2 id="h-01021000-20260209" class="toc-header">01.02.10.00 (20260209)</h2>
<p>Bug fixes.</p>
<h2 id="h-01020200-20251105" class="toc-header">01.02.02.00 (20251105)</h2>
<p>Some more text referencing 00.00.00.00 incidentally.</p>
"""


@pytest.mark.asyncio
async def test_wiki_extraction_ignores_prose_version_mentions():
    """02.00.19.47 appears only in release notes prose — it must not be listed."""
    svc = FirmwareCheckService()
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = WIKI_SAMPLE
    with patch.object(svc._client, "get", AsyncMock(return_value=mock_resp)):
        versions = await svc._fetch_all_versions_from_wiki("h2d")

    version_strs = [v for v, _ in versions]
    assert version_strs == ["01.03.00.00", "01.02.10.00", "01.02.02.00"]
    # The AMS firmware mentioned in prose must not leak in:
    assert "02.00.19.47" not in version_strs
    assert "00.00.00.00" not in version_strs
    # Release dates are captured from the anchor id:
    assert versions[0][1] == "20260303"


@pytest.mark.asyncio
async def test_wiki_extraction_returns_empty_for_unknown_api_key():
    svc = FirmwareCheckService()
    assert await svc._fetch_all_versions_from_wiki("no-such-key") == []


# P2S and X2D wiki pages publish anchor ids without a dash between the
# version bytes and the date (e.g. h-0102000020260409). Regression for #1030
# where the anchor regex required a dash and silently returned no versions,
# causing the UI to fall back to the stale download-page "Latest" value.
P2S_NODASH_ANCHOR_SAMPLE = """
<h2 id="h-0102000020260409" class="toc-header">01.02.00.00（20260409）</h2>
<h2 id="h-0101030020260209" class="toc-header">01.01.03.00（20260209）</h2>
<h2 id="h-0101010020251208" class="toc-header">01.01.01.00（20251208）</h2>
"""


@pytest.mark.asyncio
async def test_wiki_extraction_accepts_nodash_anchors():
    """P2S/X2D anchors concatenate version+date with no dash — must still parse."""
    svc = FirmwareCheckService()
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = P2S_NODASH_ANCHOR_SAMPLE
    with patch.object(svc._client, "get", AsyncMock(return_value=mock_resp)):
        versions = await svc._fetch_all_versions_from_wiki("p2s")

    assert [v for v, _ in versions] == ["01.02.00.00", "01.01.03.00", "01.01.01.00"]
    assert versions[0][1] == "20260409"


# A1, A1-mini and P2S pages render dates in full-width parens （YYYYMMDD）
# rather than ASCII parens (YYYYMMDD). Pages without version-anchors fall
# through to the text-based regex, so it must accept both paren styles.
FULLWIDTH_PAREN_FALLBACK_SAMPLE = """
<h2>01.04.00.01 （20260401）</h2>
<h2>01.03.00.00 （20260101）</h2>
"""


@pytest.mark.asyncio
async def test_wiki_extraction_fallback_accepts_fullwidth_parens():
    svc = FirmwareCheckService()
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = FULLWIDTH_PAREN_FALLBACK_SAMPLE
    with patch.object(svc._client, "get", AsyncMock(return_value=mock_resp)):
        versions = await svc._fetch_all_versions_from_wiki("a1")

    assert [v for v, _ in versions] == ["01.04.00.01", "01.03.00.00"]
    assert versions[0][1] == "20260401"


@pytest.mark.asyncio
async def test_get_available_versions_merges_sources():
    """
    Merged list must include all wiki versions (newest first), populating
    download URL + notes from the download-page JSON when present, and
    leaving download_url empty when the file is not published.
    """
    svc = FirmwareCheckService()

    wiki = [
        ("01.03.00.00", "20260303"),
        ("01.02.10.00", "20260209"),  # wiki-only — should be "unavailable"
        ("01.02.02.00", "20251105"),
    ]
    download = [
        FirmwareVersion(
            version="01.03.00.00",
            download_url="https://cdn.example/1.bin",
            release_notes="notes 1.3",
            release_time="2026-03-03",
        ),
        FirmwareVersion(
            version="01.02.02.00",
            download_url="https://cdn.example/2.bin",
            release_notes="notes 1.2.2",
            release_time="2025-11-05",
        ),
    ]

    with (
        patch.object(svc, "_fetch_all_versions_from_wiki", AsyncMock(return_value=wiki)),
        patch.object(svc, "_fetch_all_versions_from_download_page", AsyncMock(return_value=download)),
    ):
        result = await svc.get_available_versions("H2D")

    assert [v.version for v in result] == ["01.03.00.00", "01.02.10.00", "01.02.02.00"]
    assert result[0].download_url == "https://cdn.example/1.bin"
    assert result[0].release_notes == "notes 1.3"
    # Wiki-only version has no download URL → treated as unavailable by callers.
    assert result[1].download_url == ""
    assert result[1].release_notes is None
    assert result[1].release_time == "20260209"
    assert result[2].download_url == "https://cdn.example/2.bin"


@pytest.mark.asyncio
async def test_get_available_versions_sorts_newest_first():
    """Merged list must be sorted descending by version tuple regardless of input order."""
    svc = FirmwareCheckService()
    wiki = [("01.02.02.00", None)]
    download = [
        FirmwareVersion(version="01.03.00.00", download_url="a"),
        FirmwareVersion(version="01.02.10.00", download_url="b"),
    ]
    with (
        patch.object(svc, "_fetch_all_versions_from_wiki", AsyncMock(return_value=wiki)),
        patch.object(svc, "_fetch_all_versions_from_download_page", AsyncMock(return_value=download)),
    ):
        result = await svc.get_available_versions("H2D")
    assert [v.version for v in result] == ["01.03.00.00", "01.02.10.00", "01.02.02.00"]


@pytest.mark.asyncio
async def test_client_headers_identify_honestly_and_send_browser_accept():
    """
    The httpx client (used for the Bambu wiki and other non-CF-gated paths)
    must identify as Bambuddy at the HTTP layer and must send Accept +
    Accept-Language so Cloudflare on bambulab.com doesn't 403 us for
    looking like a bare scraper (#1350).
    """
    svc = FirmwareCheckService()
    headers = svc._client.headers
    assert headers["User-Agent"].startswith("Bambuddy/")
    assert "Chrome" not in headers["User-Agent"]
    assert "Accept" in headers
    assert "Accept-Language" in headers


@pytest.mark.asyncio
async def test_bambulab_curl_cffi_session_keeps_honest_user_agent():
    """
    The curl_cffi session impersonates Chrome at the TLS layer (required to
    pass Cloudflare's JA3 challenge on bambulab.com per #1666), but the
    HTTP-layer User-Agent MUST stay 'Bambuddy/...'. A future refactor that
    drops the headers= override would silently revert to curl_cffi's
    Chrome-default UA and break our compliance commitment to identify
    honestly at the application layer.

    Skipped when curl_cffi is not installed (constrained-platform fallback
    path — covered by the import-guard test below).
    """
    from backend.app.services import firmware_check as fc_module

    if not fc_module._CURL_CFFI_AVAILABLE:
        pytest.skip("curl_cffi not installed in this environment")

    svc = FirmwareCheckService()
    client = svc._get_bambulab_client()
    assert client is not None

    # curl_cffi's AsyncSession exposes the configured default headers on
    # `.headers`. The exact attribute is part of curl_cffi's public API
    # since 0.7.x — if a future upgrade renames it, this test will surface
    # the rename rather than silently passing.
    session_headers = client.headers  # type: ignore[attr-defined]
    ua = session_headers.get("User-Agent", "")
    assert ua.startswith("Bambuddy/"), f"curl_cffi session UA leaked Chrome default: {ua!r}"
    assert "Chrome" not in ua
    assert "Mozilla" not in ua


@pytest.mark.asyncio
async def test_bambulab_get_falls_back_to_httpx_when_curl_cffi_missing(monkeypatch):
    """
    When curl_cffi can't be imported (constrained platforms, alpine without
    wheels, etc.), the service must still attempt the fetch via httpx so
    wiki-based version detection isn't accidentally taken down with the
    download-URL path. The httpx attempt will likely 403 against CF — that's
    OK; the user still gets the version badge and a clear error in the UI.
    """
    from backend.app.services import firmware_check as fc_module

    monkeypatch.setattr(fc_module, "_CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(fc_module, "_CurlCffiAsyncSession", None)

    svc = FirmwareCheckService()
    mock_resp = MagicMock(status_code=403, text="<html>Forbidden</html>")

    with patch.object(svc._client, "get", AsyncMock(return_value=mock_resp)):
        status, body = await svc._bambulab_get("https://bambulab.com/whatever")

    assert status == 403
    assert "Forbidden" in body


@pytest.mark.asyncio
async def test_build_id_is_persisted_to_disk(tmp_path, monkeypatch):
    """Successful buildId fetch writes to disk so it survives restart (#1350)."""
    monkeypatch.setattr("backend.app.services.firmware_check._data_dir", tmp_path)

    svc = FirmwareCheckService()
    body = 'window.__data = {"buildId":"abc123xyz","other":"stuff"}'

    with patch.object(svc, "_bambulab_get", AsyncMock(return_value=(200, body))):
        build_id = await svc._get_build_id()

    assert build_id == "abc123xyz"
    cache_file = tmp_path / "firmware" / "build_id.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert data["build_id"] == "abc123xyz"
    assert data["fetched_at"] > 0


@pytest.mark.asyncio
async def test_build_id_falls_back_to_disk_on_403(tmp_path, monkeypatch):
    """
    When bambulab.com 403s (Cloudflare block reported in #1350, #1666) we must
    fall back to the disk-cached buildId from the previous successful fetch.
    Without this the user's screenshots happen: wiki version is detected
    but the download URL stays empty forever.
    """
    monkeypatch.setattr("backend.app.services.firmware_check._data_dir", tmp_path)

    # Pre-seed a previously-saved buildId
    cache_dir = tmp_path / "firmware"
    cache_dir.mkdir(parents=True)
    (cache_dir / "build_id.json").write_text(json.dumps({"build_id": "cached_id_42", "fetched_at": 1000.0}))

    svc = FirmwareCheckService()

    with patch.object(svc, "_bambulab_get", AsyncMock(return_value=(403, "<html>Access denied</html>"))):
        build_id = await svc._get_build_id()

    assert build_id == "cached_id_42"
    assert svc.download_page_unreachable is True


@pytest.mark.asyncio
async def test_download_page_unreachable_flag_set_on_403_json(tmp_path, monkeypatch):
    """A 403 on the per-model JSON endpoint also marks the page unreachable."""
    monkeypatch.setattr("backend.app.services.firmware_check._data_dir", tmp_path)

    svc = FirmwareCheckService()
    svc._build_id = "stale_id"
    svc._build_id_time = 9999999999.0  # never expires for this test

    with patch.object(svc, "_bambulab_get", AsyncMock(return_value=(403, "Forbidden"))):
        result = await svc._fetch_all_versions_from_download_page("x1")

    assert result == []
    assert svc.download_page_unreachable is True


@pytest.mark.asyncio
async def test_download_page_retries_once_when_buildid_stale(tmp_path, monkeypatch):
    """
    If the cached buildId returns 404 (Bambu rebuilt the page), refresh the
    buildId once and retry — but don't churn on repeated failures.
    """
    monkeypatch.setattr("backend.app.services.firmware_check._data_dir", tmp_path)

    svc = FirmwareCheckService()
    svc._build_id = "stale_id"
    svc._build_id_time = 9999999999.0

    fresh_json_body = json.dumps(
        {
            "pageProps": {
                "printerMap": {
                    "x1": {
                        "versions": [
                            {
                                "version": "01.11.02.00",
                                "url": "https://cdn/fw.bin",
                                "release_notes_en": "n",
                                "release_time": "2025-12-10",
                            }
                        ]
                    }
                }
            }
        }
    )

    # Sequence: stale JSON 404 → page refresh 200 (carries fresh buildId) → fresh JSON 200
    get = AsyncMock(
        side_effect=[
            (404, "not found"),
            (200, 'foo "buildId":"fresh_id" bar'),
            (200, fresh_json_body),
        ]
    )
    with patch.object(svc, "_bambulab_get", get):
        result = await svc._fetch_all_versions_from_download_page("x1")

    assert len(result) == 1
    assert result[0].version == "01.11.02.00"
    assert svc._build_id == "fresh_id"


@pytest.mark.asyncio
async def test_check_for_update_includes_available_versions():
    svc = FirmwareCheckService()
    available = [
        FirmwareVersion(version="01.03.00.00", download_url="https://cdn/1.bin", release_notes="x"),
        FirmwareVersion(version="01.02.10.00", download_url=""),  # unavailable
    ]
    with patch.object(svc, "get_available_versions", AsyncMock(return_value=available)):
        result = await svc.check_for_update("H2D", "01.02.02.00")

    assert result["update_available"] is True
    assert result["latest_version"] == "01.03.00.00"
    assert len(result["available_versions"]) == 2
    assert result["available_versions"][0]["file_available"] is True
    assert result["available_versions"][1]["file_available"] is False
    assert result["available_versions"][1]["download_url"] is None

"""
Unit tests for firmware version listing.

Covers:
- Wiki-page version extraction is restricted to section-heading anchors
  (incidental version-like strings in release-note prose must be ignored).
- Merging wiki + download-page versions produces a single list where
  wiki-only versions are flagged as unavailable (no download URL).
"""

from unittest.mock import AsyncMock, patch

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

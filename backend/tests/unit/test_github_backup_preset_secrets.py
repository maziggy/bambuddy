"""A printer preset's connection secrets never leave in a Git backup.

A printer connected in the slicer keeps its host's key (for a LAN printer, its
access code) in the preset, and cloud sync carries it. The backup writes those
as the placeholder; cloud profiles are never restored, so nothing reads them
back.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.github_backup import GitHubBackupService
from backend.app.services.secret_fields import SECRET_PLACEHOLDER

CONNECTION = {
    "print_host": "192.168.1.50",
    "printhost_apikey": "value-one",
    "printhost_password": "value-two",
    "printhost_user": "value-three",
    "printer_model": "X1C",
}
MASKED = {
    "print_host": "192.168.1.50",
    "printhost_apikey": SECRET_PLACEHOLDER,
    "printhost_password": SECRET_PLACEHOLDER,
    "printhost_user": SECRET_PLACEHOLDER,
    "printer_model": "X1C",
}


@pytest.mark.asyncio
async def test_an_orca_printer_preset_keeps_its_connection_secrets_out(db_session):
    svc = MagicMock()
    svc.list_profiles = AsyncMock(return_value=[{"id": 1, "name": "p", "content": {"type": "machine", **CONNECTION}}])
    svc.close = AsyncMock()
    files: dict = {}
    with patch(
        "backend.app.api.routes.orca_cloud._build_authenticated_service", new_callable=AsyncMock, return_value=svc
    ):
        await GitHubBackupService()._collect_orca_profiles(db_session, files, "global", None)

    (path,) = files
    assert files[path]["profiles"][0]["content"] == {"type": "machine", **MASKED}
    assert "value-" not in json.dumps(files)


@pytest.mark.asyncio
async def test_a_bambu_printer_preset_keeps_its_connection_secrets_out(db_session):
    cloud = MagicMock()
    cloud.is_authenticated = True
    cloud.get_slicer_settings = AsyncMock(
        return_value={"printer": {"private": [{"setting_id": "PMUS1", "name": "p", "version": "1"}], "public": []}}
    )
    cloud.get_setting_detail = AsyncMock(
        return_value={"setting_id": "PMUS1", "name": "p", "base_id": "GM001", "setting": dict(CONNECTION)}
    )
    cloud.close = AsyncMock()
    files: dict = {}
    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new_callable=AsyncMock, return_value=cloud):
        await GitHubBackupService()._collect_bambu_profiles(db_session, files, "global", None)

    assert files["cloud_profiles/bambu/global/printer.json"]["profiles"][0]["setting"] == MASKED
    assert "value-" not in json.dumps(files)


@pytest.mark.asyncio
async def test_an_empty_connection_secret_stays_empty(db_session):
    svc = MagicMock()
    svc.list_profiles = AsyncMock(return_value=[{"id": 1, "name": "p", "content": {"printhost_apikey": ""}}])
    svc.close = AsyncMock()
    files: dict = {}
    with patch(
        "backend.app.api.routes.orca_cloud._build_authenticated_service", new_callable=AsyncMock, return_value=svc
    ):
        await GitHubBackupService()._collect_orca_profiles(db_session, files, "global", None)

    (path,) = files
    assert files[path]["profiles"][0]["content"] == {"printhost_apikey": ""}


@pytest.mark.asyncio
async def test_a_login_in_a_preset_url_is_masked(db_session):
    content = {
        "print_host": "http://pi:value-four@octopi.local",
        "print_host_webui": "https://value-five@octopi.local/ui",
        "printer_settings_id": "http://octopi.local",
        "post_process": ["http://u:value-six@octopi.local/hook", "plain"],
    }
    cloud = MagicMock()
    cloud.is_authenticated = True
    cloud.get_slicer_settings = AsyncMock(
        return_value={"printer": {"private": [{"setting_id": "PMUS1", "name": "p", "version": "1"}], "public": []}}
    )
    cloud.get_setting_detail = AsyncMock(
        return_value={"setting_id": "PMUS1", "name": "p", "base_id": "GM001", "setting": dict(content)}
    )
    cloud.close = AsyncMock()
    svc = MagicMock()
    svc.list_profiles = AsyncMock(return_value=[{"id": 1, "name": "p", "content": dict(content)}])
    svc.close = AsyncMock()
    files: dict = {}
    with (
        patch("backend.app.api.routes.cloud.build_authenticated_cloud", new_callable=AsyncMock, return_value=cloud),
        patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service", new_callable=AsyncMock, return_value=svc
        ),
    ):
        await GitHubBackupService()._collect_bambu_profiles(db_session, files, "global", None)
        await GitHubBackupService()._collect_orca_profiles(db_session, files, "global", None)

    masked = {
        "print_host": "http://pi:[REDACTED]@octopi.local",
        "print_host_webui": "https://[REDACTED]@octopi.local/ui",
        "printer_settings_id": "http://octopi.local",
        "post_process": ["http://u:[REDACTED]@octopi.local/hook", "plain"],
    }
    bambu = files["cloud_profiles/bambu/global/printer.json"]["profiles"][0]["setting"]
    (orca_path,) = [path for path in files if path.startswith("cloud_profiles/orca/")]
    assert bambu == masked
    assert files[orca_path]["profiles"][0]["content"] == masked

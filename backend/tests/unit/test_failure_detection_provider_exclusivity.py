"""Provider selection stays exclusive even when a backup bypasses the settings route."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.settings import Settings
from backend.app.services.github_restore import GitHubRestoreService, _CategoryTally
from backend.app.services.obico_detection import ObicoDetectionService
from backend.app.services.octoeverywhere_detection import OctoEverywhereDetectionService


@pytest.fixture
def detectors(db_session):
    @asynccontextmanager
    async def session():
        yield db_session

    obico = ObicoDetectionService()
    octoeverywhere = OctoEverywhereDetectionService()
    with (
        patch("backend.app.services.obico_detection.async_session", session),
        patch("backend.app.services.octoeverywhere_detection.async_session", session),
    ):
        yield obico, octoeverywhere
    obico.stop()
    octoeverywhere.stop()


@pytest.mark.parametrize(
    ("obico_flag", "octoeverywhere_flag", "expected"),
    [
        ("false", "false", (False, False)),
        ("true", "false", (True, False)),
        ("false", "true", (False, True)),
        ("true", "true", (False, False)),
        ("true", None, (True, False)),
        (None, "true", (False, True)),
        (None, None, (False, False)),
    ],
)
async def test_provider_selection_from_saved_flags(db_session, detectors, obico_flag, octoeverywhere_flag, expected):
    for key, value in (("obico_enabled", obico_flag), ("octoeverywhere_enabled", octoeverywhere_flag)):
        if value is not None:
            db_session.add(Settings(key=key, value=value))
    await db_session.commit()

    loaded = [await detector._load_settings() for detector in detectors]
    assert tuple(settings["enabled"] for settings in loaded) == expected


async def test_legacy_restore_never_starts_both_providers_even_with_cached_settings(db_session, detectors):
    obico, octoeverywhere = detectors
    db_session.add_all(
        Settings(key=key, value=value)
        for key, value in {
            "obico_enabled": "false",
            "obico_ml_url": "http://obico.local:3333",
            "octoeverywhere_enabled": "true",
            "octoeverywhere_api_key": "test-key",
        }.items()
    )
    await db_session.commit()
    octoeverywhere._cache_settings(await octoeverywhere._load_settings())
    assert octoeverywhere._settings["enabled"] is True

    tally, written = _CategoryTally(), set()
    await GitHubRestoreService()._restore_settings(
        db_session, {"settings": {"obico_enabled": "true"}}, overwrite=True, tally=tally, keys_written=written
    )
    await db_session.commit()
    assert written == {"obico_enabled"}

    # Stop after one scheduler tick, retaining normal event-loop yields used by
    # the database driver and the independently scheduled printer check.
    original_sleep = asyncio.sleep

    async def one_tick(delay):
        if delay > 0:
            raise asyncio.CancelledError
        await original_sleep(0)

    obico._check_printer = AsyncMock()
    octoeverywhere._check_printer = AsyncMock()
    with (
        patch("backend.app.services.printer_manager.printer_manager") as manager,
        patch("asyncio.sleep", one_tick),
    ):
        manager.get_all_statuses.return_value = {1: SimpleNamespace(state="RUNNING", task_name="test-print")}
        manager.is_connected.return_value = True
        await obico._loop()
        await octoeverywhere._loop()
        await asyncio.gather(*octoeverywhere._checks.values())
        obico._check_printer.assert_not_awaited()
        octoeverywhere._check_printer.assert_awaited_once()

        # At its next normal settings refresh, OctoEverywhere also sees the
        # conflicting selection and stops inspecting, without a process restart.
        octoeverywhere._settings_refresh_at = 0
        await octoeverywhere._loop()
        await obico._loop()
        assert octoeverywhere._settings["enabled"] is False
        obico._check_printer.assert_not_awaited()
        octoeverywhere._check_printer.assert_awaited_once()

"""Unit tests for Spoolman location clearing when spools are removed from AMS.

Tests the clear_location_for_removed_spools method to verify that stale
Spoolman locations are cleared during both auto-sync and manual sync,
preventing the "double-booked" slot bug (#921).
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.spoolman import SpoolmanClient

BAMBU_UUID_A = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BAMBU_UUID_B = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
BAMBU_UUID_C = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
PRINTER_NAME = "My Printer"
LOCATION_PREFIX = f"{PRINTER_NAME} - "


def _make_spool(spool_id: int, location: str, tag: str = "", extra: dict | None = None) -> dict:
    """Create a mock Spoolman spool dict."""
    return {
        "id": spool_id,
        "location": location,
        "extra": extra or {"tag": tag},
    }


@pytest.fixture
def client():
    """Create a SpoolmanClient without connecting."""
    return SpoolmanClient("http://localhost:7912")


class TestClearLocationForRemovedSpools:
    """Test the clear_location_for_removed_spools method."""

    @pytest.mark.asyncio
    async def test_clears_spool_no_longer_in_ams(self, client):
        """A spool whose UUID is not in current_tray_uuids should have its location cleared."""
        cached_spools = [
            _make_spool(1, f"{LOCATION_PREFIX}AMS A Slot 1", BAMBU_UUID_A),
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock, return_value=True) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME, current_tray_uuids=set(), cached_spools=cached_spools
            )

        assert cleared == 1
        mock_update.assert_called_once_with(spool_id=1, clear_location=True)

    @pytest.mark.asyncio
    async def test_keeps_spool_still_in_ams(self, client):
        """A spool whose UUID is in current_tray_uuids should not be cleared."""
        cached_spools = [
            _make_spool(1, f"{LOCATION_PREFIX}AMS A Slot 1", BAMBU_UUID_A),
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME, current_tray_uuids={BAMBU_UUID_A}, cached_spools=cached_spools
            )

        assert cleared == 0
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_bambu_spools(self, client):
        """Spools without a 32-char RFID tag should not be cleared (non-Bambu / third-party)."""
        cached_spools = [
            _make_spool(1, f"{LOCATION_PREFIX}AMS A Slot 1", "SHORT_TAG"),
            _make_spool(2, f"{LOCATION_PREFIX}AMS A Slot 2", ""),
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME, current_tray_uuids=set(), cached_spools=cached_spools
            )

        assert cleared == 0
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_spools_from_other_printers(self, client):
        """Spools with locations for a different printer should not be touched."""
        cached_spools = [
            _make_spool(1, "Other Printer - AMS A Slot 1", BAMBU_UUID_A),
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME, current_tray_uuids=set(), cached_spools=cached_spools
            )

        assert cleared == 0
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_synced_spool_ids_protects_location_matched_spools(self, client):
        """Spools in synced_spool_ids should not be cleared even if UUID doesn't match."""
        cached_spools = [
            _make_spool(1, f"{LOCATION_PREFIX}AMS A Slot 1", BAMBU_UUID_A),
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME,
                current_tray_uuids=set(),
                cached_spools=cached_spools,
                synced_spool_ids={1},
            )

        assert cleared == 0
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_only_removed_spools_in_mixed_set(self, client):
        """With multiple spools at a printer, only clear the one that was removed."""
        cached_spools = [
            _make_spool(1, f"{LOCATION_PREFIX}AMS A Slot 1", BAMBU_UUID_A),  # Still in AMS
            _make_spool(2, f"{LOCATION_PREFIX}AMS A Slot 2", BAMBU_UUID_B),  # Removed
            _make_spool(3, f"{LOCATION_PREFIX}AMS A Slot 3", BAMBU_UUID_C),  # Still in AMS
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock, return_value=True) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME,
                current_tray_uuids={BAMBU_UUID_A, BAMBU_UUID_C},
                cached_spools=cached_spools,
            )

        assert cleared == 1
        mock_update.assert_called_once_with(spool_id=2, clear_location=True)

    @pytest.mark.asyncio
    async def test_uuid_comparison_is_case_insensitive(self, client):
        """UUID matching should work regardless of case."""
        cached_spools = [
            _make_spool(1, f"{LOCATION_PREFIX}AMS A Slot 1", BAMBU_UUID_A.lower()),
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME,
                current_tray_uuids={BAMBU_UUID_A},  # Uppercase
                cached_spools=cached_spools,
            )

        assert cleared == 0
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_spools_at_printer(self, client):
        """When no spools have locations for this printer, nothing is cleared."""
        with patch.object(client, "update_spool", new_callable=AsyncMock) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME, current_tray_uuids=set(), cached_spools=[]
            )

        assert cleared == 0
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_double_booking_scenario(self, client):
        """Reproduce #921: two spools assigned to the same printer location.

        When SpoolA is removed and SpoolB takes its slot, SpoolA's old location
        should be cleared because its UUID is no longer in current_tray_uuids.
        """
        cached_spools = [
            _make_spool(1, f"{LOCATION_PREFIX}AMS A Slot 1", BAMBU_UUID_A),  # OLD — was removed
            _make_spool(2, f"{LOCATION_PREFIX}AMS A Slot 1", BAMBU_UUID_B),  # NEW — just inserted
        ]

        with patch.object(client, "update_spool", new_callable=AsyncMock, return_value=True) as mock_update:
            cleared = await client.clear_location_for_removed_spools(
                PRINTER_NAME,
                current_tray_uuids={BAMBU_UUID_B},  # Only SpoolB is in AMS now
                cached_spools=cached_spools,
                synced_spool_ids={2},  # SpoolB was just synced
            )

        assert cleared == 1
        mock_update.assert_called_once_with(spool_id=1, clear_location=True)

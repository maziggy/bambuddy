"""Unit tests for Spoolman service.

These tests specifically target the sync_ams_tray method's disable_weight_sync
functionality that controls whether remaining_weight is updated.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.spoolman import AMSTray, SpoolmanClient


class TestSpoolmanClient:
    """Tests for SpoolmanClient class."""

    @pytest.fixture
    def client(self):
        """Create a SpoolmanClient instance."""
        return SpoolmanClient("http://localhost:7912")

    @pytest.fixture
    def sample_tray(self):
        """Create a sample AMSTray for testing."""
        return AMSTray(
            ams_id=0,
            tray_id=0,
            tray_type="PLA",
            tray_sub_brands="PLA Basic",
            tray_color="FF0000FF",
            remain=50,
            tag_uid="",
            tray_uuid="A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
            tray_info_idx="GFA00",
            tray_weight=1000,
        )

    @pytest.fixture
    def existing_spool(self):
        """Create a mock existing spool response."""
        return {
            "id": 42,
            "remaining_weight": 800,
            "extra": {"tag": '"A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"'},
            "filament": {"id": 1, "name": "PLA Red", "material": "PLA"},
        }

    @pytest.fixture
    def mock_filament(self):
        """Create a mock filament response."""
        return {"id": 1, "name": "PLA Basic", "material": "PLA"}

    # ========================================================================
    # Tests for sync_ams_tray with disable_weight_sync
    # ========================================================================

    @pytest.mark.asyncio
    async def test_sync_ams_tray_updates_weight_by_default(self, client, sample_tray, existing_spool):
        """Verify sync_ams_tray updates remaining_weight by default."""
        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=existing_spool)),
            patch.object(client, "update_spool", AsyncMock(return_value={"id": 42})) as mock_update,
        ):
            await client.sync_ams_tray(sample_tray, "TestPrinter")

            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args.kwargs
            assert "remaining_weight" in call_kwargs
            assert call_kwargs["remaining_weight"] == 500.0  # 50% of 1000g
            assert "location" in call_kwargs

    @pytest.mark.asyncio
    async def test_sync_ams_tray_skips_weight_when_disabled(self, client, sample_tray, existing_spool):
        """Verify sync_ams_tray skips remaining_weight when disable_weight_sync=True."""
        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=existing_spool)),
            patch.object(client, "update_spool", AsyncMock(return_value={"id": 42})) as mock_update,
        ):
            await client.sync_ams_tray(sample_tray, "TestPrinter", disable_weight_sync=True)

            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args.kwargs
            # remaining_weight should be None (not updated)
            assert call_kwargs.get("remaining_weight") is None
            # location should still be updated
            assert "location" in call_kwargs
            assert "TestPrinter" in call_kwargs["location"]

    @pytest.mark.asyncio
    async def test_sync_ams_tray_new_spool_always_includes_weight(self, client, sample_tray, mock_filament):
        """Verify new spool creation always includes remaining_weight even when disabled."""
        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=None)),
            patch.object(client, "_find_or_create_filament", AsyncMock(return_value=mock_filament)),
            patch.object(client, "create_spool", AsyncMock(return_value={"id": 99})) as mock_create,
        ):
            await client.sync_ams_tray(sample_tray, "TestPrinter", disable_weight_sync=True)

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            # New spools should ALWAYS include remaining_weight
            assert "remaining_weight" in call_kwargs
            assert call_kwargs["remaining_weight"] == 500.0  # 50% of 1000g

    @pytest.mark.asyncio
    async def test_sync_ams_tray_location_format(self, client, sample_tray, existing_spool):
        """Verify location format is correct when updating spool."""
        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=existing_spool)),
            patch.object(client, "update_spool", AsyncMock(return_value={"id": 42})) as mock_update,
        ):
            await client.sync_ams_tray(sample_tray, "My Printer", disable_weight_sync=True)

            call_kwargs = mock_update.call_args.kwargs
            # Location should follow pattern: "PrinterName - AMS A1"
            assert "location" in call_kwargs
            assert "My Printer" in call_kwargs["location"]
            assert "AMS" in call_kwargs["location"]

    @pytest.mark.asyncio
    async def test_sync_ams_tray_skips_non_bambu_spool(self, client):
        """Verify non-Bambu Lab spools are skipped."""
        # Third-party spool without proper identifiers
        tray = AMSTray(
            ams_id=0,
            tray_id=0,
            tray_type="PLA",
            tray_sub_brands="Third Party PLA",
            tray_color="FF0000FF",
            remain=50,
            tag_uid="",
            tray_uuid="",
            tray_info_idx="",  # No Bambu Lab preset ID
            tray_weight=1000,
        )

        result = await client.sync_ams_tray(tray, "TestPrinter")
        assert result is None

    @pytest.mark.asyncio
    async def test_sync_ams_tray_weight_calculation(self, client, existing_spool):
        """Verify remaining weight is calculated correctly for various percentages."""
        test_cases = [
            (100, 1000, 1000.0),  # Full spool
            (50, 1000, 500.0),  # Half spool
            (25, 1000, 250.0),  # Quarter spool
            (0, 1000, 0.0),  # Empty spool
            (75, 500, 375.0),  # Different spool weight
        ]

        for remain, weight, expected in test_cases:
            tray = AMSTray(
                ams_id=0,
                tray_id=0,
                tray_type="PLA",
                tray_sub_brands="PLA Basic",
                tray_color="FF0000FF",
                remain=remain,
                tag_uid="",
                tray_uuid="A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
                tray_info_idx="GFA00",
                tray_weight=weight,
            )

            with (
                patch.object(client, "find_spool_by_tag", AsyncMock(return_value=existing_spool)),
                patch.object(client, "update_spool", AsyncMock(return_value={"id": 42})) as mock_update,
            ):
                await client.sync_ams_tray(tray, "TestPrinter", disable_weight_sync=False)

                call_kwargs = mock_update.call_args.kwargs
                assert call_kwargs["remaining_weight"] == expected, (
                    f"Expected {expected}g for {remain}% of {weight}g, got {call_kwargs['remaining_weight']}"
                )

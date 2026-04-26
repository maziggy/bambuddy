"""Unit tests for Spoolman service.

These tests specifically target the sync_ams_tray method's disable_weight_sync
functionality that controls whether remaining_weight is updated.
Also includes tests for is_bambu_lab_spool RFID detection.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.app.services.spoolman import AMSTray, SpoolmanClient, init_spoolman_client


class TestIsBambuLabSpool:
    """Tests for is_bambu_lab_spool — detects BL spools via RFID hardware identifiers only."""

    @pytest.fixture
    def client(self):
        return SpoolmanClient("http://localhost:7912")

    def test_valid_tray_uuid_returns_true(self, client):
        """A non-zero 32-char hex tray_uuid identifies a BL spool."""
        assert client.is_bambu_lab_spool("A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4") is True

    def test_valid_tag_uid_returns_true(self, client):
        """A non-zero 16-char hex tag_uid identifies a BL spool (fallback)."""
        assert client.is_bambu_lab_spool("", tag_uid="A1B2C3D4E5F6A1B2") is True

    def test_zero_tray_uuid_returns_false(self, client):
        """All-zero tray_uuid means no RFID tag read."""
        assert client.is_bambu_lab_spool("00000000000000000000000000000000") is False

    def test_zero_tag_uid_returns_false(self, client):
        """All-zero tag_uid means no RFID tag read."""
        assert client.is_bambu_lab_spool("", tag_uid="0000000000000000") is False

    def test_empty_identifiers_returns_false(self, client):
        """No identifiers means no BL spool."""
        assert client.is_bambu_lab_spool("") is False
        assert client.is_bambu_lab_spool("", tag_uid="") is False

    def test_tray_info_idx_ignored(self, client):
        """tray_info_idx is NOT a reliable BL indicator — third-party spools
        using Bambu generic presets also have GF-prefixed tray_info_idx values."""
        # Third-party spool with Bambu preset but no RFID identifiers
        assert client.is_bambu_lab_spool("", tray_info_idx="GFA00") is False
        assert client.is_bambu_lab_spool("", tray_info_idx="GFB00") is False
        assert client.is_bambu_lab_spool("", tray_info_idx="GFSA02_04") is False

    def test_tray_info_idx_with_valid_uuid_returns_true(self, client):
        """BL spool with both RFID UUID and preset ID — detected by UUID."""
        assert (
            client.is_bambu_lab_spool(
                "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
                tray_info_idx="GFA00",
            )
            is True
        )

    def test_tray_uuid_preferred_over_tag_uid(self, client):
        """tray_uuid is checked before tag_uid (both valid)."""
        assert (
            client.is_bambu_lab_spool(
                "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
                tag_uid="A1B2C3D4E5F6A1B2",
            )
            is True
        )

    def test_short_tray_uuid_returns_false(self, client):
        """UUID must be exactly 32 hex chars."""
        assert client.is_bambu_lab_spool("A1B2C3D4") is False

    def test_non_hex_tray_uuid_returns_false(self, client):
        """UUID must be valid hex."""
        assert client.is_bambu_lab_spool("ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ") is False


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
            assert "location" not in call_kwargs

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
            # location must never be written by Bambuddy — user-managed in Spoolman
            assert "location" not in call_kwargs

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
    async def test_sync_ams_tray_does_not_write_location(self, client, sample_tray, existing_spool):
        """Verify sync_ams_tray never writes location= to Spoolman (user-managed field)."""
        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=existing_spool)),
            patch.object(client, "update_spool", AsyncMock(return_value={"id": 42})) as mock_update,
        ):
            await client.sync_ams_tray(sample_tray, "My Printer", disable_weight_sync=True)

            call_kwargs = mock_update.call_args.kwargs
            # Bambuddy must never auto-set spool.location — it is user-managed in Spoolman
            assert "location" not in call_kwargs

    # ========================================================================
    # T6: non-BL spool with custom RFID (H5 guard)
    # ========================================================================

    @pytest.mark.asyncio
    async def test_sync_ams_tray_non_bl_rfid_find_or_create_error_returns_none(self, client):
        """Non-BL spool with custom RFID: find_or_create_filament failure returns None, not raises.

        A third-party spool whose tag_uid is not exactly 16 hex chars is not
        identified as BL. sync_ams_tray must catch find_or_create_filament
        errors and return None instead of propagating the exception.
        """
        from backend.app.services.spoolman import SpoolmanUnavailableError

        # 8-char tag → spool_tag is set, but is_bambu_lab_spool returns False
        tray = AMSTray(
            ams_id=0,
            tray_id=2,
            tray_type="PLA",
            tray_sub_brands="eSun PLA+",
            tray_color="00FF00FF",
            remain=50,
            tag_uid="AABB1234",
            tray_uuid="",
            tray_info_idx="",
            tray_weight=1000,
        )

        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=None)),
            patch.object(
                client,
                "find_or_create_filament",
                AsyncMock(side_effect=SpoolmanUnavailableError("timeout")),
            ),
        ):
            result = await client.sync_ams_tray(tray, "TestPrinter")

        assert result is None

    # ========================================================================
    # T7: hint path uncached — get_spool(hint) called when not in cached_spools
    # ========================================================================

    @pytest.mark.asyncio
    async def test_sync_ams_tray_hint_uncached_calls_get_spool(self, client):
        """No-RFID path: when hint spool is absent from cached_spools, get_spool is called."""
        tray = AMSTray(
            ams_id=0,
            tray_id=3,
            tray_type="PETG",
            tray_sub_brands="Generic PETG",
            tray_color="0000FFFF",
            remain=75,
            tag_uid="",
            tray_uuid="",
            tray_info_idx="",
            tray_weight=1000,
        )
        # cached_spools exists but does NOT contain spool 99
        cached_spools = [{"id": 1, "extra": {}}]
        fetched_spool = {"id": 99, "extra": {}}

        with (
            patch.object(client, "get_spool", AsyncMock(return_value=fetched_spool)) as mock_get,
            patch.object(client, "update_spool", AsyncMock(return_value=fetched_spool)),
        ):
            result = await client.sync_ams_tray(
                tray,
                "TestPrinter",
                cached_spools=cached_spools,
                spoolman_spool_id_hint=99,
            )

        assert result is not None
        mock_get.assert_awaited_once_with(99)

    # ========================================================================
    # T8: hint ignored when RFID tag is present
    # ========================================================================

    @pytest.mark.asyncio
    async def test_sync_ams_tray_rfid_takes_precedence_over_hint(self, client, existing_spool):
        """When tray_uuid is set, the RFID path is used and the hint is never consulted."""
        tray = AMSTray(
            ams_id=0,
            tray_id=4,
            tray_type="PLA",
            tray_sub_brands="PLA Basic",
            tray_color="FF0000FF",
            remain=50,
            tag_uid="",
            tray_uuid="A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
            tray_info_idx="GFA00",
            tray_weight=1000,
        )

        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=existing_spool)),
            patch.object(client, "update_spool", AsyncMock(return_value={"id": 42})),
            patch.object(client, "get_spool", AsyncMock()) as mock_get_spool,
        ):
            result = await client.sync_ams_tray(
                tray,
                "TestPrinter",
                spoolman_spool_id_hint=99,
            )

        assert result is not None
        # hint path (get_spool) must NOT be called when RFID is present
        mock_get_spool.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_ams_tray_non_bambu_no_rfid_returns_none(self, client):
        """Third-party spool without any RFID and no hint returns None."""
        # Non-BL spool: no tray_uuid, no tag_uid, no spoolman_spool_id_hint → nothing to match
        tray = AMSTray(
            ams_id=0,
            tray_id=0,
            tray_type="PLA",
            tray_sub_brands="Third Party PLA",
            tray_color="FF0000FF",
            remain=50,
            tag_uid="",
            tray_uuid="",
            tray_info_idx="",
            tray_weight=1000,
        )

        result = await client.sync_ams_tray(tray, "TestPrinter")
        assert result is None

    @pytest.mark.asyncio
    async def test_sync_ams_tray_hint_updates_spool_without_rfid(self, client):
        """No-RFID fallback: spool_id_hint from local slot-assignment table updates the spool."""
        tray = AMSTray(
            ams_id=0,
            tray_id=0,
            tray_type="PLA",
            tray_sub_brands="Generic PLA",
            tray_color="00FF00FF",
            remain=80,
            tag_uid="",
            tray_uuid="",
            tray_info_idx="",
            tray_weight=1000,
        )
        cached_spools = [{"id": 99, "extra": {}}]

        with patch.object(client, "update_spool", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = {"id": 99}
            result = await client.sync_ams_tray(
                tray, "TestPrinter", cached_spools=cached_spools, spoolman_spool_id_hint=99
            )

        assert result is not None
        assert result["id"] == 99
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args.kwargs
        assert "location" not in call_kwargs

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

    # ========================================================================
    # Tests for caching functionality
    # ========================================================================

    @pytest.mark.asyncio
    async def test_find_spool_by_tag_with_cached_spools(self, client):
        """Verify find_spool_by_tag uses cached spools when provided (no API call)."""
        cached = [
            {"id": 1, "extra": {"tag": '"ABC123"'}},
            {"id": 2, "extra": {"tag": '"XYZ789"'}},
        ]

        with patch.object(client, "get_spools", AsyncMock()) as mock_get:
            result = await client.find_spool_by_tag("ABC123", cached_spools=cached)
            assert result["id"] == 1
            mock_get.assert_not_called()  # Should NOT call get_spools

    @pytest.mark.asyncio
    async def test_find_spool_by_tag_without_cached_spools(self, client):
        """Verify find_spool_by_tag fetches spools when cache not provided."""
        mock_spools = [{"id": 1, "extra": {"tag": '"ABC123"'}}]

        with patch.object(client, "get_spools", AsyncMock(return_value=mock_spools)) as mock_get:
            result = await client.find_spool_by_tag("ABC123")
            assert result["id"] == 1
            mock_get.assert_called_once()  # Should call get_spools

    @pytest.mark.asyncio
    async def test_find_spools_by_location_prefix_with_cached_spools(self, client):
        """Verify find_spools_by_location_prefix uses cached spools when provided."""
        cached = [
            {"id": 1, "location": "Printer1 - AMS A1"},
            {"id": 2, "location": "Printer2 - AMS A1"},
            {"id": 3, "location": "Printer1 - AMS A2"},
        ]

        with patch.object(client, "get_spools", AsyncMock()) as mock_get:
            result = await client.find_spools_by_location_prefix("Printer1 - ", cached_spools=cached)
            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["id"] == 3
            mock_get.assert_not_called()  # Should NOT call get_spools

    @pytest.mark.asyncio
    async def test_sync_ams_tray_with_cached_spools(self, client, sample_tray, existing_spool):
        """Verify sync_ams_tray passes cached_spools to find_spool_by_tag."""
        cached = [existing_spool]

        with (
            patch.object(client, "get_spools", AsyncMock()) as mock_get,
            patch.object(client, "update_spool", AsyncMock(return_value={"id": 42})),
        ):
            await client.sync_ams_tray(sample_tray, "TestPrinter", cached_spools=cached)
            mock_get.assert_not_called()  # Should NOT call get_spools

    @pytest.mark.asyncio
    async def test_clear_location_for_removed_spools_with_cached_spools(self, client):
        """Verify clear_location_for_removed_spools uses cached spools."""
        cached = [
            {"id": 1, "location": "Printer1 - AMS A1", "extra": {"tag": '"A1B2C3D4E5F60718293A4B5C6D7E8F90"'}},
            {"id": 2, "location": "Printer1 - AMS A2", "extra": {"tag": '"B1C2D3E4F5061728394A5B6C7D8E9F01"'}},
            {"id": 3, "location": "Printer1 - AMS A3", "extra": {"tag": '"C1D2E3F40516273849A5B6C7D8E9F012"'}},
        ]
        # Tag 3 was cleared, so only tags 1 and 2 are current
        current_tags = {
            "A1B2C3D4E5F60718293A4B5C6D7E8F90",
            "B1C2D3E4F5061728394A5B6C7D8E9F01",
        }

        with (
            patch.object(client, "get_spools", AsyncMock()) as mock_get,
            patch.object(client, "update_spool", AsyncMock(return_value={"id": 3})) as mock_update,
        ):
            cleared = await client.clear_location_for_removed_spools("Printer1", current_tags, cached_spools=cached)
            assert cleared == 1
            mock_get.assert_not_called()  # Should NOT call get_spools
            mock_update.assert_called_once()
            # Verify it cleared TAG3 (not in current_tags)
            call_kwargs = mock_update.call_args.kwargs
            assert call_kwargs["spool_id"] == 3
            assert call_kwargs.get("clear_location") is True

    # ========================================================================
    # Tests for retry logic in get_spools
    # ========================================================================

    @pytest.mark.asyncio
    async def test_get_spools_succeeds_on_first_attempt(self, client):
        """Verify get_spools succeeds immediately when no errors occur."""
        mock_spools = [{"id": 1}, {"id": 2}]

        with patch.object(client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_response.json = Mock(return_value=mock_spools)
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            result = await client.get_spools()

            assert result == mock_spools
            mock_get_client.assert_called_once()
            mock_http_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_spools_retries_on_connection_error(self, client):
        """Verify get_spools retries up to 3 times on connection errors."""
        import httpx

        mock_spools = [{"id": 1}]

        with (
            patch.object(client, "_get_client") as mock_get_client,
            patch.object(client, "close", AsyncMock()) as mock_close,
            patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            mock_http_client = AsyncMock()
            mock_get_client.return_value = mock_http_client

            # First 2 attempts fail with ReadError, 3rd succeeds
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_response.json = Mock(return_value=mock_spools)

            mock_http_client.get = AsyncMock(
                side_effect=[
                    httpx.ReadError("Connection closed"),
                    httpx.ReadError("Connection closed"),
                    mock_response,
                ]
            )

            result = await client.get_spools()

            assert result == mock_spools
            assert mock_get_client.call_count == 3
            assert mock_http_client.get.call_count == 3
            # Should close client twice (after each failed attempt)
            assert mock_close.call_count == 2
            # Should sleep twice (after first 2 attempts)
            assert mock_sleep.call_count == 2
            mock_sleep.assert_called_with(0.5)

    @pytest.mark.asyncio
    async def test_get_spools_raises_after_3_failed_attempts(self, client):
        """Verify get_spools raises exception after 3 failed attempts."""
        import httpx

        with (
            patch.object(client, "_get_client", AsyncMock()) as mock_get_client,
            patch.object(client, "close", AsyncMock()) as mock_close,
            patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            mock_http_client = AsyncMock()
            mock_get_client.return_value = mock_http_client

            # All 3 attempts fail
            mock_http_client.get.side_effect = httpx.ReadError("Connection closed")

            with pytest.raises(httpx.ReadError):
                await client.get_spools()

            assert mock_get_client.call_count == 3
            assert mock_http_client.get.call_count == 3
            # Should close client twice (after first 2 failed attempts, not after 3rd)
            assert mock_close.call_count == 2
            # Should sleep twice (after first 2 attempts, not after 3rd)
            assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_get_spools_handles_non_connection_errors(self, client):
        """Verify get_spools retries on non-connection errors without recreating client."""
        import httpx

        mock_spools = [{"id": 1}]

        with (
            patch.object(client, "_get_client") as mock_get_client,
            patch.object(client, "close", AsyncMock()) as mock_close,
            patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            mock_http_client = AsyncMock()
            mock_get_client.return_value = mock_http_client

            # First attempt fails with HTTP error, 2nd succeeds
            mock_response_error = Mock()
            mock_response_error.raise_for_status = Mock(
                side_effect=httpx.HTTPStatusError("500 Server Error", request=Mock(), response=Mock())
            )

            mock_response_success = Mock()
            mock_response_success.raise_for_status = Mock()
            mock_response_success.json = Mock(return_value=mock_spools)

            mock_http_client.get = AsyncMock(side_effect=[mock_response_error, mock_response_success])

            result = await client.get_spools()

            assert result == mock_spools
            assert mock_get_client.call_count == 2
            # Should NOT close client for HTTP errors (only connection errors)
            mock_close.assert_not_called()
            # Should sleep once (after first failed attempt)
            assert mock_sleep.call_count == 1


# ---------------------------------------------------------------------------
# init_spoolman_client — SSRF guard (B4 / T3)
# ---------------------------------------------------------------------------


class TestInitSpoolmanClientSSRFGuard:
    """init_spoolman_client must reject genuinely unsafe URLs before creating a client.

    Scope: cloud metadata endpoints, multicast, unspecified, non-http(s) schemes,
    and numeric-encoded IP bypasses. Loopback and RFC-1918 private ranges are
    explicitly allowed — Bambuddy's primary deployment is LAN-local Spoolman.
    """

    @pytest.mark.asyncio
    async def test_cloud_metadata_raises_value_error(self):
        with pytest.raises(ValueError, match="cloud metadata"):
            await init_spoolman_client("http://169.254.169.254/latest/meta-data/")

    @pytest.mark.asyncio
    async def test_multicast_raises_value_error(self):
        with pytest.raises(ValueError, match="multicast|unspecified"):
            await init_spoolman_client("http://224.0.0.1/")

    @pytest.mark.asyncio
    async def test_unspecified_raises_value_error(self):
        with pytest.raises(ValueError, match="multicast|unspecified"):
            await init_spoolman_client("http://0.0.0.0/")

    @pytest.mark.asyncio
    async def test_numeric_encoded_ip_raises_value_error(self):
        # decimal-encoded 127.0.0.1 — libc resolves these but ipaddress doesn't
        with pytest.raises(ValueError, match="numeric-encoded"):
            await init_spoolman_client("http://2130706433/")

    @pytest.mark.asyncio
    async def test_non_http_scheme_raises_value_error(self):
        with pytest.raises(ValueError, match="http or https"):
            await init_spoolman_client("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_private_ip_is_allowed(self):
        """Regression: RFC-1918 private addresses are the normal LAN topology."""
        mock_instance = AsyncMock()
        with (
            patch("backend.app.services.spoolman._spoolman_client", None),
            patch("backend.app.services.spoolman.SpoolmanClient", return_value=mock_instance) as mock_cls,
        ):
            client = await init_spoolman_client("http://192.168.1.50:7912/")
        mock_cls.assert_called_once_with("http://192.168.1.50:7912/")
        assert client is mock_instance

    @pytest.mark.asyncio
    async def test_loopback_ip_is_allowed(self):
        """Regression: same-host Spoolman via loopback is a supported topology."""
        mock_instance = AsyncMock()
        with (
            patch("backend.app.services.spoolman._spoolman_client", None),
            patch("backend.app.services.spoolman.SpoolmanClient", return_value=mock_instance) as mock_cls,
        ):
            client = await init_spoolman_client("http://127.0.0.1:7912/")
        mock_cls.assert_called_once_with("http://127.0.0.1:7912/")
        assert client is mock_instance

    @pytest.mark.asyncio
    async def test_localhost_hostname_is_allowed(self):
        # localhost (hostname, not bare IP) is a supported topology for same-host Spoolman
        mock_instance = AsyncMock()
        with (
            patch("backend.app.services.spoolman._spoolman_client", None),
            patch("backend.app.services.spoolman.SpoolmanClient", return_value=mock_instance) as mock_cls,
        ):
            client = await init_spoolman_client("http://localhost:7912/")
        mock_cls.assert_called_once_with("http://localhost:7912/")
        assert client is mock_instance

    @pytest.mark.asyncio
    async def test_public_url_is_allowed(self):
        mock_instance = AsyncMock()
        with (
            patch("backend.app.services.spoolman._spoolman_client", None),
            patch("backend.app.services.spoolman.SpoolmanClient", return_value=mock_instance) as mock_cls,
        ):
            client = await init_spoolman_client("http://spoolman.example.com:7912/")
        mock_cls.assert_called_once_with("http://spoolman.example.com:7912/")
        assert client is mock_instance

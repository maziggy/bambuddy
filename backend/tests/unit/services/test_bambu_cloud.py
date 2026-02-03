"""Tests for Bambu Cloud service - TOTP and email verification flows."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.bambu_cloud import BambuCloudService


class TestBambuCloudLogin:
    """Test login flow detection (email vs TOTP)."""

    @pytest.fixture
    def cloud_service(self):
        """Create a BambuCloudService instance."""
        return BambuCloudService()

    @pytest.mark.asyncio
    async def test_login_detects_email_verification(self, cloud_service):
        """When loginType is verifyCode, should return email verification type."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "loginType": "verifyCode",
        }

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.login_request("test@example.com", "password")

            assert result["success"] is False
            assert result["needs_verification"] is True
            assert result["verification_type"] == "email"
            assert result["tfa_key"] is None
            assert "email" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_login_detects_totp(self, cloud_service):
        """When loginType is tfa, should return TOTP verification type with tfaKey."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "loginType": "tfa",
            "tfaKey": "test-tfa-key-123",
        }

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.login_request("test@example.com", "password")

            assert result["success"] is False
            assert result["needs_verification"] is True
            assert result["verification_type"] == "totp"
            assert result["tfa_key"] == "test-tfa-key-123"
            assert "authenticator" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_login_direct_success(self, cloud_service):
        """When accessToken is returned directly, should succeed without verification."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "accessToken": "test-access-token",
            "refreshToken": "test-refresh-token",
        }

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.login_request("test@example.com", "password")

            assert result["success"] is True
            assert result["needs_verification"] is False
            assert cloud_service.access_token == "test-access-token"

    @pytest.mark.asyncio
    async def test_login_failure(self, cloud_service):
        """When login fails, should return error message."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "message": "Invalid credentials",
        }

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.login_request("test@example.com", "wrong-password")

            assert result["success"] is False
            assert result["needs_verification"] is False
            assert "Invalid credentials" in result["message"]


class TestBambuCloudEmailVerification:
    """Test email verification flow."""

    @pytest.fixture
    def cloud_service(self):
        """Create a BambuCloudService instance."""
        return BambuCloudService()

    @pytest.mark.asyncio
    async def test_verify_code_success(self, cloud_service):
        """When email code is correct, should return success with token."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "accessToken": "test-access-token",
            "refreshToken": "test-refresh-token",
        }

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.verify_code("test@example.com", "123456")

            assert result["success"] is True
            assert cloud_service.access_token == "test-access-token"

    @pytest.mark.asyncio
    async def test_verify_code_failure(self, cloud_service):
        """When email code is incorrect, should return failure."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "message": "Invalid verification code",
        }

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.verify_code("test@example.com", "000000")

            assert result["success"] is False
            assert "Invalid" in result["message"] or "Verification failed" in result["message"]


class TestBambuCloudTOTPVerification:
    """Test TOTP verification flow."""

    @pytest.fixture
    def cloud_service(self):
        """Create a BambuCloudService instance."""
        return BambuCloudService()

    @pytest.mark.asyncio
    async def test_verify_totp_success(self, cloud_service):
        """When TOTP code is correct, should return success with token."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"token": "test-access-token"}'
        mock_response.json.return_value = {
            "token": "test-access-token",
        }
        mock_response.cookies = {}

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.verify_totp("test-tfa-key", "123456")

            assert result["success"] is True
            assert cloud_service.access_token == "test-access-token"

    @pytest.mark.asyncio
    async def test_verify_totp_uses_correct_endpoint(self, cloud_service):
        """TOTP verification should use bambulab.com, not api.bambulab.com."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"token": "test-token"}'
        mock_response.json.return_value = {"token": "test-token"}
        mock_response.cookies = {}

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await cloud_service.verify_totp("test-tfa-key", "123456")

            # Check the URL used
            call_args = mock_post.call_args
            url = call_args[0][0]
            assert "bambulab.com/api/sign-in/tfa" in url
            assert "api.bambulab.com" not in url

    @pytest.mark.asyncio
    async def test_verify_totp_empty_response(self, cloud_service):
        """When TOTP returns empty response, should handle gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = ""

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.verify_totp("test-tfa-key", "123456")

            assert result["success"] is False
            assert "empty response" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_verify_totp_cloudflare_blocked(self, cloud_service):
        """When Cloudflare blocks request, should handle gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "<!DOCTYPE html><html><head><title>Just a moment...</title>"
        # json() raises an error when response is HTML
        mock_response.json.side_effect = ValueError("No JSON")

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.verify_totp("test-tfa-key", "123456")

            assert result["success"] is False
            assert "Invalid response" in result["message"]

    @pytest.mark.asyncio
    async def test_verify_totp_includes_browser_headers(self, cloud_service):
        """TOTP verification should include browser-like headers to bypass Cloudflare."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"token": "test-token"}'
        mock_response.json.return_value = {"token": "test-token"}
        mock_response.cookies = {}

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await cloud_service.verify_totp("test-tfa-key", "123456")

            # Check headers include User-Agent
            call_args = mock_post.call_args
            headers = call_args[1]["headers"]
            assert "User-Agent" in headers
            assert "Mozilla" in headers["User-Agent"]

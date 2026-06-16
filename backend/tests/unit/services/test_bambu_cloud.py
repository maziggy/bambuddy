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
        """When Cloudflare returns a 'Just a moment...' interstitial instead of
        JSON, surface the actionable CF-specific message (issue #1575) rather
        than the opaque "Invalid response from Bambu Cloud" parse error."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "<!DOCTYPE html><html><head><title>Just a moment...</title>"
        mock_response.headers = {}
        # json() raises an error when response is HTML
        mock_response.json.side_effect = ValueError("No JSON")

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud_service.verify_totp("test-tfa-key", "123456")

            assert result["success"] is False
            assert "Cloudflare" in result["message"]
            assert "bambulab.com" in result["message"]

    @pytest.mark.asyncio
    async def test_verify_totp_uses_honest_bambuddy_user_agent(self, cloud_service):
        """TOTP verification identifies as Bambuddy, not as a browser.

        The TOTP endpoint previously sent a Chrome User-Agent + Origin/Referer
        headers under the assumption Cloudflare would block non-browser
        identification. Verified 2026-05-12 that ``https://bambulab.com/api/sign-in/tfa``
        accepts ``Bambuddy/X.Y.Z`` cleanly — the expected application-level
        response comes back, no Cloudflare interstitial. Browser impersonation
        was removed to stay clearly on the right side of Bambu Lab's
        "no falsified client identity" line from the 2026-05-12 cloud-access
        blog post.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"token": "test-token"}'
        mock_response.json.return_value = {"token": "test-token"}
        mock_response.cookies = {}

        with patch.object(cloud_service._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await cloud_service.verify_totp("test-tfa-key", "123456")

            call_args = mock_post.call_args
            headers = call_args[1]["headers"]
            assert headers["User-Agent"].startswith("Bambuddy/")
            # Browser-impersonation strings must not creep back in
            assert "Mozilla" not in headers["User-Agent"]
            assert "Chrome" not in headers["User-Agent"]
            # Origin / Referer headers were spoofing bambulab.com origin — gone
            assert "Origin" not in headers
            assert "Referer" not in headers


class TestBambuCloudRegion:
    """Region routing — China-region instances must hit api.bambulab.cn."""

    def test_global_region_uses_com_base(self):
        """Default / 'global' region should use api.bambulab.com."""
        cloud = BambuCloudService()  # default region
        assert cloud.base_url == "https://api.bambulab.com"

        cloud_explicit = BambuCloudService(region="global")
        assert cloud_explicit.base_url == "https://api.bambulab.com"

    def test_china_region_uses_cn_base(self):
        """'china' region should use api.bambulab.cn."""
        cloud = BambuCloudService(region="china")
        assert cloud.base_url == "https://api.bambulab.cn"

    @pytest.mark.asyncio
    async def test_china_region_login_hits_cn_endpoint(self):
        """A login_request from a China-region instance must POST to api.bambulab.cn."""
        cloud = BambuCloudService(region="china")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"loginType": "verifyCode"}

        with patch.object(cloud._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await cloud.login_request("test@example.com", "password")

            url = mock_post.call_args[0][0]
            assert "api.bambulab.cn" in url
            assert "api.bambulab.com" not in url

    @pytest.mark.asyncio
    async def test_china_region_totp_hits_cn_tfa_endpoint(self):
        """TOTP verification from a China-region instance uses the CN TFA endpoint."""
        cloud = BambuCloudService(region="china")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"token": "t"}'
        mock_response.json.return_value = {"token": "t"}
        mock_response.cookies = {}

        with patch.object(cloud._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await cloud.verify_totp("tfa-key", "123456")

            url = mock_post.call_args[0][0]
            assert "bambulab.cn/api/sign-in/tfa" in url
            assert "bambulab.com" not in url


# ===========================================================================
# Issue #1575: Cloudflare interstitial → actionable error message
# ===========================================================================


class TestCloudflareChallengeDetection:
    """The _detect_cloudflare_challenge helper inspects a response and returns
    the user-actionable message when CF returned a challenge / mitigation page
    instead of JSON. None otherwise."""

    # The actual interstitial fragment captured from issue #1575's log — keeping
    # this verbatim so future regressions in detection are checked against the
    # exact body shape the user hit, not a stylised copy.
    _REPORTER_INTERSTITIAL = (
        '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...'
        '</title><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
        '<meta http-equiv="X-UA-Compatible" content="IE=Edge">'
        '<meta name="robots" content="noindex,nofollow">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
    )

    def test_just_a_moment_title_in_body(self):
        from backend.app.services.bambu_cloud import _detect_cloudflare_challenge

        response = MagicMock()
        response.text = self._REPORTER_INTERSTITIAL
        response.status_code = 200
        response.headers = {}
        assert _detect_cloudflare_challenge(response) is not None

    def test_challenges_cloudflare_com_in_body(self):
        from backend.app.services.bambu_cloud import _detect_cloudflare_challenge

        response = MagicMock()
        response.text = (
            '<html><body><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></body></html>'
        )
        response.status_code = 200
        response.headers = {}
        assert _detect_cloudflare_challenge(response) is not None

    def test_cf_mitigated_403(self):
        from backend.app.services.bambu_cloud import _detect_cloudflare_challenge

        response = MagicMock()
        response.text = ""
        response.status_code = 403
        response.headers = {"cf-mitigated": "challenge"}
        assert _detect_cloudflare_challenge(response) is not None

    def test_cf_ray_503(self):
        from backend.app.services.bambu_cloud import _detect_cloudflare_challenge

        response = MagicMock()
        response.text = "<html>Under attack</html>"
        response.status_code = 503
        response.headers = {"cf-ray": "abc-DEF"}
        assert _detect_cloudflare_challenge(response) is not None

    def test_real_json_400_is_not_a_challenge(self):
        """Application-level 400 with the real "Login failed" JSON the API
        normally returns must NOT be misclassified as a CF challenge — that
        would suppress the actionable upstream error."""
        from backend.app.services.bambu_cloud import _detect_cloudflare_challenge

        response = MagicMock()
        response.text = '{"code":5,"error":"Login failed"}'
        response.status_code = 400
        response.headers = {"cf-ray": "abc-DEF", "server": "cloudflare"}
        assert _detect_cloudflare_challenge(response) is None

    def test_message_mentions_bambu_lab_and_cloudflare(self):
        """The message must clearly attribute the block to Bambu Lab's
        Cloudflare protection — not to Bambuddy — so users know what to do."""
        from backend.app.services.bambu_cloud import _detect_cloudflare_challenge

        response = MagicMock()
        response.text = "<title>Just a moment...</title>"
        response.status_code = 200
        response.headers = {}
        msg = _detect_cloudflare_challenge(response)
        assert msg is not None
        assert "Cloudflare" in msg
        assert "bambulab.com" in msg

    @pytest.mark.asyncio
    async def test_verify_code_surfaces_cf_message_on_interstitial(self):
        """verify_code (email-code path) must surface the CF message when the
        endpoint returns an HTML interstitial — same shape as verify_totp."""
        cloud = BambuCloudService()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = self._REPORTER_INTERSTITIAL
        mock_response.headers = {}
        mock_response.json.side_effect = ValueError("No JSON")

        with patch.object(cloud._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud.verify_code("test@example.com", "123456")

            assert result["success"] is False
            assert "Cloudflare" in result["message"]

    @pytest.mark.asyncio
    async def test_login_request_surfaces_cf_message_on_interstitial(self):
        """login_request must surface the CF message when the endpoint returns
        an HTML interstitial. Previously the parse error bubbled to
        BambuCloudAuthError with an opaque "Expecting value..." detail."""
        cloud = BambuCloudService()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = self._REPORTER_INTERSTITIAL
        mock_response.headers = {}
        mock_response.json.side_effect = ValueError("No JSON")

        with patch.object(cloud._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await cloud.login_request("test@example.com", "password")

            assert result["success"] is False
            assert result["needs_verification"] is False
            assert "Cloudflare" in result["message"]

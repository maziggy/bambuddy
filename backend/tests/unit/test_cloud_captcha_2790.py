"""Tests for Bambu's anti-abuse CAPTCHA challenge on sign-in (#2790).

Bambu's own anti-abuse layer -- not the Cloudflare edge -- answers a request it
has flagged with ``HTTP 418`` and ``{"captchaId": ..., "error": "We need you to
confirm you are not a robot"}``. It is keyed to the source IP, no credential
will be accepted until it clears, and there is no server-side solve.

That body is well-formed JSON, so the Cloudflare detector never fired on it and
``login_request`` fell through to its generic error path, which lifted Bambu's
sentence out of ``error`` and returned it verbatim. The reporter got a bare
toast reading "We need you to confirm you are not a robot" -- no challenge to
answer, no explanation, nothing to click -- and filed it as a Bambuddy bug.

These tests pin: the challenge is recognised by shape rather than by wording,
all three sign-in calls report it as ``reason="captcha"`` with an explanation
instead of Bambu's raw string, retries are held back per-origin so Bambuddy
stops deepening the block, and the scanner names it in the next support bundle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services import bambu_cloud as bc
from backend.app.services.bambu_cloud import BambuCloudService

# Bambu's actual challenge body, as seen on both the login endpoint and the
# design-service endpoints MakerWorld imports use.
_CAPTCHA_BODY = {
    "captchaId": "3f2a9c1e64b04d7f",
    "error": "We need you to confirm you are not a robot",
}


@pytest.fixture(autouse=True)
def _clear_captcha_cooloff():
    """The cool-off map is module-level; don't leak it across tests."""
    bc._captcha_blocked_until.clear()
    yield
    bc._captcha_blocked_until.clear()


def _response(status_code: int, body: object | None = None, *, text: str | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    if body is None and text is not None:
        resp.json = MagicMock(side_effect=ValueError("not json"))
    else:
        resp.json = MagicMock(return_value=body if body is not None else {})
    resp.text = text if text is not None else "{}"
    return resp


def _service(response) -> BambuCloudService:
    svc = BambuCloudService(client=MagicMock(spec=httpx.AsyncClient))
    svc._client.post = AsyncMock(return_value=response)
    svc._client.get = AsyncMock(return_value=response)
    return svc


class TestChallengeIsRecognisedByShape:
    def test_captcha_id_marks_the_challenge(self):
        assert bc.is_captcha_challenge(_response(418, _CAPTCHA_BODY)) is True

    def test_wording_alone_is_enough(self):
        """No captchaId, but the text says what it is. Bambu has shipped the
        challenge under more than one body shape."""
        assert bc.is_captcha_challenge(_response(418, {"error": "please confirm you are not a robot"})) is True

    def test_a_418_without_a_marker_is_not_reported_as_a_captcha(self):
        """Telling a user to solve a CAPTCHA that was never offered is the exact
        confusion this issue is about -- don't invent one for any stray 418."""
        assert bc.is_captcha_challenge(_response(418, {"error": "Too many requests"})) is False

    def test_status_alone_does_not_decide_it(self):
        """A captchaId on a 200 is not a refusal -- only the 418 is."""
        assert bc.is_captcha_challenge(_response(200, _CAPTCHA_BODY)) is False

    def test_a_non_json_challenge_is_still_recognised(self):
        resp = _response(418, None, text="<html><body>captcha required</body></html>")
        assert bc.is_captcha_challenge(resp) is True

    def test_a_non_json_body_without_markers_is_not(self):
        resp = _response(418, None, text="<html><body>Service unavailable</body></html>")
        assert bc.is_captcha_challenge(resp) is False


class TestSignInReportsTheChallenge:
    @pytest.mark.asyncio
    async def test_login_explains_instead_of_echoing_bambu(self):
        svc = _service(_response(418, _CAPTCHA_BODY))

        result = await svc.login_request("user@example.com", "pw")

        assert result["success"] is False
        assert result["needs_verification"] is False
        assert result["reason"] == "captcha"
        # The regression in one line: this used to BE Bambu's sentence.
        assert result["message"] != _CAPTCHA_BODY["error"]
        assert "CAPTCHA" in result["message"]
        # The two things the reporter had no way to know.
        assert "password" in result["message"].lower()
        assert "access token" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_email_code_verification_reports_it_too(self):
        svc = _service(_response(418, _CAPTCHA_BODY))

        result = await svc.verify_code("user@example.com", "123456")

        assert result["reason"] == "captcha"
        assert result["message"] != _CAPTCHA_BODY["error"]

    @pytest.mark.asyncio
    async def test_totp_verification_reports_it_too(self):
        svc = _service(_response(418, _CAPTCHA_BODY))
        with patch.object(svc, "_fetch_csrf_token", AsyncMock(return_value="csrf-token")):
            result = await svc.verify_totp("tfa-key", "123456")

        assert result["reason"] == "captcha"
        assert result["message"] != _CAPTCHA_BODY["error"]

    @pytest.mark.asyncio
    async def test_an_ordinary_rejection_is_unchanged(self):
        """Wrong password still says what Bambu said, and carries no reason --
        the UI must keep toasting those rather than showing the CAPTCHA panel."""
        svc = _service(_response(400, {"error": "Login failed"}))

        result = await svc.login_request("user@example.com", "wrong")

        assert result["message"] == "Login failed"
        assert result.get("reason") is None
        assert not bc.captcha_cooloff_active(svc.base_url)


class TestRetriesAreHeldBack:
    @pytest.mark.asyncio
    async def test_a_second_attempt_is_not_sent_to_bambu(self):
        """The reporter's log shows four attempts in eighteen seconds. Every one
        of them is more evidence for the thing that flagged us."""
        svc = _service(_response(418, _CAPTCHA_BODY))
        await svc.login_request("user@example.com", "pw")
        assert svc._client.post.await_count == 1

        result = await svc.login_request("user@example.com", "pw")

        assert svc._client.post.await_count == 1
        assert result["reason"] == "captcha"

    @pytest.mark.asyncio
    async def test_the_cooloff_covers_a_fresh_service_instance(self):
        """Services are built per request, so the cool-off has to outlive one."""
        await _service(_response(418, _CAPTCHA_BODY)).login_request("user@example.com", "pw")

        second = _service(_response(200, {"loginType": "verifyCode"}))
        result = await second.login_request("user@example.com", "pw")

        second._client.post.assert_not_awaited()
        assert result["reason"] == "captcha"

    @pytest.mark.asyncio
    async def test_the_cooloff_expires(self):
        svc = _service(_response(418, _CAPTCHA_BODY))
        await svc.login_request("user@example.com", "pw")

        bc._captcha_blocked_until[svc.base_url] = bc.time.monotonic() - 1
        svc._client.post = AsyncMock(return_value=_response(200, {"loginType": "verifyCode"}))
        result = await svc.login_request("user@example.com", "pw")

        assert result["needs_verification"] is True
        assert bc._captcha_blocked_until == {}, "the expired entry should be dropped on the way past"

    @pytest.mark.asyncio
    async def test_a_challenge_on_the_api_host_does_not_strand_a_totp_sign_in(self):
        """TOTP verification goes to bambulab.com, everything else to
        api.bambulab.com. Blocking one on the other's behalf would leave a user
        halfway through two-factor with no way forward."""
        svc = _service(_response(418, _CAPTCHA_BODY))
        await svc.login_request("user@example.com", "pw")

        svc._client.post = AsyncMock(return_value=_response(200, {"accessToken": "tok"}))
        with patch.object(svc, "_fetch_csrf_token", AsyncMock(return_value="csrf-token")):
            result = await svc.verify_totp("tfa-key", "123456")

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_the_china_region_is_tracked_separately(self):
        """The block lives at the edge in front of one region."""
        await _service(_response(418, _CAPTCHA_BODY)).login_request("user@example.com", "pw")

        cn = BambuCloudService(region="china", client=MagicMock(spec=httpx.AsyncClient))
        cn._client.post = AsyncMock(return_value=_response(200, {"loginType": "verifyCode"}))
        result = await cn.login_request("user@example.com", "pw")

        cn._client.post.assert_awaited_once()
        assert result["needs_verification"] is True


class TestMakerWorldSharesTheDetector:
    @pytest.mark.asyncio
    async def test_a_challenge_worded_differently_is_still_named(self):
        """MakerWorld used to require the literal word "robot" in the error text
        and reported anything else as an unexplained block."""
        from backend.app.services.makerworld import MakerWorldService, MakerWorldUnavailableError

        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient), auth_token="tok")
        svc._client.get = AsyncMock(return_value=_response(418, {"captchaId": "abc", "error": "verification required"}))

        with pytest.raises(MakerWorldUnavailableError) as exc:
            await svc._get_json("/design/1")

        assert "CAPTCHA" in str(exc.value)
        assert "Open on MakerWorld" in str(exc.value)


class TestTheSupportBundleNamesIt:
    def test_the_warning_we_log_matches_the_signature(self, tmp_path, monkeypatch, caplog):
        """The reporter's bundle came back with zero log-health findings while
        the log was full of the failure -- tie the two ends together."""
        from backend.app.core.config import settings as app_settings
        from backend.app.services.log_health import scan_logs

        svc = _service(_response(418, _CAPTCHA_BODY))
        with caplog.at_level("WARNING", logger="backend.app.services.bambu_cloud"):
            svc._note_captcha(_response(418, _CAPTCHA_BODY))
        logged = caplog.records[-1].getMessage()

        log_file = tmp_path / "bambuddy.log"
        log_file.write_text(
            f"2026-08-08 05:15:37,068 WARNING [backend.app.services.bambu_cloud] {logged}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(app_settings, "log_dir", tmp_path)

        findings = scan_logs().findings

        assert [f.signature_id for f in findings] == ["bambu-cloud-captcha"]
        assert findings[0].wiki_anchor == "bambu-cloud-captcha"

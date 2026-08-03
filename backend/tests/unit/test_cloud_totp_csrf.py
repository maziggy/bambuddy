"""Tests for the CSRF handshake on Bambu Cloud TOTP sign-in (#2696).

Bambu added double-submit CSRF protection to the ``bambulab.com`` web origin,
which is where — and only where — this service posts. Verified against the live
endpoint while diagnosing the report:

    POST /api/sign-in/tfa  (bare)                     403 {"reason":"missing_cookie"}
    GET  /api/csrf                                    204 + Set-Cookie: bbl_csrf_token
    POST /api/sign-in/tfa  (cookie only)              403 {"reason":"missing_header"}
    POST /api/sign-in/tfa  (cookie + x-bbl-csrf-token) 400 {"code":5,"error":"Login failed"}

The last line is the endpoint reaching application logic with a deliberately
invalid key — i.e. CSRF satisfied. Four header spellings were tried;
``x-bbl-csrf-token`` is the only one accepted, so the exact name is pinned here.
Landing on the sign-in page first does not help: it sets only Cloudflare's
``__cf_bm``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.bambu_cloud import BambuCloudService


def _response(status: int, body: str, *, cookies: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.text = body
    response.json.return_value = json.loads(body) if body else {}
    response.cookies = cookies or {}
    return response


def _service(*, csrf_token: str | None = "csrf-abc123", region: str = "global") -> BambuCloudService:
    service = BambuCloudService(region=region)
    client = MagicMock()
    client.get = AsyncMock(return_value=_response(204, ""))
    client.post = AsyncMock(return_value=_response(200, '{"accessToken": "tok"}'))
    jar = MagicMock()
    jar.get.return_value = csrf_token
    client.cookies = jar
    service._client = client
    return service


class TestCsrfHandshake:
    @pytest.mark.asyncio
    async def test_fetches_the_token_before_posting_the_code(self):
        service = _service()

        result = await service.verify_totp("tfa-key", "123456")

        assert result["success"] is True
        service._client.get.assert_awaited_once()
        assert service._client.get.await_args.args[0] == "https://bambulab.com/api/csrf"

    @pytest.mark.asyncio
    async def test_echoes_the_cookie_in_the_x_bbl_csrf_token_header(self):
        service = _service(csrf_token="csrf-abc123")

        await service.verify_totp("tfa-key", "123456")

        headers = service._client.post.await_args.kwargs["headers"]
        # Pinned deliberately: every other spelling tried against the live
        # endpoint still returned "missing_header".
        assert headers["x-bbl-csrf-token"] == "csrf-abc123"

    @pytest.mark.asyncio
    async def test_posts_to_the_tfa_endpoint_with_the_key_and_code(self):
        service = _service()

        await service.verify_totp("tfa-key", "123456")

        assert service._client.post.await_args.args[0] == "https://bambulab.com/api/sign-in/tfa"
        assert service._client.post.await_args.kwargs["json"] == {"tfaKey": "tfa-key", "tfaCode": "123456"}

    @pytest.mark.asyncio
    async def test_uses_the_china_origin_for_the_china_region(self):
        service = _service(region="china")

        await service.verify_totp("tfa-key", "123456")

        assert service._client.get.await_args.args[0] == "https://bambulab.cn/api/csrf"
        assert service._client.post.await_args.args[0] == "https://bambulab.cn/api/sign-in/tfa"

    @pytest.mark.asyncio
    async def test_does_not_post_the_code_when_no_token_could_be_obtained(self):
        service = _service(csrf_token=None)

        result = await service.verify_totp("tfa-key", "123456")

        assert result["success"] is False
        assert "security token" in result["message"]
        # Sending the code without CSRF would burn a one-shot TOTP window on a
        # request Bambu is guaranteed to refuse.
        service._client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failing_csrf_fetch_is_reported_not_swallowed(self):
        service = _service()
        service._client.get = AsyncMock(side_effect=RuntimeError("connection reset"))

        result = await service.verify_totp("tfa-key", "123456")

        assert result["success"] is False
        assert "security token" in result["message"]
        service._client.post.assert_not_awaited()


class TestCsrfRejectionMessage:
    """A CSRF refusal must not read as a wrong code — that misdiagnosis is what
    sent the reporter chasing clock drift and leading-zero parsing."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason", ["missing_cookie", "missing_header"])
    async def test_csrf_rejection_says_the_code_was_never_checked(self, reason):
        service = _service()
        body = json.dumps({"error": f"CSRF error: {reason}", "reason": reason})
        service._client.post = AsyncMock(return_value=_response(403, body))

        result = await service.verify_totp("tfa-key", "123456")

        assert result["success"] is False
        assert "before checking your code" in result["message"]
        assert "Invalid" not in result["message"]

    @pytest.mark.asyncio
    async def test_a_genuinely_wrong_code_still_reports_bambus_own_message(self):
        service = _service()
        service._client.post = AsyncMock(return_value=_response(400, '{"code":5,"error":"Login failed"}'))

        result = await service.verify_totp("tfa-key", "000000")

        assert result["success"] is False
        assert result["message"] == "Login failed"

    @pytest.mark.asyncio
    async def test_expired_session_keeps_its_dedicated_message(self):
        service = _service()
        service._client.post = AsyncMock(return_value=_response(400, '{"message":"tfaKey expired"}'))

        result = await service.verify_totp("tfa-key", "123456")

        assert result["success"] is False
        assert "expired" in result["message"].lower()

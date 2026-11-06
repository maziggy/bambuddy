"""Bambu Cloud being unreachable is a bad gateway, not a server fault.

The cloud routes answered 500 when Bambu Cloud failed (no network, a refused
connection, an error from the cloud), and a login or verification that never
reached Bambu Cloud answered 401 as if the credentials were wrong. Both are an
upstream failure: 502, as the preset lookups already answer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient

from backend.app.services.bambu_cloud import BambuCloudAuthError, BambuCloudError, BambuCloudService

UPSTREAM = BambuCloudError("Request failed: [Errno 101] Network is unreachable")


def _failing_cloud() -> MagicMock:
    cloud = MagicMock()
    cloud.is_authenticated = True
    for method in (
        "get_slicer_settings",
        "get_setting_detail",
        "get_devices",
        "create_setting",
        "update_setting",
        "delete_setting",
    ):
        setattr(cloud, method, AsyncMock(side_effect=UPSTREAM))
    cloud.close = AsyncMock()
    return cloud


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/cloud/settings", None),
        ("GET", "/api/v1/cloud/settings/PFUS123", None),
        ("GET", "/api/v1/cloud/devices", None),
        ("GET", "/api/v1/cloud/firmware-updates", None),
        ("POST", "/api/v1/cloud/settings", {"type": "filament", "name": "N", "base_id": "GFSA00"}),
        ("PUT", "/api/v1/cloud/settings/PFUS123", {"name": "N"}),
        ("DELETE", "/api/v1/cloud/settings/PFUS123", None),
    ],
)
async def test_an_unreachable_cloud_is_a_bad_gateway(async_client: AsyncClient, method, path, body):
    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=AsyncMock(return_value=_failing_cloud())):
        response = await async_client.request(method, path, json=body)

    assert response.status_code == 502, response.text
    assert "Network is unreachable" in response.text


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "body", "call"),
    [
        ("/api/v1/cloud/login", {"email": "a@example.com", "password": "pw"}, "login_request"),
        ("/api/v1/cloud/verify", {"email": "a@example.com", "code": "123456"}, "verify_code"),
    ],
)
async def test_a_login_that_never_reached_the_cloud_is_not_a_bad_password(async_client: AsyncClient, path, body, call):
    """The client really cannot connect: a network error, not wrong credentials."""

    def _unreachable_service(*args, **kwargs):
        service = BambuCloudService(*args, **kwargs)
        service._client.post = AsyncMock(side_effect=httpx.ConnectError("[Errno 101] Network is unreachable"))
        return service

    with patch("backend.app.api.routes.cloud.BambuCloudService", new=_unreachable_service):
        response = await async_client.post(path, json=body)

    assert response.status_code == 502, response.text


@pytest.mark.asyncio
async def test_the_service_reports_a_network_failure_as_a_cloud_error_not_an_auth_error():
    service = BambuCloudService()
    try:
        with patch.object(service._client, "post", new=AsyncMock(side_effect=httpx.ConnectError("unreachable"))):
            for attempt in (service.login_request("a@example.com", "pw"), service.verify_code("a@example.com", "1")):
                with pytest.raises(BambuCloudError) as raised:
                    await attempt
                assert not isinstance(raised.value, BambuCloudAuthError)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_expired_session_is_still_unauthorized(async_client: AsyncClient):
    cloud = _failing_cloud()
    cloud.get_devices = AsyncMock(side_effect=BambuCloudAuthError("Token expired"))

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=AsyncMock(return_value=cloud)):
        response = await async_client.get("/api/v1/cloud/devices")

    assert response.status_code == 401, response.text


def _service_answering(status: int, body: bytes, content_type: str = "text/html"):
    """A BambuCloudService whose requests all get this answer."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    def factory(*args, **kwargs):
        service = BambuCloudService(*args, **kwargs)
        service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return service

    return factory


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, 401), (403, 401), (500, 502), (503, 502)],
)
async def test_a_pasted_token_is_invalid_only_when_bambu_says_so(async_client: AsyncClient, status, expected):
    with patch(
        "backend.app.api.routes.cloud.BambuCloudService", new=_service_answering(status, b"{}", "application/json")
    ):
        response = await async_client.post("/api/v1/cloud/token", json={"access_token": "tok"})

    assert response.status_code == expected, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_pasted_token_with_the_cloud_unreachable_is_a_bad_gateway(async_client: AsyncClient):
    def _unreachable_service(*args, **kwargs):
        service = BambuCloudService(*args, **kwargs)
        service._client.get = AsyncMock(side_effect=httpx.ConnectError("[Errno 101] Network is unreachable"))
        return service

    with patch("backend.app.api.routes.cloud.BambuCloudService", new=_unreachable_service):
        response = await async_client.post("/api/v1/cloud/token", json={"access_token": "tok"})

    assert response.status_code == 502, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "args"),
    [
        ("get_user_profile", ()),
        ("get_slicer_settings", ()),
        ("get_setting_detail", ("PFUS1",)),
        ("create_setting", ()),
        ("update_setting", ("PFUS1", "N")),
        ("delete_setting", ("PFUS1",)),
        ("get_devices", ()),
        ("get_firmware_version", ("DEV1",)),
    ],
)
@pytest.mark.parametrize("status", [200, 503])
async def test_an_html_page_from_the_cloud_is_a_cloud_error(call, args, status):
    """A Cloudflare challenge or an edge error page is not JSON."""
    if call == "delete_setting" and status == 200:
        pytest.skip("a delete answered 200 succeeded; its body is not read")
    service = _service_answering(status, b"<html>Just a moment...</html>")()
    service.set_token("tok")
    try:
        if call == "create_setting":
            attempt = service.create_setting(preset_type="filament", name="N", base_id="GFSA00", setting={})
        else:
            attempt = getattr(service, call)(*args)
        with pytest.raises(BambuCloudError) as raised:
            await attempt
        assert not isinstance(raised.value, BambuCloudAuthError)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "args"),
    [
        ("get_slicer_settings", ()),
        ("get_setting_detail", ("PFUS1",)),
        ("create_setting", ()),
        ("update_setting", ("PFUS1", "N")),
        ("delete_setting", ("PFUS1",)),
        ("get_devices", ()),
        ("get_firmware_version", ("DEV1",)),
    ],
)
async def test_an_expired_session_is_an_auth_error_not_an_outage(call, args):
    """Bambu's expiry answer signs the token out; the caller is told the session
    expired (401 at the routes), not that the cloud failed."""
    service = _service_answering(401, b'{"code":4,"error":"Please login.","message":""}', "application/json")()
    service.set_token("tok")
    try:
        if call == "create_setting":
            attempt = service.create_setting(preset_type="filament", name="N", base_id="GFSA00", setting={})
        else:
            attempt = getattr(service, call)(*args)
        with pytest.raises(BambuCloudAuthError):
            await attempt
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_stray_401_is_not_taken_for_an_expired_session():
    service = _service_answering(401, b'{"code":1,"message":"region"}', "application/json")()
    service.set_token("tok")
    try:
        with pytest.raises(BambuCloudError) as raised:
            await service.get_devices()
        assert not isinstance(raised.value, BambuCloudAuthError)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_expired_session_keeps_the_sign_in_for_the_expired_banner(async_client: AsyncClient, db_session):
    """/cloud/status owns the sign-in: it shows 'sign-in expired' from the
    stored token and its invalid flag, and the region is kept for the next
    sign-in. A cloud view that sees the expiry answers 401 and clears nothing."""
    from backend.app.api.routes.cloud import get_stored_token, store_token

    await store_token(db_session, "tok", "a@example.com", "china", None)
    await db_session.commit()
    cloud = _failing_cloud()
    cloud.get_devices = AsyncMock(side_effect=BambuCloudAuthError("Bambu Cloud session expired"))

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=AsyncMock(return_value=cloud)):
        response = await async_client.get("/api/v1/cloud/devices")

    assert response.status_code == 401, response.text
    db_session.expire_all()
    assert await get_stored_token(db_session, None) == ("tok", "a@example.com", "china")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_expiry_during_the_firmware_check_is_not_an_unknown_version(async_client: AsyncClient):
    cloud = _failing_cloud()
    cloud.get_devices = AsyncMock(
        return_value={"devices": [{"dev_id": "D1", "name": "A"}, {"dev_id": "D2", "name": "B"}]}
    )
    cloud.get_firmware_version = AsyncMock(side_effect=BambuCloudAuthError("Bambu Cloud session expired"))

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=AsyncMock(return_value=cloud)):
        response = await async_client.get("/api/v1/cloud/firmware-updates")

    assert response.status_code == 401, response.text
    assert cloud.get_firmware_version.await_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_pasted_token_blocked_by_cloudflare_is_not_invalid(async_client: AsyncClient):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"<html>blocked</html>", headers={"cf-mitigated": "challenge"})

    def factory(*args, **kwargs):
        service = BambuCloudService(*args, **kwargs)
        service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return service

    with patch("backend.app.api.routes.cloud.BambuCloudService", new=factory):
        response = await async_client.post("/api/v1/cloud/token", json={"access_token": "tok"})

    assert response.status_code == 502, response.text


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("status", [401, 403])
async def test_a_pasted_token_refused_by_an_edge_page_is_not_invalid(async_client: AsyncClient, status):
    """Only Bambu's own (JSON) answer judges the token; an HTML 401/403 from a
    proxy or a Cloudflare access rule does not."""
    with patch(
        "backend.app.api.routes.cloud.BambuCloudService",
        new=_service_answering(status, b"<html>Access denied</html>", "text/html"),
    ):
        response = await async_client.post("/api/v1/cloud/token", json={"access_token": "tok"})

    assert response.status_code == 502, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bambus_own_refusal_of_a_pasted_token_is_invalid(async_client: AsyncClient):
    """Measured: Bambu answers a wrong token on this endpoint with a 401 and an
    empty body (content-length 0)."""
    with patch("backend.app.api.routes.cloud.BambuCloudService", new=_service_answering(401, b"", "text/plain")):
        response = await async_client.post("/api/v1/cloud/token", json={"access_token": "tok"})

    assert response.status_code == 401, response.text
    assert "Invalid token" in response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_pasted_token_with_surrounding_whitespace_is_trimmed(async_client: AsyncClient, db_session):
    from backend.app.api.routes.cloud import get_stored_token

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"uid": 1})

    def factory(*args, **kwargs):
        service = BambuCloudService(*args, **kwargs)
        service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return service

    with patch("backend.app.api.routes.cloud.BambuCloudService", new=factory):
        response = await async_client.post("/api/v1/cloud/token", json={"access_token": "  tok-value \n"})

    assert response.status_code == 200, response.text
    assert seen == ["Bearer tok-value"]
    db_session.expire_all()
    assert (await get_stored_token(db_session, None))[0] == "tok-value"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("token", ["tok\x00value", "tok value", "tök", ""])
async def test_a_token_that_cannot_be_sent_is_invalid_not_an_outage(async_client: AsyncClient, token):
    calls: list[int] = []

    def factory(*args, **kwargs):
        service = BambuCloudService(*args, **kwargs)
        service._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: calls.append(1) or httpx.Response(200, json={}))
        )
        return service

    with patch("backend.app.api.routes.cloud.BambuCloudService", new=factory):
        response = await async_client.post("/api/v1/cloud/token", json={"access_token": token})

    assert response.status_code == 401, response.text
    assert "Invalid token" in response.text
    assert token.strip() == "" or token not in response.text
    assert calls == []

"""Wire contracts for persistent Notify Lock Screen widgets."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.services.notify_client import NotifyClient, NotifyError, notify_credentials


async def test_widgets_create_explicitly_then_use_the_exact_saved_handle():
    requests = []
    widget = {
        "widgetId": "WG123456",
        "content": {"title": "X1C", "value": "62", "unit": "%", "progress": 62},
        "createdAt": "2026-10-07T00:00:00Z",
        "updatedAt": "2026-10-07T00:00:00Z",
    }

    def handle(request):
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200, json={"success": True, "deleted": True, "widgetId": "WG123456"})
        if request.method == "GET" and request.url.path.endswith("ABC12345"):
            return httpx.Response(200, json={"widgets": [widget]})
        return httpx.Response(201 if "new" in request.url.params else 200, json=widget)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = NotifyClient(http)
        assert await client.create_widget("ABC12345", "a+b&c", widget["content"]) == widget
        assert await client.update_widget("WG123456", "a+b&c", {"value": "Idle", "progress": None}) == widget
        assert await client.get_widget("WG123456", "a+b&c") == widget
        assert await client.list_widgets("ABC12345", "a+b&c") == {"widgets": [widget]}
        assert (await client.delete_widget("WG123456", "a+b&c"))["deleted"] is True

    assert requests[0].url.path == "/widgets/ABC12345"
    assert requests[0].url.params["new"] == "1"
    assert requests[0].headers["content-type"] == "application/json"
    assert json.loads(requests[0].content) == widget["content"]
    assert requests[1].url.path == requests[2].url.path == requests[4].url.path == "/widgets/WG123456"
    assert json.loads(requests[1].content) == {"value": "Idle", "progress": None}
    for request in requests[1:]:
        assert dict(request.url.params) == {"token": "a+b&c"}
    assert [r.method for r in requests] == ["POST", "POST", "GET", "GET", "DELETE"]
    assert not requests[2].content and not requests[3].content and not requests[4].content


async def test_pasted_credentials_are_normalized_for_activity_and_widget_requests():
    requests = []

    def handle(request):
        requests.append(request)
        key, handle = (
            ("widgetId", "WG123456") if request.url.path.startswith("/widgets/") else ("activityId", "LA123456")
        )
        return httpx.Response(200, json={key: handle})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = NotifyClient(http)
        await client.start_activity("  IO12345678901234\n", "  a+b&c\n", {"title": "X1C"})
        await client.update_activity("LA123456", "  a+b&c\n", {"progress": 62})
        await client.create_widget("  IO12345678901234\n", "  a+b&c\n", {"title": "X1C"})
        await client.update_widget("WG123456", "  a+b&c\n", {"progress": 62})
    assert requests[0].url.path == "/live-activity/IO12345678901234"
    assert requests[2].url.path == "/widgets/IO12345678901234"
    assert all(request.url.params["token"] == "a+b&c" for request in requests)


@pytest.mark.parametrize(("status", "body"), [(404, {}), (200, {"type": "group"}), (200, {"success": True})])
async def test_ambiguous_la_id_cannot_update_an_unowned_activity(status, body):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(NotifyError):
            await NotifyClient(http).start_activity(" LA123456 ", "secret", {"title": "X1C"})
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/link"
    assert requests[0].url.params["id"] == "LA123456"


async def test_legacy_la_prefixed_device_can_start_after_identity_verification():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/link":
            return httpx.Response(200, json={"type": "device", "id": "LA123456"})
        return httpx.Response(200, json={"activityId": "LA654321"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await NotifyClient(http).start_activity("LA123456", "secret", {"title": "X1C"})
    assert result["activityId"] == "LA654321"
    assert [r.method for r in requests] == ["GET", "POST"]
    assert requests[1].url.path == "/live-activity/LA123456"
    assert requests[1].url.params["new"] == "1"


@pytest.mark.parametrize("method", ["update_widget", "get_widget", "delete_widget"])
@pytest.mark.parametrize("widget_id", ["ABC12345", "WGbad123", "WG12345", "WG1234567", "../WG123456", None])
async def test_widget_operations_reject_device_dialect_and_invalid_handles(method, widget_id):
    http = AsyncMock(spec=httpx.AsyncClient)
    args = (widget_id, "secret", {}) if method == "update_widget" else (widget_id, "secret")
    with pytest.raises(NotifyError, match="precise widget ID"):
        await getattr(NotifyClient(http), method)(*args)
    http.request.assert_not_called()


@pytest.mark.parametrize("body", [{}, [], {"success": True}, {"widgetId": "ABC12345"}, {"widgetId": "bad/id"}])
async def test_create_without_a_precise_handle_is_uncertain(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(201, json=body))) as http:
        with pytest.raises(NotifyError) as error:
            await NotifyClient(http).create_widget("ABC12345", "secret", {"title": "X1C"})
    assert error.value.delivery_state == "unknown"


@pytest.mark.parametrize(
    "body",
    [{"success": True}, {"widgets": None}, {"widgets": {}}, {"widgets": [None]}, {"widgets": [{"widgetId": "bad"}]}],
)
async def test_malformed_widget_list_cannot_prove_that_owned_widget_was_deleted(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as http:
        with pytest.raises(NotifyError, match="invalid widget list"):
            await NotifyClient(http).list_widgets("ABC12345", "secret")


async def test_empty_authenticated_widget_list_is_valid():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"widgets": []}))
    ) as http:
        assert await NotifyClient(http).list_widgets("ABC12345", "secret") == {"widgets": []}


@pytest.mark.parametrize(
    ("status", "body", "delivery", "widget_id", "retry"),
    [
        (500, {}, "unknown", None, None),
        (502, {"widgetId": "WG123456"}, "unknown", "WG123456", None),
        (503, {}, None, None, None),
        (429, {"retryAfterSeconds": 180}, None, None, 180),
        (502, {"deliveryState": "not-delivered", "retryAfterSeconds": 60}, "not-delivered", None, 60),
        (400, {"message": "This device already has the maximum of 10 widgets. Delete one first."}, None, None, None),
        (403, {}, None, None, None),
    ],
)
async def test_create_preserves_recovery_hints_without_retrying(status, body, delivery, widget_id, retry):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={**body, "private": "secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(NotifyError) as error:
            await NotifyClient(http).create_widget("ABC12345", "secret", {"title": "X1C"})
    assert len(requests) == 1
    assert error.value.status_code == status
    assert error.value.delivery_state == delivery
    assert error.value.widget_id == widget_id
    assert error.value.retry_after_seconds == retry
    assert "secret" not in str(error.value)
    assert "Live Activity" not in str(error.value)
    if status == 400:
        assert "ten widget limit" in str(error.value)


async def test_widget_create_timeout_is_uncertain_and_does_not_expose_token():
    def handle(request):
        raise httpx.ReadTimeout(f"Timed out: {request.url}", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(NotifyError) as error:
            await NotifyClient(http).create_widget("ABC12345", "secret", {"title": "X1C"})
    assert error.value.delivery_state == "unknown"
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("device_id", ["GRP12345", "WB12345678901234", "MC12345678901234"])
def test_bambuddy_widgets_require_ios_while_push_remains_available(device_id):
    config = {"device_id": device_id, "token": "secret", "lock_screen_widgets": True}
    with pytest.raises(NotifyError, match="Lock Screen widgets require an iOS device ID"):
        notify_credentials(config)
    config["lock_screen_widgets"] = False
    assert notify_credentials(config) == (device_id, "secret")


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, []])
def test_widget_switch_requires_a_boolean(value):
    with pytest.raises(NotifyError, match="lock_screen_widgets must be a boolean"):
        notify_credentials({"device_id": "ABC12345", "token": "secret", "lock_screen_widgets": value})


@pytest.mark.parametrize("device_id", ["ABC12345", "IO12345678901234"])
def test_widgets_can_be_enabled_independently_of_live_activities(device_id):
    assert notify_credentials(
        {"device_id": device_id, "token": "secret", "lock_screen_widgets": True, "live_activities": False}
    ) == (device_id, "secret")

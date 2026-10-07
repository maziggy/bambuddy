"""Notify wire contracts, including failures that must not create duplicate tiles."""

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.app.models.notification import NotificationProvider
from backend.app.schemas.notification import NotificationProviderCreate
from backend.app.services.notification_service import NotificationService
from backend.app.services.notify_client import NotifyClient, NotifyError, notify_credentials


@pytest.mark.parametrize(
    "device_id", ["ABC12345", "IO12345678901234", "WB12345678901234", "MC12345678901234", "GRP12345"]
)
async def test_push_uses_unified_json_endpoint(device_id):
    def handle(request):
        assert request.method == "POST"
        assert request.url.path == f"/notify-json/{device_id}"
        assert request.url.params["token"] == "a+b&c"
        assert request.headers["content-type"] == "application/json"
        expected = {
            "title": "Print complete",
            "text": "Pièce terminée 🖨",
            "groupType": "bambuddy",
            "iconUrl": "https://example.com/icon.png",
            "imageUrl": "https://example.com/photo.jpg",
        }
        if device_id.startswith(("WB", "GRP")):
            expected.pop("imageUrl")
        assert json.loads(request.content) == expected
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await NotifyClient(http).send_notification(
            device_id,
            "a+b&c",
            title="Print complete",
            text="Pièce terminée 🖨",
            group_type="bambuddy",
            icon_url="https://example.com/icon.png",
            image_url="https://example.com/photo.jpg",
        )


async def test_photo_on_http_only_installation_still_delivers_text():
    def handle(request):
        assert "imageUrl" not in json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await NotifyClient(http).send_notification(
            "ABC12345", "secret", title="Done", text="Done", image_url="http://lan/photo"
        )


async def test_activity_start_is_explicit_and_updates_use_precise_id():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"success": True, "activityId": "LA123456", "pushed": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = NotifyClient(http)
        await client.start_activity("ABC12345", "secret", {"title": "Printer", "endsIn": 3600})
        result = await client.update_activity("LA123456", "secret", {"progress": 55, "endsIn": None})
        await client.end_activity("LA123456", "secret", {"progress": 100, "keepFor": 300})
    assert requests[0].url.params["new"] == "1"
    assert requests[1].url.path == requests[2].url.path == "/live-activity/LA123456"
    assert "new" not in requests[1].url.params
    assert json.loads(requests[1].content)["endsIn"] is None
    assert requests[2].method == "DELETE"
    assert result["pushed"] is False  # Stored while the tile token rotates: not a failed update.


@pytest.mark.parametrize(
    ("status", "body", "headers", "retry", "activity_id"),
    [
        (502, {"deliveryState": "unknown", "activityId": "LA123456"}, {}, None, "LA123456"),
        (502, {"deliveryState": "not-delivered", "retryAfterSeconds": 900}, {}, 900, None),
        (429, {"retryAfterSeconds": 1043, "openingTheAppMayHelp": False}, {}, 1043, None),
        (429, {}, {"Retry-After": "60"}, 60, None),
        (400, {}, {}, None, None),
        (410, {"endReason": "dismissed"}, {}, None, None),
    ],
)
async def test_gateway_recovery_hints_are_preserved_without_retrying(status, body, headers, retry, activity_id):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={**body, "message": "secret"}, headers=headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(NotifyError) as error:
            await NotifyClient(http).start_activity("ABC12345", "secret", {"title": "Printer"})
    assert len(calls) == 1
    assert error.value.status_code == status
    assert error.value.retry_after_seconds == retry
    assert error.value.activity_id == activity_id
    assert error.value.delivery_state == body.get("deliveryState")
    assert "secret" not in str(error.value)


async def test_transport_exception_is_opaque_and_start_is_uncertain():
    def handle(request):
        raise httpx.ReadTimeout(f"Timed out: {request.url}", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(NotifyError) as error:
            await NotifyClient(http).start_activity("ABC12345", "secret", {"title": "Printer"})
    assert error.value.delivery_state == "unknown"
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


async def test_httpx_logs_redact_query_token(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"success": True}))
    ) as http:
        await NotifyClient(http).send_notification("ABC12345", "top-secret", title="Done", text="Done")
    assert "top-secret" not in caplog.text
    assert "token=[REDACTED]" in caplog.text


async def test_end_never_accepts_device_dialect():
    http = AsyncMock(spec=httpx.AsyncClient)
    with pytest.raises(NotifyError, match="precise activity ID"):
        await NotifyClient(http).end_activity("ABC12345", "secret")
    http.request.assert_not_called()


@pytest.mark.parametrize("body", [{}, [], {"success": True}, {"success": True, "activityId": "bad/id"}])
async def test_start_missing_handle_is_uncertain(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as http:
        with pytest.raises(NotifyError) as error:
            await NotifyClient(http).start_activity("ABC12345", "secret", {"title": "Printer"})
    assert error.value.delivery_state == "unknown"


@pytest.mark.parametrize("body", [{"success": False}, {"success": True, "failureCount": 2}, {"message": "ok"}])
async def test_push_does_not_claim_delivery_for_application_errors_or_partial_groups(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as http:
        with pytest.raises(NotifyError):
            await NotifyClient(http).send_notification("GRP12345", "secret", title="Done", text="Done")


async def test_rejected_browser_push_does_not_suggest_live_activity_setup():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"error": "bad"}))
    ) as http:
        with pytest.raises(NotifyError) as error:
            await NotifyClient(http).send_notification(
                "WB12345678901234", "secret", title="Error", text="Printer error"
            )
    assert "Live Activity" not in str(error.value)
    assert "device registration" in str(error.value)


@pytest.mark.parametrize(
    "config",
    [
        {"device_id": "../../etc", "token": "secret"},
        {"device_id": "ABC12345", "token": ""},
        {"device_id": "GRP12345", "token": "secret", "live_activities": True},
    ],
)
def test_invalid_credentials_and_group_live_activities_are_rejected(config):
    with pytest.raises(NotifyError):
        notify_credentials(config)


@pytest.mark.parametrize(
    "extra",
    [
        {"live_activities": "false"},
        {"live_activity_privacy": "true"},
        {"icon_url": "http://example.com/icon.png"},
        {"live_activity_button_url": "https://user:secret@example.com"},
        {"live_activity_style": "broken"},
        {"live_activity_tint": "green"},
        {"live_activity_metrics": "layers"},
        {"live_activity_metrics": [["layers"]]},
        {"device_id": "WB12345678901234", "live_activities": True},
        {"device_id": "MC12345678901234", "live_activities": True},
    ],
)
def test_invalid_display_config_is_rejected_before_it_reaches_worker(extra):
    with pytest.raises(NotifyError):
        notify_credentials({"device_id": "ABC12345", "token": "secret", **extra})


@pytest.mark.parametrize("device_id", ["GRP12345", "WB12345678901234", "MC12345678901234"])
def test_non_ios_targets_remain_eligible_for_push_notifications(device_id):
    assert notify_credentials({"device_id": device_id, "token": "secret", "live_activities": False}) == (
        device_id,
        "secret",
    )


async def test_notify_provider_dispatch_and_test_use_existing_photo_toggle():
    service = NotificationService()
    provider = NotificationProvider(
        name="Notify",
        provider_type="notify",
        config=json.dumps({"device_id": "ABC12345", "token": "secret"}),
        attach_photo=False,
        quiet_hours_enabled=False,
    )
    with patch.object(service, "_send_notify", new_callable=AsyncMock, return_value=(True, "sent")) as send:
        await service._send_to_provider(provider, "Title", "Body", image_data=b"image")
        send.assert_awaited_once_with(
            json.loads(provider.config), "Title", "Body", image_url=None, event_type=None, printer_id=None
        )
        send.reset_mock()
        await service.send_test_notification("notify", json.loads(provider.config), attach_photo=False)
        assert send.await_count == 1
        assert send.call_args.kwargs["image_url"] is None


@pytest.mark.parametrize("device_id", ["GRP12345", "WB12345678901234", "grp12345", "wb12345678901234"])
async def test_browser_and_group_error_alerts_never_publish_or_reuse_camera_photos(device_id):
    service = NotificationService()
    config = {"device_id": device_id, "token": "secret"}
    provider = NotificationProvider(
        name="Notify", provider_type="notify", config=json.dumps(config), attach_photo=True, quiet_hours_enabled=False
    )
    with (
        patch.object(service, "_send_notify", new_callable=AsyncMock, return_value=(True, "sent")) as send,
        patch.object(service, "_get_or_build_photo_url", new_callable=AsyncMock) as photo,
        patch("backend.app.services.notification_service._load_sample_notification_image") as sample,
    ):
        await service._send_to_provider(
            provider,
            "Printer error",
            "Filament runout",
            image_data=b"camera",
            event_type="printer_error",
            photo_cache={"url": "https://example.com/camera.jpg"},
            printer_id=42,
        )
        assert send.call_args.kwargs["image_url"] is None
        await service.send_test_notification("notify", config, attach_photo=True)
        assert send.call_args.kwargs["image_url"] is None
    photo.assert_not_awaited()
    sample.assert_not_called()


async def test_ios_error_alert_includes_camera_photo_when_enabled():
    service = NotificationService()
    config = {"device_id": "IO12345678901234", "token": "secret"}
    provider = NotificationProvider(
        name="Notify", provider_type="notify", config=json.dumps(config), attach_photo=True, quiet_hours_enabled=False
    )
    with (
        patch.object(service, "_send_notify", new_callable=AsyncMock, return_value=(True, "sent")) as send,
        patch.object(
            service, "_get_or_build_photo_url", new_callable=AsyncMock, return_value="https://example.com/camera.jpg"
        ) as photo,
    ):
        await service._send_to_provider(
            provider,
            "Printer error",
            "Filament runout",
            image_data=b"camera",
            event_type="printer_error",
            printer_id=42,
        )
        photo.assert_awaited_once()
        assert send.call_args.kwargs["image_url"] == "https://example.com/camera.jpg"


async def test_notify_push_still_respects_quiet_hours():
    service = NotificationService()
    provider = NotificationProvider(name="Notify", provider_type="notify", config="{}")
    with (
        patch.object(service, "_is_in_quiet_hours", return_value=True),
        patch.object(service, "_send_notify", new_callable=AsyncMock) as send,
    ):
        result = await service._send_to_provider(provider, "Title", "Body")
    send.assert_not_awaited()
    assert result == (True, "Skipped - quiet hours")


def test_provider_schema_accepts_notify():
    provider = NotificationProviderCreate(
        name="Notify", provider_type="notify", config={"device_id": "ABC12345", "token": "x"}
    )
    assert provider.provider_type == "notify"


@pytest.mark.parametrize(
    ("event", "time_sensitive"),
    [
        ("print_failed", True),
        ("print_stopped", True),
        ("printer_error", True),
        ("print_complete", False),
        (None, False),
    ],
)
async def test_problem_alert_priority_and_per_printer_thread(event, time_sensitive):
    service = NotificationService()
    config = {"device_id": "ABC12345", "token": "secret", "time_sensitive": True}
    with patch.object(NotifyClient, "send_notification", new_callable=AsyncMock) as send:
        try:
            success, _ = await service._send_notify(config, "Title", "Body", event_type=event, printer_id=42)
        finally:
            await service.close()
    assert success
    assert send.call_args.kwargs["time_sensitive"] is time_sensitive
    assert send.call_args.kwargs["group_type"] == "bambuddy-printer-42"

"""Notify configuration validation and lifecycle cleanup through the public API."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.notification_lock_screen_widget import NotificationLockScreenWidget
from backend.app.services.notify_live_activities import _credential_key

CONFIG = {
    "device_id": "IO12345678901234",
    "token": "example-token",
    "live_activities": True,
    "lock_screen_widgets": True,
}


async def create_provider(client):
    response = await client.post(
        "/api/v1/notifications/", json={"name": "Notify", "provider_type": "notify", "config": CONFIG}
    )
    assert response.status_code == 200
    return response.json()["id"]


async def test_notify_settings_round_trip_and_push_flags_remain_independent(async_client):
    provider_id = await create_provider(async_client)
    response = await async_client.get(f"/api/v1/notifications/{provider_id}")
    assert response.json()["config"] == CONFIG
    assert response.json()["on_print_start"] is False
    assert response.json()["on_print_progress"] is False


@pytest.mark.parametrize(
    "config",
    [
        {},
        {**CONFIG, "device_id": "GRP12345"},
        {**CONFIG, "device_id": "WB12345678901234"},
        {**CONFIG, "live_activities": "false"},
        {**CONFIG, "live_activities": False, "lock_screen_widgets": "false"},
        {**CONFIG, "device_id": "GRP12345", "live_activities": False},
        {**CONFIG, "device_id": "WB12345678901234", "live_activities": False},
    ],
)
async def test_invalid_notify_configuration_cannot_be_created(async_client, config):
    response = await async_client.post(
        "/api/v1/notifications/", json={"name": "Notify", "provider_type": "notify", "config": config}
    )
    assert response.status_code == 422


async def test_empty_config_update_is_rejected_and_keeps_previous_credentials(async_client):
    provider_id = await create_provider(async_client)
    response = await async_client.patch(f"/api/v1/notifications/{provider_id}", json={"config": {}})
    assert response.status_code == 422
    saved = await async_client.get(f"/api/v1/notifications/{provider_id}")
    assert saved.json()["config"] == CONFIG


async def test_credential_change_ends_old_device_tiles_with_old_credentials(async_client):
    provider_id = await create_provider(async_client)
    with (
        patch(
            "backend.app.api.routes.notifications.notify_live_activities.cleanup_provider", new_callable=AsyncMock
        ) as cleanup,
        patch(
            "backend.app.api.routes.notifications.notify_widgets.cleanup_provider", new_callable=AsyncMock
        ) as widget_cleanup,
    ):
        response = await async_client.patch(
            f"/api/v1/notifications/{provider_id}", json={"config": {**CONFIG, "token": "replacement-token"}}
        )
    assert response.status_code == 200
    cleanup.assert_awaited_once_with(provider_id, CONFIG)
    widget_cleanup.assert_awaited_once_with(provider_id, CONFIG)
    assert response.json()["config"]["token"] == "replacement-token"


async def test_disabling_or_changing_push_thread_leaves_cleanup_to_durable_worker(async_client):
    provider_id = await create_provider(async_client)
    with (
        patch(
            "backend.app.api.routes.notifications.notify_live_activities.cleanup_provider", new_callable=AsyncMock
        ) as cleanup,
        patch(
            "backend.app.api.routes.notifications.notify_widgets.cleanup_provider", new_callable=AsyncMock
        ) as widget_cleanup,
    ):
        response = await async_client.patch(
            f"/api/v1/notifications/{provider_id}",
            json={"enabled": False, "config": {**CONFIG, "group_type": "workshop"}},
        )
    assert response.status_code == 200
    cleanup.assert_not_awaited()
    widget_cleanup.assert_not_awaited()


async def test_delete_ends_tiles_before_forgetting_credentials(async_client):
    provider_id = await create_provider(async_client)
    with (
        patch(
            "backend.app.api.routes.notifications.notify_live_activities.cleanup_provider", new_callable=AsyncMock
        ) as cleanup,
        patch(
            "backend.app.api.routes.notifications.notify_widgets.cleanup_provider", new_callable=AsyncMock
        ) as widget_cleanup,
    ):
        response = await async_client.delete(f"/api/v1/notifications/{provider_id}")
    assert response.status_code == 200
    cleanup.assert_awaited_once_with(provider_id, CONFIG)
    widget_cleanup.assert_awaited_once_with(provider_id, CONFIG)
    assert (await async_client.get(f"/api/v1/notifications/{provider_id}")).status_code == 404


async def test_lock_screen_widgets_can_be_enabled_without_live_activities_or_push_events(async_client):
    config = {**CONFIG, "live_activities": False}
    response = await async_client.post(
        "/api/v1/notifications/",
        json={
            "name": "Printer widget",
            "provider_type": "notify",
            "config": config,
            "on_print_start": False,
            "on_print_complete": False,
            "on_print_failed": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["config"] == config
    assert response.json()["on_print_start"] is False
    assert response.json()["on_print_complete"] is False
    assert response.json()["on_print_failed"] is False


async def test_switching_provider_type_cleans_up_both_owned_notify_resources(async_client):
    provider_id = await create_provider(async_client)
    with (
        patch(
            "backend.app.api.routes.notifications.notify_live_activities.cleanup_provider", new_callable=AsyncMock
        ) as cleanup,
        patch(
            "backend.app.api.routes.notifications.notify_widgets.cleanup_provider", new_callable=AsyncMock
        ) as widget_cleanup,
    ):
        response = await async_client.patch(
            f"/api/v1/notifications/{provider_id}",
            json={"provider_type": "ntfy", "config": {"topic": "prints"}},
        )
    assert response.status_code == 200
    cleanup.assert_awaited_once_with(provider_id, CONFIG)
    widget_cleanup.assert_awaited_once_with(provider_id, CONFIG)


@pytest.mark.parametrize("disable", [{"config": {**CONFIG, "lock_screen_widgets": False}}, {"enabled": False}])
async def test_quick_widget_disable_and_reenable_preserves_cleanup_intent(async_client, db_session, disable):
    provider_id = await create_provider(async_client)
    row = NotificationLockScreenWidget(
        provider_id=provider_id,
        printer_id=42,
        credential_key=_credential_key(CONFIG),
        widget_id="WG123456",
        state="active",
        content="{}",
    )
    db_session.add(row)
    await db_session.commit()
    response = await async_client.patch(f"/api/v1/notifications/{provider_id}", json=disable)
    assert response.status_code == 200
    await db_session.refresh(row)
    assert row.state == "deleting"
    response = await async_client.patch(
        f"/api/v1/notifications/{provider_id}", json={"config": CONFIG, "enabled": True}
    )
    assert response.status_code == 200
    await db_session.refresh(row)
    assert row.state == "deleting"
    assert row.widget_id == "WG123456"

"""OctoEverywhere uses the existing printer controls and AI event subscription."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.services.octoeverywhere_actions import execute_action


@pytest.mark.parametrize("action", ["notify", "pause", "pause_and_off"])
async def test_dispatches_action_with_quality_without_fake_confidence(action):
    with patch("backend.app.services.octoeverywhere_actions.execute_detection_action", new_callable=AsyncMock) as run:
        await execute_action(1, action, "benchy", 2)
    run.assert_awaited_once_with(1, action, "benchy", None, print_quality=2)


async def test_pause_and_power_off_also_notify():
    from backend.app.services.obico_actions import execute_action as run

    with (
        patch("backend.app.services.obico_actions._get_printer_name", return_value="Printer"),
        patch("backend.app.services.obico_actions._pause_print") as pause,
        patch("backend.app.services.obico_actions._turn_off_linked_plugs", new_callable=AsyncMock) as off,
        patch("backend.app.services.obico_actions._notify", new_callable=AsyncMock) as notify,
    ):
        await run(1, "pause_and_off", "benchy", None, print_quality=1)
    pause.assert_called_once_with(1)
    off.assert_awaited_once_with(1)
    notify.assert_awaited_once_with(1, "Printer", "benchy", None, "pause_and_off", None, print_quality=1)


async def test_notification_shows_quality_and_preserves_event_subscription():
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    db = MagicMock()
    template = NotificationTemplate(**next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "ai_failure_detection"))
    original_body = template.body_template
    with (
        patch.object(service, "_get_providers_for_event", return_value=["provider"]) as providers,
        patch.object(service, "_get_template", return_value=template),
        patch.object(service, "_send_to_providers", new_callable=AsyncMock) as send,
    ):
        await service.on_ai_failure_detection(1, "Printer", "benchy", None, "notify", db, print_quality=2)
    providers.assert_awaited_once_with(db, "on_ai_failure_detection", 1)
    assert send.await_args.args[1] == "Possible Print Failure Detected"
    assert send.await_args.args[2] == "Printer: benchy\nOctoEverywhere print quality: 2/10\nAction taken: notify"
    assert send.await_args.kwargs["variables"]["confidence"] == "N/A"
    assert template.body_template == original_body


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Please check {printer}.", "Please check Printer."),
        ("{provider}: {task_name}, quality {print_quality}", "OctoEverywhere: benchy, quality 2/10"),
    ],
)
async def test_notification_preserves_custom_title_and_body(body, expected):
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    template = NotificationTemplate(
        event_type="ai_failure_detection",
        name="AI Failure Detection",
        title_template="Check {printer}",
        body_template=body,
    )
    with (
        patch.object(service, "_get_providers_for_event", return_value=["provider"]),
        patch.object(service, "_get_template", return_value=template),
        patch.object(service, "_send_to_providers", new_callable=AsyncMock) as send,
    ):
        await service.on_ai_failure_detection(1, "Printer", "benchy", None, "notify", MagicMock(), print_quality=2)
    assert send.await_args.args[1:3] == ("Check Printer", expected)
    assert template.body_template == body


async def test_obico_default_notification_keeps_confidence():
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    template = NotificationTemplate(**next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "ai_failure_detection"))
    with (
        patch.object(service, "_get_providers_for_event", return_value=["provider"]),
        patch.object(service, "_get_template", return_value=template),
        patch.object(service, "_send_to_providers", new_callable=AsyncMock) as send,
    ):
        await service.on_ai_failure_detection(1, "Printer", "benchy", 0.85, "pause", MagicMock())
    assert send.await_args.args[2] == "Printer: benchy\nConfidence: 0.85\nAction taken: pause"


async def test_ai_notification_template_preview_includes_quality_variables():
    from backend.app.api.routes.notification_templates import preview_template
    from backend.app.schemas.notification_template import EVENT_VARIABLES, EventType, TemplatePreviewRequest

    assert {"provider", "print_quality", "task_name", "confidence", "action"} <= set(
        EVENT_VARIABLES[EventType.AI_FAILURE_DETECTION]
    )
    result = await preview_template(
        TemplatePreviewRequest(
            event_type=EventType.AI_FAILURE_DETECTION,
            title_template="{provider}: {printer}",
            body_template="{task_name}: {print_quality}, {confidence}, {action}",
        )
    )
    assert result.title == "OctoEverywhere: Bambu X1C"
    assert result.body == "Benchy.3mf: 2/10, N/A, pause"

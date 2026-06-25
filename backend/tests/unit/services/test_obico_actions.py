"""Regression tests for obico_actions (#1794).

Before #1794, `obico_actions._notify` routed AI failure-detection events
through `notification_service.on_printer_error`, multiplexing them with
HMS hardware errors. Users couldn't subscribe to one without the other,
and the reporter on #1794 found that turning OFF the "Printer Error"
toggle on a Discord provider silently disabled spaghetti alerts too.

This file pins the post-#1794 wiring: `execute_action` calls
`on_ai_failure_detection`, not `on_printer_error`.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.obico_actions import execute_action


@asynccontextmanager
async def _fake_session(printer):
    result = SimpleNamespace(scalar_one_or_none=lambda: printer)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    yield session


@pytest.fixture
def fake_printer():
    return SimpleNamespace(id=7, name="X1 Carbon")


@pytest.fixture(autouse=True)
def _patch_session(fake_printer):
    with patch("backend.app.services.obico_actions.async_session", lambda: _fake_session(fake_printer)):
        yield


async def test_notify_routes_to_on_ai_failure_detection(fake_printer):
    """Regression guard for #1794: action='notify' must call
    on_ai_failure_detection, not on_printer_error. If anyone reverts the
    handoff, the reporter's symptom (Discord silent when "Printer Error"
    is OFF and "AI Failure Detection" is ON) returns."""
    with (
        patch(
            "backend.app.services.notification_service.notification_service.on_ai_failure_detection",
            new_callable=AsyncMock,
        ) as mock_ai,
        patch(
            "backend.app.services.notification_service.notification_service.on_printer_error",
            new_callable=AsyncMock,
        ) as mock_err,
    ):
        await execute_action(
            printer_id=fake_printer.id,
            action="notify",
            task_name="benchy.3mf",
            score=0.91,
        )

        mock_ai.assert_awaited_once()
        mock_err.assert_not_awaited()  # the bug the user reported

        call_kwargs = mock_ai.await_args.kwargs
        assert call_kwargs["printer_id"] == fake_printer.id
        assert call_kwargs["printer_name"] == fake_printer.name
        assert call_kwargs["task_name"] == "benchy.3mf"
        assert call_kwargs["confidence"] == 0.91
        assert call_kwargs["action"] == "notify"


async def test_pause_action_still_pauses_and_notifies(fake_printer):
    """`pause` calls pause_print AND fires the AI notification — the
    notification fan-out shape isn't different for the pause action."""
    fake_client = SimpleNamespace(pause_print=lambda: True)

    with (
        patch(
            "backend.app.services.printer_manager.printer_manager.get_client",
            return_value=fake_client,
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_ai_failure_detection",
            new_callable=AsyncMock,
        ) as mock_ai,
    ):
        await execute_action(
            printer_id=fake_printer.id,
            action="pause",
            task_name="benchy.3mf",
            score=0.5,
        )

        mock_ai.assert_awaited_once()
        assert mock_ai.await_args.kwargs["action"] == "pause"


async def test_notify_swallows_notification_service_exceptions(fake_printer):
    """Notification failure must not propagate — Obico's detection loop
    keeps polling; one transient Discord blip shouldn't kill it."""
    with patch(
        "backend.app.services.notification_service.notification_service.on_ai_failure_detection",
        new_callable=AsyncMock,
        side_effect=RuntimeError("discord 502"),
    ):
        # Must not raise.
        await execute_action(
            printer_id=fake_printer.id,
            action="notify",
            task_name="benchy.3mf",
            score=0.91,
        )

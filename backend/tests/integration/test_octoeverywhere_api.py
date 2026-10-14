"""Settings, permissions, and status routes for OctoEverywhere detection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.printer_scope import ALL_PRINTERS, PrinterScope
from backend.app.services.octoeverywhere_detection import octoeverywhere_detection_service as service


@pytest.fixture(autouse=True)
def clear_detection_state():
    service._states.clear()
    service._history.clear()
    service._last_error = None
    service._last_error_code = None
    yield
    service._states.clear()
    service._history.clear()
    service._last_error = None
    service._last_error_code = None


@pytest.mark.integration
async def test_settings_round_trip_and_provider_switch(async_client):
    settings = (await async_client.get("/api/v1/settings/")).json()
    assert settings["octoeverywhere_enabled"] is False
    assert settings["octoeverywhere_api_key"] == ""
    assert settings["octoeverywhere_api_key_configured"] is False
    assert settings["octoeverywhere_confidence"] == "medium"
    assert settings["octoeverywhere_poll_interval"] == 20
    assert settings["octoeverywhere_action"] == "notify"

    await async_client.put("/api/v1/settings/", json={"obico_enabled": True})
    response = await async_client.put(
        "/api/v1/settings/",
        json={
            "octoeverywhere_enabled": True,
            "octoeverywhere_api_key": "  test-key  ",
            "octoeverywhere_confidence": "high",
            "octoeverywhere_action": "pause",
            "octoeverywhere_enabled_printers": "[3, 1]",
            "octoeverywhere_poll_interval": 15,
        },
    )
    assert response.status_code == 200
    assert response.json()["octoeverywhere_api_key"] == ""
    assert response.json()["octoeverywhere_api_key_configured"] is True
    assert response.json()["octoeverywhere_poll_interval"] == 15
    assert response.json()["obico_enabled"] is False
    status = (await async_client.get("/api/v1/octoeverywhere/status")).json()
    assert status["enabled"] is True
    assert status["api_key_configured"] is True
    assert status["confidence"] == "high"
    assert status["action"] == "pause"
    assert status["poll_interval"] == 15
    assert "test-key" not in str(status)
    cards = (await async_client.get("/api/v1/octoeverywhere/printer-status")).json()
    assert cards["monitored_printers"] == [1, 3]
    for key in ("api_key", "api_key_configured", "action", "history", "confidence"):
        assert key not in cards

    response = await async_client.put("/api/v1/settings/", json={"obico_enabled": True})
    assert response.json()["octoeverywhere_enabled"] is False
    assert response.json()["octoeverywhere_api_key"] == ""
    assert response.json()["octoeverywhere_api_key_configured"] is True
    assert response.json()["octoeverywhere_poll_interval"] == 15


@pytest.mark.integration
async def test_write_only_key_preserved_replaced_and_removed(async_client):
    await async_client.put("/api/v1/settings/", json={"octoeverywhere_api_key": "first-key"})
    for payload in ({"octoeverywhere_confidence": "low"}, {"octoeverywhere_api_key": None}):
        response = await async_client.put("/api/v1/settings/", json=payload)
        assert response.json()["octoeverywhere_api_key"] == ""
        assert response.json()["octoeverywhere_api_key_configured"] is True
        assert (await service._load_settings())["api_key"] == "first-key"
    await async_client.put("/api/v1/settings/", json={"octoeverywhere_api_key": "replacement-key"})
    settings = (await async_client.get("/api/v1/settings/")).json()
    assert settings["octoeverywhere_api_key"] == ""
    assert settings["octoeverywhere_api_key_configured"] is True
    assert (await service._load_settings())["api_key"] == "replacement-key"
    response = await async_client.put("/api/v1/settings/", json={"octoeverywhere_api_key": ""})
    assert response.json()["octoeverywhere_api_key_configured"] is False
    assert (await service._load_settings())["api_key"] == ""


@pytest.mark.integration
async def test_fixed_interval_round_trip(async_client):
    for interval in (5, 30, 20):
        response = await async_client.put("/api/v1/settings/", json={"octoeverywhere_poll_interval": interval})
        assert response.status_code == 200
        assert response.json()["octoeverywhere_poll_interval"] == interval
        settings = (await async_client.get("/api/v1/settings/")).json()
        assert settings["octoeverywhere_poll_interval"] == interval
        assert (await service._load_settings())["poll_interval"] == interval


@pytest.mark.integration
@pytest.mark.parametrize("confidence", ["lowest", "low", "medium", "high", "highest"])
async def test_confidence_round_trip_and_connection_validation(async_client, confidence):
    response = await async_client.put(
        "/api/v1/settings/", json={"octoeverywhere_api_key": "saved-key", "octoeverywhere_confidence": confidence}
    )
    assert response.status_code == 200
    assert response.json()["octoeverywhere_confidence"] == confidence
    assert (await async_client.get("/api/v1/settings/")).json()["octoeverywhere_confidence"] == confidence
    assert (await async_client.get("/api/v1/octoeverywhere/status")).json()["confidence"] == confidence
    assert (await service._load_settings())["confidence"] == confidence

    with patch.object(service, "test_connection", new_callable=AsyncMock) as check:
        check.return_value = {"ok": True, "status_code": 200, "error": None, "error_code": None}
        for payload in ({}, {"confidence": confidence}):
            response = await async_client.post("/api/v1/octoeverywhere/test-connection", json=payload)
            assert response.status_code == 200
            assert response.json()["ok"] is True
            check.assert_awaited_once_with("saved-key", confidence)
            check.reset_mock()


@pytest.mark.integration
@pytest.mark.parametrize("confidence", ["maximum", "Lowest", "", 5, True])
async def test_invalid_confidence_rejected_by_settings_and_connection(async_client, confidence):
    response = await async_client.put("/api/v1/settings/", json={"octoeverywhere_confidence": confidence})
    assert response.status_code == 422
    with patch.object(service, "test_connection", new_callable=AsyncMock) as check:
        response = await async_client.post("/api/v1/octoeverywhere/test-connection", json={"confidence": confidence})
    assert response.status_code == 422
    check.assert_not_awaited()


@pytest.mark.integration
@pytest.mark.parametrize("sensitivity", ["lowest", "highest"])
async def test_obico_rejects_octoeverywhere_only_levels(async_client, sensitivity):
    response = await async_client.put("/api/v1/settings/", json={"obico_sensitivity": sensitivity})
    assert response.status_code == 422


@pytest.mark.integration
@pytest.mark.parametrize("sensitivity", ["low", "medium", "high"])
async def test_obico_existing_sensitivity_levels_remain_supported(async_client, sensitivity):
    response = await async_client.put("/api/v1/settings/", json={"obico_sensitivity": sensitivity})
    assert response.status_code == 200
    assert response.json()["obico_sensitivity"] == sensitivity


@pytest.mark.integration
async def test_rejects_two_enabled_providers(async_client):
    response = await async_client.put("/api/v1/settings/", json={"obico_enabled": True, "octoeverywhere_enabled": True})
    assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.parametrize(
    "payload",
    [
        {"octoeverywhere_confidence": "maximum"},
        {"octoeverywhere_action": "stop"},
        {"octoeverywhere_enabled_printers": "{}"},
        {"octoeverywhere_enabled_printers": "[true]"},
        {"octoeverywhere_enabled_printers": "[-1]"},
        {"octoeverywhere_enabled_printers": '["1"]'},
        {"octoeverywhere_api_key": "key\r\nheader"},
        {"octoeverywhere_poll_interval": 0},
        {"octoeverywhere_poll_interval": 4},
        {"octoeverywhere_poll_interval": 31},
        {"octoeverywhere_poll_interval": 5.5},
        {"octoeverywhere_poll_interval": True},
        {"octoeverywhere_poll_interval": None},
        {"octoeverywhere_poll_interval": "20"},
    ],
)
async def test_invalid_settings_rejected(async_client, payload):
    response = await async_client.put("/api/v1/settings/", json=payload)
    assert response.status_code == 422


@pytest.mark.integration
async def test_connection_uses_saved_or_explicit_key(async_client):
    await async_client.put(
        "/api/v1/settings/", json={"octoeverywhere_api_key": "saved-key", "octoeverywhere_confidence": "low"}
    )
    with patch.object(service, "test_connection", new_callable=AsyncMock) as check:
        check.return_value = {"ok": True, "status_code": 200, "error": None, "error_code": None}
        response = await async_client.post("/api/v1/octoeverywhere/test-connection", json={})
        assert response.json()["ok"] is True
        check.assert_awaited_once_with("saved-key", "low")
        check.reset_mock()
        await async_client.post("/api/v1/octoeverywhere/test-connection", json={"api_key": ""})
        check.assert_awaited_once_with("", "low")


@pytest.mark.parametrize("can_see_error", [False, True])
async def test_printer_status_respects_error_permissions(can_see_error):
    from backend.app.api.routes.octoeverywhere import get_printer_status

    user = MagicMock()
    user.has_permission.return_value = can_see_error
    service._last_error = "private diagnostic"
    service._last_error_code = "OE_FREE_USAGE_LIMIT_REACHED"
    entry = {
        "class": "error",
        "print_quality": None,
        "frame_count": 0,
        "error": "private diagnostic",
        "error_code": "OE_FREE_USAGE_LIMIT_REACHED",
    }
    with (
        patch.object(service, "_load_settings", return_value={"enabled": True, "enabled_printers": None}),
        patch.object(service, "get_per_printer", return_value={1: entry}),
    ):
        result = await get_printer_status(user=user, printer_scope=ALL_PRINTERS, actor=user)
    assert result["per_printer"][1]["class"] == "error"
    assert result["per_printer"][1]["error"] == ("private diagnostic" if can_see_error else None)
    assert result["per_printer"][1]["error_code"] == ("OE_FREE_USAGE_LIMIT_REACHED" if can_see_error else None)
    assert result["last_error"] == ("private diagnostic" if can_see_error else None)
    assert result["last_error_code"] == ("OE_FREE_USAGE_LIMIT_REACHED" if can_see_error else None)


@pytest.mark.integration
@pytest.mark.parametrize("can_see_error", [False, True])
async def test_printer_status_checks_api_key_owner_permissions(async_client, can_see_error):
    from backend.app.core.auth import ApiKeyActor, get_request_actor
    from backend.app.core.permissions import Permission
    from backend.app.main import app
    from backend.app.models.api_key import APIKey
    from backend.app.models.group import Group
    from backend.app.models.user import User

    # Permission dependencies return user=None for API keys too. Their actor
    # must still enforce settings:read when deciding whether to expose errors.
    permissions = [Permission.PRINTERS_READ.value]
    if can_see_error:
        permissions.append(Permission.SETTINGS_READ.value)
    owner = User(id=1, username="viewer", role="user", groups=[Group(name="Viewer", permissions=permissions)])
    actor = ApiKeyActor(APIKey(can_read_status=True), owner=owner)
    service._last_error = "private diagnostic"
    service._last_error_code = "OE_FREE_USAGE_LIMIT_REACHED"
    entry = {
        "class": "error",
        "print_quality": None,
        "frame_count": 0,
        "error": "private diagnostic",
        "error_code": "OE_FREE_USAGE_LIMIT_REACHED",
    }
    app.dependency_overrides[get_request_actor] = lambda: actor
    try:
        with (
            patch.object(service, "_load_settings", return_value={"enabled": True, "enabled_printers": None}),
            patch.object(service, "get_per_printer", return_value={1: entry}),
        ):
            response = await async_client.get("/api/v1/octoeverywhere/printer-status")
        assert response.status_code == 200
        result = response.json()
        assert result["per_printer"]["1"]["class"] == "error"
        assert result["per_printer"]["1"]["error"] == ("private diagnostic" if can_see_error else None)
        assert result["per_printer"]["1"]["error_code"] == ("OE_FREE_USAGE_LIMIT_REACHED" if can_see_error else None)
        assert result["last_error"] == ("private diagnostic" if can_see_error else None)
        assert result["last_error_code"] == ("OE_FREE_USAGE_LIMIT_REACHED" if can_see_error else None)
    finally:
        app.dependency_overrides.pop(get_request_actor, None)


@pytest.mark.integration
@pytest.mark.parametrize("allowed_ids", [frozenset({1}), frozenset()])
@pytest.mark.parametrize("enabled_printers", [None, {1, 2}])
async def test_printer_status_hides_printers_outside_caller_scope(async_client, allowed_ids, enabled_printers):
    from backend.app.core.auth import get_printer_scope_if_auth_enabled
    from backend.app.main import app

    entry = {"class": "safe", "print_quality": 95, "frame_count": 1, "error": None, "error_code": None}
    app.dependency_overrides[get_printer_scope_if_auth_enabled] = lambda: PrinterScope(allowed_ids)
    try:
        with (
            patch.object(
                service, "_load_settings", return_value={"enabled": True, "enabled_printers": enabled_printers}
            ),
            patch.object(service, "get_per_printer", return_value={1: entry, 2: entry}),
        ):
            response = await async_client.get("/api/v1/octoeverywhere/printer-status")
        assert response.status_code == 200
        result = response.json()
        assert set(result["per_printer"]) == {str(pid) for pid in allowed_ids}
        assert result["monitored_printers"] == (None if enabled_printers is None else sorted(allowed_ids))
    finally:
        app.dependency_overrides.pop(get_printer_scope_if_auth_enabled, None)


@pytest.mark.integration
@pytest.mark.parametrize("allowed_ids", [None, frozenset({1}), frozenset()])
@pytest.mark.parametrize("provider_printer_id", [None, 1, 2])
async def test_status_limits_history_and_notification_readiness_to_caller_scope(
    async_client, db_session, allowed_ids, provider_printer_id
):
    from backend.app.core.auth import get_printer_scope_if_auth_enabled
    from backend.app.main import app
    from backend.app.models.notification import NotificationProvider
    from backend.app.models.printer import Printer

    for pid in (1, 2):
        db_session.add(
            Printer(
                id=pid,
                name=f"Printer {pid}",
                serial_number=f"TEST{pid}",
                ip_address="192.0.2.1",
                access_code="12345678",
                is_active=True,
            )
        )
    await db_session.flush()
    db_session.add(
        NotificationProvider(
            name="Alerts",
            provider_type="ntfy",
            config="{}",
            enabled=True,
            on_ai_failure_detection=True,
            printer_id=provider_printer_id,
        )
    )
    await db_session.commit()

    entry = {"class": "safe", "print_quality": 95, "frame_count": 1, "error": None, "error_code": None}
    status = {
        "is_running": True,
        "last_error": None,
        "last_error_code": None,
        "per_printer": {1: entry, 2: entry},
        "history": [
            {"printer_id": 2, "task_name": "Private print"},
            {"printer_id": 1, "task_name": "Allowed print"},
        ],
    }
    app.dependency_overrides[get_printer_scope_if_auth_enabled] = lambda: PrinterScope(allowed_ids)
    try:
        with patch.object(service, "get_status", return_value=status):
            response = await async_client.get("/api/v1/octoeverywhere/status")
        assert response.status_code == 200
        result = response.json()
        visible_ids = {1, 2} if allowed_ids is None else allowed_ids
        assert set(result["per_printer"]) == {str(pid) for pid in visible_ids}
        assert result["history"] == [entry for entry in status["history"] if entry["printer_id"] in visible_ids]
        assert result["notifications"] == {
            "configured": provider_printer_id is None or provider_printer_id in visible_ids,
            "uncovered_printers": ([] if provider_printer_id is None else sorted(visible_ids - {provider_printer_id})),
        }
        if 2 not in visible_ids:
            assert "Private print" not in response.text
        # Filtering a response must not remove another caller's service state.
        assert set(status["per_printer"]) == {1, 2}
        assert len(status["history"]) == 2
    finally:
        app.dependency_overrides.pop(get_printer_scope_if_auth_enabled, None)


@pytest.mark.integration
async def test_usage_limit_code_reaches_settings_and_printer_status(async_client):
    from backend.app.services.octoeverywhere_detection import _PrintState

    code = "OE_FREE_USAGE_LIMIT_REACHED"
    message = "Usage limit reached. Set up billing to continue."
    service._states[1] = _PrintState(print_id="job", error=message, error_code=code)
    service._last_error = message
    service._last_error_code = code

    for endpoint in ("status", "printer-status"):
        response = await async_client.get(f"/api/v1/octoeverywhere/{endpoint}")
        assert response.status_code == 200
        assert response.json()["last_error_code"] == code
        assert response.json()["per_printer"]["1"]["error_code"] == code
        assert response.json()["per_printer"]["1"]["class"] == "error"


@pytest.mark.integration
async def test_connection_preserves_usage_limit_code(async_client):
    result = {
        "ok": False,
        "status_code": 429,
        "error": "Usage limit reached. Set up billing to continue.",
        "error_code": "OE_FREE_USAGE_LIMIT_REACHED",
    }
    with patch.object(service, "test_connection", return_value=result):
        response = await async_client.post("/api/v1/octoeverywhere/test-connection", json={})
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.parametrize("is_api_key", [True, False])
async def test_settings_response_always_redacts_oe_key(db_session, is_api_key):
    from backend.app.api.routes.settings import _build_settings_response, set_setting

    await set_setting(db_session, "octoeverywhere_api_key", "secret-key")
    await db_session.commit()
    settings = await _build_settings_response(db_session, is_api_key=is_api_key)
    assert settings.octoeverywhere_api_key == ""
    assert settings.octoeverywhere_api_key_configured is True


@pytest.mark.integration
@pytest.mark.parametrize(
    ("provider_settings", "scope", "configured", "uncovered"),
    [
        ([], "", False, [1, 2]),
        ([{"enabled": False}], "", False, [1, 2]),
        ([{"on_ai_failure_detection": False}], "", False, [1, 2]),
        ([{}], "", True, []),
        ([{"printer_id": 1}], "", True, [2]),
        ([{"printer_id": 1}], "[1]", True, []),
        ([{"printer_id": 1}], "[2]", True, [2]),
        ([{"printer_id": 1}, {"printer_id": 2}], "", True, []),
        ([], "[]", False, []),
    ],
)
async def test_notification_readiness_matches_subscriptions(
    async_client, db_session, provider_settings, scope, configured, uncovered
):
    from backend.app.models.notification import NotificationProvider
    from backend.app.models.printer import Printer

    for pid in (1, 2, 3):
        db_session.add(
            Printer(
                id=pid,
                name=f"Printer {pid}",
                serial_number=f"TEST{pid}",
                ip_address="192.0.2.1",
                access_code="12345678",
                is_active=pid != 3,
            )
        )
    await db_session.flush()
    for overrides in provider_settings:
        values = {"enabled": True, "on_ai_failure_detection": True, **overrides}
        db_session.add(NotificationProvider(name="Alerts", provider_type="ntfy", config='{"token":"secret"}', **values))
    await db_session.commit()
    await async_client.put("/api/v1/settings/", json={"octoeverywhere_enabled_printers": scope})

    response = await async_client.get("/api/v1/octoeverywhere/status")
    assert response.status_code == 200
    assert response.json()["notifications"] == {"configured": configured, "uncovered_printers": uncovered}
    assert "secret" not in response.text


async def test_routes_require_existing_permissions():
    from fastapi import HTTPException

    from backend.app.api.routes.octoeverywhere import get_printer_status, get_status, test_connection
    from backend.app.core.permissions import Permission

    for route, permission in (
        (get_status, Permission.SETTINGS_READ),
        (get_printer_status, Permission.PRINTERS_READ),
        (test_connection, Permission.SETTINGS_UPDATE),
    ):
        with (
            patch("backend.app.core.auth.is_auth_enabled", return_value=True),
            patch("backend.app.core.auth._validate_api_key", return_value=MagicMock()),
            patch("backend.app.core.auth.authorize_api_key", side_effect=HTTPException(403)) as authorize,
            pytest.raises(HTTPException) as exc,
        ):
            await route.__defaults__[0].dependency(x_api_key="bb_test")
        assert exc.value.status_code == 403
        assert authorize.await_args.args[2] == [permission.value]

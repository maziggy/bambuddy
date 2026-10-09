"""Unit tests for OctoEverywhere's per-print AI detection integration."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services.octoeverywhere_detection import (
    API_ERRORS,
    CREATE_CONTEXT_URL,
    ERROR_RETRY_INTERVAL,
    IP_RESTRICTED_ERROR,
    MAX_IMAGE_BYTES,
    OctoEverywhereDetectionService,
)

MODULE = "backend.app.services.octoeverywhere_detection"
FAKE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\xff\xd9"
PRIMARY_URL = "https://gadget-region.octoeverywhere.com/api/gadget/v1/process/context-one"
FALLBACK_URL = "https://gadget-pv1-oeapi.octoeverywhere.com/api/gadget/v1/process/context-one"
USAGE_LIMIT_ERROR = "OE_FREE_USAGE_LIMIT_REACHED"


def quota_response(retry_after=30):
    return httpx.Response(
        429,
        headers={"Retry-After": str(retry_after)},
        json={"ErrorType": USAGE_LIMIT_ERROR, "ErrorDetails": "secret-api-key"},
    )


def ip_restricted_response():
    return httpx.Response(403, json={"ErrorType": IP_RESTRICTED_ERROR, "ErrorDetails": "secret-api-key"})


def context_payload(**overrides):
    payload = {
        "ContextId": "context-one",
        "ProcessRequestUrl": PRIMARY_URL,
        "FallbackProcessRequestUrl": FALLBACK_URL,
    }
    payload.update(overrides)
    return payload


def process_payload(**overrides):
    payload = {
        "NextProcessIntervalSec": {"Minimum": 40, "Recommended": 40},
        "PrintQuality": 9,
        "WarningSuggested": False,
        "PauseSuggested": False,
        "Score": 0,
    }
    payload.update(overrides)
    return payload


def settings(**overrides):
    values = {
        "enabled": True,
        "api_key": "secret-api-key",
        "confidence": "medium",
        "action": "notify",
        "enabled_printers": None,
        "poll_interval": 20,
    }
    values.update(overrides)
    return values


def printer_status(**overrides):
    values = {
        "state": "RUNNING",
        "subtask_name": "test-print",
        "subtask_id": "print-1",
        "gcode_file": "plate_1.gcode",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def poll_once(detector, configuration):
    """Wait for checks when a test needs the results of a complete polling cycle."""
    await detector._poll_once(configuration)
    await asyncio.gather(*detector._checks.values(), return_exceptions=True)


@pytest.fixture
def detector():
    service = OctoEverywhereDetectionService()
    service._capture_frame = AsyncMock(return_value=FAKE_JPEG)
    service._dispatch_action = AsyncMock()
    return service


@pytest.fixture
def clock():
    # Patch this module's time reference rather than the process-wide clock
    # used by asyncio's own scheduler.
    with patch(f"{MODULE}.time") as mocked:
        mocked.monotonic.return_value = 100.0
        yield mocked.monotonic


@pytest.fixture
def http_client():
    with patch(f"{MODULE}.httpx.AsyncClient") as factory:
        client = MagicMock()
        client.post = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        factory.return_value = client
        yield client


@pytest.fixture
def manager():
    with patch("backend.app.services.printer_manager.printer_manager") as mocked:
        mocked.is_connected.return_value = True
        mocked.get_all_statuses.return_value = {1: printer_status()}
        yield mocked


class TestConnection:
    @pytest.mark.parametrize(
        "confidence, level", [("lowest", 1), ("low", 2), ("medium", 3), ("high", 4), ("highest", 5)]
    )
    async def test_creates_context_without_uploading_photo(self, detector, http_client, confidence, level):
        http_client.post.return_value = httpx.Response(201, json=context_payload())

        result = await detector.test_connection("  secret-api-key  ", confidence)

        assert result == {"ok": True, "status_code": 201, "error": None, "error_code": None}
        http_client.post.assert_awaited_once_with(
            CREATE_CONTEXT_URL,
            headers={"X-API-Key": "secret-api-key"},
            json={"WarningConfidenceLevel": level, "PauseConfidenceLevel": level},
        )
        detector._capture_frame.assert_not_awaited()
        assert detector.get_per_printer() == {}

    async def test_empty_key_never_makes_a_request(self, detector, http_client):
        result = await detector.test_connection("  ")
        assert result["ok"] is False
        assert "required" in result["error"]
        assert result["error_code"] is None
        http_client.post.assert_not_awaited()

    @pytest.mark.parametrize("field", ["ProcessRequestUrl", "FallbackProcessRequestUrl"])
    @pytest.mark.parametrize(
        "url",
        [
            "http://gadget.octoeverywhere.com/process",
            "https://octoeverywhere.com.attacker.example/process",
            "https://attacker.example/process",
            "https://gadget.octoeverywhere.com@attacker.example/process",
            "https://user:password@gadget.octoeverywhere.com/process",
            "https://gadget.octoeverywhere.com:444/process",
            "https://127.0.0.1/process",
            "https://gadget.octoeverywhere.com/process\n",
            None,
        ],
    )
    async def test_rejects_untrusted_processing_urls(self, detector, http_client, field, url):
        http_client.post.return_value = httpx.Response(200, json=context_payload(**{field: url}))
        result = await detector.test_connection("secret-api-key")
        assert result["ok"] is False
        assert "invalid processing URL" in result["error"]
        assert http_client.post.await_count == 1

    @pytest.mark.parametrize("payload", [None, [], {"ContextId": ""}, {"ErrorType": "OE_INVALID_API_KEY"}])
    async def test_malformed_context_is_not_success(self, detector, http_client, payload):
        http_client.post.return_value = httpx.Response(200, json=payload)
        assert (await detector.test_connection("secret-api-key"))["ok"] is False

    @pytest.mark.parametrize(
        "error_type, expected",
        [
            ("OE_INVALID_API_KEY", "rejected the Gadget API key"),
            ("OE_API_KEY_DISABLED", "disabled"),
            ("OE_API_KEY_BLOCKED_PAYMENT_FAILED", "Check the account status"),
            (IP_RESTRICTED_ERROR, "already in use by another"),
        ],
    )
    async def test_actionable_api_errors_do_not_echo_secrets(self, detector, http_client, error_type, expected):
        http_client.post.return_value = httpx.Response(
            403, json={"ErrorType": error_type, "ErrorDetails": "secret-api-key"}
        )
        result = await detector.test_connection("secret-api-key")
        assert result["ok"] is False
        assert result["status_code"] == 403
        assert expected in result["error"]
        assert "secret-api-key" not in result["error"]
        assert result["error_code"] == error_type

    async def test_transport_errors_do_not_echo_secrets(self, detector, http_client):
        http_client.post.side_effect = httpx.ConnectError("secret-api-key")
        result = await detector.test_connection("secret-api-key")
        assert result["ok"] is False
        assert "secret-api-key" not in result["error"]
        assert result["error_code"] is None

    async def test_redirects_are_not_followed(self, detector, http_client):
        http_client.post.return_value = httpx.Response(302, headers={"Location": "https://attacker.example"})
        result = await detector.test_connection("secret-api-key")
        assert result["ok"] is False
        assert http_client.post.await_count == 1
        from backend.app.services.octoeverywhere_detection import httpx as service_httpx

        assert service_httpx.AsyncClient.call_args.kwargs["follow_redirects"] is False


class TestProcessing:
    @pytest.mark.parametrize(
        "confidence, level", [("lowest", 1), ("low", 2), ("medium", 3), ("high", 4), ("highest", 5)]
    )
    async def test_inspection_context_uses_configured_confidence(self, detector, http_client, clock, confidence, level):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await detector._check_printer(1, printer_status(), settings(confidence=confidence))
        creation = http_client.post.await_args_list[0]
        assert creation.args == (CREATE_CONTEXT_URL,)
        assert creation.kwargs["json"] == {
            "WarningConfidenceLevel": level,
            "PauseConfidenceLevel": level,
        }
        assert detector.get_per_printer()[1]["frame_count"] == 1

    async def test_wire_format_and_server_controlled_interval(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 75, "Recommended": 20})),
            httpx.Response(200, json=process_payload()),
        ]
        status = printer_status()
        await detector._check_printer(1, status, settings())

        assert http_client.post.await_args.args == (PRIMARY_URL,)
        assert http_client.post.await_args.kwargs == {
            "headers": {"X-API-Key": "secret-api-key"},
            "files": {"image": ("snapshot.jpg", FAKE_JPEG, "image/jpeg")},
        }
        assert detector.get_per_printer()[1] == {
            "class": "safe",
            "print_quality": 9,
            "frame_count": 1,
            "error": None,
            "error_code": None,
        }
        clock.return_value = 174
        await detector._check_printer(1, status, settings())
        assert http_client.post.await_count == 2
        clock.return_value = 175
        await detector._check_printer(1, status, settings())
        assert http_client.post.await_count == 3
        assert detector._capture_frame.await_count == 2
        assert detector.get_per_printer()[1]["frame_count"] == 2

    async def test_interval_is_measured_after_response(self, detector, http_client, clock):
        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            clock.return_value = 125
            return httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 60, "Recommended": 20}))

        http_client.post.side_effect = respond
        await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].next_check_at == 185

    async def test_default_interval_is_twenty_seconds_when_server_allows_faster(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 20})),
        ]
        await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].next_check_at == 120

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"Recommended": 1},
            {"Recommended": 5},
            {"Recommended": 20},
            {"Recommended": 300},
            {"Recommended": None},
            {"Recommended": True},
            {"Recommended": "20"},
            {"Recommended": {}},
            {"Recommended": []},
        ],
    )
    async def test_recommended_interval_ignored_without_faster_inspection_hint(
        self, detector, http_client, clock, fields
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 5, **fields})),
        ]
        await detector._check_printer(1, printer_status(), settings(poll_interval=12))
        assert detector._states[1].interval == 5
        assert detector._states[1].next_check_at == 112

    async def test_latest_server_minimum_can_increase_and_decrease(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 20})),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 75, "Recommended": 90})),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 10, "Recommended": 20})),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 20})),
        ]
        configured = settings(poll_interval=20)
        for now, minimum, next_check in [(100, 5, 120), (120, 75, 195), (195, 10, 215), (215, 5, 235)]:
            clock.return_value = now
            await detector._check_printer(1, printer_status(), configured)
            assert detector._states[1].interval == minimum
            assert detector._states[1].next_check_at == next_check
            clock.return_value = next_check - 0.1
            call_count = http_client.post.await_count
            await detector._check_printer(1, printer_status(), configured)
            assert http_client.post.await_count == call_count
        assert http_client.post.await_count == 5

    @pytest.mark.parametrize("poll_interval", [5, 20, 30])
    async def test_first_inspection_is_immediate_with_configured_baseline(
        self, detector, http_client, clock, poll_interval
    ):
        async def capture(printer_id):
            assert detector._states[printer_id].interval is None
            assert detector._states[printer_id].next_check_at == 100 + poll_interval
            return FAKE_JPEG

        detector._capture_frame.side_effect = capture
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 20})),
        ]
        await detector._check_printer(1, printer_status(), settings(poll_interval=poll_interval))
        assert http_client.post.await_count == 2
        assert detector._states[1].next_check_at == 100 + poll_interval

    @pytest.mark.parametrize(
        "server_interval, poll_interval, expected",
        [(5, 20, 20), (10, 5, 10), (40, 30, 40), (40, 20, 40), (5, 5, 5), (5, 21, 21), (180, 30, 180)],
    )
    async def test_inspection_interval_is_longer_of_setting_and_server_minimum(
        self, detector, http_client, clock, server_interval, poll_interval, expected
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200, json=process_payload(NextProcessIntervalSec={"Minimum": server_interval, "Recommended": 20})
            ),
            httpx.Response(
                200, json=process_payload(NextProcessIntervalSec={"Minimum": server_interval, "Recommended": 20})
            ),
        ]
        configured = settings(poll_interval=poll_interval)
        await detector._check_printer(1, printer_status(), configured)
        assert detector._states[1].interval == server_interval
        assert detector._states[1].next_check_at == 100 + expected
        clock.return_value = 100 + expected - 0.1
        await detector._check_printer(1, printer_status(), configured)
        assert http_client.post.await_count == 2
        clock.return_value = 100 + expected
        await detector._check_printer(1, printer_status(), configured)
        assert http_client.post.await_count == 3

    async def test_interval_edits_preserve_context_and_completed_actions(self, detector, http_client, manager, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(PauseSuggested=True, NextProcessIntervalSec={"Minimum": 10, "Recommended": 20}),
            ),
            httpx.Response(
                200,
                json=process_payload(PauseSuggested=True, NextProcessIntervalSec={"Minimum": 10, "Recommended": 20}),
            ),
            httpx.Response(
                200,
                json=process_payload(PauseSuggested=True, NextProcessIntervalSec={"Minimum": 10, "Recommended": 20}),
            ),
        ]
        await poll_once(detector, settings(poll_interval=30))
        context = detector._states[1].context
        clock.return_value = 105
        await poll_once(detector, settings(poll_interval=5))
        assert http_client.post.await_count == 2
        assert detector._states[1].next_check_at == 110
        clock.return_value = 110
        await poll_once(detector, settings(poll_interval=5))
        assert http_client.post.await_count == 3
        assert detector._states[1].next_check_at == 120
        clock.return_value = 115
        await poll_once(detector, settings(poll_interval=21))
        assert http_client.post.await_count == 3
        assert detector._states[1].next_check_at == 131
        clock.return_value = 131
        await poll_once(detector, settings(poll_interval=21))
        assert http_client.post.await_count == 4
        assert detector._states[1].context is context
        detector._dispatch_action.assert_awaited_once()

    @pytest.mark.parametrize("retry_after, expected", [(None, 460), ("300", 580)])
    async def test_interval_edits_never_shorten_rate_limit_backoff(
        self, detector, http_client, clock, retry_after, expected
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 180, "Recommended": 20})),
            httpx.Response(
                429,
                headers={"Retry-After": retry_after} if retry_after else {},
                json={"ErrorType": "OE_CONTEXT_RATE_LIMITED"},
            ),
        ]
        await detector._check_printer(1, printer_status(), settings(poll_interval=30))
        clock.return_value = 280
        await detector._check_printer(1, printer_status(), settings(poll_interval=30))
        assert detector._states[1].next_check_at == expected
        clock.return_value = expected - 1
        await detector._check_printer(1, printer_status(), settings(poll_interval=5))
        assert http_client.post.await_count == 3
        assert detector.get_per_printer()[1]["class"] == "error"

    async def test_raw_score_and_quality_never_trigger_actions(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(Score=100, PrintQuality=1)),
        ]
        await detector._check_printer(1, printer_status(), settings(action="pause_and_off"))
        assert detector.get_per_printer()[1]["class"] == "safe"
        detector._dispatch_action.assert_not_awaited()

    @pytest.mark.parametrize("action", ["notify", "pause", "pause_and_off"])
    async def test_warning_then_pause_dispatches_each_action_once(self, detector, http_client, clock, action):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(WarningSuggested=True, PrintQuality=4)),
            httpx.Response(200, json=process_payload(WarningSuggested=True, PrintQuality=3)),
            httpx.Response(200, json=process_payload(WarningSuggested=True, PauseSuggested=True, PrintQuality=1)),
            httpx.Response(200, json=process_payload(PauseSuggested=True, PrintQuality=1)),
        ]
        for now in (100, 140, 180, 220):
            clock.return_value = now
            await detector._check_printer(1, printer_status(), settings(action=action))

        calls = detector._dispatch_action.await_args_list
        assert calls[0].args == (1, "notify", "test-print", 4, FAKE_JPEG)
        if action == "notify":
            assert len(calls) == 1
        else:
            assert len(calls) == 2
            assert calls[1].args == (1, action, "test-print", 1, FAKE_JPEG)
        status = detector.get_status()
        assert status["per_printer"][1]["class"] == "failure"
        assert len(status["history"]) == 4
        assert status["history"][0]["print_quality"] == 1
        assert status["history"][0]["timestamp"].endswith("+00:00")

    async def test_pause_without_warning_notifies_once(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(PauseSuggested=True, PrintQuality=2)),
        ]
        await detector._check_printer(1, printer_status(), settings(action="pause"))
        detector._dispatch_action.assert_awaited_once_with(1, "pause", "test-print", 2, FAKE_JPEG)

    @pytest.mark.parametrize("action", ["pause", "pause_and_off"])
    async def test_pause_after_warning_sends_its_own_notification(self, detector, http_client, clock, action):
        # Run the real dispatcher: the user must hear that the printer stopped,
        # not only about the warning that came before it.
        detector._dispatch_action = OctoEverywhereDetectionService._dispatch_action.__get__(detector)
        frames = [FAKE_JPEG + b"warning", FAKE_JPEG + b"pause"]
        detector._capture_frame.side_effect = frames
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(WarningSuggested=True, PrintQuality=4)),
            httpx.Response(200, json=process_payload(WarningSuggested=True, PauseSuggested=True, PrintQuality=1)),
        ]
        with (
            patch("backend.app.services.obico_actions._get_printer_name", return_value="Printer"),
            patch("backend.app.services.obico_actions._pause_print") as pause,
            patch("backend.app.services.obico_actions._turn_off_linked_plugs", new_callable=AsyncMock),
            patch("backend.app.services.obico_actions._notify", new_callable=AsyncMock) as notify,
        ):
            for now in (100, 140):
                clock.return_value = now
                await detector._check_printer(1, printer_status(), settings(action=action))
        pause.assert_called_once_with(1)
        assert [call.args[4] for call in notify.await_args_list] == ["notify", action]
        assert [call.args[5] for call in notify.await_args_list] == frames

    @pytest.mark.parametrize(
        "fields",
        [
            {"PrintQuality": True},
            {"PrintQuality": 0},
            {"PrintQuality": 11},
            {"PrintQuality": None},
            {"PauseSuggested": "false"},
            {"WarningSuggested": 1},
        ],
    )
    async def test_invalid_assessment_is_an_error_and_never_acts(self, detector, http_client, clock, fields):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(**fields)),
        ]
        await detector._check_printer(1, printer_status(), settings())
        result = detector.get_per_printer()[1]
        assert result["class"] == "error"
        assert result["frame_count"] == 0
        assert result["print_quality"] is None
        detector._dispatch_action.assert_not_awaited()

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"NextProcessIntervalSec": None},
            {"NextProcessIntervalSec": True},
            {"NextProcessIntervalSec": 20},
            {"NextProcessIntervalSec": "20"},
            {"NextProcessIntervalSec": []},
            {"NextProcessIntervalSec": {}},
            {"NextProcessIntervalSec": {"Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": None, "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": True, "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": False, "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": 0, "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": -1, "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": 5.5, "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": "5", "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": {}, "Recommended": 20}},
            {"NextProcessIntervalSec": {"Minimum": [], "Recommended": 20}},
        ],
    )
    async def test_invalid_interval_retries_without_actions(self, detector, http_client, clock, fields):
        payload = process_payload(PauseSuggested=True)
        payload.pop("NextProcessIntervalSec")
        payload.update(fields)
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=payload),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 20})),
        ]
        configured = settings(poll_interval=5, action="pause_and_off")
        await detector._check_printer(1, printer_status(), configured)
        state = detector._states[1]
        context = state.context
        assert detector.get_per_printer()[1]["class"] == "error"
        assert "invalid processing interval" in state.error
        assert state.frame_count == 0
        assert state.interval is None
        assert state.next_check_at == 100 + ERROR_RETRY_INTERVAL
        detector._dispatch_action.assert_not_awaited()

        clock.return_value = 100 + ERROR_RETRY_INTERVAL - 0.1
        await detector._check_printer(1, printer_status(), configured)
        assert http_client.post.await_count == 2
        clock.return_value = 100 + ERROR_RETRY_INTERVAL
        await detector._check_printer(1, printer_status(), configured)
        assert http_client.post.await_count == 3
        assert state.context is context
        assert state.error is None
        assert state.frame_count == 1
        assert state.next_check_at == 100 + ERROR_RETRY_INTERVAL + 5
        detector._dispatch_action.assert_not_awaited()

    async def test_failure_uses_fallback_after_backoff_and_keeps_it(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.ConnectError("secret-api-key"),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await detector._check_printer(1, printer_status(), settings())
        assert detector.get_per_printer()[1]["class"] == "error"
        assert "secret-api-key" not in detector.get_status()["last_error"]
        assert http_client.post.await_count == 2
        clock.return_value = 100 + ERROR_RETRY_INTERVAL - 1
        await detector._check_printer(1, printer_status(), settings())
        assert http_client.post.await_count == 2
        for now in (160, 200):
            clock.return_value = now
            await detector._check_printer(1, printer_status(), settings())
        assert [call.args[0] for call in http_client.post.await_args_list] == [
            CREATE_CONTEXT_URL,
            PRIMARY_URL,
            FALLBACK_URL,
            FALLBACK_URL,
        ]
        assert detector.get_status()["last_error"] is None
        assert detector.get_per_printer()[1]["class"] == "safe"

    async def test_retry_after_and_previous_server_interval_are_honored(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 180, "Recommended": 20})),
            httpx.Response(429, headers={"Retry-After": "300"}, json={"ErrorType": "OE_CONTEXT_RATE_LIMITED"}),
        ]
        await detector._check_printer(1, printer_status(), settings())
        clock.return_value = 280
        await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].next_check_at == 580
        assert detector._states[1].frame_count == 1

    async def test_invalid_assessment_still_honors_valid_long_interval(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200, json=process_payload(NextProcessIntervalSec={"Minimum": 600, "Recommended": 20}, PrintQuality=None)
            ),
        ]
        await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].next_check_at == 700

    async def test_failed_capture_does_not_upload_and_backs_off(self, detector, http_client, clock):
        detector._capture_frame.return_value = None
        await detector._check_printer(1, printer_status(), settings())
        await detector._check_printer(1, printer_status(), settings())
        http_client.post.assert_not_awaited()
        detector._capture_frame.assert_awaited_once()
        assert detector.get_per_printer()[1]["class"] == "error"
        assert detector._states[1].next_check_at == 160

    @pytest.mark.parametrize("oversized", [False, True])
    async def test_only_oversized_snapshots_are_prepared_before_upload(self, detector, http_client, clock, oversized):
        frame = b"x" * (MAX_IMAGE_BYTES + int(oversized))
        detector._capture_frame.return_value = frame
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        with patch(f"{MODULE}.prepare_frame", return_value=FAKE_JPEG) as prepare:
            await detector._check_printer(1, printer_status(), settings())
        if oversized:
            prepare.assert_called_once_with(frame)
        else:
            prepare.assert_not_called()
        uploaded = http_client.post.await_args.kwargs["files"]["image"]
        assert uploaded == ("snapshot.jpg", FAKE_JPEG if oversized else frame, "image/jpeg")
        assert len(uploaded[1]) <= MAX_IMAGE_BYTES
        assert detector.get_per_printer()[1]["class"] == "safe"

    async def test_unprocessable_oversized_snapshot_is_not_uploaded(self, detector, http_client, clock):
        frame = b"x" * (MAX_IMAGE_BYTES + 1)
        detector._capture_frame.return_value = frame
        with patch(f"{MODULE}.prepare_frame", side_effect=ValueError("private-image-details")) as prepare:
            await detector._check_printer(1, printer_status(), settings(action="pause_and_off"))
        prepare.assert_called_once_with(frame)
        http_client.post.assert_not_awaited()
        detector._dispatch_action.assert_not_awaited()
        assert detector.get_per_printer()[1]["class"] == "error"
        assert "6 MiB" in detector.get_status()["last_error"]
        assert "private-image-details" not in str(detector.get_status())
        assert detector._states[1].next_check_at == 160

    async def test_reset_during_capture_does_not_create_or_upload_context(self, detector, http_client, clock):
        async def capture(printer_id):
            detector.reset_printer(printer_id)
            return FAKE_JPEG

        detector._capture_frame.side_effect = capture
        await detector._check_printer(1, printer_status(), settings())
        http_client.post.assert_not_awaited()
        assert detector.get_per_printer() == {}

    async def test_completion_during_processing_cannot_pause_next_print(self, detector, http_client, clock):
        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            detector.reset_printer(1)
            return httpx.Response(200, json=process_payload(PauseSuggested=True))

        http_client.post.side_effect = respond
        await detector._check_printer(1, printer_status(), settings(action="pause"))
        detector._dispatch_action.assert_not_awaited()
        assert detector.get_per_printer() == {}

    @pytest.mark.parametrize(
        "changes",
        [
            {"enabled": False},
            {"api_key": "replacement-key"},
            {"enabled_printers": set()},
            {"action": "notify"},
        ],
    )
    async def test_saved_settings_invalidate_in_flight_actions(self, detector, http_client, manager, clock, changes):
        detector._load_settings = AsyncMock(return_value=settings(**changes))

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            await detector.refresh_settings()
            return httpx.Response(
                200,
                json=process_payload(PauseSuggested=True, NextProcessIntervalSec={"Minimum": 120, "Recommended": 20}),
            )

        http_client.post.side_effect = respond
        await poll_once(detector, settings(action="pause"))

        detector._dispatch_action.assert_not_awaited()
        if "action" in changes:
            # Action changes keep temporal context and server pacing, while
            # discarding the decision made under the previous configuration.
            assert detector._states[1].context is not None
            assert detector._states[1].next_check_at == 220
        elif "api_key" in changes:
            assert detector._states[1].context is None
            assert detector._states[1].next_check_at == 220
        else:
            assert detector.get_per_printer() == {}

    async def test_unknown_until_first_inference_returns(self, detector, http_client, clock):
        async def capture(printer_id):
            assert detector.get_per_printer()[printer_id] == {
                "class": "unknown",
                "print_quality": None,
                "frame_count": 0,
                "error": None,
                "error_code": None,
            }
            return FAKE_JPEG

        detector._capture_frame.side_effect = capture
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await detector._check_printer(1, printer_status(), settings())
        assert detector.get_per_printer()[1]["class"] == "safe"

    async def test_invalid_json_is_not_a_safe_frame(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, text="not json: secret-api-key"),
        ]
        await detector._check_printer(1, printer_status(), settings())
        assert detector.get_per_printer()[1]["class"] == "error"
        assert "secret-api-key" not in detector.get_status()["last_error"]

    @pytest.mark.parametrize("flag", ["WarningSuggested", "PauseSuggested"])
    async def test_expired_context_is_recreated_without_repeating_actions(self, detector, http_client, clock, flag):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(**{flag: True})),
            httpx.Response(
                500, json={"ErrorType": "OE_INTERNAL_ERROR", "ErrorDetails": "Failed to get context object."}
            ),
            httpx.Response(200, json=context_payload(ContextId="new-context")),
            httpx.Response(200, json=process_payload(**{flag: True})),
        ]
        for now in (100, 140, 200):
            clock.return_value = now
            await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].context.context_id == "new-context"
        detector._dispatch_action.assert_awaited_once()

    @pytest.mark.parametrize(
        "status_code, payload",
        [
            (500, {"ErrorType": "OE_INTERNAL_ERROR", "ErrorDetails": "ML Model Exception"}),
            (500, {"ErrorType": "OE_BACKEND_THROTTLED", "ErrorDetails": "Failed to get context object."}),
            (404, {"ErrorType": "OE_INTERNAL_ERROR", "ErrorDetails": "Failed to get context object."}),
            (500, {"ErrorType": "OE_INTERNAL_ERROR", "ErrorDetails": "Failed to get context object. secret-api-key"}),
        ],
    )
    async def test_other_errors_retain_context_and_do_not_echo_details(
        self, detector, http_client, clock, status_code, payload
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(status_code, json=payload),
            httpx.Response(status_code, json=payload),
        ]
        for now in (100, 160):
            clock.return_value = now
            await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].context.context_id == "context-one"
        assert detector._states[1].context.process_url == (FALLBACK_URL if status_code >= 500 else PRIMARY_URL)
        assert "secret-api-key" not in detector.get_status()["last_error"]
        assert http_client.post.await_count == 3

    async def test_one_printers_success_does_not_hide_another_printers_error(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(503),
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await detector._check_printer(1, printer_status(), settings())
        await detector._check_printer(2, printer_status(), settings())
        assert detector.get_per_printer()[1]["class"] == "error"
        assert detector.get_per_printer()[2]["class"] == "safe"
        assert detector.get_status()["last_error"] is not None


class TestFasterInspectionTiming:
    @pytest.mark.parametrize(
        "minimum, recommended, configured, delay",
        [(5, 8, 20, 8), (5, 45, 5, 5), (5, 45, 20, 20), (40, 2, 30, 40)],
    )
    async def test_timing_hint_only_shortens_configured_interval_and_respects_minimum(
        self, detector, http_client, clock, caplog, minimum, recommended, configured, delay
    ):
        caplog.set_level(logging.DEBUG, logger=MODULE)
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": minimum, "Recommended": recommended},
                    FasterInspectionSuggested=True,
                    PrintQuality=2,
                ),
            ),
        ]
        await detector._check_printer(1, printer_status(), settings(poll_interval=configured, action="pause_and_off"))
        assert detector._states[1].recommended_interval == recommended
        assert detector._states[1].next_check_at == 100 + delay
        clock.return_value = 100 + delay - 0.1
        await detector._check_printer(1, printer_status(), settings(poll_interval=configured, action="pause_and_off"))
        assert http_client.post.await_count == 2
        assert detector.get_per_printer()[1]["class"] == "safe"
        detector._dispatch_action.assert_not_awaited()
        results = [
            record.message
            for record in caplog.records
            if record.name == MODULE and "inspection result" in record.message
        ]
        assert len(results) == 1
        assert "faster_inspection_suggested=True" in results[0]
        assert f"next_check_in={delay}s" in results[0]

    async def test_latest_hint_changes_cadence_and_false_or_missing_restores_setting(
        self, detector, http_client, clock
    ):
        responses = [(True, 8), (True, 6), (False, 5), (True, 5), (None, 5)]
        http_client.post.side_effect = [httpx.Response(200, json=context_payload())] + [
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": recommended},
                    **({"FasterInspectionSuggested": hint} if hint is not None else {}),
                ),
            )
            for hint, recommended in responses
        ]
        context = None
        for now, next_check, active_interval in [
            (100, 108, 8),
            (108, 114, 6),
            (114, 134, None),
            (134, 139, 5),
            (139, 159, None),
        ]:
            clock.return_value = now
            await detector._check_printer(1, printer_status(), settings())
            state = detector._states[1]
            if context is None:
                context = state.context
            assert state.context is context
            assert state.recommended_interval == active_interval
            assert state.next_check_at == next_check
        assert [call.args[0] for call in http_client.post.await_args_list] == [CREATE_CONTEXT_URL] + [PRIMARY_URL] * 5
        detector._dispatch_action.assert_not_awaited()

    async def test_interval_edit_preserves_hint_then_uses_new_setting_when_hint_clears(
        self, detector, http_client, manager, clock
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=True
                ),
            ),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=False
                ),
            ),
        ]
        await poll_once(detector, settings())
        context = detector._states[1].context
        clock.return_value = 101
        await poll_once(detector, settings(poll_interval=30))
        assert detector._states[1].next_check_at == 105
        assert http_client.post.await_count == 2
        clock.return_value = 105
        await poll_once(detector, settings(poll_interval=30))
        assert detector._states[1].context is context
        assert detector._states[1].recommended_interval is None
        assert detector._states[1].next_check_at == 135

    @pytest.mark.parametrize(
        "hint, interval_fields",
        [
            (None, {"Recommended": 5}),
            (1, {"Recommended": 5}),
            ("true", {"Recommended": 5}),
            (True, {}),
            (True, {"Recommended": None}),
            (True, {"Recommended": True}),
            (True, {"Recommended": 0}),
            (True, {"Recommended": -1}),
            (True, {"Recommended": 2.5}),
            (True, {"Recommended": "private-recommendation"}),
        ],
    )
    async def test_invalid_optional_hint_restores_setting_without_losing_warning(
        self, detector, http_client, clock, caplog, hint, interval_fields
    ):
        caplog.set_level(logging.DEBUG, logger=MODULE)
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=True
                ),
            ),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, **interval_fields},
                    FasterInspectionSuggested=hint,
                    WarningSuggested=True,
                    PrintQuality=4,
                ),
            ),
        ]
        await detector._check_printer(1, printer_status(), settings())
        clock.return_value = 105
        await detector._check_printer(1, printer_status(), settings())
        state = detector._states[1]
        assert state.recommended_interval is None
        assert state.next_check_at == 125
        assert state.frame_count == 2
        assert state.error is None
        assert state.verdict == "warning"
        detector._dispatch_action.assert_awaited_once_with(1, "notify", "test-print", 4, FAKE_JPEG)
        assert "private-recommendation" not in "\n".join(
            record.message for record in caplog.records if record.name == MODULE
        )

    async def test_timing_hint_is_scoped_to_printer_and_print(self, detector, http_client, manager, clock):
        manager.get_all_statuses.return_value[2] = printer_status(subtask_id="other-print")
        fast = process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=True)
        normal = process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 5})
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=fast),
            httpx.Response(200, json=context_payload(ContextId="other-context")),
            httpx.Response(200, json=normal),
            httpx.Response(200, json=context_payload(ContextId="new-context")),
            httpx.Response(200, json=normal),
        ]
        await poll_once(detector, settings())
        assert detector._states[1].next_check_at == 105
        assert detector._states[2].next_check_at == 120
        old_state = detector._states[1]
        manager.get_all_statuses.return_value[1].subtask_id = "new-print"
        clock.return_value = 101

        async def capture(printer_id):
            assert printer_id == 1
            assert detector._states[printer_id].recommended_interval is None
            assert detector._states[printer_id].next_check_at == 121
            return FAKE_JPEG

        detector._capture_frame.side_effect = capture
        await poll_once(detector, settings())
        assert detector._states[1] is not old_state
        assert detector._states[1].next_check_at == 121
        assert detector._states[2].next_check_at == 120

    @pytest.mark.parametrize("changes", [{"api_key": "new-key"}, {"confidence": "highest"}])
    async def test_context_configuration_change_clears_timing_hint(
        self, detector, http_client, manager, clock, changes
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=True
                ),
            ),
        ]
        await poll_once(detector, settings())
        clock.return_value = 101
        await poll_once(detector, settings(**changes))
        assert detector._states[1].recommended_interval is None
        assert detector._states[1].context is None
        assert detector._states[1].next_check_at == 120
        assert http_client.post.await_count == 2

    async def test_expired_context_clears_timing_hint_and_respects_backoff(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=True
                ),
            ),
            httpx.Response(
                500, json={"ErrorType": "OE_INTERNAL_ERROR", "ErrorDetails": "Failed to get context object."}
            ),
            httpx.Response(200, json=context_payload(ContextId="new-context")),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 5, "Recommended": 5})),
        ]
        await detector._check_printer(1, printer_status(), settings())
        clock.return_value = 105
        await detector._check_printer(1, printer_status(), settings())
        state = detector._states[1]
        assert state.context is None
        assert state.recommended_interval is None
        assert state.next_check_at == 165
        clock.return_value = 164
        await detector._check_printer(1, printer_status(), settings())
        assert http_client.post.await_count == 3
        clock.return_value = 165
        await detector._check_printer(1, printer_status(), settings())
        assert state.context.context_id == "new-context"
        assert state.next_check_at == 185

    @pytest.mark.parametrize(
        "response, delay, host",
        [
            (httpx.ConnectError("private-transport-details"), 60, FALLBACK_URL),
            (
                httpx.Response(429, headers={"Retry-After": "300"}, json={"ErrorType": "OE_CONTEXT_RATE_LIMITED"}),
                300,
                PRIMARY_URL,
            ),
        ],
    )
    async def test_active_timing_hint_never_shortens_error_backoff(
        self, detector, http_client, clock, response, delay, host
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=True
                ),
            ),
            response,
        ]
        await detector._check_printer(1, printer_status(), settings())
        clock.return_value = 105
        await detector._check_printer(1, printer_status(), settings())
        state = detector._states[1]
        assert state.next_check_at == 105 + delay
        assert state.context.process_url == host
        clock.return_value = 105 + delay - 1
        await detector._check_printer(1, printer_status(), settings(poll_interval=5))
        assert state.next_check_at == 105 + delay
        assert http_client.post.await_count == 3
        detector._dispatch_action.assert_not_awaited()

    async def test_invalid_assessment_keeps_latest_minimum_without_accepting_new_hint(
        self, detector, http_client, clock
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5}, FasterInspectionSuggested=True
                ),
            ),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 90, "Recommended": 2},
                    FasterInspectionSuggested=True,
                    PrintQuality=None,
                ),
            ),
        ]
        await detector._check_printer(1, printer_status(), settings())
        clock.return_value = 105
        await detector._check_printer(1, printer_status(), settings())
        state = detector._states[1]
        assert state.interval == 90
        assert state.recommended_interval == 5
        assert state.next_check_at == 195
        assert state.frame_count == 1
        detector._dispatch_action.assert_not_awaited()

    async def test_canceled_fast_inspection_preserves_minimum_before_retry(self, detector, http_client, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 10, "Recommended": 5}, FasterInspectionSuggested=True
                ),
            ),
        ]
        await detector._check_printer(1, printer_status(), settings())

        async def cancel_response(url, **kwargs):
            clock.return_value = 115
            raise asyncio.CancelledError

        http_client.post.side_effect = cancel_response
        clock.return_value = 110
        with pytest.raises(asyncio.CancelledError):
            await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].recommended_interval == 5
        assert detector._states[1].next_check_at == 125
        clock.return_value = 124
        await detector._check_printer(1, printer_status(), settings())
        assert http_client.post.await_count == 3

    async def test_stale_response_does_not_apply_timing_hint(self, detector, http_client, clock):
        status = printer_status()

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            status.subtask_id = "new-print"
            return httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5},
                    FasterInspectionSuggested=True,
                    PauseSuggested=True,
                ),
            )

        http_client.post.side_effect = respond
        await detector._check_printer(1, status, settings(action="pause"))
        assert detector._states[1].recommended_interval is None
        assert detector._states[1].frame_count == 0
        detector._dispatch_action.assert_not_awaited()


class TestDebugLogging:
    @pytest.mark.parametrize(
        "warning, pause, quality, verdict, minimum, interval",
        [(False, False, 9, "safe", 5, 20), (True, False, 4, "warning", 40, 40), (True, True, 1, "failure", 5, 20)],
    )
    async def test_each_due_inspection_logs_results_and_context_reuse(
        self, detector, http_client, clock, caplog, warning, pause, quality, verdict, minimum, interval
    ):
        caplog.set_level(logging.DEBUG, logger=MODULE)
        payload = process_payload(
            WarningSuggested=warning,
            PauseSuggested=pause,
            PrintQuality=quality,
            NextProcessIntervalSec={"Minimum": minimum, "Recommended": 20},
            ExtraDetails="private-response-details",
        )
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=payload),
            httpx.Response(200, json=payload),
        ]
        await detector._check_printer(1, printer_status(), settings())
        first_messages = [record.message for record in caplog.records if record.name == MODULE]
        clock.return_value = 100 + interval - 1
        await detector._check_printer(1, printer_status(), settings())
        assert [record.message for record in caplog.records if record.name == MODULE] == first_messages

        clock.return_value = 100 + interval
        await detector._check_printer(1, printer_status(), settings())
        records = [record for record in caplog.records if record.name == MODULE]
        messages = [record.message for record in records]
        assert all(record.levelno == logging.DEBUG for record in records)
        assert messages.count("OctoEverywhere context created for printer 1") == 1
        for frame, reused in ((1, False), (2, True)):
            assert (
                f"OctoEverywhere inspection starting for printer 1 (frame={frame}, context_reused={reused})"
            ) in messages
            assert (
                f"OctoEverywhere inspection result for printer 1: frame={frame}, verdict={verdict}, "
                f"print_quality={quality}, warning_suggested={warning}, pause_suggested={pause}, "
                "faster_inspection_suggested=False, "
                f"minimum_interval={minimum}s, next_check_in={interval}s"
            ) in messages
        assert len(messages) == 5
        log_text = "\n".join(messages)
        for private_value in ("secret-api-key", "context-one", PRIMARY_URL, FALLBACK_URL, "private-response-details"):
            assert private_value not in log_text
        assert http_client.post.await_count == 3

    async def test_repeated_errors_log_each_retry_with_safe_error_code(self, detector, http_client, clock, caplog):
        caplog.set_level(logging.DEBUG, logger=MODULE)
        response = httpx.Response(
            429,
            headers={"Retry-After": "30"},
            json={"ErrorType": "OE_CONTEXT_RATE_LIMITED", "ErrorDetails": "private-response-details secret-api-key"},
        )
        http_client.post.side_effect = [httpx.Response(200, json=context_payload()), response, response]
        for now in (100, 160):
            clock.return_value = now
            await detector._check_printer(1, printer_status(), settings())

        records = [record for record in caplog.records if record.name == MODULE]
        failures = [record for record in records if "inspection failed" in record.message]
        assert len(failures) == 2
        assert all(record.levelno == logging.DEBUG for record in failures)
        for record, delay in zip(failures, (60, 120), strict=True):
            assert f"error_code=OE_CONTEXT_RATE_LIMITED, retry_in={delay:.1f}s" in record.message
            assert API_ERRORS["OE_CONTEXT_RATE_LIMITED"] in record.message
        assert len([record for record in records if record.levelno == logging.WARNING]) == 1
        assert not any("inspection result" in record.message for record in records)
        log_text = "\n".join(record.message for record in records)
        for private_value in ("secret-api-key", "context-one", PRIMARY_URL, FALLBACK_URL, "private-response-details"):
            assert private_value not in log_text

    @pytest.mark.parametrize(
        "response, expected_error",
        [
            (
                httpx.Response(
                    200,
                    json=process_payload(
                        PrintQuality="private-invalid-quality",
                        WarningSuggested="private-invalid-warning",
                        PauseSuggested="private-invalid-pause",
                    ),
                ),
                "invalid print assessment",
            ),
            (
                httpx.Response(
                    200,
                    json=process_payload(NextProcessIntervalSec={"Minimum": "private-invalid-interval"}),
                ),
                "invalid processing interval",
            ),
            (
                httpx.Response(
                    500,
                    json={"ErrorType": "private-unknown-code", "ErrorDetails": "private-error-details secret-api-key"},
                ),
                "API request failed (HTTP 500)",
            ),
            (httpx.ConnectError(f"private-transport-details {PRIMARY_URL} secret-api-key"), "Could not reach"),
            (httpx.Response(200, text="private-raw-payload secret-api-key"), "invalid response"),
        ],
        ids=["invalid-assessment", "invalid-interval", "unknown-error", "transport-error", "invalid-json"],
    )
    async def test_failures_do_not_log_remote_payloads_or_credentials(
        self, detector, http_client, clock, caplog, response, expected_error
    ):
        caplog.set_level(logging.DEBUG, logger=MODULE)
        http_client.post.side_effect = [httpx.Response(200, json=context_payload()), response]
        await detector._check_printer(1, printer_status(), settings())
        records = [record for record in caplog.records if record.name == MODULE]
        failures = [record for record in records if "inspection failed" in record.message]
        assert len(failures) == 1
        assert failures[0].levelno == logging.DEBUG
        assert expected_error in failures[0].message
        assert "error_code=None, retry_in=60.0s" in failures[0].message
        assert not any("inspection result" in record.message for record in records)
        log_text = "\n".join(record.message for record in records)
        for private_value in ("secret-api-key", "context-one", PRIMARY_URL, FALLBACK_URL, "private-"):
            assert private_value not in log_text

    async def test_stale_result_logs_discard_without_logging_assessment(self, detector, http_client, clock, caplog):
        caplog.set_level(logging.DEBUG, logger=MODULE)

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            detector.reset_printer(1)
            return httpx.Response(200, json=process_payload(PauseSuggested=True))

        http_client.post.side_effect = respond
        await detector._check_printer(1, printer_status(), settings(action="pause"))
        records = [record for record in caplog.records if record.name == MODULE]
        discarded = [record for record in records if "inspection discarded" in record.message]
        assert len(discarded) == 1
        assert discarded[0].levelno == logging.DEBUG
        assert not any("inspection result" in record.message for record in records)
        assert not any("inspection failed" in record.message for record in records)
        detector._dispatch_action.assert_not_awaited()

    async def test_canceled_inspection_logs_cancellation_without_exception_details(
        self, detector, http_client, clock, caplog
    ):
        caplog.set_level(logging.DEBUG, logger=MODULE)
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            asyncio.CancelledError("private-cancellation-details secret-api-key"),
        ]
        with pytest.raises(asyncio.CancelledError):
            await detector._check_printer(1, printer_status(), settings())
        records = [record for record in caplog.records if record.name == MODULE]
        canceled = [record for record in records if "inspection canceled" in record.message]
        assert len(canceled) == 1
        assert canceled[0].levelno == logging.DEBUG
        assert not any("inspection result" in record.message for record in records)
        assert not any("inspection failed" in record.message for record in records)
        log_text = "\n".join(record.message for record in records)
        assert "private-cancellation-details" not in log_text
        assert "secret-api-key" not in log_text


class TestErrorCodes:
    async def test_connection_exposes_quota_code_and_safe_message(self, detector, http_client):
        http_client.post.return_value = quota_response()
        result = await detector.test_connection("secret-api-key")
        assert result == {
            "ok": False,
            "status_code": 429,
            "error": API_ERRORS[USAGE_LIMIT_ERROR],
            "error_code": USAGE_LIMIT_ERROR,
        }
        assert result["error"] == "API usage limit reached. Set up billing to continue."
        assert "secret-api-key" not in result["error"]
        detector._capture_frame.assert_not_awaited()
        detector._dispatch_action.assert_not_awaited()

    @pytest.mark.parametrize(
        "unknown_code", [None, "OE_FUTURE_ERROR_secret-api-key", ["secret-api-key"], {"secret": "secret-api-key"}]
    )
    async def test_unknown_remote_codes_are_not_exposed(self, detector, http_client, clock, unknown_code):
        response = httpx.Response(429, json={"ErrorType": unknown_code, "ErrorDetails": "secret-api-key"})
        http_client.post.return_value = response
        result = await detector.test_connection("secret-api-key")
        assert result["error_code"] is None
        assert "secret-api-key" not in result["error"]
        http_client.post.side_effect = [httpx.Response(200, json=context_payload()), response]
        await detector._check_printer(1, printer_status(), settings())
        status = detector.get_status()
        assert status["last_error_code"] is None
        assert status["per_printer"][1]["error_code"] is None
        assert "secret-api-key" not in str(status)

    @pytest.mark.parametrize(
        "error_code",
        [
            "OE_INVALID_API_KEY",
            "OE_API_KEY_DISABLED",
            "OE_API_KEY_BLOCKED_PAYMENT_FAILED",
            IP_RESTRICTED_ERROR,
            USAGE_LIMIT_ERROR,
        ],
    )
    @pytest.mark.parametrize("stage", ["context", "inspection"])
    async def test_account_errors_stop_all_printers_without_automatic_retries(
        self, detector, http_client, manager, clock, error_code, stage
    ):
        manager.get_all_statuses.return_value[2] = printer_status(subtask_id="print-2")
        response = httpx.Response(
            429 if error_code == USAGE_LIMIT_ERROR else 403,
            headers={"Retry-After": "30"},
            json={"ErrorType": error_code, "ErrorDetails": "secret-api-key"},
        )
        http_client.post.side_effect = (
            [httpx.Response(200, json=context_payload()), response] if stage == "inspection" else [response]
        )
        await poll_once(detector, settings(action="pause_and_off"))
        calls = http_client.post.await_count
        detector._capture_frame.assert_awaited_once_with(1)
        for now in (130, 3700, 100 + 31 * 24 * 3600):
            clock.return_value = now
            await poll_once(detector, settings(poll_interval=5, action="pause_and_off"))
        assert http_client.post.await_count == calls
        detector._capture_frame.assert_awaited_once_with(1)
        detector._dispatch_action.assert_not_awaited()
        status = detector.get_status()
        assert status["last_error_code"] == error_code
        assert status["last_error"] == API_ERRORS[error_code]
        assert set(status["per_printer"]) == {1, 2}
        assert all(entry["error_code"] == error_code for entry in status["per_printer"].values())
        assert all(entry["class"] == "error" for entry in status["per_printer"].values())
        assert status["history"] == []
        assert "secret-api-key" not in str(status)
        if stage == "inspection":
            assert detector._states[1].context.process_url == PRIMARY_URL

    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_unrecognized_auth_response_still_blocks_the_account(self, detector, http_client, clock, status_code):
        http_client.post.return_value = httpx.Response(status_code, text="secret-api-key")
        await detector._check_printer(1, printer_status(), settings())
        clock.return_value = 1000
        await detector._check_printer(2, printer_status(), settings())
        assert detector.get_status()["last_error_code"] == "OE_INVALID_API_KEY"
        http_client.post.assert_awaited_once()
        detector._capture_frame.assert_awaited_once()

    @pytest.mark.parametrize(
        "change", ["confidence", "interval", "action", "selection", "disabled", "reset", "new_print", "disconnect"]
    )
    async def test_ordinary_settings_and_lifecycle_changes_do_not_clear_account_block(
        self, detector, http_client, manager, clock, change
    ):
        http_client.post.side_effect = [httpx.Response(200, json=context_payload()), quota_response()]
        await poll_once(detector, settings())
        clock.return_value = 1000
        configuration = settings()
        if change == "reset":
            detector.reset_printer(1)
        elif change == "new_print":
            manager.get_all_statuses.return_value[1] = printer_status(subtask_id="print-2")
        elif change == "disconnect":
            manager.is_connected.return_value = False
        else:
            configuration.update(
                {
                    "confidence": {"confidence": "high"},
                    "interval": {"poll_interval": 5},
                    "action": {"action": "pause_and_off"},
                    "selection": {"enabled_printers": set()},
                    "disabled": {"enabled": False},
                }[change]
            )
        await poll_once(detector, configuration)
        assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        manager.is_connected.return_value = True
        await poll_once(detector, settings())
        status = detector.get_status()
        assert status["last_error_code"] == USAGE_LIMIT_ERROR
        assert status["last_error"] == API_ERRORS[USAGE_LIMIT_ERROR]
        assert status["per_printer"][1]["error_code"] == USAGE_LIMIT_ERROR
        assert http_client.post.await_count == 2
        detector._capture_frame.assert_awaited_once()
        detector._dispatch_action.assert_not_awaited()

    @pytest.mark.parametrize("tested_key, recovered", [("secret-api-key", True), ("another-key", False)])
    async def test_only_successful_test_of_configured_key_resumes_checks(
        self, detector, http_client, manager, clock, tested_key, recovered
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            ip_restricted_response(),
            httpx.Response(200, json=context_payload(ContextId="test-context")),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        context = detector._states[1].context
        assert (await detector.test_connection(tested_key))["ok"] is True
        clock.return_value = 120
        await poll_once(detector, settings())
        assert http_client.post.await_count == (4 if recovered else 3)
        assert detector.get_per_printer()[1]["class"] == ("safe" if recovered else "error")
        assert detector._states[1].context is context
        assert detector._states[1].context.process_url == PRIMARY_URL

    async def test_recovery_preserves_context_actions_and_server_deadline(self, detector, http_client, manager, clock):
        assessment = process_payload(NextProcessIntervalSec={"Minimum": 300, "Recommended": 20}, PauseSuggested=True)
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=assessment),
            quota_response(),
            httpx.Response(200, json=context_payload(ContextId="test-context")),
            httpx.Response(200, json=assessment),
        ]
        configuration = settings(action="pause_and_off")
        await poll_once(detector, configuration)
        state = detector._states[1]
        context = state.context
        clock.return_value = 400
        await poll_once(detector, configuration)
        assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        assert (await detector.test_connection("secret-api-key"))["ok"] is True
        assert detector._states[1] is state
        assert state.context is context
        assert state.warning_fired and state.action_fired
        assert state.interval == 300
        assert state.next_check_at == 700
        clock.return_value = 699
        await poll_once(detector, configuration)
        assert http_client.post.await_count == 4
        clock.return_value = 700
        await poll_once(detector, configuration)
        assert http_client.post.await_count == 5
        assert http_client.post.await_args.args == (PRIMARY_URL,)
        assert detector.get_status()["last_error_code"] is None
        assert detector.get_status()["last_error"] is None
        assert state.context is context
        detector._dispatch_action.assert_awaited_once()

    async def test_saving_replacement_key_recovers_without_repeating_actions(
        self, detector, http_client, manager, clock
    ):
        assessment = process_payload(NextProcessIntervalSec={"Minimum": 300, "Recommended": 20}, PauseSuggested=True)
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=assessment),
            quota_response(),
            httpx.Response(200, json=context_payload(ContextId="new-context")),
            httpx.Response(200, json=assessment),
        ]
        await poll_once(detector, settings(action="pause"))
        state = detector._states[1]
        clock.return_value = 400
        await poll_once(detector, settings(action="pause"))
        configuration = settings(api_key="replacement-key", action="pause")
        clock.return_value = 401
        await poll_once(detector, configuration)
        assert detector.get_status()["last_error_code"] is None
        assert state.context is None
        assert state.interval == 300
        assert state.next_check_at == 700
        assert state.warning_fired and state.action_fired
        assert http_client.post.await_count == 3
        clock.return_value = 700
        await poll_once(detector, configuration)
        assert state.context.context_id == "new-context"
        creation = http_client.post.await_args_list[3]
        assert creation.args == (CREATE_CONTEXT_URL,)
        assert creation.kwargs["headers"]["X-API-Key"] == "replacement-key"
        assert detector.get_per_printer()[1]["class"] == "failure"
        detector._dispatch_action.assert_awaited_once()

    async def test_successful_test_cannot_confirm_remaining_inspection_allowance(
        self, detector, http_client, manager, clock
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            quota_response(),
            httpx.Response(200, json=context_payload(ContextId="test-context")),
            quota_response(),
        ]
        await poll_once(detector, settings())
        assert (await detector.test_connection("secret-api-key"))["ok"] is True
        clock.return_value = 120
        await poll_once(detector, settings())
        assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        clock.return_value = 1000
        await poll_once(detector, settings())
        assert http_client.post.await_count == 4
        assert detector._capture_frame.await_count == 2

    @pytest.mark.parametrize("response", [httpx.Response(503), httpx.ReadTimeout("private-details")])
    async def test_failed_test_does_not_clear_account_block(self, detector, http_client, manager, clock, response):
        http_client.post.side_effect = [httpx.Response(200, json=context_payload()), quota_response(), response]
        await poll_once(detector, settings())
        assert (await detector.test_connection("secret-api-key"))["ok"] is False
        clock.return_value = 1000
        await poll_once(detector, settings())
        assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        assert http_client.post.await_count == 3
        detector._capture_frame.assert_awaited_once()

    @pytest.mark.parametrize("tested_key, blocks", [("secret-api-key", True), ("another-key", False)])
    async def test_account_error_testing_configured_key_stops_monitoring(
        self, detector, http_client, manager, clock, tested_key, blocks
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            ip_restricted_response(),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        assert (await detector.test_connection(tested_key))["ok"] is False
        clock.return_value = 140
        await poll_once(detector, settings())
        assert http_client.post.await_count == (3 if blocks else 4)
        assert detector.get_status()["last_error_code"] == (IP_RESTRICTED_ERROR if blocks else None)

    @pytest.mark.parametrize("replace_key", [False, True])
    async def test_stale_successful_test_cannot_clear_a_newer_account_block(
        self, detector, http_client, manager, clock, replace_key
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        started = asyncio.Event()
        release = asyncio.Event()

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                if kwargs["headers"]["X-API-Key"] == "secret-api-key":
                    started.set()
                    await release.wait()
                return httpx.Response(200, json=context_payload(ContextId="test-context"))
            return quota_response()

        http_client.post.side_effect = respond
        test = asyncio.create_task(detector.test_connection("secret-api-key"))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            configuration = settings(api_key="replacement-key") if replace_key else settings()
            clock.return_value = 140
            await poll_once(detector, configuration)
            assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
            release.set()
            assert (await asyncio.wait_for(test, timeout=1))["ok"] is True
            clock.return_value = 1000
            captures = detector._capture_frame.await_count
            await poll_once(detector, configuration)
            assert detector._capture_frame.await_count == captures
            assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        finally:
            release.set()
            await asyncio.gather(test, return_exceptions=True)

    async def test_stale_failed_test_cannot_block_a_replacement_key(self, detector, http_client, manager, clock):
        detector._apply_settings(settings())
        started = asyncio.Event()
        release = asyncio.Event()

        async def respond(url, **kwargs):
            started.set()
            await release.wait()
            return ip_restricted_response()

        http_client.post.side_effect = respond
        test = asyncio.create_task(detector.test_connection("secret-api-key"))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            detector._apply_settings(settings(api_key="replacement-key"))
            release.set()
            assert (await asyncio.wait_for(test, timeout=1))["ok"] is False
            assert detector.get_status()["last_error_code"] is None
        finally:
            release.set()
            await asyncio.gather(test, return_exceptions=True)

    @pytest.mark.parametrize("change", ["paused", "finished", "reset"])
    async def test_account_error_from_ending_print_still_blocks_other_printers(
        self, detector, http_client, clock, change
    ):
        status = printer_status()
        detector._apply_settings(settings())

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            if change == "reset":
                detector.reset_printer(1)
            else:
                status.state = "PAUSE" if change == "paused" else "FINISH"
            return quota_response()

        http_client.post.side_effect = respond
        await detector._check_printer(1, status, settings())
        await detector._check_printer(2, printer_status(subtask_id="print-2"), settings())
        assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        assert detector.get_per_printer()[2]["error_code"] == USAGE_LIMIT_ERROR
        assert http_client.post.await_count == 2
        detector._capture_frame.assert_awaited_once_with(1)
        detector._dispatch_action.assert_not_awaited()

    @pytest.mark.parametrize("changes", [{"api_key": "replacement-key"}, {"confidence": "high"}])
    async def test_account_error_from_old_configuration_cannot_block_new_configuration(
        self, detector, http_client, clock, changes
    ):
        detector._apply_settings(settings())

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            detector._apply_settings(settings(**changes))
            return quota_response()

        http_client.post.side_effect = respond
        await detector._check_printer(1, printer_status(), settings())
        assert detector.get_status()["last_error_code"] is None
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        clock.return_value = 120
        await detector._check_printer(1, printer_status(), settings(**changes))
        assert detector.get_per_printer()[1]["class"] == "safe"
        assert http_client.post.await_count == 4

    @pytest.mark.parametrize("stage", ["camera", "inspection"])
    @pytest.mark.parametrize("ignore_cancellation", [False, True])
    async def test_account_block_cancels_sibling_checks_and_discards_late_decisions(
        self, detector, http_client, manager, clock, stage, ignore_cancellation
    ):
        manager.get_all_statuses.return_value = {2: printer_status(subtask_id="print-2"), 1: printer_status()}
        started = asyncio.Event()
        canceled = asyncio.Event()

        async def wait_for_block():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                canceled.set()
                if not ignore_cancellation:
                    raise

        async def capture(printer_id):
            if printer_id == 2 and stage == "camera":
                await wait_for_block()
            if printer_id == 1:
                await started.wait()
            return FAKE_JPEG + bytes([printer_id])

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            if kwargs["files"]["image"][1][-1] == 1:
                return quota_response()
            await wait_for_block()
            return httpx.Response(200, json=process_payload(PauseSuggested=True))

        detector._capture_frame.side_effect = capture
        http_client.post.side_effect = respond
        await detector._poll_once(settings(action="pause_and_off"))
        checks = list(detector._checks.values())
        try:
            await asyncio.wait_for(asyncio.gather(*checks, return_exceptions=True), timeout=1)
            assert canceled.is_set()
            assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
            assert detector._states[2].frame_count == 0
            assert not detector._states[2].action_fired
            assert detector.get_status()["history"] == []
            detector._dispatch_action.assert_not_awaited()
            captures = detector._capture_frame.await_count
            calls = http_client.post.await_count
            clock.return_value = 1000
            await poll_once(detector, settings(action="pause_and_off"))
            assert detector._capture_frame.await_count == captures
            assert http_client.post.await_count == calls
        finally:
            detector.stop()
            await asyncio.gather(*checks, return_exceptions=True)

    @pytest.mark.parametrize(
        "response, expected_url",
        [
            (httpx.Response(400, json={"ErrorType": "OE_BAD_ARGS"}), PRIMARY_URL),
            (httpx.Response(400, json={"ErrorType": "OE_ARGS_PARSE_FAILED"}), PRIMARY_URL),
            (httpx.Response(422, json={"ErrorType": "OE_IMAGE_DECODE_FAILED"}), PRIMARY_URL),
            (httpx.Response(429, json={"ErrorType": "OE_CONTEXT_RATE_LIMITED"}), PRIMARY_URL),
            (httpx.Response(500, json={"ErrorType": "OE_BAD_ARGS"}), PRIMARY_URL),
            (httpx.Response(200, text="not-json"), PRIMARY_URL),
            (httpx.Response(503, json={"ErrorType": "OE_BACKEND_THROTTLED"}), FALLBACK_URL),
            (httpx.Response(500, json={"ErrorType": "OE_INTERNAL_ERROR"}), FALLBACK_URL),
            (httpx.Response(502, text="not-json"), FALLBACK_URL),
            (httpx.ConnectError("private-details"), FALLBACK_URL),
            (httpx.ReadTimeout("private-details"), FALLBACK_URL),
        ],
    )
    async def test_fallback_is_only_used_for_transport_or_server_failures(
        self, detector, http_client, clock, response, expected_url
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            response,
            httpx.Response(200, json=process_payload()),
        ]
        await detector._check_printer(1, printer_status(), settings())
        context = detector._states[1].context
        assert context.process_url == expected_url
        clock.return_value = 160
        await detector._check_printer(1, printer_status(), settings())
        assert detector._states[1].context is context
        assert http_client.post.await_args.args == (expected_url,)
        assert detector.get_per_printer()[1]["class"] == "safe"

    async def test_loop_error_does_not_hide_account_block(self, detector, http_client, clock):
        http_client.post.side_effect = [httpx.Response(200, json=context_payload()), quota_response()]
        await detector._check_printer(1, printer_status(), settings())
        detector._load_settings = AsyncMock(side_effect=RuntimeError("secret-api-key"))
        with (
            patch(f"{MODULE}.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
            pytest.raises(asyncio.CancelledError),
        ):
            await detector._loop()
        assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        assert detector.get_status()["last_error"] == API_ERRORS[USAGE_LIMIT_ERROR]

    async def test_action_error_does_not_hide_account_block(self, detector, http_client, clock):
        http_client.post.side_effect = [httpx.Response(200, json=context_payload()), quota_response()]
        await detector._check_printer(1, printer_status(), settings())
        detector._dispatch_action = OctoEverywhereDetectionService._dispatch_action.__get__(detector)
        with patch(
            "backend.app.services.octoeverywhere_actions.execute_action", side_effect=RuntimeError("secret-api-key")
        ):
            await detector._dispatch_action(1, "notify", "test-print", 1)
        assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
        assert detector.get_status()["last_error"] == API_ERRORS[USAGE_LIMIT_ERROR]


class TestScheduling:
    @pytest.mark.parametrize("slow_stage", ["camera", "api"])
    @pytest.mark.parametrize("faster, interval", [(False, 20), (True, 5)])
    async def test_slow_printer_does_not_delay_other_inspections(
        self, detector, http_client, manager, clock, slow_stage, faster, interval
    ):
        blocked = asyncio.Event()
        manager.get_all_statuses.return_value[2] = printer_status(subtask_id="print-2")

        async def capture(printer_id):
            if printer_id == 1 and slow_stage == "camera":
                blocked.set()
                await asyncio.Event().wait()
            return FAKE_JPEG + bytes([printer_id])

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            if kwargs["files"]["image"][1][-1] == 1 and slow_stage == "api":
                blocked.set()
                await asyncio.Event().wait()
            return httpx.Response(
                200,
                json=process_payload(
                    NextProcessIntervalSec={"Minimum": 5, "Recommended": 5},
                    FasterInspectionSuggested=faster,
                ),
            )

        detector._capture_frame.side_effect = capture
        http_client.post.side_effect = respond
        poll = asyncio.create_task(detector._poll_once(settings()))
        try:
            await asyncio.wait_for(blocked.wait(), timeout=1)
            await asyncio.wait_for(asyncio.shield(poll), timeout=1)
            slow_check = detector._checks[1]
            context = detector._states[2].context
            assert detector._states[2].frame_count == 1

            clock.return_value = 100 + interval - 1
            await asyncio.wait_for(detector._poll_once(settings()), timeout=1)
            await asyncio.sleep(0)
            assert detector._states[2].frame_count == 1

            for frame_count in (2, 3):
                clock.return_value = 100 + interval * (frame_count - 1)
                await asyncio.wait_for(detector._poll_once(settings()), timeout=1)
                await asyncio.sleep(0)
                assert detector._states[2].frame_count == frame_count
                assert detector._states[2].context is context
                assert detector._checks[1] is slow_check
                assert not slow_check.done()

            captures = [call.args[0] for call in detector._capture_frame.await_args_list]
            assert captures.count(1) == 1
            assert captures.count(2) == 3
        finally:
            checks = list(detector._checks.values())
            detector.stop()
            poll.cancel()
            await asyncio.gather(poll, *checks, return_exceptions=True)

    @pytest.mark.parametrize("change", ["paused", "unknown", "disconnected", "finished", "removed", "deselected"])
    async def test_poll_cancels_stale_inspection_without_waiting_for_response(
        self, detector, http_client, manager, clock, change
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings(action="pause"))
        state = detector._states[1]
        context = state.context
        processing = asyncio.Event()

        async def pending_response(url, **kwargs):
            processing.set()
            await asyncio.Event().wait()

        http_client.post.side_effect = pending_response
        clock.return_value = 140
        await detector._poll_once(settings(action="pause"))
        check = detector._checks[1]
        try:
            await asyncio.wait_for(processing.wait(), timeout=1)
            configuration = settings(action="pause")
            if change in ("paused", "unknown", "finished"):
                manager.get_all_statuses.return_value[1] = printer_status(
                    state={"paused": "PAUSE", "unknown": "unknown", "finished": "FINISH"}[change]
                )
            elif change == "disconnected":
                manager.is_connected.return_value = False
            elif change == "removed":
                manager.get_all_statuses.return_value = {}
            else:
                configuration["enabled_printers"] = set()
            await detector._poll_once(configuration)
            await asyncio.wait_for(asyncio.gather(check, return_exceptions=True), timeout=1)

            assert check.cancelled()
            assert detector._checks == {}
            detector._dispatch_action.assert_not_awaited()
            if change in ("paused", "unknown", "disconnected"):
                assert detector._states[1] is state
                assert state.context is context
                assert state.frame_count == 1
            else:
                assert detector.get_per_printer() == {}
        finally:
            detector.stop()
            await asyncio.gather(check, return_exceptions=True)

    async def test_completed_old_check_cannot_remove_replacement(self, detector, manager, clock):
        started = asyncio.Event()

        async def capture(printer_id):
            started.set()
            await asyncio.Event().wait()

        detector._capture_frame.side_effect = capture
        await detector._poll_once(settings())
        old_check = detector._checks[1]
        await asyncio.wait_for(started.wait(), timeout=1)
        manager.get_all_statuses.return_value[1] = printer_status(subtask_id="print-2")
        await detector._poll_once(settings())
        new_check = detector._checks[1]
        try:
            await asyncio.wait_for(asyncio.gather(old_check, return_exceptions=True), timeout=1)
            assert old_check.cancelled()
            assert new_check is not old_check
            assert detector._checks[1] is new_check
            assert not new_check.done()
            assert detector._states[1].print_id == "print-2"
        finally:
            detector.stop()
            await asyncio.gather(old_check, new_check, return_exceptions=True)

    async def test_unexpected_check_error_is_logged_safely_and_does_not_block_retry(self, detector, manager, caplog):
        detector._check_printer = AsyncMock(side_effect=[RuntimeError("secret-api-key"), None])
        with caplog.at_level(logging.ERROR, logger=MODULE):
            await poll_once(detector, settings())
            assert detector._checks == {}
            await poll_once(detector, settings())

        assert detector._check_printer.await_count == 2
        assert "OctoEverywhere printer check failed: RuntimeError" in caplog.text
        assert "secret-api-key" not in caplog.text


class TestLifecycle:
    @pytest.mark.parametrize("initial_id", [None, "", "0", 0])
    async def test_late_job_id_reuses_context_and_later_new_job_replaces_it(
        self, detector, http_client, manager, clock, initial_id
    ):
        new_url = PRIMARY_URL.replace("context-one", "context-two")
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=context_payload(ContextId="context-two", ProcessRequestUrl=new_url)),
            httpx.Response(200, json=process_payload()),
        ]
        status = manager.get_all_statuses.return_value[1]
        status.subtask_id = initial_id
        await poll_once(detector, settings())
        state = detector._states[1]
        context = state.context

        status.subtask_id = "print-1"
        clock.return_value = 140
        await poll_once(detector, settings())
        assert detector._states[1] is state
        assert state.context is context
        assert state.frame_count == 2

        status.subtask_id = "print-2"
        clock.return_value = 180
        await poll_once(detector, settings())
        assert detector._states[1] is not state
        assert detector._states[1].context.context_id == "context-two"
        assert detector._states[1].frame_count == 1
        assert [call.args[0] for call in http_client.post.await_args_list] == [
            CREATE_CONTEXT_URL,
            PRIMARY_URL,
            PRIMARY_URL,
            CREATE_CONTEXT_URL,
            new_url,
        ]

    @pytest.mark.parametrize("missing_id", [None, "", "0", 0])
    async def test_missing_job_id_preserves_context_and_last_known_job(
        self, detector, http_client, manager, clock, missing_id
    ):
        new_url = PRIMARY_URL.replace("context-one", "context-two")
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=context_payload(ContextId="context-two", ProcessRequestUrl=new_url)),
            httpx.Response(200, json=process_payload()),
        ]
        status = manager.get_all_statuses.return_value[1]
        await poll_once(detector, settings())
        state = detector._states[1]
        context = state.context

        for now, job_id in ((140, missing_id), (180, "print-1"), (220, missing_id)):
            status.subtask_id = job_id
            clock.return_value = now
            await poll_once(detector, settings())
            assert detector._states[1] is state
            assert state.context is context
        assert state.frame_count == 4

        status.subtask_id = "print-2"
        clock.return_value = 260
        await poll_once(detector, settings())
        assert detector._states[1] is not state
        assert detector._states[1].context.context_id == "context-two"
        assert [call.args[0] for call in http_client.post.await_args_list] == [
            CREATE_CONTEXT_URL,
            PRIMARY_URL,
            PRIMARY_URL,
            PRIMARY_URL,
            PRIMARY_URL,
            CREATE_CONTEXT_URL,
            new_url,
        ]

    async def test_filename_and_name_changes_without_job_id_reuse_printer_context(
        self, detector, http_client, manager, clock
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        status = manager.get_all_statuses.return_value[1]
        status.subtask_id = None
        await poll_once(detector, settings())
        state = detector._states[1]
        context = state.context

        for now, name, filename in ((140, "renamed-print", "plate_2.gcode"), (180, None, None)):
            status.subtask_name = name
            status.gcode_file = filename
            clock.return_value = now
            await poll_once(detector, settings())
            assert detector._states[1] is state
            assert state.context is context
        assert state.frame_count == 3
        assert [call.args[0] for call in http_client.post.await_args_list] == [
            CREATE_CONTEXT_URL,
            PRIMARY_URL,
            PRIMARY_URL,
            PRIMARY_URL,
        ]

    @pytest.mark.parametrize(
        "transient_status",
        [printer_status(state="unknown"), printer_status(state="PREPARE"), printer_status(state=None), None],
    )
    async def test_transient_printer_status_preserves_context_without_uploading(
        self, detector, http_client, manager, clock, transient_status
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        status = manager.get_all_statuses.return_value[1]
        await poll_once(detector, settings())
        state = detector._states[1]
        context = state.context

        manager.get_all_statuses.return_value[1] = transient_status
        clock.return_value = 300
        await poll_once(detector, settings())
        assert detector._states[1] is state
        assert state.context is context
        assert http_client.post.await_count == 2
        detector._capture_frame.assert_awaited_once()

        manager.get_all_statuses.return_value[1] = status
        await poll_once(detector, settings())
        assert detector._states[1] is state
        assert state.context is context
        assert state.frame_count == 2
        assert [call.args[0] for call in http_client.post.await_args_list] == [
            CREATE_CONTEXT_URL,
            PRIMARY_URL,
            PRIMARY_URL,
        ]

    @pytest.mark.parametrize("phase", ["capture", "context", "process"])
    async def test_late_metadata_during_inspection_preserves_context_and_result(
        self, detector, http_client, clock, phase
    ):
        status = printer_status(subtask_id=None)

        def update_metadata():
            status.subtask_id = "print-1"
            status.subtask_name = "updated-print-name"
            status.gcode_file = "updated-plate.gcode"

        async def capture(printer_id):
            if phase == "capture":
                update_metadata()
            return FAKE_JPEG

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                if phase == "context":
                    update_metadata()
                return httpx.Response(200, json=context_payload())
            if phase == "process":
                update_metadata()
            return httpx.Response(200, json=process_payload())

        detector._capture_frame.side_effect = capture
        http_client.post.side_effect = respond
        await detector._check_printer(1, status, settings())
        state = detector._states[1]
        context = state.context
        assert context is not None
        assert state.frame_count == 1
        assert detector.get_per_printer()[1]["class"] == "safe"

        clock.return_value = 140
        await detector._check_printer(1, status, settings())
        assert detector._states[1] is state
        assert state.context is context
        assert state.frame_count == 2
        assert [call.args[0] for call in http_client.post.await_args_list] == [
            CREATE_CONTEXT_URL,
            PRIMARY_URL,
            PRIMARY_URL,
        ]

    async def test_known_job_change_during_processing_discards_stale_result(self, detector, http_client, clock):
        status = printer_status()

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            status.subtask_id = "print-2"
            return httpx.Response(200, json=process_payload(PauseSuggested=True))

        http_client.post.side_effect = respond
        await detector._check_printer(1, status, settings(action="pause"))
        state = detector._states[1]
        assert state.frame_count == 0
        assert detector.get_status()["history"] == []
        detector._dispatch_action.assert_not_awaited()

        new_url = PRIMARY_URL.replace("context-one", "context-two")
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload(ContextId="context-two", ProcessRequestUrl=new_url)),
            httpx.Response(200, json=process_payload()),
        ]
        await detector._check_printer(1, status, settings(action="pause"))
        assert detector._states[1] is not state
        assert detector._states[1].context.context_id == "context-two"
        assert detector._states[1].frame_count == 1
        assert [call.args[0] for call in http_client.post.await_args_list] == [
            CREATE_CONTEXT_URL,
            PRIMARY_URL,
            CREATE_CONTEXT_URL,
            new_url,
        ]
        detector._dispatch_action.assert_not_awaited()

    async def test_pause_preserves_context_and_does_not_upload(self, detector, http_client, manager, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        context = detector._states[1].context
        manager.get_all_statuses.return_value[1].state = "PAUSE"
        clock.return_value = 300
        await poll_once(detector, settings())
        assert detector._states[1].context is context
        assert http_client.post.await_count == 2
        manager.get_all_statuses.return_value[1].state = "RUNNING"
        await poll_once(detector, settings())
        assert detector._states[1].context is context
        assert http_client.post.await_count == 3

    @pytest.mark.parametrize("terminal_state", ["FINISH", "FAILED", "IDLE"])
    async def test_end_clears_state_and_identical_job_gets_new_context(
        self, detector, http_client, manager, clock, terminal_state
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=context_payload(ContextId="new-context")),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        manager.get_all_statuses.return_value[1].state = terminal_state
        await poll_once(detector, settings())
        assert detector.get_per_printer() == {}
        manager.get_all_statuses.return_value[1].state = "RUNNING"
        await poll_once(detector, settings())
        assert detector._states[1].context.context_id == "new-context"
        assert detector._states[1].frame_count == 1

    async def test_same_filename_new_job_id_gets_fresh_context(self, detector, http_client, manager, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(PauseSuggested=True)),
            httpx.Response(200, json=context_payload(ContextId="new-context")),
            httpx.Response(200, json=process_payload(PauseSuggested=True)),
        ]
        await poll_once(detector, settings())
        manager.get_all_statuses.return_value[1].subtask_id = "print-2"
        await poll_once(detector, settings())
        assert detector._states[1].context.context_id == "new-context"
        assert detector._dispatch_action.await_count == 2

    @pytest.mark.parametrize("changes", [{"api_key": "replacement-key"}, {"confidence": "high"}])
    async def test_credentials_or_confidence_change_recreates_context(
        self, detector, http_client, manager, clock, changes
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(PauseSuggested=True)),
            httpx.Response(200, json=context_payload(ContextId="new-context")),
            httpx.Response(200, json=process_payload(PauseSuggested=True)),
        ]
        await poll_once(detector, settings())
        await poll_once(detector, settings(**changes))
        assert detector._states[1].context is None
        assert detector._states[1].next_check_at == 140
        assert http_client.post.await_count == 2
        clock.return_value = 140
        await poll_once(detector, settings(**changes))
        assert detector._states[1].context.context_id == "new-context"
        detector._dispatch_action.assert_awaited_once()
        creation = http_client.post.await_args_list[2]
        assert creation.kwargs["headers"]["X-API-Key"] == changes.get("api_key", "secret-api-key")
        assert creation.kwargs["json"]["PauseConfidenceLevel"] == (4 if "confidence" in changes else 3)

    @pytest.mark.parametrize(
        "changes", [{"enabled": False}, {"api_key": ""}, {"enabled_printers": set()}, {"enabled_printers": {2}}]
    )
    async def test_disabled_printers_stop_uploading_and_clear_status(
        self, detector, http_client, manager, clock, changes
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        await poll_once(detector, settings(**changes))
        assert detector.get_per_printer() == {}
        assert http_client.post.await_count == 2

    async def test_deleted_printer_clears_state(self, detector, http_client, manager, clock):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        manager.get_all_statuses.return_value = {}
        await poll_once(detector, settings())
        assert detector.get_per_printer() == {}

    async def test_disconnected_printer_retains_context_without_stale_safe_status(
        self, detector, http_client, manager, clock
    ):
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload()),
            httpx.Response(200, json=process_payload()),
        ]
        await poll_once(detector, settings())
        context = detector._states[1].context
        manager.is_connected.return_value = False
        clock.return_value = 300
        await poll_once(detector, settings())
        assert detector.get_per_printer()[1]["class"] == "error"
        assert detector._states[1].context is context
        assert http_client.post.await_count == 2
        manager.is_connected.return_value = True
        await poll_once(detector, settings())
        assert detector._states[1].context is context
        assert detector.get_per_printer()[1]["class"] == "safe"

    async def test_each_printer_owns_a_separate_context_and_interval(self, detector, http_client, manager, clock):
        manager.get_all_statuses.return_value[2] = printer_status(subtask_id="print-2")
        count = 0

        async def respond(url, **kwargs):
            nonlocal count
            if url == CREATE_CONTEXT_URL:
                count += 1
                return httpx.Response(200, json=context_payload(ContextId=f"context-{count}"))
            return httpx.Response(
                200, json=process_payload(NextProcessIntervalSec={"Minimum": 40 * count, "Recommended": 20})
            )

        http_client.post.side_effect = respond
        await poll_once(detector, settings())
        assert {state.context.context_id for state in detector._states.values()} == {"context-1", "context-2"}
        assert detector._states[1].next_check_at == 140
        assert detector._states[2].next_check_at == 180

    def test_stop_cancels_service_and_clears_live_status(self, detector):
        task = MagicMock()
        detector._task = task
        detector._last_error = "an old error"
        detector.stop()
        task.cancel.assert_called_once()
        assert detector.get_status() == {
            "is_running": False,
            "last_error": None,
            "last_error_code": None,
            "per_printer": {},
            "history": [],
        }

    async def test_enabled_without_key_reports_configuration_error(self, detector, http_client, manager):
        await poll_once(detector, settings(api_key=""))
        assert "API key is required" in detector.get_status()["last_error"]
        http_client.post.assert_not_awaited()

    async def test_start_is_idempotent(self, detector):
        with patch.object(detector, "_loop", new=AsyncMock()):
            await detector.start()
            first_task = detector._task
            await detector.start()
            assert detector._task is first_task
            assert detector.get_status()["is_running"] is True
            detector.stop()
            with pytest.raises(asyncio.CancelledError):
                await first_task

    @pytest.mark.parametrize(
        "enabled, poll_interval, expected_poll", [(False, 20, 5), (True, 5, 1), (True, 21, 1), (True, 30, 1)]
    )
    async def test_enabled_detection_uses_one_second_scheduler_resolution(
        self, detector, enabled, poll_interval, expected_poll
    ):
        detector._load_settings = AsyncMock(return_value=settings(enabled=enabled, poll_interval=poll_interval))
        detector._poll_once = AsyncMock()
        with patch(f"{MODULE}.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)) as sleep:
            await detector._loop()
        sleep.assert_awaited_once_with(expected_poll)

    @pytest.mark.parametrize("poll_interval", [5, 20, 21, 30])
    async def test_scheduler_caches_settings_until_configured_poll_interval(self, detector, clock, poll_interval):
        original = settings(poll_interval=poll_interval)
        updated = settings(poll_interval=poll_interval, action="pause")
        detector._load_settings = AsyncMock(side_effect=[original, updated])
        detector._poll_once = AsyncMock()

        async def advance_time(seconds):
            clock.return_value += seconds
            if clock.return_value >= 100 + poll_interval + 2:
                raise asyncio.CancelledError

        with patch(f"{MODULE}.asyncio.sleep", side_effect=advance_time):
            await detector._loop()

        assert detector._load_settings.await_count == 2
        polled_settings = [call.args[0] for call in detector._poll_once.await_args_list]
        assert polled_settings == [original] * poll_interval + [updated] * 2

    async def test_refresh_replaces_cached_settings_immediately_without_reloading_on_restart(self, detector, clock):
        original = settings()
        updated = settings(action="pause")
        detector._load_settings = AsyncMock(side_effect=[original, updated])
        polled = asyncio.Event()
        detector._poll_once = AsyncMock(side_effect=lambda configuration: polled.set())
        await detector.start()
        try:
            await asyncio.wait_for(polled.wait(), timeout=1)
            detector._load_settings.assert_awaited_once()
            polled.clear()
            await detector.refresh_settings()
            await asyncio.wait_for(polled.wait(), timeout=1)
            assert detector._load_settings.await_count == 2
            assert detector._poll_once.await_args.args == (updated,)
            assert detector._settings is updated
        finally:
            task = detector._task
            detector.stop()
            await asyncio.gather(task, return_exceptions=True)
        assert detector._settings is None

    async def test_stale_settings_load_never_populates_cache(self, detector, clock):
        original = settings()
        updated = settings(action="pause")

        async def load_settings():
            if detector._load_settings.await_count == 1:
                detector._generation += 1
                return original
            return updated

        detector._load_settings = AsyncMock(side_effect=load_settings)
        detector._poll_once = AsyncMock()
        with patch(f"{MODULE}.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            await detector._loop()
        assert detector._load_settings.await_count == 2
        detector._poll_once.assert_awaited_once_with(updated)
        assert detector._settings is updated

    async def test_concurrent_refresh_keeps_the_latest_settings_in_cache(self, detector, clock):
        loading = asyncio.Event()
        release = asyncio.Event()
        updated = settings(action="pause")

        async def load_settings():
            if detector._load_settings.await_count == 1:
                loading.set()
                await release.wait()
                return settings()
            return updated

        detector._load_settings = AsyncMock(side_effect=load_settings)
        refresh = asyncio.create_task(detector.refresh_settings())
        try:
            await asyncio.wait_for(loading.wait(), timeout=1)
            await detector.refresh_settings()
        finally:
            release.set()
            await asyncio.wait_for(refresh, timeout=1)
        assert detector._settings is updated

    async def test_refresh_cancels_stale_settings_load_before_polling(self, detector, clock):
        loading = asyncio.Event()
        cancelled = asyncio.Event()

        async def load_old_settings():
            loading.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        detector._load_settings = AsyncMock(side_effect=load_old_settings)
        detector._poll_once = AsyncMock()
        await detector.start()
        await asyncio.wait_for(loading.wait(), timeout=1)
        detector._load_settings = AsyncMock(return_value=settings(enabled=False))
        await detector.refresh_settings()
        assert cancelled.is_set()
        assert detector.get_status()["is_running"] is True
        await asyncio.sleep(0)
        for call in detector._poll_once.await_args_list:
            assert call.args[0]["enabled"] is False
        task = detector._task
        detector.stop()
        await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.parametrize("change", ["print_ended", "disabled", "key_changed", "account_blocked"])
    async def test_cancel_during_action_lookup_prevents_hardware_control(
        self, detector, http_client, manager, clock, change
    ):
        lookup_started = asyncio.Event()
        lookup_cancelled = asyncio.Event()

        async def get_printer_name(printer_id):
            lookup_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                lookup_cancelled.set()
                raise

        # Exercise the real action dispatcher: its printer-name database lookup
        # awaits before pause/power-off, which used to leave a cancellation gap.
        detector._dispatch_action = OctoEverywhereDetectionService._dispatch_action.__get__(detector)
        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(PauseSuggested=True)),
        ]
        with (
            patch("backend.app.services.obico_actions._get_printer_name", side_effect=get_printer_name),
            patch("backend.app.services.obico_actions._pause_print") as pause,
            patch("backend.app.services.obico_actions._turn_off_linked_plugs", new=AsyncMock()) as turn_off,
            patch("backend.app.services.obico_actions._notify", new=AsyncMock()) as notify,
        ):
            poll = asyncio.create_task(poll_once(detector, settings(action="pause_and_off")))
            await asyncio.wait_for(lookup_started.wait(), timeout=1)
            if change == "print_ended":
                detector.reset_printer(1)
            elif change == "account_blocked":
                http_client.post.side_effect = [httpx.Response(200, json=context_payload()), quota_response()]
                await detector._check_printer(2, printer_status(subtask_id="print-2"), settings(action="pause_and_off"))
            else:
                detector._load_settings = AsyncMock(
                    return_value=settings(enabled=False)
                    if change == "disabled"
                    else settings(api_key="replacement-key")
                )
                await detector.refresh_settings()
            await asyncio.wait_for(poll, timeout=1)
            assert lookup_cancelled.is_set()
            pause.assert_not_called()
            turn_off.assert_not_awaited()
            notify.assert_not_awaited()
            if change == "key_changed":
                assert detector._states[1].action_fired is False
                assert detector._states[1].warning_fired is False
                assert detector._states[1].context is None
            elif change == "account_blocked":
                assert detector.get_status()["last_error_code"] == USAGE_LIMIT_ERROR
                assert detector._states[1].action_fired is False
                assert detector._states[1].warning_fired is False
            else:
                assert detector.get_per_printer() == {}

    async def test_cancelled_request_preserves_deadline_on_key_change(self, detector, http_client, manager, clock):
        processing = asyncio.Event()

        async def respond(url, **kwargs):
            if url == CREATE_CONTEXT_URL:
                return httpx.Response(200, json=context_payload())
            processing.set()
            await asyncio.Event().wait()

        http_client.post.side_effect = respond
        poll = asyncio.create_task(poll_once(detector, settings()))
        await asyncio.wait_for(processing.wait(), timeout=1)
        clock.return_value = 120
        detector._load_settings = AsyncMock(return_value=settings(api_key="replacement-key"))
        await detector.refresh_settings()
        await asyncio.wait_for(poll, timeout=1)
        assert detector._states[1].next_check_at == 140
        assert detector._states[1].context is None
        await poll_once(detector, settings(api_key="replacement-key"))
        assert http_client.post.await_count == 2

    async def test_interval_edit_during_request_preserves_server_minimum(self, detector, http_client, manager, clock):
        processing = asyncio.Event()

        async def pending_response(url, **kwargs):
            processing.set()
            await asyncio.Event().wait()

        http_client.post.side_effect = [
            httpx.Response(200, json=context_payload()),
            httpx.Response(200, json=process_payload(NextProcessIntervalSec={"Minimum": 60, "Recommended": 20})),
        ]
        await poll_once(detector, settings(poll_interval=30))
        http_client.post.side_effect = pending_response
        clock.return_value = 160
        poll = asyncio.create_task(poll_once(detector, settings(poll_interval=30)))
        await asyncio.wait_for(processing.wait(), timeout=1)
        clock.return_value = 165
        detector._load_settings = AsyncMock(return_value=settings(poll_interval=5))
        await detector.refresh_settings()
        await asyncio.wait_for(poll, timeout=1)
        assert detector._states[1].next_check_at == 225
        clock.return_value = 224
        await poll_once(detector, settings(poll_interval=5))
        assert http_client.post.await_count == 3


class TestSettings:
    @pytest.mark.parametrize(
        "raw, expected", [("", None), ("[1, 3]", {1, 3}), ("[]", set()), ("bad", set()), ("{}", set())]
    )
    async def test_loads_settings_and_printer_selection(self, detector, raw, expected):
        rows = [
            SimpleNamespace(key="octoeverywhere_enabled", value="true"),
            SimpleNamespace(key="octoeverywhere_api_key", value="  secret-api-key  "),
            SimpleNamespace(key="octoeverywhere_enabled_printers", value=raw),
        ]
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        db.execute.return_value = result
        with patch(f"{MODULE}.async_session") as session:
            session.return_value.__aenter__.return_value = db
            loaded = await detector._load_settings()

        assert loaded == settings(enabled_printers=expected)

    @pytest.mark.parametrize(
        "raw, expected",
        [(None, 20), ("", 20), ("null", 20), ("5", 5), ("20", 20), ("21", 21), ("30", 30), ("4", 20), ("31", 20)],
    )
    async def test_loads_inspection_interval(self, detector, raw, expected):
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [SimpleNamespace(key="octoeverywhere_poll_interval", value=raw)]
        db.execute.return_value = result
        with patch(f"{MODULE}.async_session") as session:
            session.return_value.__aenter__.return_value = db
            loaded = await detector._load_settings()
        assert loaded["poll_interval"] == expected

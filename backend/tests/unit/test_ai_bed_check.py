"""Unit tests for the AI bed-check service (selectable backend for the
build-plate empty check, see services/bedcheck_ai.py).

Mirrors test_obico_detection.py's conventions: module-level service tests,
no DB fixture, httpx.AsyncClient mocked via
patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", ...). The
dispatcher tests for check_plate_empty()'s selector logic live in
backend/tests/unit/services/test_plate_detection.py's TestSelectorDispatch
instead (that file's existing importlib-reload + mocked-cv2/numpy style is
what those tests need; this module never touches cv2 at all).
"""

import asyncio
import io
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from backend.app.schemas.settings import AppSettingsUpdate
from backend.app.services import bedcheck_ai as bedcheck_ai_module
from backend.app.services.bedcheck_ai import (
    AiBedCheckError,
    _analyze_frame_ai,
    _clamp_confidence,
    _classify_shape_error,
    _generic_fail_open_reason,
    _get_shape,
    _parse_verdict_json,
    _post_chat,
    _shape_cache_key,
    build_ai_result,
    check_bed_ai,
    get_health,
    reset_health_state,
    test_connection as bedcheck_ai_test_connection,
)


@pytest.fixture(autouse=True)
def _reset_bedcheck_ai_module_state():
    """_last_outcome / _shape_cache / _last_unavailable_notified_at are
    module-level globals that persist across tests in the same process --
    reset them before and after every test so outcome-transition, shape-cache,
    and health-registry behavior is never order-dependent on what other tests
    in this file ran first."""
    bedcheck_ai_module._last_outcome.clear()
    bedcheck_ai_module._shape_cache.clear()
    bedcheck_ai_module._last_unavailable_notified_at.clear()
    bedcheck_ai_module._pending_notify_tasks.clear()
    yield
    bedcheck_ai_module._last_outcome.clear()
    bedcheck_ai_module._shape_cache.clear()
    bedcheck_ai_module._last_unavailable_notified_at.clear()
    bedcheck_ai_module._pending_notify_tasks.clear()


def _real_jpeg_bytes() -> bytes:
    """A minimal but genuinely decodable JPEG -- _downscale_jpeg runs real
    Pillow decode/resize/re-encode over every camera frame, so a hand-typed
    byte literal (which is not a valid JPEG) only exercises the "undecodable
    frame" failure path, not the happy path. Distinct from that intentional
    garbage-bytes case in test_fails_open_on_undecodable_frame."""
    img = Image.new("RGB", (32, 32), (100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


FAKE_JPEG = _real_jpeg_bytes()
CONFIGURED_URL = "http://192.168.1.20:11434/v1"


def _mock_client(post_result=None, post_side_effect=None):
    """Build a mock httpx.AsyncClient whose .post() returns post_result (a
    mock response with .raise_for_status()/.json() already configured) or
    raises post_side_effect."""
    mock_client = MagicMock()
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=post_result)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _mock_200_response(body: dict):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    return resp


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """A real httpx.HTTPStatusError, so isinstance() checks in
    _generic_fail_open_reason work, and whose str() embeds the configured
    request URL -- exactly what _generic_fail_open_reason must strip out
    before the message reaches the user."""
    request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
    response = httpx.Response(status_code, request=request, text="unauthorized" if status_code == 401 else "error")
    return httpx.HTTPStatusError(
        f"Client error '{status_code}' for url '{request.url}'", request=request, response=response
    )


def _patch_settings(base_url=CONFIGURED_URL, model="qwen2.5vl:7b", api_key=""):
    return patch(
        "backend.app.services.bedcheck_ai._load_ai_settings",
        AsyncMock(return_value={"base_url": base_url, "model": model, "api_key": api_key}),
    )


class TestSettingsSchemaValidators:
    """Guard rails on the new bedcheck_* AppSettings fields (two values only,
    'opencv'/'ai' -- no 'both')."""

    def test_backend_accepts_valid_values(self):
        for value in ("opencv", "ai"):
            u = AppSettingsUpdate(bedcheck_backend=value)
            assert u.bedcheck_backend == value

    def test_backend_rejects_garbage(self):
        with pytest.raises(ValueError, match="bedcheck_backend"):
            AppSettingsUpdate(bedcheck_backend="nonsense")

    def test_backend_rejects_both(self):
        """Regression coverage that 'both' mode (considered and cut before
        v1) really was removed from the schema, not just left undocumented."""
        with pytest.raises(ValueError, match="bedcheck_backend"):
            AppSettingsUpdate(bedcheck_backend="both")

    def test_backend_none_passes_through(self):
        """A partial-update payload omitting bedcheck_backend must not be
        coerced into a validation error."""
        assert AppSettingsUpdate(bedcheck_backend=None).bedcheck_backend is None

    def test_base_url_model_api_key_accept_free_strings(self):
        """bedcheck_ai_model / bedcheck_ai_api_key are free strings (no
        validator); bedcheck_ai_base_url is exercised by the LAN-service-URL
        parametrized suite in test_outbound_url_ssrf_guards.py rather than
        duplicated here."""
        u = AppSettingsUpdate(
            bedcheck_ai_base_url="http://192.168.1.20:11434/v1",
            bedcheck_ai_model="qwen2.5vl:7b",
            bedcheck_ai_api_key="s3cret",
        )
        assert u.bedcheck_ai_base_url == "http://192.168.1.20:11434/v1"
        assert u.bedcheck_ai_model == "qwen2.5vl:7b"
        assert u.bedcheck_ai_api_key == "s3cret"


class TestConfidenceClamp:
    """_clamp_confidence's exact algorithm: a schema-valid but
    semantically-wrong percentage-style value (e.g. 95 instead of 0.95) must
    never reach main.py's `:.0%` format unclamped."""

    def test_fraction_passes_through(self):
        assert _clamp_confidence(0.87) == 0.87

    def test_percent_style_value_normalized(self):
        assert _clamp_confidence(95) == 0.95

    def test_over_100_still_clamps(self):
        assert _clamp_confidence(150) == 1.0

    def test_negative_clamps_to_zero(self):
        assert _clamp_confidence(-5) == 0.0

    def test_non_numeric_returns_zero(self):
        assert _clamp_confidence("high") == 0.0

    def test_none_returns_zero(self):
        assert _clamp_confidence(None) == 0.0

    def test_exactly_one_passes_through(self):
        """Boundary: 1.0 is a valid fraction, not a percent-style value --
        must not be divided by 100."""
        assert _clamp_confidence(1.0) == 1.0

    def test_mutation_proof_unclamped_stub_fails(self):
        """Red-proof for the value-normalization behavior: a naive
        implementation that only clamps range but never rescales an
        out-of-[0,1] value would return 95.0 (then be min()-clamped to 1.0,
        silently hiding the percent-style bug) instead of 0.95. This test
        demonstrates the *real* clamp is what produces 0.95, by asserting the
        exact value a range-clamp-only stub would get wrong."""

        def unclamped_stub(value):
            try:
                c = float(value)
            except (TypeError, ValueError):
                return 0.0
            return max(0.0, min(1.0, c))  # no /100 rescale -- the mutation

        # The real function rescales 95 -> 0.95; the mutated stub would
        # instead clamp 95 straight to 1.0, silently masking the bug.
        assert _clamp_confidence(95) == 0.95
        assert unclamped_stub(95) == 1.0
        assert _clamp_confidence(95) != unclamped_stub(95)


class TestVerdictParsing:
    """_parse_verdict_json: clean JSON, markdown-fenced JSON, and the two
    malformed-but-parseable shapes that must be treated as parse failures."""

    def test_clean_json_parses(self):
        raw = '{"is_empty": true, "confidence": 0.9, "reason": "bare plate"}'
        data = _parse_verdict_json(raw)
        assert data == {"is_empty": True, "confidence": 0.9, "reason": "bare plate"}

    def test_markdown_fenced_json_parses(self):
        raw = '```json\n{"is_empty": false, "confidence": 0.8, "reason": "a print is on it"}\n```'
        data = _parse_verdict_json(raw)
        assert data["is_empty"] is False
        assert data["confidence"] == 0.8

    def test_whitespace_padded_json_parses(self):
        raw = '   \n  {"is_empty": true, "confidence": 1.0, "reason": ""}\n  '
        data = _parse_verdict_json(raw)
        assert data["is_empty"] is True

    def test_missing_required_key_treated_as_failure(self):
        raw = '{"confidence": 0.9, "reason": "no is_empty key"}'
        with pytest.raises(AiBedCheckError, match="invalid response from AI backend"):
            _parse_verdict_json(raw)

    def test_non_bool_is_empty_treated_as_failure(self):
        raw = '{"is_empty": "yes", "confidence": 0.9, "reason": "string not bool"}'
        with pytest.raises(AiBedCheckError):
            _parse_verdict_json(raw)

    @pytest.mark.parametrize(
        "field_fragment",
        [
            '"confidence": null, "reason": "bare"',
            '"confidence": "0.9", "reason": "bare"',
            '"confidence": true, "reason": "bare"',
            '"confidence": NaN, "reason": "bare"',
            '"confidence": 0.9',
            '"confidence": 0.9, "reason": null',
            '"confidence": 0.9, "reason": 123',
        ],
    )
    def test_malformed_confidence_or_reason_is_rejected(self, field_fragment):
        with pytest.raises(AiBedCheckError, match="invalid response from AI backend"):
            _parse_verdict_json('{"is_empty": true, ' + field_fragment + "}")

    def test_numeric_percentage_remains_accepted_for_normalization(self):
        data = _parse_verdict_json('{"is_empty": true, "confidence": 95, "reason": "bare"}')
        assert _clamp_confidence(data["confidence"]) == 0.95

    def test_garbage_text_fails_to_parse(self):
        raw = "I cannot determine this from the image."
        with pytest.raises(AiBedCheckError, match="invalid response from AI backend"):
            _parse_verdict_json(raw)


class TestVerdictMapping:
    """build_ai_result: pure formatter mapping a verdict onto
    PlateDetectionResult, including the confidence*100 formula."""

    def test_occupied_verdict_sets_ai_confidence_and_nulls_difference_percent(self):
        """difference_percent is an OpenCV-only pixel-diff concept -- the AI
        backend must always report None for it (never confidence*100, which
        was the pre-review-response behavior), and carry the real value in
        ai_confidence instead."""
        result = build_ai_result(is_empty=False, confidence=0.95, reason="a print is on it", camera_source="built-in")
        assert result.is_empty is False
        assert result.difference_percent is None
        assert result.ai_confidence == 0.95

    def test_empty_verdict_also_nulls_difference_percent(self):
        result = build_ai_result(is_empty=True, confidence=0.9, reason="bare plate", camera_source="built-in")
        assert result.is_empty is True
        assert result.difference_percent is None
        assert result.ai_confidence == 0.9

    def test_default_outcome_is_ok(self):
        result = build_ai_result(is_empty=True, confidence=0.9, reason="bare plate", camera_source="built-in")
        assert result.outcome == "ok"

    def test_outcome_param_is_carried_through(self):
        result = build_ai_result(
            is_empty=True, confidence=0.9, reason="bare plate", camera_source="built-in", outcome="degraded"
        )
        assert result.outcome == "degraded"

    def test_needs_calibration_always_false(self):
        """The AI backend never needs calibration -- must preserve main.py's
        pause gate exactly regardless of verdict."""
        empty = build_ai_result(is_empty=True, confidence=0.5, reason="", camera_source="external")
        occupied = build_ai_result(is_empty=False, confidence=0.5, reason="", camera_source="external")
        assert empty.needs_calibration is False
        assert occupied.needs_calibration is False

    def test_message_includes_camera_source_prefix(self):
        result = build_ai_result(
            is_empty=True, confidence=0.9, reason="bare plate", camera_source="external (buffered)"
        )
        assert result.message.startswith("[external (buffered)]")

    def test_message_includes_reason_suffix(self):
        result = build_ai_result(
            is_empty=False, confidence=0.8, reason="a spool is on the plate", camera_source="built-in"
        )
        assert result.message.endswith(": a spool is on the plate")

    def test_message_omits_suffix_when_reason_empty(self):
        result = build_ai_result(is_empty=True, confidence=1.0, reason="", camera_source="built-in")
        assert not result.message.rstrip().endswith(":")

    def test_to_dict_confidence_and_ai_confidence_are_floats_difference_percent_is_none(self):
        result = build_ai_result(is_empty=False, confidence=0.87, reason="x", camera_source="built-in")
        d = result.to_dict()
        assert isinstance(d["confidence"], float)
        assert isinstance(d["ai_confidence"], float)
        assert d["difference_percent"] is None

    def test_to_dict_includes_outcome(self):
        result = build_ai_result(
            is_empty=False, confidence=0.87, reason="x", camera_source="built-in", outcome="degraded"
        )
        assert result.to_dict()["outcome"] == "degraded"

    def test_ai_result_carries_structured_backend_and_reason(self):
        """The decision-matrix UI reads these fields instead of parsing message."""
        result = build_ai_result(
            is_empty=False, confidence=0.8, reason="a spool is on the plate", camera_source="built-in"
        )
        d = result.to_dict()
        assert d["backend"] == "ai"
        assert d["ai_reason"] == "a spool is on the plate"

    def test_ai_result_empty_reason_serializes_as_none(self):
        result = build_ai_result(is_empty=True, confidence=1.0, reason="", camera_source="built-in")
        assert result.to_dict()["ai_reason"] is None

    def test_default_backend_is_opencv_for_unmodified_construction(self):
        """Every pre-existing PlateDetectionResult construction site (all the
        OpenCV paths) passes no backend kwarg -- the default must keep them
        reporting 'opencv' with no ai_reason."""
        from backend.app.services.plate_detection import PlateDetectionResult

        d = PlateDetectionResult(is_empty=True, confidence=1.0, difference_percent=0.0, message="m").to_dict()
        assert d["backend"] == "opencv"
        assert d["ai_reason"] is None
        # New fields (outcome/ai_confidence) must not disturb any pre-existing
        # OpenCV construction site's meaning either.
        assert d["outcome"] == "ok"
        assert d["ai_confidence"] is None
        assert d["difference_percent"] == 0.0


class TestFailOpenPerErrorClass:
    """The universal-fail-open requirement -- one test per failure class.
    Every case asserts the full fail-open shape (is_empty=True,
    confidence=0.0, difference_percent=0.0, needs_calibration=False) plus:
    (1) message equals the exact expected generic string, not a substring
    match, so a future accidental str(e) reintroduction is caught even if it
    happens to still contain the right phrase as a substring; (2) wherever a
    real (mocked) HTTP call is involved, the configured base URL is asserted
    ABSENT from message -- the leak this whole class exists to prevent.
    """

    @pytest.fixture(autouse=True)
    def _no_notify(self):
        """This class asserts exact fail-open shape/message text -- the
        ok->unavailable notification (covered separately in
        TestHealthRegistryAndNotifications) is orthogonal and would otherwise
        add real (if harmless, try/excepted) DB I/O to every case here."""
        with patch("backend.app.services.bedcheck_ai._dispatch_unavailable_transition", MagicMock()):
            yield

    def _assert_fail_open_shape(self, result, expected_message):
        assert result.is_empty is True
        assert result.confidence == 0.0
        assert result.difference_percent is None
        assert result.ai_confidence is None
        assert result.needs_calibration is False
        assert result.message == expected_message
        # Even a failed AI check must self-identify as the AI backend so the
        # decision-matrix UI attributes the fail-open verdict correctly.
        assert result.backend == "ai"
        assert result.ai_reason is None
        assert result.outcome == "unavailable"

    @pytest.mark.asyncio
    async def test_fails_open_on_timeout(self):
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.TimeoutException("timed out")),
            ),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: request timed out")

    @pytest.mark.asyncio
    async def test_fails_open_on_connection_refused(self):
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: connection failed")

    @pytest.mark.asyncio
    async def test_fails_open_on_non_2xx(self):
        """Also asserts the configured base URL is absent from message --
        this is exactly the case httpx.HTTPStatusError.__str__() would leak
        it in if _generic_fail_open_reason ever returned str(e) directly."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=_http_status_error(500))
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_result=resp),
            ),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: AI backend returned an error")
        assert CONFIGURED_URL not in result.message
        assert "11434" not in result.message

    @pytest.mark.asyncio
    async def test_fails_open_on_401(self):
        """Same HTTPStatusError class as a bad-key 401, same generic message
        and URL-absence assertion."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=_http_status_error(401))
        with (
            _patch_settings(api_key="wrong-key"),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_result=resp),
            ),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: AI backend returned an error")
        assert CONFIGURED_URL not in result.message

    @pytest.mark.asyncio
    async def test_fails_open_on_unparseable_json_after_retry(self):
        """Both the initial attempt and the one retry return prose -- no
        retry loop, exactly one retry."""
        resp = _mock_200_response({"choices": [{"message": {"content": "I cannot say."}}]})
        client = _mock_client(post_result=resp)
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: invalid response from AI backend")
        assert client.post.await_count == 2  # one original + one retry, never more

    @pytest.mark.asyncio
    async def test_fails_open_on_missing_required_field_after_retry(self):
        resp = _mock_200_response({"choices": [{"message": {"content": '{"confidence": 0.9}'}}]})
        client = _mock_client(post_result=resp)
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: invalid response from AI backend")
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_fails_open_on_invalid_confidence_after_retry(self):
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": null, "reason": "bare"}'}}]}
        )
        client = _mock_client(post_result=resp)
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: invalid response from AI backend")
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_fails_open_on_empty_base_url(self):
        """No network call attempted -- an empty base_url short-circuits before any request is built."""
        with _patch_settings(base_url=""):
            with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient") as mock_ac:
                result = await check_bed_ai(1, FAKE_JPEG, "built-in")
            mock_ac.assert_not_called()
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: AI backend not configured")

    @pytest.mark.asyncio
    async def test_fails_open_on_empty_model(self):
        """Same short-circuit path as empty base_url, same generic message --
        the empty-config check covers model, not just base_url."""
        with _patch_settings(model=""):
            with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient") as mock_ac:
                result = await check_bed_ai(1, FAKE_JPEG, "built-in")
            mock_ac.assert_not_called()
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: AI backend not configured")

    @pytest.mark.asyncio
    async def test_fails_open_on_undecodable_frame(self):
        """_downscale_jpeg fed truncated/non-JPEG bytes."""
        with _patch_settings():
            with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient") as mock_ac:
                result = await check_bed_ai(1, b"not a jpeg at all", "built-in")
            mock_ac.assert_not_called()  # image decode fails before any request is built
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: camera frame could not be processed")

    @pytest.mark.asyncio
    async def test_fails_open_on_empty_choices(self):
        """A 200 response with 'choices': [] -- a well-formed 200 with a
        malformed envelope."""
        resp = _mock_200_response({"choices": []})
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=_mock_client(post_result=resp)),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: invalid response from AI backend")

    @pytest.mark.asyncio
    async def test_fails_open_on_error_body_with_200(self):
        """A 200 response whose body is {"error": {...}} with no 'choices'
        key at all -- some OpenAI-compat proxies do this."""
        resp = _mock_200_response({"error": {"message": "model not found"}})
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=_mock_client(post_result=resp)),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: invalid response from AI backend")

    @pytest.mark.asyncio
    async def test_fails_open_on_null_content(self):
        """A 200 response with "content": null -- a tool-call-only message on
        some OpenAI-compat backends. The choices[0].message.content lookup
        succeeds without raising, so this needs its own type check to fail
        open instead of a TypeError escaping out of the JSON-verdict parser."""
        resp = _mock_200_response({"choices": [{"message": {"content": None, "tool_calls": []}}]})
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=_mock_client(post_result=resp)),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: invalid response from AI backend")

    @pytest.mark.asyncio
    async def test_fails_open_on_settings_load_failure(self):
        """A DB error inside _load_ai_settings() (e.g. sqlalchemy's
        OperationalError, which can embed a connection string) is caught by
        check_bed_ai's outer except Exception. Asserts both the standard
        fail-open shape AND that the DB error's own text (including anything
        connection-string-shaped) is absent from message, not merely that
        *some* generic message was returned."""
        db_error_text = "connection to server at postgresql://user:hunter2@10.0.20.1 failed"
        with patch(
            "backend.app.services.bedcheck_ai._load_ai_settings",
            AsyncMock(side_effect=RuntimeError(db_error_text)),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in")
        self._assert_fail_open_shape(result, "[built-in] AI bed-check unavailable: AI backend unavailable")
        assert "hunter2" not in result.message
        assert "postgresql://" not in result.message

    @pytest.mark.asyncio
    async def test_mutation_proof_str_e_leak_would_fail_url_absence_assertion(self):
        """Red-proof for the URL-absence assertions used throughout this
        class: demonstrate that a naive `str(e)` wrapper (a plausible but
        wrong implementation of _generic_fail_open_reason) WOULD have failed
        them -- i.e. that these tests actually catch the regression, not
        merely exercise a code path that happens to already be safe."""
        error = _http_status_error(500)

        def naive_str_reason(e: Exception) -> str:
            # A plausible-but-wrong implementation: echoing the raw
            # exception text (or its type name) straight to the user.
            return str(e) or type(e).__name__

        leaky_message = f"[built-in] AI bed-check unavailable: {naive_str_reason(error)}"
        # The real, fixed function must NOT reproduce this leak...
        assert _generic_fail_open_reason(error) != naive_str_reason(error)
        # ...and the leaky variant this test constructs to prove the point
        # does in fact contain the configured URL, confirming the assertion
        # style used above would have caught the naive behavior.
        assert CONFIGURED_URL in leaky_message


class TestAnalyzeFrameAi:
    """A few direct tests of _analyze_frame_ai (the raising primitive) and
    _generic_fail_open_reason's classification table, underneath
    check_bed_ai's public fail-open wrapper -- useful for asserting the raise
    behavior precisely (check_bed_ai only ever shows the mapped message)."""

    @pytest.mark.asyncio
    async def test_success_returns_tuple(self):
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.92, "reason": "bare plate"}'}}]}
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=_mock_client(post_result=resp)),
        ):
            is_empty, confidence, reason, request_mode = await _analyze_frame_ai(FAKE_JPEG, printer_id=1)
        assert is_empty is True
        assert confidence == 0.92
        assert reason == "bare plate"
        assert request_mode == "json_schema"  # default shape, empty cache

    @pytest.mark.asyncio
    async def test_retry_recovers_a_transient_bad_response(self):
        """First response is unparseable prose, the retry returns valid
        JSON -- the retry must actually be able to succeed, not just be
        attempted."""
        bad = _mock_200_response({"choices": [{"message": {"content": "sorry, I can't help with that"}}]})
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": false, "confidence": 0.7, "reason": "a print"}'}}]}
        )
        client = _mock_client()
        client.post = AsyncMock(side_effect=[bad, good])
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
        ):
            is_empty, confidence, reason, request_mode = await _analyze_frame_ai(FAKE_JPEG, printer_id=1)
        assert is_empty is False
        assert confidence == 0.7
        assert client.post.await_count == 2
        assert request_mode == "json_schema"

    def test_generic_fail_open_reason_classification_table(self):
        """Direct coverage of every branch in _generic_fail_open_reason,
        independent of check_bed_ai's wrapping -- the single source of truth
        for the per-class generic strings asserted throughout
        TestFailOpenPerErrorClass."""
        assert _generic_fail_open_reason(httpx.TimeoutException("x")) == "request timed out"
        assert _generic_fail_open_reason(httpx.ConnectError("x")) == "connection failed"
        assert _generic_fail_open_reason(_http_status_error(500)) == "AI backend returned an error"
        assert _generic_fail_open_reason(httpx.ReadError("x")) == "connection failed"
        assert _generic_fail_open_reason(AiBedCheckError("AI backend not configured")) == "AI backend not configured"
        assert _generic_fail_open_reason(RuntimeError("db blew up")) == "AI backend unavailable"
        assert _generic_fail_open_reason(ValueError("anything")) == "AI backend unavailable"


def _mock_400_response(error_body: dict, status_code: int = 400) -> MagicMock:
    """A response whose raise_for_status() raises a real httpx.HTTPStatusError
    carrying a real httpx.Response (so e.response.json() works exactly like
    it would against a live server) with the given error body."""
    request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
    real_response = httpx.Response(status_code, request=request, json=error_body)
    resp = MagicMock()
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("bad request", request=request, response=real_response)
    )
    return resp


async def _drain_notification_tasks() -> None:
    """Let the fire-and-forget unavailable-notification tasks finish.

    check_bed_ai() schedules _maybe_notify_unavailable_transition with
    asyncio.create_task rather than awaiting it (item 3b -- opening a DB
    session and awaiting every provider's outbound HTTP send must not sit
    inside the print-start deadline budget), so any test that asserts on the
    send has to give the loop a turn first. Bounded, so a task that never
    settles fails the assertion instead of hanging the suite.
    """
    for _ in range(10):
        tasks = list(bedcheck_ai_module._pending_notify_tasks)
        if not tasks:
            break
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)


def _fake_session_factory(printer_name: str | None = None):
    """Build a fake async_session() factory shaped for
    _maybe_notify_unavailable_transition's
    `async with async_session() as db: await db.execute(select(Printer)...)`.

    printer_name=None (the default) simulates "no such printer row" -- the
    function falls back to a generic "Printer {id}" name in that case, which
    is fine for tests that only assert on notification call count / that
    error_detail mentions "unavailable".
    """
    row = None
    if printer_name is not None:
        row = MagicMock()
        row.name = printer_name  # MagicMock(name=...) would set the mock's
        # own repr name instead of a `.name` attribute -- must assign after
        # construction.
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_cm)


class TestDeadline:
    """Item 3: deadline_seconds is a TOTAL wall-clock budget for
    _analyze_frame_ai (downscale + request), and skips the parse retry
    entirely -- the print-start safety path can't afford to double its
    latency on a bad response the way the diagnostic path can."""

    @pytest.mark.asyncio
    async def test_deadline_set_skips_retry_on_parse_failure(self):
        """Red-proof: with deadline_seconds=None (existing behavior, see
        TestFailOpenPerErrorClass.test_fails_open_on_unparseable_json_after_retry)
        the same unparseable response costs 2 POSTs. With a deadline set it
        must cost exactly 1 -- a stub that always retries would fail this."""
        resp = _mock_200_response({"choices": [{"message": {"content": "not json at all"}}]})
        client = _mock_client(post_result=resp)
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
            pytest.raises(AiBedCheckError),
        ):
            await _analyze_frame_ai(FAKE_JPEG, printer_id=1, deadline_seconds=5.0)
        assert client.post.await_count == 1

    @pytest.mark.asyncio
    async def test_deadline_httpx_timeout_is_remaining_budget(self):
        """timeout passed to httpx.AsyncClient must be (deadline_seconds -
        elapsed-since-start), not the raw deadline_seconds and not
        DEFAULT_TIMEOUT."""
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client_calls: list[dict] = []

        def _client_factory(*_args, **kwargs):
            client_calls.append(kwargs)
            return _mock_client(post_result=resp)

        # 1st call = start = time.monotonic() in _analyze_frame_ai; 2nd call =
        # inside _timeout() for the single request issued (3.0s elapsed out
        # of a 10.0s budget leaves 7.0s). A plain list side_effect would
        # StopIteration here: patching `bedcheck_ai.time.monotonic` patches
        # the real time module's attribute (bedcheck_ai.time IS the time
        # module), so asyncio.wait_for's own internal loop.time() calls draw
        # from the same iterator too -- a function with a stable fallback
        # keeps those extra, assertion-irrelevant calls from crashing.
        values = iter([0.0, 3.0])

        def _fake_monotonic():
            try:
                return next(values)
            except StopIteration:
                return 3.0

        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.time.monotonic", side_effect=_fake_monotonic),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", side_effect=_client_factory),
        ):
            await _analyze_frame_ai(FAKE_JPEG, printer_id=1, deadline_seconds=10.0)

        assert len(client_calls) == 1
        # httpx.Timeout, not a bare float: a float is applied to each of
        # connect/read/write/pool independently, making a "7s budget" a 28s
        # worst case (item 3a).
        timeout = client_calls[0]["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == pytest.approx(7.0)
        assert timeout.write == pytest.approx(7.0)
        assert timeout.pool == pytest.approx(7.0)
        assert timeout.connect == pytest.approx(bedcheck_ai_module.CONNECT_TIMEOUT_CAP)

    @pytest.mark.asyncio
    async def test_deadline_none_keeps_default_timeout_per_request(self):
        """Red-proof for the "None = current behavior" contract: with no
        deadline, timeout must stay DEFAULT_TIMEOUT regardless of elapsed
        time -- a stub that always derives a remaining-budget timeout would
        fail this."""
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client_calls: list[dict] = []

        def _client_factory(*_args, **kwargs):
            client_calls.append(kwargs)
            return _mock_client(post_result=resp)

        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", side_effect=_client_factory),
        ):
            await _analyze_frame_ai(FAKE_JPEG, printer_id=1, deadline_seconds=None)

        assert client_calls[0]["timeout"].read == bedcheck_ai_module.DEFAULT_TIMEOUT

    @pytest.mark.asyncio
    async def test_check_bed_ai_forwards_deadline_seconds(self):
        """check_bed_ai's deadline_seconds param reaches _analyze_frame_ai --
        exercised end-to-end via the retry-skip side effect."""
        resp = _mock_200_response({"choices": [{"message": {"content": "not json"}}]})
        client = _mock_client(post_result=resp)
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
        ):
            result = await check_bed_ai(1, FAKE_JPEG, "built-in", deadline_seconds=5.0)
        assert client.post.await_count == 1
        assert result.outcome == "unavailable"


class TestDeadlineExhaustionAndDispatch:
    """Item 3 follow-ups from the adversarial gate: the deadline is a real
    wall-clock bound (nothing is issued once it is spent) and the fail-open
    path does no unbounded work after the budget is gone."""

    @pytest.mark.asyncio
    async def test_exhausted_deadline_raises_before_issuing_a_request(self):
        """Red-proof for the `max(remaining, 0.05)` floor: that floor still
        issued a request with the budget already spent -- a socket, a connect
        attempt and a guaranteed timeout, all past the deadline the caller was
        promised. Now the deadline is refused outright and nothing is sent."""
        values = iter([0.0, 25.0])

        def _fake_monotonic():
            try:
                return next(values)
            except StopIteration:
                return 25.0

        client = _mock_client(
            post_result=_mock_200_response(
                {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
            )
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.time.monotonic", side_effect=_fake_monotonic),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
            pytest.raises(AiBedCheckError, match="deadline exceeded"),
        ):
            await _analyze_frame_ai(FAKE_JPEG, printer_id=1, deadline_seconds=20.0)

        assert client.post.await_count == 0

    @pytest.mark.asyncio
    async def test_timeout_exactly_zero_remaining_raises_deadline_exceeded(self):
        """Coverage for the `<= 0` boundary in _timeout() itself (the test
        above only exercises an already-negative remaining budget). A mutant
        that weakened this to `< 0` would let remaining == 0.0 slip through
        and hand httpx (and now asyncio.wait_for) a zero-second timeout
        instead of failing the deadline outright before issuing anything."""
        values = iter([0.0, 20.0])

        def _fake_monotonic():
            try:
                return next(values)
            except StopIteration:
                return 20.0

        client = _mock_client(
            post_result=_mock_200_response(
                {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
            )
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.time.monotonic", side_effect=_fake_monotonic),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
            pytest.raises(AiBedCheckError, match="deadline exceeded"),
        ):
            await _analyze_frame_ai(FAKE_JPEG, printer_id=1, deadline_seconds=20.0)

        assert client.post.await_count == 0

    @pytest.mark.asyncio
    async def test_deadline_bounds_a_drip_feeding_backend_to_the_total_budget(self):
        """Item 1 (aggregate wall-clock bound): a bare httpx.Timeout only
        bounds idle time between reads, not the call as a whole, so a
        backend that trickles bytes just often enough to keep resetting the
        read timer could otherwise run 2-3x the deadline before ever
        tripping it. _analyze_frame_ai additionally wraps the deadline-path
        request in asyncio.wait_for() against the same remaining budget, so
        a _post_chat that simply never returns in time must still fail open
        promptly. Red-proof: removing that asyncio.wait_for() wrapper makes
        this hang for the full 5s sleep below instead of returning near the
        0.2s deadline.
        """

        async def _drip_feeding_post_chat(*_args, **_kwargs):
            await asyncio.sleep(5.0)
            return '{"is_empty": true, "confidence": 0.9, "reason": ""}'  # pragma: no cover - never reached

        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai._post_chat", AsyncMock(side_effect=_drip_feeding_post_chat)),
            patch("backend.app.services.bedcheck_ai._dispatch_unavailable_transition", MagicMock()),
        ):
            start = time.monotonic()
            result = await check_bed_ai(63, FAKE_JPEG, "built-in", deadline_seconds=0.2)
            elapsed = time.monotonic() - start

        assert result.outcome == "unavailable"
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_exhausted_deadline_fails_open_with_the_default_request_mode(self):
        """The whole-call view of the case above: check_bed_ai still fails
        open (never raises) and still records a health entry, labelled with
        the shape that was actually resolved rather than re-derived by a
        second settings read."""
        values = iter([0.0, 25.0])

        def _fake_monotonic():
            try:
                return next(values)
            except StopIteration:
                return 25.0

        client = _mock_client(
            post_result=_mock_200_response(
                {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
            )
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.time.monotonic", side_effect=_fake_monotonic),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client),
            patch("backend.app.services.bedcheck_ai._dispatch_unavailable_transition", MagicMock()),
        ):
            result = await check_bed_ai(21, FAKE_JPEG, "built-in", deadline_seconds=20.0)

        assert result.is_empty is True
        assert result.outcome == "unavailable"
        assert get_health()[21]["reason"] == "deadline exceeded"
        assert get_health()[21]["request_mode"] == "json_schema"

    @pytest.mark.asyncio
    async def test_failure_path_does_not_reload_settings_for_the_health_entry(self):
        """The failure branch used to await a second _load_ai_settings() (an
        unbounded DB round trip) purely to label the health entry, outside the
        deadline budget on the print-start path. The shape is now captured
        before the request instead, so exactly one settings read happens."""
        settings_mock = AsyncMock(return_value={"base_url": CONFIGURED_URL, "model": "m", "api_key": ""})
        with (
            patch("backend.app.services.bedcheck_ai._load_ai_settings", settings_mock),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai._dispatch_unavailable_transition", MagicMock()),
        ):
            await check_bed_ai(22, FAKE_JPEG, "built-in", deadline_seconds=20.0)

        assert settings_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_unavailable_notification_does_not_block_check_bed_ai_return(self):
        """Item 3b: the notification opens a DB session and then awaits every
        configured provider's outbound HTTP send. Awaiting that inline puts
        unbounded work between a failed bed check and the print waiting on its
        verdict. Here the provider send blocks on an Event that is only set
        after check_bed_ai has already returned -- if the send were awaited
        inline, check_bed_ai would never return and wait_for would time out."""
        gate = asyncio.Event()
        order: list[str] = []

        async def _blocking_send(**_kwargs):
            order.append("send-start")
            await gate.wait()
            order.append("send-finish")

        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock(side_effect=_blocking_send)
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            result = await asyncio.wait_for(check_bed_ai(23, FAKE_JPEG, "built-in", deadline_seconds=20.0), timeout=5.0)
            order.append("check-returned")
            assert result.outcome == "unavailable"
            assert "send-finish" not in order
            gate.set()
            await _drain_notification_tasks()

        assert "send-finish" in order
        assert order.index("check-returned") < order.index("send-finish")

    @pytest.mark.asyncio
    async def test_dispatched_notification_task_is_strongly_referenced(self):
        """asyncio holds only a weak reference to a running task, so a
        create_task() result nobody keeps can be collected mid-await and the
        notification silently vanish. The module-level set is that reference;
        the done-callback must clear it again so it never grows."""
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            await check_bed_ai(24, FAKE_JPEG, "built-in")
            assert len(bedcheck_ai_module._pending_notify_tasks) == 1
            await _drain_notification_tasks()

        mock_notify_service.on_printer_error.assert_awaited_once()
        assert bedcheck_ai_module._pending_notify_tasks == set()


class TestAdaptiveRequestShape:
    """Item 4: per-(base_url, model, api_key) request-shape cache. Discovery
    (probing a 400/422 and retrying with an adjusted shape) runs only inside
    test_connection(); the print/manual path (_post_chat consulted via
    _analyze_frame_ai) only ever reads the cache, never probes."""

    def test_get_shape_default_when_uncached(self):
        assert _get_shape("http://x", "m", "k") == {"token_param": "max_tokens", "response_mode": "json_schema"}

    @pytest.mark.asyncio
    async def test_post_chat_empty_cache_sends_default_shape(self):
        """The print-path shape lookup with an empty cache must send the
        default shape (max_tokens + json_schema) and never attempt to probe
        or retry on a shape dimension -- that only happens in
        test_connection()."""
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client = _mock_client(post_result=resp)
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            await _post_chat(CONFIGURED_URL, "qwen2.5vl:7b", "", [{"role": "user", "content": "x"}])
        payload = client.post.await_args.kwargs["json"]
        assert payload["max_tokens"] == 300
        assert "max_completion_tokens" not in payload
        assert payload["response_format"] == {
            "type": "json_schema",
            "json_schema": bedcheck_ai_module.VERDICT_JSON_SCHEMA,
        }
        assert client.post.await_count == 1  # no shape-retry on this path

    @pytest.mark.asyncio
    async def test_post_chat_cache_hit_changes_payload(self):
        """A pre-populated cache entry (as if test_connection() already
        discovered the working shape for this base_url/model/api_key) must
        change what the print-path payload looks like."""
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "qwen2.5vl:7b", "")
        bedcheck_ai_module._shape_cache[cache_key] = {
            "token_param": "max_completion_tokens",
            "response_mode": "json_object",
        }
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client = _mock_client(post_result=resp)
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            await _post_chat(CONFIGURED_URL, "qwen2.5vl:7b", "", [{"role": "user", "content": "x"}])
        payload = client.post.await_args.kwargs["json"]
        assert payload["max_completion_tokens"] == 300
        assert "max_tokens" not in payload
        assert payload["response_format"] == {"type": "json_object"}

    def test_classify_shape_error_structured_param(self):
        request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
        resp = httpx.Response(400, request=request, json={"error": {"param": "max_tokens", "code": "unsupported"}})
        assert _classify_shape_error(resp) == "token_param"

    def test_classify_shape_error_structured_code_response_format(self):
        request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
        resp = httpx.Response(
            400, request=request, json={"error": {"param": "", "code": "response_format_not_supported"}}
        )
        assert _classify_shape_error(resp) == "response_mode"

    def test_classify_shape_error_message_fallback(self):
        request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
        resp = httpx.Response(
            400,
            request=request,
            json={"error": {"message": "Unrecognized request argument supplied: max_tokens"}},
        )
        assert _classify_shape_error(resp) == "token_param"

    def test_classify_shape_error_unclassifiable_returns_none(self):
        request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
        resp = httpx.Response(400, request=request, json={"error": {"message": "invalid api key"}})
        assert _classify_shape_error(resp) is None

    @pytest.mark.asyncio
    async def test_test_connection_max_tokens_error_retries_and_caches(self):
        bad = _mock_400_response({"error": {"param": "max_tokens", "message": "unsupported parameter"}})
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": "bare"}'}}]}
        )
        client = _mock_client()
        client.post = AsyncMock(side_effect=[bad, good])
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is True
        assert result["request_mode"] == "json_schema"  # unaffected dimension
        assert client.post.await_count == 2
        second_payload = client.post.await_args_list[1].kwargs["json"]
        assert "max_completion_tokens" in second_payload
        assert "max_tokens" not in second_payload
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "some-model", "key")
        assert bedcheck_ai_module._shape_cache[cache_key]["token_param"] == "max_completion_tokens"

    @pytest.mark.asyncio
    async def test_test_connection_response_format_error_retries_with_json_object_and_caches(self):
        bad = _mock_400_response({"error": {"param": "response_format", "message": "json_schema not supported"}})
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": false, "confidence": 0.6, "reason": "print"}'}}]}
        )
        client = _mock_client()
        client.post = AsyncMock(side_effect=[bad, good])
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is True
        assert result["request_mode"] == "json_object"
        second_payload = client.post.await_args_list[1].kwargs["json"]
        assert second_payload["response_format"] == {"type": "json_object"}
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "some-model", "key")
        assert bedcheck_ai_module._shape_cache[cache_key]["response_mode"] == "json_object"

    @pytest.mark.asyncio
    async def test_test_connection_at_most_one_retry_per_dimension(self):
        """Both dimensions wrong: 3 requests total (original + one retry per
        dimension), never more."""
        bad_tokens = _mock_400_response({"error": {"param": "max_tokens", "message": "unsupported"}})
        bad_format = _mock_400_response({"error": {"param": "response_format", "message": "unsupported"}})
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client = _mock_client()
        client.post = AsyncMock(side_effect=[bad_tokens, bad_format, good])
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is True
        assert client.post.await_count == 3
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "some-model", "key")
        cached = bedcheck_ai_module._shape_cache[cache_key]
        assert cached == {"token_param": "max_completion_tokens", "response_mode": "json_object"}

    @pytest.mark.asyncio
    async def test_test_connection_unclassifiable_400_does_not_retry(self):
        """A real config error (bad API key) must not be treated as a shape
        problem -- one request, ok=False, nothing cached."""
        bad = _mock_400_response({"error": {"message": "invalid api key"}})
        client = _mock_client(post_result=bad)
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is False
        assert client.post.await_count == 1
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "some-model", "key")
        assert cache_key not in bedcheck_ai_module._shape_cache

    @pytest.mark.asyncio
    async def test_test_connection_success_overwrites_stale_cached_shape(self):
        """Recovery path: a stale 'degraded' cache entry (backend since fixed
        to accept the default shape again) must be overwritten on the next
        successful test_connection(), not left stuck on the old shape."""
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "some-model", "key")
        bedcheck_ai_module._shape_cache[cache_key] = {
            "token_param": "max_completion_tokens",
            "response_mode": "json_object",
        }
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client = _mock_client(post_result=good)
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is True
        # test_connection used the (stale) cached shape for its one request...
        payload = client.post.await_args.kwargs["json"]
        assert "max_completion_tokens" in payload
        # ...but since it succeeded outright, the cache still gets rewritten
        # (unconditionally, per the "overwriting" contract) rather than left
        # untouched -- here that happens to be a no-op value-wise, but the
        # request_mode returned reflects what was actually cached going
        # forward.
        assert bedcheck_ai_module._shape_cache[cache_key]["response_mode"] == "json_object"
        assert result["request_mode"] == "json_object"


class TestShapeCacheKeyNormalization:
    """BLOCKING finding: routes/bedcheck_ai.py hands test_connection() the RAW
    request body, while the print/manual path reads the settings back through
    _load_ai_settings(), which rstrips the URL and strips the model/key. The
    key function therefore has to normalise, or discovery is keyed under one
    tuple and read under another."""

    def test_key_normalizes_trailing_slash_and_whitespace(self):
        canonical = _shape_cache_key(CONFIGURED_URL, "qwen2.5vl:7b", "key")
        assert _shape_cache_key(CONFIGURED_URL + "/", " qwen2.5vl:7b ", " key ") == canonical
        assert _shape_cache_key(CONFIGURED_URL + "///", "qwen2.5vl:7b", "key") == canonical
        # Whitespace-padded base_url (not just model/api_key) must normalize
        # too -- kills a mutant that drops the .strip() call ahead of the
        # rstrip("/") on base_url in _shape_cache_key.
        assert _shape_cache_key(f"  {CONFIGURED_URL}  ", "qwen2.5vl:7b", "key") == canonical
        assert _shape_cache_key(f"  {CONFIGURED_URL}/  ", "qwen2.5vl:7b", "key") == canonical

    def test_key_includes_the_api_key_hash(self):
        """A key rotation on the same URL/model must not reuse the shape
        discovered under the old key -- a mutant that dropped the hash from
        the tuple would collapse both into one entry."""
        k1 = _shape_cache_key(CONFIGURED_URL, "m", "key-one")
        k2 = _shape_cache_key(CONFIGURED_URL, "m", "key-two")
        assert k1 != k2
        assert k1[0] == k2[0] and k1[1] == k2[1]  # only the hash differs
        assert k1[2] != k2[2]
        bedcheck_ai_module._shape_cache[k1] = {
            "token_param": "max_completion_tokens",
            "response_mode": "json_object",
        }
        assert _get_shape(CONFIGURED_URL, "m", "key-two") == bedcheck_ai_module._DEFAULT_SHAPE
        assert _get_shape(CONFIGURED_URL, "m", "key-one")["response_mode"] == "json_object"

    def test_key_still_separates_different_urls_and_models(self):
        """Normalisation must not over-collapse: two genuinely different
        targets keep different keys."""
        base = _shape_cache_key(CONFIGURED_URL, "m", "k")
        assert _shape_cache_key("http://192.168.1.21:11434/v1", "m", "k") != base
        assert _shape_cache_key(CONFIGURED_URL, "other-model", "k") != base

    @pytest.mark.asyncio
    async def test_trailing_slash_discovery_is_visible_to_the_print_path(self):
        """End-to-end proof of the finding: discover the shape through
        test_connection() with the URL spelled exactly as a user may have
        saved it ("…/v1/"), then run the print path against the stored
        (normalised) settings and assert it sends the DISCOVERED shape. Before
        the fix this test sees max_tokens -- i.e. every real check 400s and
        fails open while test-connection reports ok."""
        bad = _mock_400_response({"error": {"param": "max_tokens", "message": "unsupported parameter"}})
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": "bare"}'}}]}
        )
        probe_client = _mock_client()
        probe_client.post = AsyncMock(side_effect=[bad, good])
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=probe_client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL + "/", " qwen2.5vl:7b ", " key ")
        assert result["ok"] is True

        verdict = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        print_client = _mock_client(post_result=verdict)
        with (
            # Exactly what _load_ai_settings() hands back for that saved value.
            _patch_settings(base_url=CONFIGURED_URL, model="qwen2.5vl:7b", api_key="key"),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=print_client),
        ):
            await _analyze_frame_ai(FAKE_JPEG, printer_id=1)

        payload = print_client.post.await_args.kwargs["json"]
        assert "max_completion_tokens" in payload
        assert "max_tokens" not in payload


class TestDiscoveryLoopBounds:
    """The discovery loop must terminate and must not let a wedged endpoint
    hold the settings UI for 3 x DEFAULT_TIMEOUT."""

    @pytest.mark.asyncio
    async def test_repeated_same_dimension_error_does_not_loop(self):
        """A backend that keeps naming the same dimension no matter what we
        send: at most one retry per dimension, so this stops after 2 requests
        and reports failure -- never spins."""
        bad = _mock_400_response({"error": {"param": "max_tokens", "message": "unsupported"}})
        client = _mock_client(post_result=bad)
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is False
        assert client.post.await_count == 2
        assert client.post.await_count <= 3
        assert _shape_cache_key(CONFIGURED_URL, "some-model", "key") not in bedcheck_ai_module._shape_cache

    @pytest.mark.asyncio
    async def test_both_shapes_fail_is_bounded_at_three_requests(self):
        """Both dimensions rejected and still failing after both retries:
        3 requests total, ok=False, nothing cached."""
        bad_tokens = _mock_400_response({"error": {"param": "max_tokens", "message": "unsupported"}})
        bad_format = _mock_400_response({"error": {"param": "response_format", "message": "unsupported"}})
        client = _mock_client()
        client.post = AsyncMock(side_effect=[bad_tokens, bad_format, bad_format])
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is False
        assert client.post.await_count == 3
        assert _shape_cache_key(CONFIGURED_URL, "some-model", "key") not in bedcheck_ai_module._shape_cache

    @pytest.mark.asyncio
    async def test_422_is_treated_as_a_shape_error(self):
        """Some OpenAI-compatible backends validate the request shape as a
        schema violation (422) rather than a bad request (400) -- both must
        reach the classifier, or discovery never runs against them."""
        bad = _mock_400_response(
            {"error": {"param": "response_format", "message": "json_schema not supported"}}, status_code=422
        )
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client = _mock_client()
        client.post = AsyncMock(side_effect=[bad, good])
        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=client):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is True
        assert result["request_mode"] == "json_object"
        assert client.post.await_count == 2

    def test_classify_shape_error_reads_a_422_body(self):
        request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
        resp = httpx.Response(422, request=request, json={"error": {"param": "max_tokens", "code": "unsupported"}})
        assert _classify_shape_error(resp) == "token_param"

    def test_classify_shape_error_prefers_the_dimension_named_first(self):
        """When one body names both, the one the backend led with is the one
        it actually rejected -- a fixed dimension order would burn a retry on
        the wrong one."""
        request = httpx.Request("POST", f"{CONFIGURED_URL}/chat/completions")
        format_first = httpx.Response(
            400,
            request=request,
            json={"error": {"message": "response_format json_schema is unsupported; also pass max_tokens"}},
        )
        assert _classify_shape_error(format_first) == "response_mode"
        tokens_first = httpx.Response(
            400,
            request=request,
            json={"error": {"message": "max_tokens is not supported here; response_format is fine"}},
        )
        assert _classify_shape_error(tokens_first) == "token_param"

    @pytest.mark.asyncio
    async def test_discovery_retries_use_a_bounded_per_request_timeout(self):
        """The first probe keeps the full ceiling (it may pay a model cold
        start); the retries cannot -- the backend has already answered once --
        so they are capped, and a hung endpoint can't hold this for 3 x 60s."""
        bad = _mock_400_response({"error": {"param": "max_tokens", "message": "unsupported"}})
        good = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        client = _mock_client()
        client.post = AsyncMock(side_effect=[bad, good])
        client_calls: list[dict] = []

        def _client_factory(*_args, **kwargs):
            client_calls.append(kwargs)
            return client

        with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", side_effect=_client_factory):
            result = await bedcheck_ai_test_connection(CONFIGURED_URL, "some-model", "key")

        assert result["ok"] is True
        assert len(client_calls) == 2
        assert client_calls[0]["timeout"].read == bedcheck_ai_module.DEFAULT_TIMEOUT
        assert client_calls[1]["timeout"].read == bedcheck_ai_module.DISCOVERY_RETRY_TIMEOUT
        assert bedcheck_ai_module.DISCOVERY_RETRY_TIMEOUT < bedcheck_ai_module.DEFAULT_TIMEOUT


class TestHealthRegistryAndNotifications:
    """Item 1: the module-level health registry (get_health()) and the
    rate-limited ok->unavailable notification."""

    @pytest.mark.asyncio
    async def test_get_health_empty_initially(self):
        assert get_health() == {}

    @pytest.mark.asyncio
    async def test_check_bed_ai_success_records_ok(self):
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=_mock_client(post_result=resp)),
        ):
            await check_bed_ai(7, FAKE_JPEG, "built-in")
        health = get_health()
        assert health[7]["outcome"] == "ok"
        assert health[7]["reason"] is None
        assert health[7]["request_mode"] == "json_schema"
        assert "at" in health[7]

    @pytest.mark.asyncio
    async def test_check_bed_ai_json_object_shape_records_degraded(self):
        """A successful verdict obtained via the json_object fallback shape
        must be reported as 'degraded', not 'ok' -- the check itself didn't
        fail, but it's running in a reduced mode."""
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "qwen2.5vl:7b", "")
        bedcheck_ai_module._shape_cache[cache_key] = {
            "token_param": "max_completion_tokens",
            "response_mode": "json_object",
        }
        resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=_mock_client(post_result=resp)),
        ):
            result = await check_bed_ai(7, FAKE_JPEG, "built-in")
        assert result.outcome == "degraded"
        assert get_health()[7]["outcome"] == "degraded"

    @pytest.mark.asyncio
    async def test_check_bed_ai_fail_open_records_unavailable(self):
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai._dispatch_unavailable_transition", MagicMock()),
        ):
            await check_bed_ai(9, FAKE_JPEG, "built-in")
        health = get_health()
        assert health[9]["outcome"] == "unavailable"
        assert health[9]["reason"] == "connection failed"

    @pytest.mark.asyncio
    async def test_fail_open_records_request_mode_from_the_shape_out_holder(self):
        """Item 2: shape_out is filled in by _analyze_frame_ai the moment the
        request shape is resolved -- BEFORE any network I/O -- specifically so
        check_bed_ai()'s failure path can label the health entry with the
        shape actually in play, without a second (out-of-budget) settings
        read. Seed the cache with the json_object shape, which differs from
        _DEFAULT_SHAPE, so the assertion actually distinguishes the two: if
        the two shape_out-fill lines in _analyze_frame_ai were deleted,
        observed_shape would stay {} and the failure path would fall back to
        _DEFAULT_SHAPE's "json_schema" instead."""
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "qwen2.5vl:7b", "")
        bedcheck_ai_module._shape_cache[cache_key] = {
            "token_param": "max_completion_tokens",
            "response_mode": "json_object",
        }
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai._dispatch_unavailable_transition", MagicMock()),
        ):
            await check_bed_ai(64, FAKE_JPEG, "built-in")
        assert get_health()[64]["request_mode"] == "json_object"

    @pytest.mark.asyncio
    async def test_ok_to_unavailable_sends_exactly_one_notification(self):
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            await check_bed_ai(11, FAKE_JPEG, "built-in")
            await _drain_notification_tasks()

        mock_notify_service.on_printer_error.assert_awaited_once()
        call_kwargs = mock_notify_service.on_printer_error.await_args.kwargs
        assert call_kwargs["printer_id"] == 11
        assert "unavailable" in call_kwargs["error_detail"].lower()

    @pytest.mark.asyncio
    async def test_second_consecutive_failure_sends_no_notification(self):
        """previous_outcome is already 'unavailable' after the first failure --
        the second failure is not an ok->unavailable transition."""
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            await check_bed_ai(12, FAKE_JPEG, "built-in")
            await _drain_notification_tasks()
            await check_bed_ai(12, FAKE_JPEG, "built-in")
            await _drain_notification_tasks()

        mock_notify_service.on_printer_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recovery_then_failure_past_cooldown_notifies_again(self):
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        fail_resp = _mock_client(post_side_effect=httpx.ConnectError("refused"))
        ok_resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=fail_resp):
                await check_bed_ai(13, FAKE_JPEG, "built-in")  # 1st failure -> notifies
                await _drain_notification_tasks()
            with patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_result=ok_resp),
            ):
                await check_bed_ai(13, FAKE_JPEG, "built-in")  # recovers to ok
            # Simulate the cooldown window having elapsed.
            bedcheck_ai_module._last_unavailable_notified_at[13] -= (
                bedcheck_ai_module._UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS + 1
            )
            with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=fail_resp):
                await check_bed_ai(13, FAKE_JPEG, "built-in")  # 2nd failure, past window -> notifies again
                await _drain_notification_tasks()

        assert mock_notify_service.on_printer_error.await_count == 2

    @pytest.mark.asyncio
    async def test_recovery_then_failure_within_cooldown_does_not_notify(self):
        """Same recovery-then-failure shape as above, but without advancing
        past the cooldown window -- must NOT re-notify."""
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        fail_resp = _mock_client(post_side_effect=httpx.ConnectError("refused"))
        ok_resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=fail_resp):
                await check_bed_ai(14, FAKE_JPEG, "built-in")
                await _drain_notification_tasks()
            with patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_result=ok_resp),
            ):
                await check_bed_ai(14, FAKE_JPEG, "built-in")
            with patch("backend.app.services.bedcheck_ai.httpx.AsyncClient", return_value=fail_resp):
                await check_bed_ai(14, FAKE_JPEG, "built-in")
                await _drain_notification_tasks()

        mock_notify_service.on_printer_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_degraded_to_unavailable_sends_a_notification(self):
        """BLOCKING finding: an install whose backend needs the json_object
        fallback records "degraded" on EVERY successful check. Suppressing the
        notification for any previous_outcome other than None/"ok" therefore
        meant such an install was never told when its AI bed-check died --
        permanently, not just once. Only an already-"unavailable" state may
        suppress."""
        cache_key = bedcheck_ai_module._shape_cache_key(CONFIGURED_URL, "qwen2.5vl:7b", "")
        bedcheck_ai_module._shape_cache[cache_key] = {
            "token_param": "max_completion_tokens",
            "response_mode": "json_object",
        }
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        ok_resp = _mock_200_response(
            {"choices": [{"message": {"content": '{"is_empty": true, "confidence": 0.9, "reason": ""}'}}]}
        )
        with (
            _patch_settings(),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            with patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_result=ok_resp),
            ):
                await check_bed_ai(31, FAKE_JPEG, "built-in")
            assert get_health()[31]["outcome"] == "degraded"
            with patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ):
                await check_bed_ai(31, FAKE_JPEG, "built-in")
                await _drain_notification_tasks()

        assert get_health()[31]["outcome"] == "unavailable"
        mock_notify_service.on_printer_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unavailable_to_unavailable_is_still_suppressed(self):
        """The one state that must suppress: a repeat of an already-reported
        outage. Guards against "fix the degraded case by removing the check
        entirely", which would re-page on every single check."""
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        bedcheck_ai_module._record_outcome(32, "unavailable", "connection failed", "json_schema")
        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            await check_bed_ai(32, FAKE_JPEG, "built-in")
            await _drain_notification_tasks()

        mock_notify_service.on_printer_error.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_health_returns_a_copy_not_the_live_registry(self):
        """get_health() feeds a route response. A caller mutating what it gets
        back must not be able to corrupt the registry the transition logic
        reads -- at either level of the mapping."""
        bedcheck_ai_module._record_outcome(41, "ok", None, "json_schema")

        snapshot = get_health()
        assert snapshot is not bedcheck_ai_module._last_outcome
        assert snapshot[41] is not bedcheck_ai_module._last_outcome[41]

        snapshot[41]["outcome"] = "tampered"
        snapshot[99] = {"outcome": "invented"}

        assert bedcheck_ai_module._last_outcome[41]["outcome"] == "ok"
        assert 99 not in bedcheck_ai_module._last_outcome
        assert get_health()[41]["outcome"] == "ok"

    def test_reset_health_state_clears_registry_and_notification_cooldown(self):
        """Connection settings changing means the recorded outcomes describe a
        backend nobody is calling any more -- and, left alone, an armed
        cooldown that would swallow the NEW backend's first real failure."""
        bedcheck_ai_module._record_outcome(51, "unavailable", "connection failed", "json_schema")
        bedcheck_ai_module._last_unavailable_notified_at[51] = 12345.0

        reset_health_state()

        assert get_health() == {}
        assert bedcheck_ai_module._last_unavailable_notified_at == {}

    @pytest.mark.asyncio
    async def test_first_failure_after_reset_notifies_again(self):
        """The reason the cooldown has to be cleared too: after a settings
        change the next failure is a first failure, not a repeat."""
        mock_notify_service = MagicMock()
        mock_notify_service.on_printer_error = AsyncMock()
        bedcheck_ai_module._record_outcome(52, "unavailable", "connection failed", "json_schema")
        bedcheck_ai_module._last_unavailable_notified_at[52] = time.monotonic()

        reset_health_state()

        with (
            _patch_settings(),
            patch(
                "backend.app.services.bedcheck_ai.httpx.AsyncClient",
                return_value=_mock_client(post_side_effect=httpx.ConnectError("refused")),
            ),
            patch("backend.app.services.bedcheck_ai.async_session", _fake_session_factory()),
            patch("backend.app.services.notification_service.notification_service", mock_notify_service),
        ):
            await check_bed_ai(52, FAKE_JPEG, "built-in")
            await _drain_notification_tasks()

        mock_notify_service.on_printer_error.assert_awaited_once()


class TestDispatchWithNoRunningLoop:
    """Item 3: asyncio.create_task() constructs its coroutine argument BEFORE
    calling asyncio.get_running_loop() to schedule it, so a RuntimeError from
    that call (no running loop -- only reachable from sync test/CLI contexts)
    still leaves a real, never-awaited coroutine object behind unless it is
    closed explicitly."""

    def test_no_running_loop_closes_the_coroutine(self):
        """Asserts close() is actually called on the constructed coroutine
        when asyncio.create_task() raises RuntimeError (no running loop).

        Uses a substitute object returned by a patched
        _maybe_notify_unavailable_transition rather than a real coroutine +
        gc-based "was never awaited" warning detection: unittest.mock
        records call arguments on the patched asyncio.create_task, which
        keeps a live reference to the real coroutine object for the
        lifetime of the `with patch(...)` block and masks the refcount drop
        a gc.collect()-based assertion would rely on -- i.e. that style of
        test passes whether or not `coro.close()` is actually called, which
        defeats the point of a red-proof. Asserting close() was invoked is
        deterministic and directly proves the fix. Red-proof: deleting the
        `coro.close()` line in _dispatch_unavailable_transition's except
        branch makes this fail with close.assert_called_once() unsatisfied.
        """
        fake_coro = MagicMock(name="fake_notify_coroutine")
        with (
            patch(
                "backend.app.services.bedcheck_ai._maybe_notify_unavailable_transition",
                MagicMock(return_value=fake_coro),
            ),
            patch(
                "backend.app.services.bedcheck_ai.asyncio.create_task",
                side_effect=RuntimeError("no running event loop"),
            ),
        ):
            bedcheck_ai_module._dispatch_unavailable_transition(99, "ok", "connection failed")

        fake_coro.close.assert_called_once()

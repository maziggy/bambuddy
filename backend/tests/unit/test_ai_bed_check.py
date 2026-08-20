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

import io
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from backend.app.schemas.settings import AppSettingsUpdate
from backend.app.services.bedcheck_ai import (
    AiBedCheckError,
    _analyze_frame_ai,
    _clamp_confidence,
    _generic_fail_open_reason,
    _parse_verdict_json,
    build_ai_result,
    check_bed_ai,
)


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
    malformed-but-parseable shapes that must be treated as parse failures
    (missing required key, non-bool is_empty)."""

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

    def test_garbage_text_fails_to_parse(self):
        raw = "I cannot determine this from the image."
        with pytest.raises(AiBedCheckError, match="invalid response from AI backend"):
            _parse_verdict_json(raw)


class TestVerdictMapping:
    """build_ai_result: pure formatter mapping a verdict onto
    PlateDetectionResult, including the confidence*100 formula."""

    def test_occupied_verdict_sets_difference_percent_from_confidence(self):
        """difference_percent = confidence * 100 when occupied, not
        (1 - confidence) * 100 -- a confident occupied detection must render
        a high, not low, difference number."""
        result = build_ai_result(is_empty=False, confidence=0.95, reason="a print is on it", camera_source="built-in")
        assert result.is_empty is False
        assert result.difference_percent == 95.0

    def test_empty_verdict_zeroes_difference_percent(self):
        result = build_ai_result(is_empty=True, confidence=0.9, reason="bare plate", camera_source="built-in")
        assert result.is_empty is True
        assert result.difference_percent == 0.0

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

    def test_to_dict_confidence_and_difference_percent_are_always_floats(self):
        result = build_ai_result(is_empty=False, confidence=0.87, reason="x", camera_source="built-in")
        d = result.to_dict()
        assert isinstance(d["confidence"], float)
        assert isinstance(d["difference_percent"], float)


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

    def _assert_fail_open_shape(self, result, expected_message):
        assert result.is_empty is True
        assert result.confidence == 0.0
        assert result.difference_percent == 0.0
        assert result.needs_calibration is False
        assert result.message == expected_message

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
            is_empty, confidence, reason = await _analyze_frame_ai(FAKE_JPEG, printer_id=1)
        assert is_empty is True
        assert confidence == 0.92
        assert reason == "bare plate"

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
            is_empty, confidence, reason = await _analyze_frame_ai(FAKE_JPEG, printer_id=1)
        assert is_empty is False
        assert confidence == 0.7
        assert client.post.await_count == 2

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

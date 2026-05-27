"""Unit tests for Obico detection smoothing math."""

import pytest

from backend.app.services.obico_smoothing import (
    BASE_HIGH,
    BASE_LOW,
    WARMUP_FRAMES,
    PrintState,
    classify,
    score_from_detections,
    thresholds,
)


class TestThresholds:
    def test_medium_matches_base(self):
        low, high = thresholds("medium")
        assert low == pytest.approx(BASE_LOW)
        assert high == pytest.approx(BASE_HIGH)

    def test_low_sensitivity_is_stricter(self):
        low, high = thresholds("low")
        assert low > BASE_LOW
        assert high > BASE_HIGH

    def test_high_sensitivity_is_looser(self):
        low, high = thresholds("high")
        assert low < BASE_LOW
        assert high < BASE_HIGH

    def test_unknown_falls_back_to_medium(self):
        assert thresholds("bogus") == thresholds("medium")


class TestScoreFromDetections:
    def test_empty(self):
        assert score_from_detections([]) == 0.0
        assert score_from_detections(None) == 0.0

    def test_sums_confidences(self):
        dets = [["failure", 0.3, [0, 0, 10, 10]], ["failure", 0.5, [0, 0, 10, 10]]]
        assert score_from_detections(dets) == pytest.approx(0.8)

    def test_ignores_malformed(self):
        dets = [["failure", 0.4, []], ["bad"], ["failure", "nan", []]]
        assert score_from_detections(dets) == pytest.approx(0.4)


class TestPrintState:
    def test_warmup_returns_zero(self):
        state = PrintState()
        for _ in range(WARMUP_FRAMES):
            assert state.update(0.9) == 0.0

    def test_after_warmup_returns_nonzero_for_hits(self):
        state = PrintState()
        for _ in range(WARMUP_FRAMES):
            state.update(0.9)
        score = state.update(0.9)
        assert score > 0.0

    def test_sustained_zero_stays_safe(self):
        state = PrintState()
        scores = [state.update(0.0) for _ in range(WARMUP_FRAMES + 50)]
        assert max(scores) == 0.0

    def test_sustained_hits_eventually_cross_high(self):
        """A stream of high-confidence frames must escalate to 'failure'."""
        state = PrintState()
        final = 0.0
        for _ in range(WARMUP_FRAMES + 200):
            final = state.update(1.0)
        _, high = thresholds("medium")
        assert final >= high

    def test_isolated_spike_does_not_trigger_failure(self):
        """A single noisy frame in a clean stream must not cross HIGH."""
        state = PrintState()
        for _ in range(WARMUP_FRAMES):
            state.update(0.0)
        score = state.update(1.0)
        _, high = thresholds("medium")
        assert score < high


class TestClassify:
    def test_safe(self):
        assert classify(0.0, "medium") == "safe"
        assert classify(BASE_LOW - 0.01, "medium") == "safe"

    def test_warning(self):
        assert classify(BASE_LOW, "medium") == "warning"
        assert classify((BASE_LOW + BASE_HIGH) / 2, "medium") == "warning"

    def test_failure(self):
        assert classify(BASE_HIGH, "medium") == "failure"
        assert classify(1.0, "medium") == "failure"

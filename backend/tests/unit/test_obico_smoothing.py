"""Unit tests for Obico detection smoothing math.

The decision is Obico's own is_failing() (#3459): the score is the EWM minus
the printer's long-term mean, so what the camera always sees is baseline.
"""

import pytest

from backend.app.services.obico_smoothing import (
    BASE_HIGH,
    BASE_LOW,
    ESCALATING_FACTOR,
    WARMUP_FRAMES,
    Baseline,
    PrintState,
    Reading,
    classify,
    score_from_detections,
    thresholds,
    triggers_action,
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


def _feed(state: PrintState, value: float, frames: int) -> Reading:
    reading = Reading()
    for _ in range(frames):
        reading = state.update(value)
    return reading


class TestPrintState:
    def test_warmup_is_always_safe(self):
        state = PrintState()
        for _ in range(WARMUP_FRAMES):
            assert classify(state.update(5.0), "high") == "safe"

    def test_sustained_zero_stays_safe(self):
        state = PrintState()
        assert classify(_feed(state, 0.0, WARMUP_FRAMES + 50), "high") == "safe"

    def test_a_steady_background_from_the_start_is_baseline(self):
        # The plate artwork in #3459: 5 boxes summing to 0.82 in every frame.
        # The raw EWM sat above HIGH; minus the baseline it is nothing.
        state = PrintState()
        reading = _feed(state, 0.82, WARMUP_FRAMES + 2000)
        assert state.ewm_mean > BASE_HIGH
        assert reading.score == pytest.approx(0.0, abs=1e-9)
        assert classify(reading, "high") == "safe"

    def test_the_baseline_outlives_the_print(self):
        # Seen on every print of this printer: the next print starts with it.
        baseline = Baseline()
        _feed(PrintState(baseline=baseline), 0.82, 3000)
        reading = _feed(PrintState(baseline=baseline), 0.82, WARMUP_FRAMES + 5)
        assert classify(reading, "medium") == "safe"
        # Without it, the same frames appearing on a clean printer are a
        # sudden sustained rise, which is what Obico pauses for.
        fresh = PrintState()
        _feed(fresh, 0.0, 600)
        assert classify(_feed(fresh, 0.82, 40), "medium") == "failure"

    def test_a_sudden_sustained_rise_pauses(self):
        state = PrintState()
        _feed(state, 0.0, 600)
        reading = _feed(state, 3.0, 40)
        assert reading.score / ESCALATING_FACTOR > BASE_HIGH
        assert classify(reading, "medium") == "failure"

    def test_a_late_sudden_background_is_a_rise_then_settles(self):
        # #3459: ~4 h of nothing, then boxes (~0.8) in every frame. That is a
        # sudden sustained rise, which Obico pauses for, like spaghetti; once
        # the last ~52 min account for it, it is no longer failing.
        state = PrintState()
        _feed(state, 0.0, 1550)
        verdicts = [classify(state.update(0.8), "medium") for _ in range(200)]
        assert "failure" in verdicts[:40]
        assert verdicts[-40:] == ["safe"] * 40

    def test_a_slow_build_up_warns_without_pausing(self):
        # The same 0.8 reached over ~25 minutes: Obico alerts, never pauses.
        state = PrintState()
        _feed(state, 0.0, 1550)
        verdicts = {classify(state.update(0.8 * min(1.0, i / 150)), "medium") for i in range(1, 300)}
        assert verdicts == {"safe", "warning"}

    def test_in_the_band_the_short_window_rise_must_be_beaten(self):
        # Between LOW and HIGH, a score that a slow build-up over the last
        # ~52 min accounts for (x3.8) is not failing.
        between = (BASE_LOW + BASE_HIGH) / 2
        assert classify(Reading(score=between, short_rise=between / 3.8 + 0.01, warmup=False), "medium") == "safe"
        assert classify(Reading(score=between, short_rise=between / 3.8 - 0.01, warmup=False), "medium") == "warning"

    def test_an_isolated_spike_does_not_alert(self):
        state = PrintState()
        _feed(state, 0.0, WARMUP_FRAMES + 100)
        assert classify(state.update(1.0), "medium") == "safe"


class TestClassify:
    @staticmethod
    def _r(score: float, short_rise: float = 0.0) -> Reading:
        return Reading(score=score, short_rise=short_rise, warmup=False)

    def test_safe_below_low(self):
        assert classify(self._r(BASE_LOW - 0.01), "medium") == "safe"

    def test_warning_above_high(self):
        assert classify(self._r(BASE_HIGH + 0.01, short_rise=1.0), "medium") == "warning"

    def test_failure_needs_the_escalated_score(self):
        assert classify(self._r(BASE_HIGH * ESCALATING_FACTOR - 0.01, short_rise=1.0), "medium") == "warning"
        assert classify(self._r(BASE_HIGH * ESCALATING_FACTOR + 0.01), "medium") == "failure"

    def test_sensitivity_scales_the_short_window_check_too(self):
        # Obico multiplies the score by the sensitivity; we scale thresholds.
        # Medium in the band, 0.5 vs 0.13 * 3.8 = 0.494: failing. Low (x1.25
        # thresholds, 0.475..0.975): 0.5 vs 0.494 * 1.25 = 0.6175: not.
        r = self._r(0.5, short_rise=0.13)
        assert classify(r, "medium") == "warning"
        assert classify(r, "low") == "safe"

    def test_warmup_is_safe_whatever_the_score(self):
        assert classify(Reading(score=10.0, short_rise=0.0, warmup=True), "high") == "safe"


class TestTriggersAction:
    def test_notify_fires_at_the_alert_level(self):
        assert triggers_action("warning", "notify")
        assert triggers_action("failure", "notify")
        assert not triggers_action("safe", "notify")

    @pytest.mark.parametrize("action", ["pause", "pause_and_off"])
    def test_pausing_waits_for_the_pause_level(self, action):
        assert not triggers_action("warning", action)
        assert triggers_action("failure", action)

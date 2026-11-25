"""Temporal smoothing for Obico ML detection scores.

A port of Obico's own decision (obico-server ``lib/prediction.py``,
``update_prediction_with_detections`` and ``is_failing``; parameters from
``FD_1ST_GEN_PARAMS``):

- per-frame ``current_p`` = sum of detection confidences
- ``ewm_mean`` = exponentially weighted mean (alpha = 2 / (span + 1), span = 12)
- ``rolling_mean_short`` = mean of the print's last 310 frames (~52 min at 10 s)
- ``rolling_mean_long`` = mean of the printer's last 7200 frames (~20 h),
  kept across prints: whatever the camera always sees (plate texture,
  labels) is baseline, not failure (#3459)
- the score is ``ewm_mean - rolling_mean_long``. Below LOW it is safe, above
  HIGH failing; in between it is failing only when it also beats
  ``(rolling_mean_short - rolling_mean_long) * 3.8``
- "warning" is failing at the plain thresholds (Obico's alert); "failure"
  is failing with the score divided by 1.75 (Obico's pause), so pausing
  takes a much clearer signal than a notification
- the first ``WARMUP_FRAMES`` frames of a print are always safe

Obico multiplies the score by the printer's sensitivity; scaling the LOW/HIGH
thresholds by its inverse is the same, and is what the status panel shows.
"""

import math
from collections import deque
from dataclasses import dataclass, field

EWM_SPAN = 12
EWM_ALPHA = 2.0 / (EWM_SPAN + 1)
ROLLING_SHORT = 310
ROLLING_LONG = 7200
WARMUP_FRAMES = 30
ROLLING_MEAN_SHORT_MULTIPLE = 3.8
ESCALATING_FACTOR = 1.75

# Base thresholds; sensitivity multipliers adjust them
BASE_LOW = 0.38
BASE_HIGH = 0.78

SENSITIVITY_MULT = {
    "low": 1.25,  # harder to trigger — higher thresholds
    "medium": 1.0,
    "high": 0.75,  # easier to trigger — lower thresholds
}


def thresholds(sensitivity: str) -> tuple[float, float]:
    mult = SENSITIVITY_MULT.get(sensitivity, 1.0)
    return BASE_LOW * mult, BASE_HIGH * mult


class _RollingMean:
    """Exact mean of the last ``size`` values."""

    def __init__(self, size: int):
        self._buf: deque = deque(maxlen=size)
        self._sum = 0.0

    def add(self, value: float) -> None:
        if len(self._buf) == self._buf.maxlen:
            self._sum -= self._buf[0]
        self._buf.append(value)
        self._sum += value

    @property
    def mean(self) -> float:
        return self._sum / len(self._buf) if self._buf else 0.0


class Baseline(_RollingMean):
    """A printer's long-term mean, kept across its prints (in memory)."""

    def __init__(self):
        super().__init__(ROLLING_LONG)


@dataclass
class Reading:
    """One frame's smoothed state, as Obico's is_failing() sees it."""

    score: float = 0.0  # ewm_mean - rolling_mean_long
    short_rise: float = 0.0  # rolling_mean_short - rolling_mean_long
    warmup: bool = True


@dataclass
class PrintState:
    """Per-print smoothing state. Reset when a new print starts; the
    baseline it is given belongs to the printer and outlives it."""

    baseline: Baseline = field(default_factory=Baseline)
    frame_count: int = 0
    ewm_mean: float = 0.0
    short: _RollingMean = field(default_factory=lambda: _RollingMean(ROLLING_SHORT))
    last: Reading = field(default_factory=Reading)

    def update(self, current_p: float) -> Reading:
        """Feed a new per-frame score and return the smoothed reading."""
        self.frame_count += 1
        if self.frame_count == 1:
            self.ewm_mean = current_p
        else:
            self.ewm_mean = EWM_ALPHA * current_p + (1 - EWM_ALPHA) * self.ewm_mean
        self.short.add(current_p)
        self.baseline.add(current_p)
        long_mean = self.baseline.mean
        self.last = Reading(
            score=self.ewm_mean - long_mean,
            short_rise=self.short.mean - long_mean,
            warmup=self.frame_count <= WARMUP_FRAMES,
        )
        return self.last


def _is_failing(reading: Reading, sensitivity: str, escalating_factor: float) -> bool:
    if reading.warmup:
        return False
    low, high = thresholds(sensitivity)
    score = reading.score / escalating_factor
    if score < low:
        return False
    if score > high:
        return True
    # Thresholds carry the sensitivity here; Obico scales the score instead,
    # so the short-window check gets the same factor to compare like with like.
    mult = SENSITIVITY_MULT.get(sensitivity, 1.0)
    return score > reading.short_rise * ROLLING_MEAN_SHORT_MULTIPLE * mult


def classify(reading: Reading, sensitivity: str) -> str:
    """'safe', 'warning' (Obico would alert) or 'failure' (Obico would pause)."""
    if _is_failing(reading, sensitivity, ESCALATING_FACTOR):
        return "failure"
    if _is_failing(reading, sensitivity, 1.0):
        return "warning"
    return "safe"


def triggers_action(verdict: str, action: str) -> bool:
    """A notification goes out at Obico's alert level; pausing, with or
    without power-off, waits for its pause level."""
    if action == "notify":
        return verdict in ("warning", "failure")
    return verdict == "failure"


def score_from_detections(detections: list) -> float:
    """Sum confidences from the ML API `detections` array.

    Each detection is `[label, confidence, [x, y, w, h]]`. We only care about
    the confidence column — label is always "failure" for the single-class model.
    """
    total = 0.0
    for det in detections or []:
        try:
            value = float(det[1])
        except (IndexError, TypeError, ValueError):
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        total += value
    return total

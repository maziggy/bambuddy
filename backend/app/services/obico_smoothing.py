"""Temporal smoothing for Obico ML detection scores.

Ports Obico's failure-detection math:
- per-frame `current_p` = sum of detection confidences
- `ewm_mean` = exponentially weighted mean (alpha = 2 / (span + 1), span = 12)
- `rolling_mean_short` = ~310 frames of recent activity (≈52 min at 10s/frame)
- `rolling_mean_long`  = ~7200 frames of long-term baseline noise
- First `WARMUP_FRAMES` frames always report "safe" while the state settles
- Final score = max(ewm_mean, rolling_mean_short - rolling_mean_long)
- Thresholds: LOW < score < HIGH is "warning", >= HIGH is "failure"
"""

import math
from collections import deque
from dataclasses import dataclass, field

EWM_SPAN = 12
EWM_ALPHA = 2.0 / (EWM_SPAN + 1)
ROLLING_SHORT = 310
ROLLING_LONG = 7200
WARMUP_FRAMES = 30

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


@dataclass
class PrintState:
    """Per-print smoothing state. Reset when a new print starts."""

    frame_count: int = 0
    ewm_mean: float = 0.0
    short_sum: float = 0.0
    long_sum: float = 0.0
    short_buf: deque = field(default_factory=lambda: deque(maxlen=ROLLING_SHORT))
    long_buf: deque = field(default_factory=lambda: deque(maxlen=ROLLING_LONG))

    def update(self, current_p: float) -> float:
        """Feed a new per-frame score and return the smoothed score.

        Returns 0.0 during warmup so early noise doesn't trigger actions.
        """
        self.frame_count += 1

        if self.frame_count == 1:
            self.ewm_mean = current_p
        else:
            self.ewm_mean = EWM_ALPHA * current_p + (1 - EWM_ALPHA) * self.ewm_mean

        if len(self.short_buf) == self.short_buf.maxlen:
            self.short_sum -= self.short_buf[0]
        self.short_buf.append(current_p)
        self.short_sum += current_p

        if len(self.long_buf) == self.long_buf.maxlen:
            self.long_sum -= self.long_buf[0]
        self.long_buf.append(current_p)
        self.long_sum += current_p

        if self.frame_count <= WARMUP_FRAMES:
            return 0.0

        short_mean = self.short_sum / len(self.short_buf)
        long_mean = self.long_sum / len(self.long_buf)
        return max(self.ewm_mean, short_mean - long_mean)


def classify(score: float, sensitivity: str) -> str:
    """Return 'safe', 'warning', or 'failure' for a smoothed score."""
    low, high = thresholds(sensitivity)
    if score >= high:
        return "failure"
    if score >= low:
        return "warning"
    return "safe"


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

"""Scale reader wrapper with stability detection and calibration."""

import logging
import time
from collections import deque
from threading import RLock

logger = logging.getLogger(__name__)

MOVING_AVG_SIZE = 20


class ScaleReader:
    def __init__(
        self,
        tare_offset: int = 0,
        calibration_factor: float = 1.0,
        driver: str = "nau7802",
        data_pin: int = 5,
        clock_pin: int = 6,
    ):
        self._driver = driver
        self._lock = RLock()
        self._has_sample = False
        self._scale = None
        self._tare_offset = tare_offset
        self._calibration_factor = calibration_factor
        self._samples: deque[float] = deque(maxlen=MOVING_AVG_SIZE)
        self._stability_history: deque[tuple[float, float]] = deque(maxlen=20)
        self._ok = False
        self._last_raw = 0
        self._initialization_error: str | None = None

        try:
            if driver == "hx711":
                from .hx711 import HX711

                self._scale = HX711(data_pin, clock_pin)
            elif driver == "nau7802":
                from .nau7802 import NAU7802

                self._scale = NAU7802()
            else:
                raise ValueError(f"Unknown scale driver: {driver}")
            self._scale.init()
            self._ok = True
            logger.info("Scale initialized: %s (tare=%d, cal=%.6f)", driver, tare_offset, calibration_factor)
        except Exception as e:
            self._initialization_error = str(e)
            self.close()
            if driver == "hx711":
                logger.warning("HX711 scale unavailable: %s", e)
            else:
                logger.info("Scale not available: %s", e)

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def last_raw(self) -> int:
        return self._last_raw

    def close(self):
        with self._lock:
            self._ok = False
            try:
                if self._scale:
                    self._scale.close()
            except Exception:
                pass

    def update_calibration(self, tare_offset: int, calibration_factor: float):
        with self._lock:
            self._tare_offset = tare_offset
            self._calibration_factor = calibration_factor
            self._samples.clear()
            self._stability_history.clear()
            logger.info("Calibration updated: tare=%d, factor=%.6f", tare_offset, calibration_factor)

    def tare(self):
        with self._lock:
            if self._has_sample:
                self._tare_offset = self._last_raw
                self._samples.clear()
                self._stability_history.clear()
                logger.info("Tared at raw=%d", self._tare_offset)
            return self._tare_offset

    def diagnostic(self) -> str:
        """Read the active driver without a second process claiming its GPIOs."""
        with self._lock:
            if self._initialization_error is not None:
                raise RuntimeError(f"Scale initialization failed: {self._initialization_error}")
            values = []
            info = self._scale.diagnostic_info() if hasattr(self._scale, "diagnostic_info") else ""
            deadline = time.monotonic() + 5.0
            while len(values) < 10:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Scale diagnostic timed out")
                if self._scale.data_ready():
                    values.append(self._scale.read_raw())
                else:
                    time.sleep(0.005)
            return (
                f"{type(self._scale).__name__} scale diagnostic\n"
                f"{info}\n"
                f"Samples: {len(values)}\nMin: {min(values)}\nMax: {max(values)}\n"
                f"Average: {sum(values) / len(values):.1f} counts\n"
                f"Spread: {max(values) - min(values)} counts\n"
                f"Tare: {self._tare_offset}\nCalibration: {self._calibration_factor} g/count"
            )

    def read(self) -> tuple[float, bool, int] | None:
        """Read current weight. Returns (grams, stable, raw_adc) or None."""
        with self._lock:
            return self._read()

    def _read(self) -> tuple[float, bool, int] | None:
        try:
            if not self._scale.data_ready():
                return None

            raw = self._scale.read_raw()
            self._last_raw = raw
            self._has_sample = True
            self._ok = True

            grams = (raw - self._tare_offset) * self._calibration_factor
            self._samples.append(grams)

            # Moving average
            avg_grams = sum(self._samples) / len(self._samples)

            # Stability: track readings over time
            now = time.monotonic()
            self._stability_history.append((now, avg_grams))

            # Stable if all readings within 1s window are within 2g of each other
            stable = False
            if len(self._stability_history) >= 5:
                cutoff = now - 1.0
                recent = [g for t, g in self._stability_history if t >= cutoff]
                if len(recent) >= 3:
                    spread = max(recent) - min(recent)
                    stable = spread < 2.0

            return round(avg_grams, 1), stable, raw

        except Exception as e:
            logger.debug("Scale read error: %s", e)
            if self._driver == "hx711":
                if self._ok:
                    logger.warning("HX711 scale read failed: %s", e)
                self._ok = False
                self._samples.clear()
                self._stability_history.clear()
            return None

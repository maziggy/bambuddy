"""HX711 channel A / gain 128 through Linux IIO.

Linux owns both GPIOs and generates the timing-critical clock pulses. The daemon
only reads sysfs as its normal service user. Kernel offset-binary samples are
converted to the signed counts used by existing tare/calibration settings.
"""

import struct
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path

SYSFS_ROOT = Path("/sys")
# Keep I2C, SPI and the SpoolBuddy PN5180 control lines available.
RESERVED_PINS = frozenset((0, 1, 2, 3, 7, 8, 9, 10, 11, 23, 24, 25))


class HX711:
    def __init__(self, data_pin: int = 5, clock_pin: int = 6):
        if not (0 <= data_pin <= 27 and 0 <= clock_pin <= 27) or data_pin == clock_pin:
            raise ValueError("HX711 requires two different GPIO numbers between 0 and 27")
        if {data_pin, clock_pin} & RESERVED_PINS:
            raise ValueError("HX711 pins conflict with reserved I2C/SPI or PN5180 wiring")
        self._data_pin = data_pin
        self._clock_pin = clock_pin
        self._device: Path | None = None
        self._gain128_scale: Decimal | None = None

    def _matches_wiring(self, device: Path) -> bool:
        for node in (device / "of_node", device / "device/of_node", device.parent / "of_node"):
            if not node.is_dir():
                continue
            if b"avia,hx711" not in (node / "compatible").read_bytes().split(b"\0"):
                continue
            try:
                dout = struct.unpack(">III", (node / "dout-gpios").read_bytes())
                sck = struct.unpack(">III", (node / "sck-gpios").read_bytes())
            except struct.error as error:
                raise ValueError("Unsupported HX711 device-tree GPIO format") from error
            return dout[0] == sck[0] and dout[1:] == (self._data_pin, 0) and sck[1:] == (self._clock_pin, 0)
        return False

    @staticmethod
    def _scale_value(text: str) -> Decimal:
        try:
            value = Decimal(text.strip())
        except InvalidOperation as error:
            raise ValueError("Invalid HX711 IIO scale") from error
        if not value.is_finite() or value <= 0:
            raise ValueError("Invalid HX711 IIO scale")
        return value

    def init(self):
        self.close()
        service = subprocess.run(
            ["systemctl", "is-failed", "--quiet", "spoolbuddy-hx711.service"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        if service.returncode == 0:
            raise RuntimeError("spoolbuddy-hx711.service failed; check journalctl -u spoolbuddy-hx711.service")
        matches = []
        for entry in (SYSFS_ROOT / "bus/iio/devices").glob("iio:device*"):
            device = entry.resolve()
            if not device.is_relative_to(SYSFS_ROOT / "devices"):
                continue
            if (device / "name").read_text().strip() == "hx711" and self._matches_wiring(device):
                matches.append(device)
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one HX711 kernel device on DT GPIO{self._data_pin}/SCK GPIO{self._clock_pin}; "
                f"found {len(matches)}. Check spoolbuddy-hx711.service."
            )
        self._device = matches[0]
        try:
            scales = (self._device / "in_voltage0_scale_available").read_text().split()
            if not scales:
                raise ValueError("HX711 channel A has no available gain settings")
            # Channel A supports gains 64 and 128; the smaller scale is gain 128.
            self._gain128_scale = min(self._scale_value(value) for value in scales)
            # Verify real read access and discard the initial conversion.
            self.read_raw()
        except Exception:
            self.close()
            raise

    def data_ready(self) -> bool:
        if self._device is None:
            raise RuntimeError("HX711 is closed")
        if not (self._device / "in_voltage0_raw").is_file():
            raise OSError("HX711 kernel device disappeared")
        # The kernel waits for DOUT with its own bounded timeout during read_raw.
        return True

    def read_raw(self) -> int:
        self.data_ready()
        assert self._device is not None
        scale = self._scale_value((self._device / "in_voltage_scale").read_text())
        if scale != self._gain128_scale:
            raise OSError("HX711 channel A gain changed; restart spoolbuddy-hx711.service")
        text = (self._device / "in_voltage0_raw").read_text(encoding="ascii").strip()
        if not text.isascii() or not text.isdecimal():
            raise ValueError("Invalid HX711 raw ADC value")
        raw = int(text)
        if not 0 < raw < 0xFFFFFF:
            raise OSError("HX711 input saturated or invalid; check load cell wiring or overload")
        return raw - 0x800000

    def close(self):
        self._device = None
        self._gain128_scale = None

    def diagnostic_info(self) -> str:
        self.data_ready()
        return (
            f"Linux IIO device: {self._device}\n"
            f"DT: GPIO{self._data_pin}; SCK: GPIO{self._clock_pin}\n"
            "Channel: A; gain: 128\n"
            "Samples: signed 24-bit counts (not grams)\n"
            "HX711 rate is selected on the board; supply voltage is not measured by this diagnostic."
        )

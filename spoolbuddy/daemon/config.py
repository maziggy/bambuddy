"""Configuration loader for SpoolBuddy daemon.

All configuration is via environment variables. The systemd service file
or a shell wrapper sets these before launching the daemon.

Required:
    SPOOLBUDDY_BACKEND_URL  — Bambuddy server URL (e.g. http://192.168.1.100:5000)
    SPOOLBUDDY_API_KEY      — API key created in Bambuddy Settings → API Keys

Optional:
    SPOOLBUDDY_DEVICE_ID    — Unique device identifier (default: derived from MAC)
    SPOOLBUDDY_HOSTNAME     — Display name (default: system hostname)
"""

import os
import socket
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    backend_url: str = ""
    api_key: str = ""
    device_id: str = ""
    hostname: str = ""

    nfc_poll_interval: float = 0.3
    scale_read_interval: float = 0.1
    scale_report_interval: float = 1.0
    heartbeat_interval: float = 10.0
    stability_threshold: float = 2.0
    stability_window: float = 1.0

    tare_offset: int = 0
    calibration_factor: float = 1.0

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()

        cfg.backend_url = os.environ.get("SPOOLBUDDY_BACKEND_URL", "")
        cfg.api_key = os.environ.get("SPOOLBUDDY_API_KEY", "")
        cfg.device_id = os.environ.get("SPOOLBUDDY_DEVICE_ID", "")
        cfg.hostname = os.environ.get("SPOOLBUDDY_HOSTNAME", "")

        if not cfg.backend_url:
            raise RuntimeError("SPOOLBUDDY_BACKEND_URL is required (e.g. http://192.168.1.100:5000)")
        if not cfg.api_key:
            raise RuntimeError("SPOOLBUDDY_API_KEY is required (create one in Bambuddy Settings → API Keys)")

        # Default device_id from MAC address
        if not cfg.device_id:
            cfg.device_id = _get_mac_id()

        # Default hostname from system
        if not cfg.hostname:
            cfg.hostname = socket.gethostname()

        return cfg


def _get_mac_id() -> str:
    """Generate a device ID from the primary network interface MAC address."""
    try:
        for iface in Path("/sys/class/net").iterdir():
            if iface.name == "lo":
                continue
            addr_file = iface / "address"
            if addr_file.exists():
                mac = addr_file.read_text().strip().replace(":", "")
                if mac and mac != "000000000000":
                    return f"sb-{mac}"
    except Exception:
        pass
    import uuid

    return f"sb-{uuid.uuid4().hex[:12]}"

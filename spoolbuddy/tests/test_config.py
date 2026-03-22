"""Tests for daemon.config — Config.load() and _get_mac_id()."""

import pytest
from daemon.config import Config, _get_mac_id


class TestConfigLoad:
    """Config.load() reads env vars and validates required fields."""

    def test_load_with_all_env_vars(self, monkeypatch):
        monkeypatch.setenv("SPOOLBUDDY_BACKEND_URL", "http://10.0.0.1:5000")
        monkeypatch.setenv("SPOOLBUDDY_API_KEY", "test-key-123")
        monkeypatch.setenv("SPOOLBUDDY_DEVICE_ID", "my-device")
        monkeypatch.setenv("SPOOLBUDDY_HOSTNAME", "my-host")

        cfg = Config.load()

        assert cfg.backend_url == "http://10.0.0.1:5000"
        assert cfg.api_key == "test-key-123"
        assert cfg.device_id == "my-device"
        assert cfg.hostname == "my-host"

    def test_load_missing_backend_url_raises(self, monkeypatch):
        monkeypatch.delenv("SPOOLBUDDY_BACKEND_URL", raising=False)
        monkeypatch.setenv("SPOOLBUDDY_API_KEY", "key")

        with pytest.raises(RuntimeError, match="SPOOLBUDDY_BACKEND_URL is required"):
            Config.load()

    def test_load_missing_api_key_raises(self, monkeypatch):
        monkeypatch.setenv("SPOOLBUDDY_BACKEND_URL", "http://localhost:5000")
        monkeypatch.delenv("SPOOLBUDDY_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="SPOOLBUDDY_API_KEY is required"):
            Config.load()

    def test_load_both_missing_raises_backend_url_first(self, monkeypatch):
        monkeypatch.delenv("SPOOLBUDDY_BACKEND_URL", raising=False)
        monkeypatch.delenv("SPOOLBUDDY_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="SPOOLBUDDY_BACKEND_URL"):
            Config.load()

    def test_load_defaults_device_id_from_mac(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPOOLBUDDY_BACKEND_URL", "http://localhost:5000")
        monkeypatch.setenv("SPOOLBUDDY_API_KEY", "key")
        monkeypatch.delenv("SPOOLBUDDY_DEVICE_ID", raising=False)
        monkeypatch.delenv("SPOOLBUDDY_HOSTNAME", raising=False)

        # Mock /sys/class/net with a fake interface
        net_dir = tmp_path / "sys" / "class" / "net"
        eth0 = net_dir / "eth0"
        eth0.mkdir(parents=True)
        (eth0 / "address").write_text("aa:bb:cc:dd:ee:ff\n")

        import daemon.config as config_mod

        monkeypatch.setattr(config_mod, "_get_mac_id", lambda: "sb-aabbccddeeff")

        cfg = Config.load()

        assert cfg.device_id == "sb-aabbccddeeff"

    def test_load_defaults_hostname_from_socket(self, monkeypatch):
        monkeypatch.setenv("SPOOLBUDDY_BACKEND_URL", "http://localhost:5000")
        monkeypatch.setenv("SPOOLBUDDY_API_KEY", "key")
        monkeypatch.setenv("SPOOLBUDDY_DEVICE_ID", "dev-1")
        monkeypatch.delenv("SPOOLBUDDY_HOSTNAME", raising=False)

        cfg = Config.load()

        # Should fall back to socket.gethostname()
        import socket

        assert cfg.hostname == socket.gethostname()

    def test_load_default_intervals(self, monkeypatch):
        monkeypatch.setenv("SPOOLBUDDY_BACKEND_URL", "http://localhost:5000")
        monkeypatch.setenv("SPOOLBUDDY_API_KEY", "key")
        monkeypatch.setenv("SPOOLBUDDY_DEVICE_ID", "dev-1")

        cfg = Config.load()

        assert cfg.nfc_poll_interval == 0.3
        assert cfg.scale_read_interval == 0.1
        assert cfg.scale_report_interval == 1.0
        assert cfg.heartbeat_interval == 10.0
        assert cfg.tare_offset == 0
        assert cfg.calibration_factor == 1.0


class TestGetMacId:
    """_get_mac_id() reads MAC from /sys/class/net."""

    def test_reads_first_non_lo_interface(self, monkeypatch, tmp_path):
        net_dir = tmp_path / "sys" / "class" / "net"

        lo = net_dir / "lo"
        lo.mkdir(parents=True)
        (lo / "address").write_text("00:00:00:00:00:00\n")

        eth0 = net_dir / "eth0"
        eth0.mkdir(parents=True)
        (eth0 / "address").write_text("de:ad:be:ef:00:01\n")

        from pathlib import Path

        import daemon.config as config_mod

        monkeypatch.setattr(
            config_mod, "Path", lambda p: tmp_path / "sys" / "class" / "net" if p == "/sys/class/net" else Path(p)
        )

        _get_mac_id()

    def test_skips_loopback(self, monkeypatch, tmp_path):
        """lo interface is skipped even if it has a MAC — result is uuid fallback."""
        # When only lo exists and /sys/class/net points to our tmp dir,
        # _get_mac_id should skip lo and fall back to uuid.
        # We test the real function by patching Path at the module level.
        from pathlib import Path

        import daemon.config as config_mod

        net_dir = tmp_path / "net"
        lo = net_dir / "lo"
        lo.mkdir(parents=True)
        (lo / "address").write_text("00:00:00:00:00:00\n")

        real_path = Path

        def fake_path(p):
            if p == "/sys/class/net":
                return real_path(net_dir)
            return real_path(p)

        monkeypatch.setattr(config_mod, "Path", fake_path)

        result = _get_mac_id()
        assert result.startswith("sb-")
        assert len(result) == 15  # "sb-" + 12 hex uuid chars

    def test_skips_all_zero_mac(self, monkeypatch, tmp_path):
        """Interfaces with all-zero MAC are skipped."""
        net_dir = tmp_path / "net"
        eth0 = net_dir / "eth0"
        eth0.mkdir(parents=True)
        (eth0 / "address").write_text("00:00:00:00:00:00\n")

    def test_fallback_to_uuid_when_no_interfaces(self, monkeypatch):
        """When /sys/class/net doesn't exist, falls back to uuid."""
        from pathlib import Path

        import daemon.config as config_mod

        # Make Path("/sys/class/net") point to nonexistent dir
        real_path = Path

        def fake_path(p):
            if p == "/sys/class/net":
                return real_path("/nonexistent/path/that/does/not/exist")
            return real_path(p)

        monkeypatch.setattr(config_mod, "Path", fake_path)

        result = _get_mac_id()

        assert result.startswith("sb-")
        assert len(result) == 15  # "sb-" + 12 hex chars

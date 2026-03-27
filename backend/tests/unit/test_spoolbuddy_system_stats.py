"""Tests for SpoolBuddy daemon system_stats collector."""

import pytest

pytest.importorskip("spoolbuddy", reason="spoolbuddy package not available in Docker")

from unittest.mock import patch

from spoolbuddy.daemon.system_stats import (
    _cpu_count,
    _cpu_temp,
    _disk_info,
    _load_avg,
    _memory_info,
    _os_info,
    _system_uptime,
    collect,
)


class TestCpuTemp:
    def test_reads_thermal_zone(self):
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value="52100"):
            assert _cpu_temp() == 52.1

    def test_returns_none_on_missing_file(self):
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value=None):
            assert _cpu_temp() is None

    def test_returns_none_on_bad_value(self):
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value="not_a_number"):
            assert _cpu_temp() is None


class TestMemoryInfo:
    SAMPLE_MEMINFO = (
        "MemTotal:        1024000 kB\n"
        "MemFree:          200000 kB\n"
        "MemAvailable:     512000 kB\n"
        "Buffers:           50000 kB\n"
    )

    def test_parses_meminfo(self):
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value=self.SAMPLE_MEMINFO):
            result = _memory_info()
            assert result is not None
            assert result["total_mb"] == 1000
            assert result["available_mb"] == 500
            assert result["used_mb"] == 500
            assert result["percent"] == 50.0

    def test_returns_none_on_missing(self):
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value=None):
            assert _memory_info() is None


class TestDiskInfo:
    def test_returns_disk_stats(self):
        result = _disk_info()
        # Should always work on Linux
        assert result is not None
        assert "total_gb" in result
        assert "used_gb" in result
        assert "free_gb" in result
        assert "percent" in result
        assert 0 <= result["percent"] <= 100


class TestLoadAvg:
    def test_returns_three_values(self):
        result = _load_avg()
        assert result is not None
        assert len(result) == 3
        for val in result:
            assert isinstance(val, float)


class TestCpuCount:
    def test_returns_positive_int(self):
        result = _cpu_count()
        assert result is not None
        assert result > 0


class TestOsInfo:
    def test_returns_required_keys(self):
        result = _os_info()
        assert "os" in result
        assert "kernel" in result
        assert "arch" in result
        assert "python" in result

    def test_parses_pretty_name(self):
        fake_release = 'PRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"\nID=raspbian\n'
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value=fake_release):
            result = _os_info()
            assert result["os"] == "Raspbian GNU/Linux 12 (bookworm)"


class TestSystemUptime:
    def test_parses_uptime(self):
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value="86400.55 172000.10"):
            assert _system_uptime() == 86400

    def test_returns_none_on_missing(self):
        with patch("spoolbuddy.daemon.system_stats._read_file", return_value=None):
            assert _system_uptime() is None


class TestCollect:
    def test_returns_dict_with_expected_keys(self):
        result = collect()
        assert isinstance(result, dict)
        assert "os" in result
        # These may or may not be present depending on platform, but os is always present

    def test_all_values_are_json_serializable(self):
        import json

        result = collect()
        # Should not raise
        json.dumps(result)

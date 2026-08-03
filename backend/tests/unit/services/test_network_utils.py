"""Tests for network interface enumeration.

Focus: the platform routing in get_network_interfaces(). macOS/BSD have fcntl
but not the Linux SIOCGIFADDR/SIOCGIFNETMASK ioctls, so the ioctl path there
silently returns nothing and the VP bind-interface dropdown comes up empty.
Everything that isn't Linux must go through the cross-platform psutil path.
"""

import socket
from collections import namedtuple
from unittest.mock import patch

from backend.app.services import network_utils

# Mimic the shape of psutil.net_if_addrs() / net_if_stats() entries we read.
_Addr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])
_Stats = namedtuple("snicstats", ["isup", "duplex", "speed", "mtu", "flags"])


def _fake_psutil():
    addrs = {
        "en0": [_Addr(socket.AF_INET, "192.168.1.50", "255.255.255.0", None, None)],
        "lo0": [_Addr(socket.AF_INET, "127.0.0.1", "255.0.0.0", None, None)],
        "awdl0": [_Addr(socket.AF_INET, "169.254.10.20", "255.255.0.0", None, None)],
        "utun3": [_Addr(socket.AF_INET, "100.64.0.7", "255.255.255.255", None, None)],
        "en5": [_Addr(socket.AF_INET, "10.0.0.9", "255.255.255.0", None, None)],
    }
    stats = {
        "en0": _Stats(True, 0, 0, 1500, 0),
        "lo0": _Stats(True, 0, 0, 16384, 0),
        "awdl0": _Stats(True, 0, 0, 1500, 0),
        "utun3": _Stats(True, 0, 0, 1500, 0),
        "en5": _Stats(False, 0, 0, 1500, 0),  # down → skipped
    }
    return addrs, stats


@patch("backend.app.services.network_utils.sys")
def test_macos_routes_to_psutil(mock_sys):
    """On darwin, get_network_interfaces() must use psutil, not the ioctl path."""
    mock_sys.platform = "darwin"
    with patch.object(network_utils, "_get_network_interfaces_psutil", return_value=[{"name": "en0"}]) as psutil_path:
        result = network_utils.get_network_interfaces()
    psutil_path.assert_called_once()
    assert result == [{"name": "en0"}]


@patch("backend.app.services.network_utils.sys")
def test_windows_routes_to_psutil(mock_sys):
    mock_sys.platform = "win32"
    with patch.object(network_utils, "_get_network_interfaces_psutil", return_value=[]) as psutil_path:
        network_utils.get_network_interfaces()
    psutil_path.assert_called_once()


@patch("backend.app.services.network_utils.sys")
def test_linux_does_not_use_psutil(mock_sys):
    """Linux keeps the ioctl path — psutil helper must not be invoked."""
    mock_sys.platform = "linux"
    with patch.object(network_utils, "_get_network_interfaces_psutil") as psutil_path:
        # The ioctl path runs for real here; we only assert it wasn't short-circuited
        # to psutil. Its actual return depends on the host, so we don't assert on it.
        network_utils.get_network_interfaces()
    psutil_path.assert_not_called()


def test_psutil_path_filters_and_returns_bindable_ips():
    """The psutil path drops loopback/link-local/down ifaces, keeps real + VPN ones."""
    addrs, stats = _fake_psutil()
    with (
        patch("psutil.net_if_addrs", return_value=addrs),
        patch("psutil.net_if_stats", return_value=stats),
    ):
        result = network_utils._get_network_interfaces_psutil()

    by_name = {i["name"]: i for i in result}
    assert "en0" in by_name  # normal LAN interface
    assert by_name["en0"]["ip"] == "192.168.1.50"
    assert by_name["en0"]["subnet"] == "192.168.1.0/24"
    assert "utun3" in by_name  # Tailscale/VPN — legitimately bindable
    assert "lo0" not in by_name  # loopback filtered
    assert "awdl0" not in by_name  # link-local (169.254) filtered
    assert "en5" not in by_name  # interface down, skipped

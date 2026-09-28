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


_IP_ADDR_JSON = """[
  {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
  {"ifname": "enp3s0", "addr_info": [{"family": "inet", "local": "192.168.96.9", "prefixlen": 22}]},
  {"ifname": "enp4s0", "addr_info": [
     {"family": "inet", "local": "10.0.0.5", "prefixlen": 24},
     {"family": "inet", "local": "10.0.0.6", "prefixlen": 24, "label": "enp4s0:vp1"}
  ]},
  {"ifname": "docker0", "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]}
]"""


def _fake_ip_addr():
    """Patch `ip -j addr show` with a fixed multi-homed Linux host."""
    result = namedtuple("CompletedProcess", ["returncode", "stdout", "stderr"])(0, _IP_ADDR_JSON, "")
    return patch.object(network_utils, "subprocess", **{"run.return_value": result})


class TestFindLocalIPv4Network:
    """#3092: an address carries no prefix, so it has to be read off the interface."""

    def test_reads_the_configured_prefix_not_a_guessed_24(self):
        with _fake_ip_addr(), patch.object(network_utils, "_IP_CMD", "/usr/sbin/ip"):
            assert str(network_utils.find_local_ipv4_network("192.168.96.9")) == "192.168.96.0/22"

    def test_an_alias_address_resolves_too(self):
        # The VP binds aliases; an alias is a perfectly good route source.
        with _fake_ip_addr(), patch.object(network_utils, "_IP_CMD", "/usr/sbin/ip"):
            assert str(network_utils.find_local_ipv4_network("10.0.0.6")) == "10.0.0.0/24"

    def test_an_excluded_interface_still_answers(self):
        """EXCLUDED_INTERFACE_PREFIXES keeps docker0 out of the VP dropdown.

        It must not also make the kernel's own choice of route source
        unanswerable — "unknown" would be a worse answer than the truth.
        """
        with _fake_ip_addr(), patch.object(network_utils, "_IP_CMD", "/usr/sbin/ip"):
            assert str(network_utils.find_local_ipv4_network("172.17.0.1")) == "172.17.0.0/16"
            assert not [i for i in network_utils.get_all_interface_ips() if i["name"] == "docker0"]

    def test_an_address_no_interface_holds_is_none(self):
        with _fake_ip_addr(), patch.object(network_utils, "_IP_CMD", "/usr/sbin/ip"):
            assert network_utils.find_local_ipv4_network("192.168.1.1") is None

    def test_a_hostname_is_none(self):
        assert network_utils.find_local_ipv4_network("printer.local") is None


def _fake_windows_psutil():
    """#3121's host: one vmxnet3 NIC carrying three IPv4 addresses.

    "Local Area Connection" is here on purpose — it starts with ``lo``, so it
    is what EXCLUDED_INTERFACE_PREFIXES would eat if the Linux name filter were
    applied to Windows adapter names.
    """
    addrs = {
        "Ethernet0": [
            _Addr(socket.AF_INET, "10.10.24.6", "255.255.255.0", None, None),
            _Addr(socket.AF_INET, "10.10.24.7", "255.255.255.0", None, None),
            _Addr(socket.AF_INET, "10.10.24.8", "255.255.255.0", None, None),
        ],
        "Local Area Connection": [_Addr(socket.AF_INET, "192.168.7.5", "255.255.255.0", None, None)],
    }
    stats = {
        "Ethernet0": _Stats(True, 0, 0, 1500, 0),
        "Local Area Connection": _Stats(True, 0, 0, 1500, 0),
    }
    return addrs, stats


def _patch_psutil(addrs, stats):
    """Both psutil calls the enumerator makes, as one context manager."""
    return patch.multiple(
        "psutil",
        net_if_addrs=lambda: addrs,
        net_if_stats=lambda: stats,
    )


class TestSecondaryAddresses:
    """#3121: a NIC with several IPv4 addresses is several VP bind targets.

    The Virtual Printer needs one bind IP per printer. Linux gets one dropdown
    entry per alias from `ip -j addr show`; Windows and macOS have no `ip`, so
    everything they offer comes out of psutil.
    """

    def test_every_ipv4_on_an_interface_is_listed(self):
        addrs, stats = _fake_windows_psutil()
        with _patch_psutil(addrs, stats):
            entries = network_utils._psutil_ipv4_entries()

        eth0 = [e for e in entries if e["name"] == "Ethernet0"]
        assert [e["ip"] for e in eth0] == ["10.10.24.6", "10.10.24.7", "10.10.24.8"]
        # Position is the only alias signal on this path: first = primary.
        assert [e["is_alias"] for e in eth0] == [False, True, True]
        assert {e["subnet"] for e in eth0} == {"10.10.24.0/24"}

    def test_get_network_interfaces_still_returns_one_per_interface(self):
        """Discovery subnets and the support bundle want interfaces, not aliases."""
        addrs, stats = _fake_windows_psutil()
        with _patch_psutil(addrs, stats):
            result = network_utils._get_network_interfaces_psutil()

        assert [i["ip"] for i in result if i["name"] == "Ethernet0"] == ["10.10.24.6"]
        # The narrower shape this function has always returned.
        assert set(result[0]) == {"name", "ip", "netmask", "subnet"}

    @patch("backend.app.services.network_utils.sys")
    def test_windows_dropdown_offers_each_secondary_ip(self, mock_sys):
        """The actual bug: only one entry per NIC reached the bind dropdown."""
        mock_sys.platform = "win32"
        addrs, stats = _fake_windows_psutil()
        with _patch_psutil(addrs, stats):
            entries = network_utils.get_all_interface_ips()

        assert [e["ip"] for e in entries if e["name"] == "Ethernet0"] == [
            "10.10.24.6",
            "10.10.24.7",
            "10.10.24.8",
        ]

    @patch("backend.app.services.network_utils.sys")
    def test_windows_keeps_adapters_matching_a_linux_prefix(self, mock_sys):
        """EXCLUDED_INTERFACE_PREFIXES must not run against Windows names."""
        mock_sys.platform = "win32"
        addrs, stats = _fake_windows_psutil()
        with _patch_psutil(addrs, stats):
            entries = network_utils.get_all_interface_ips()

        assert "Local Area Connection" in {e["name"] for e in entries}

    @patch("backend.app.services.network_utils.sys")
    def test_linux_without_iproute2_gets_aliases_and_keeps_its_exclusions(self, mock_sys):
        """psutil replaces the ioctl fallback, so no-`ip` hosts see aliases too.

        The name exclusions still apply here — unlike Windows, these really are
        the local device names, and docker0 has no business in the dropdown.
        """
        mock_sys.platform = "linux"
        addrs = {
            "eth0": [
                _Addr(socket.AF_INET, "192.168.1.100", "255.255.255.0", None, None),
                _Addr(socket.AF_INET, "192.168.1.101", "255.255.255.0", None, None),
            ],
            "docker0": [_Addr(socket.AF_INET, "172.17.0.1", "255.255.0.0", None, None)],
        }
        stats = {"eth0": _Stats(True, 0, 0, 1500, 0), "docker0": _Stats(True, 0, 0, 1500, 0)}

        with _patch_psutil(addrs, stats), patch.object(network_utils, "_IP_CMD", None):
            entries = network_utils.get_all_interface_ips()
            unfiltered = network_utils.get_all_interface_ips(include_excluded=True)

        assert [e["ip"] for e in entries] == ["192.168.1.100", "192.168.1.101"]
        assert "docker0" not in {e["name"] for e in entries}
        assert "docker0" in {e["name"] for e in unfiltered}

    @patch("backend.app.services.network_utils.sys")
    def test_ioctl_remains_the_last_resort(self, mock_sys):
        """A venv without psutil still enumerates, just without the aliases."""
        mock_sys.platform = "linux"
        with (
            patch.object(network_utils, "_IP_CMD", None),
            patch.object(network_utils, "_psutil_ipv4_entries", return_value=[]),
            patch.object(
                network_utils,
                "get_network_interfaces",
                return_value=[
                    {"name": "eth0", "ip": "192.168.1.100", "netmask": "255.255.255.0", "subnet": "192.168.1.0/24"}
                ],
            ),
        ):
            entries = network_utils.get_all_interface_ips()

        assert entries == [
            {
                "name": "eth0",
                "ip": "192.168.1.100",
                "netmask": "255.255.255.0",
                "subnet": "192.168.1.0/24",
                "is_alias": False,
                "label": "eth0",
            }
        ]

    @patch("backend.app.services.network_utils.sys")
    def test_adapter_order_is_preserved_for_source_ip_selection(self, mock_sys):
        """find_interface_for_ip() answers with the first match, so order matters.

        The MQTT bridge takes that answer as the source IP for the #1429
        rewrite and the SSDP proxy as its local interface. On a host with two
        adapters on one subnet, re-ordering the enumeration would silently
        re-pick both, so this path stays in psutil's adapter order rather than
        being sorted by name the way the iproute2 path is.
        """
        mock_sys.platform = "win32"
        addrs = {
            "Zeta": [_Addr(socket.AF_INET, "10.10.24.6", "255.255.255.0", None, None)],
            "Alpha": [_Addr(socket.AF_INET, "10.10.24.9", "255.255.255.0", None, None)],
        }
        stats = {"Zeta": _Stats(True, 0, 0, 1500, 0), "Alpha": _Stats(True, 0, 0, 1500, 0)}

        with _patch_psutil(addrs, stats):
            assert [e["name"] for e in network_utils.get_all_interface_ips()] == ["Zeta", "Alpha"]
            assert network_utils.find_interface_for_ip("10.10.24.200")["name"] == "Zeta"

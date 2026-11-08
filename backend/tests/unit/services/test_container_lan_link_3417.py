"""A macvlan or ipvlan container is on the LAN, not on bridge networking (#3417).

Its interface is created in the host's namespace and moved into the
container's, so like a veth its ``iflink`` names an interface elsewhere: the
diagnostic called it bridge networking and told the user to recreate the
container with host networking, though discovery and the Virtual Printer work.
"""

import json
import subprocess
from unittest.mock import patch

import pytest

from backend.app.services import discovery
from backend.app.services.printer_diagnostic import (
    _detect_container_network_mode,
    _lan_link_kinds,
)
from backend.tests.unit.services.test_container_runtime_3092 import _BRIDGE_NETNS, _HOST_NETNS, MOD, _netns


def _link(name, kind, mode=None):
    info = {"info_kind": kind}
    if mode:
        info["info_data"] = {"mode": mode}
    return {"ifname": name, "linkinfo": info}


_MACVLAN = [_link("lo", None), _link("eth0", "macvlan")]


class TestDetect:
    def test_macvlan_is_on_the_lan(self):
        with _netns(_BRIDGE_NETNS, _MACVLAN):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "macvlan"

    def test_ipvlan_l2_is_on_the_lan(self):
        with _netns(_BRIDGE_NETNS, [_link("eth0", "ipvlan", "l2")]):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "ipvlan"

    @pytest.mark.parametrize("mode", ["l3", "l3s"])
    def test_ipvlan_l3_routes(self, mode):
        with _netns(_BRIDGE_NETNS, [_link("eth0", "ipvlan", mode)]):
            assert _detect_container_network_mode(discovery.RUNTIME_PODMAN) == "ipvlan_l3"

    def test_a_bridge_and_a_macvlan_network_reach_the_lan_through_the_macvlan(self):
        interfaces = {**_BRIDGE_NETNS, "eth1": (3, 46, False)}
        with _netns(interfaces, [_link("eth0", "veth"), _link("eth1", "macvlan")]):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "macvlan"

    def test_an_ipvlan_mode_not_known_to_route_is_not_warned(self):
        """iproute2 prints "unknown" for a mode it has no name for."""
        with _netns(_BRIDGE_NETNS, [_link("eth0", "ipvlan", "unknown")]):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "ipvlan"

    def test_ipvlan_wins_over_its_l3_sibling(self):
        interfaces = {**_BRIDGE_NETNS, "eth1": (3, 46, False)}
        with _netns(interfaces, [_link("eth0", "ipvlan", "l3"), _link("eth1", "ipvlan", "l2")]):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "ipvlan"

    def test_a_veth_is_still_bridge(self):
        with _netns(_BRIDGE_NETNS, [_link("eth0", "veth")]):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "bridge"

    def test_host_networking_answers_first(self):
        """A host may carry a macvlan shim to reach its containers."""
        with _netns({**_HOST_NETNS, "shim": (3, 2, False)}, [_link("shim", "macvlan")]):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "host"

    def test_no_engine_is_still_unknown(self):
        with _netns(_BRIDGE_NETNS, _MACVLAN):
            assert _detect_container_network_mode(None) is None


class TestUnreadableLinks:
    """Without the link kinds a veth and a macvlan look alike: say so."""

    @pytest.mark.parametrize(
        "ip",
        [
            FileNotFoundError("ip"),
            subprocess.CompletedProcess([], 1, stdout="", stderr="no netlink"),
            subprocess.CompletedProcess([], 0, stdout="not json", stderr=""),
        ],
    )
    def test_the_mode_is_unknown_rather_than_bridge(self, ip):
        with _netns(_BRIDGE_NETNS):
            kwargs = {"side_effect": ip} if isinstance(ip, Exception) else {"return_value": ip}
            with patch(f"{MOD}.subprocess.run", **kwargs):
                assert _lan_link_kinds() is None
                assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) is None

    def test_host_networking_needs_no_link_kinds(self):
        with _netns(_HOST_NETNS), patch(f"{MOD}.subprocess.run", side_effect=FileNotFoundError("ip")):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "host"


class TestSupportPackage:
    def test_the_support_package_reads_the_mode_as_the_diagnostic_does(self):
        from backend.app.api.routes.support import _detect_docker_network_mode

        with (
            _netns(_BRIDGE_NETNS, _MACVLAN),
            patch("backend.app.services.discovery.detect_container_runtime", return_value=discovery.RUNTIME_DOCKER),
        ):
            assert _detect_docker_network_mode() == "macvlan"

    def test_podman_host_networking_is_no_longer_bridge_there(self):
        from backend.app.api.routes.support import _detect_docker_network_mode

        with (
            _netns(_HOST_NETNS),
            patch("backend.app.services.discovery.detect_container_runtime", return_value=discovery.RUNTIME_PODMAN),
        ):
            assert _detect_docker_network_mode() == "host"

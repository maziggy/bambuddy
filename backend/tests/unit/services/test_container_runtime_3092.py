"""Container detection for the connection diagnostic (#3092).

The reporter ran Bambuddy in a Podman container with host networking and was
told "Not running in Docker - not applicable", which reads as "you are on
bare metal" and sends people looking for the problem somewhere else. Two
separate questions are pinned here: which engine we are under, and whether
its network namespace is the host's.
"""

import builtins
import io
import os
from contextlib import contextmanager
from unittest.mock import patch

from backend.app.services import discovery
from backend.app.services.printer_diagnostic import (
    _detect_container_network_mode,
    _has_native_interface,
)

MOD = "backend.app.services.printer_diagnostic"


@contextmanager
def _host(files=None, env=None):
    """Present a fixed set of marker files and environment to the detector."""
    files = files or {}

    class _Path:
        def __init__(self, p):
            self._p = str(p)

        def __str__(self):
            return self._p

        def exists(self):
            return self._p in files

    def _read(path):
        return files.get(str(path), "")

    real_open = builtins.open

    def _open(path, *args, **kwargs):
        # is_running_in_docker() reads /proc/1/cgroup with a plain open() and
        # is deliberately left that way, so intercept only the paths under
        # test and let everything else through untouched.
        key = str(path)
        if key in files:
            return io.StringIO(files[key])
        if key in ("/proc/1/cgroup", "/run/systemd/container"):
            raise FileNotFoundError(key)
        return real_open(path, *args, **kwargs)

    with (
        patch.object(discovery, "_read_text", _read),
        patch.object(discovery, "Path", _Path),
        patch.object(builtins, "open", _open),
        patch.dict(os.environ, env or {}, clear=True),
    ):
        yield


class TestDetectContainerRuntime:
    def test_bare_metal_is_none(self):
        with _host():
            assert discovery.detect_container_runtime() is None

    def test_podman_by_its_own_marker_file(self):
        with _host({"/run/.containerenv": ""}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_PODMAN

    def test_podman_by_the_older_root_marker(self):
        with _host({"/.containerenv": ""}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_PODMAN

    def test_podman_by_cgroup(self):
        with _host({"/proc/1/cgroup": "0::/machine.slice/libpod-abc.scope\n"}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_PODMAN

    def test_docker_by_its_own_marker_file(self):
        with _host({"/.dockerenv": ""}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_DOCKER

    def test_systemd_names_the_engine_and_wins(self):
        """The only signal that tells the two apart without guessing.

        Podman leaves /.dockerenv alone, but a Docker-compatible shim may not,
        so the file that carries the engine's own name is consulted first.
        """
        with _host({"/run/systemd/container": "podman\n", "/.dockerenv": ""}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_PODMAN

    def test_kubernetes(self):
        with _host({"/proc/1/cgroup": "11:memory:/kubepods/besteffort/pod123\n"}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_KUBERNETES

    def test_lxc_is_named_not_mistaken_for_docker(self):
        with _host({"/run/systemd/container": "lxc\n"}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_LXC

    def test_an_unnamed_engine_still_counts_as_a_container(self):
        with _host({"/run/systemd/container": "some-new-engine\n"}):
            assert discovery.detect_container_runtime() == discovery.RUNTIME_OTHER

    def test_lxc_is_not_an_oci_runtime(self):
        """A system container is bridged onto the LAN like a small VM.

        There is no "recreate it with host networking" advice to give, so it
        must not fall into the branch that gives it.
        """
        assert discovery.RUNTIME_LXC not in discovery.OCI_RUNTIMES
        assert discovery.RUNTIME_PODMAN in discovery.OCI_RUNTIMES


class TestIsRunningInDockerIsUnchanged:
    """Naming Podman must not widen the flag three other callers key off.

    /api/discovery/info feeds it to the Add-Printer flow, where isDocker
    switches discovery from SSDP to subnet scanning. SSDP works for a
    host-networked Podman container, so answering True there would take a
    working feature away.
    """

    def test_podman_does_not_read_as_docker(self):
        with _host({"/run/.containerenv": "", "/proc/1/cgroup": "0::/machine.slice/libpod-abc.scope\n"}):
            assert discovery.is_running_in_docker() is False

    def test_docker_still_reads_as_docker(self):
        with _host({"/.dockerenv": ""}):
            assert discovery.is_running_in_docker() is True

    def test_containerd_still_reads_as_docker(self):
        with _host({"/proc/1/cgroup": "0::/system.slice/containerd.service\n"}):
            assert discovery.is_running_in_docker() is True


def _sysfs(interfaces):
    """Present a fixed /sys/class/net to _has_native_interface().

    ``interfaces`` maps name -> (ifindex, iflink, is_tun). A veth's iflink is
    its peer's index in another namespace, so the two never agree.
    """

    class _Path:
        def __init__(self, p):
            self._p = str(p)

        def __truediv__(self, other):
            return _Path(f"{self._p}/{other}")

        def _parts(self):
            name, _, leaf = self._p.removeprefix("/sys/class/net/").partition("/")
            return interfaces.get(name), leaf

        def exists(self):
            spec, leaf = self._parts()
            return bool(spec) and leaf == "tun_flags" and spec[2]

        def read_text(self):
            spec, leaf = self._parts()
            if not spec:
                raise FileNotFoundError(self._p)
            return f"{spec[0] if leaf == 'ifindex' else spec[1]}\n"

    return (
        patch(f"{MOD}.Path", _Path),
        # The kernel names each interface with the same index sysfs reports,
        # which is exactly what the cross-check below relies on.
        patch(f"{MOD}.socket.if_nameindex", return_value=[(spec[0], name) for name, spec in interfaces.items()]),
    )


@contextmanager
def _netns(interfaces):
    path_patch, names_patch = _sysfs(interfaces)
    with path_patch, names_patch:
        yield


# A NAT-networked container: one veth per attached network, nothing else.
_BRIDGE_NETNS = {"lo": (1, 1, False), "eth0": (2, 45, False)}
# Host networking on a plain Linux box: a real NIC, native to this namespace.
_HOST_NETNS = {"lo": (1, 1, False), "enp3s0": (2, 2, False)}


class TestHasNativeInterface:
    def test_a_natted_container_sees_only_veths(self):
        with _netns(_BRIDGE_NETNS):
            assert _has_native_interface() is False

    def test_a_shared_host_namespace_has_a_real_nic(self):
        with _netns(_HOST_NETNS):
            assert _has_native_interface() is True

    def test_a_bridge_counts(self):
        """A Proxmox/libvirt host may have nothing but vmbr0 with an address."""
        with _netns({"lo": (1, 1, False), "vmbr0": (2, 2, False)}):
            assert _has_native_interface() is True

    def test_a_bind_mounted_host_sys_is_not_this_namespace(self):
        """sysfs is namespace-tagged, but a bind mount of the host's /sys is not.

        A container given ``-v /sys:/sys`` sees the host's interfaces under
        names that can collide with its own, and reading their numbers would
        be reading another namespace's answer. The entry found in sysfs has
        to be the one the kernel just named.
        """
        interfaces = {"lo": (1, 1, False), "eth0": (2, 45, False)}
        path_patch, _ = _sysfs({"lo": (1, 1, False), "eth0": (7, 7, False)})
        with (
            path_patch,
            patch(f"{MOD}.socket.if_nameindex", return_value=[(i, n) for n, (i, _l, _t) in interfaces.items()]),
        ):
            assert _has_native_interface() is False

    def test_a_containers_own_vpn_does_not_count(self):
        """A container can run WireGuard or Tailscale; its tun is native here.

        That says nothing about whose namespace this is, and counting it would
        report host networking to a bridge-mode container.
        """
        with _netns({"lo": (1, 1, False), "eth0": (2, 45, False), "wg0": (3, 3, True)}):
            assert _has_native_interface() is False


class TestDetectContainerNetworkMode:
    def test_docker_host_mode_by_the_original_signal(self):
        """A Docker host always has a docker0, whatever else is going on."""
        with _netns({"lo": (1, 1, False), "eth0": (2, 45, False), "docker0": (3, 3, False)}):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "host"

    def test_docker_bridge_mode(self):
        with _netns(_BRIDGE_NETNS):
            assert _detect_container_network_mode(discovery.RUNTIME_DOCKER) == "bridge"

    def test_podman_host_mode_on_a_host_with_no_engine_bridges(self):
        """#3092 itself.

        A Podman host running no bridge containers creates no docker0, no
        podman0 and no veth, so the original signal finds nothing and the old
        code concluded bridge networking.
        """
        with _netns(_HOST_NETNS):
            assert _detect_container_network_mode(discovery.RUNTIME_PODMAN) == "host"

    def test_podman_bridge_mode(self):
        with _netns(_BRIDGE_NETNS):
            assert _detect_container_network_mode(discovery.RUNTIME_PODMAN) == "bridge"

    def test_podmans_own_bridge_is_a_host_signal_too(self):
        with _netns({"lo": (1, 1, False), "eth0": (2, 45, False), "podman0": (3, 3, False)}):
            assert _detect_container_network_mode(discovery.RUNTIME_PODMAN) == "host"

    def test_an_isolated_namespace_under_no_known_engine_is_unknown(self):
        """Never guess bridge for something we cannot name — say so instead."""
        with _netns(_BRIDGE_NETNS):
            assert _detect_container_network_mode(None) is None

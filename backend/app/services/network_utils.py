"""Network utility functions for interface detection."""

import ipaddress
import json
import logging
import shutil
import socket
import struct
import subprocess
import sys

logger = logging.getLogger(__name__)

# Interfaces to exclude from selection (Linux only — Windows adapter names
# don't follow these prefixes and there's no equivalent uniform Windows
# exclude list worth hard-coding; the psutil path filters on address class
# (loopback, link-local) and interface up-state instead).
EXCLUDED_INTERFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr")

# Resolve full path to `ip` command (may not be in PATH for service users)
_IP_CMD: str | None = shutil.which("ip") or shutil.which("ip", path="/usr/sbin:/sbin:/usr/bin:/bin")


def _is_excluded(name: str) -> bool:
    """Check if an interface name should be excluded."""
    return any(name.startswith(prefix) for prefix in EXCLUDED_INTERFACE_PREFIXES)


def _psutil_ipv4_entries(exclude_by_name: bool = False) -> list[dict]:
    """Every bindable IPv4 address psutil reports, one entry per address.

    The ioctl request numbers in the Linux path (SIOCGIFADDR 0x8915,
    SIOCGIFNETMASK 0x891B) and the sockaddr layout they return are
    Linux-specific. On macOS/BSD ``fcntl`` still imports, so those ioctls
    don't raise ImportError — they raise ``OSError`` per interface and the
    Linux path silently returns an empty list (no VP bind interfaces).
    Windows has no ``fcntl``/``ip`` at all. psutil is already a Bambuddy dep
    (``psutil>=6.0.0``) and gives cross-platform name + IPv4 + netmask in one
    call, so we use it for everything that isn't Linux.

    Secondary addresses are included. psutil returns every unicast address
    bound to an adapter, so a Windows host with three IPs on one NIC offers
    three bind targets rather than one (#3121) — the same thing iproute2 gives
    Linux. ``is_alias`` marks every address after an interface's first, which
    is the closest Windows equivalent of an iproute2 alias label.

    Filters: IPv4 only (matches the Linux path), skip loopback and
    link-local (169.254.0.0/16), skip interfaces psutil reports as down.

    Args:
        exclude_by_name: apply ``EXCLUDED_INTERFACE_PREFIXES``. Only ever true
            on Linux — those are Linux device names, and a Windows adapter
            named "Local Area Connection" would match the ``lo`` prefix. The
            address-class filters above cover the equivalent ground elsewhere,
            and users may legitimately want to bind a VP to a Hyper-V / WSL /
            Tailscale / utun adapter.
    """
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available, interface detection unavailable on this platform")
        return []

    entries = []
    try:
        addrs_by_iface = psutil.net_if_addrs()
        stats_by_iface = psutil.net_if_stats()
    except Exception as e:
        logger.error("psutil failed to enumerate interfaces: %s", e)
        return []

    for name, addrs in addrs_by_iface.items():
        if exclude_by_name and _is_excluded(name):
            continue

        stats = stats_by_iface.get(name)
        if stats is not None and not stats.isup:
            continue

        ipv4_count = 0
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            netmask = addr.netmask
            if not ip or not netmask:
                continue

            try:
                ip_obj = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            if ip_obj.is_loopback or ip_obj.is_link_local:
                continue

            try:
                network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
            except ValueError:
                continue

            entries.append(
                {
                    "name": name,
                    "ip": ip,
                    "netmask": netmask,
                    "subnet": str(network),
                    # No label to read on this path, so position is all we
                    # have: the first address an adapter reports is its
                    # primary, the rest are secondaries.
                    "is_alias": ipv4_count > 0,
                    "label": name,
                }
            )
            ipv4_count += 1

    return entries


def _get_network_interfaces_psutil() -> list[dict]:
    """The primary IPv4 of each interface, in ``get_network_interfaces`` shape.

    That function's callers want one subnet per interface — discovery scan
    targets, the support bundle — not one entry per alias, so the secondary
    addresses are dropped here rather than never collected.
    """
    return [
        {key: entry[key] for key in ("name", "ip", "netmask", "subnet")}
        for entry in _psutil_ipv4_entries()
        if not entry["is_alias"]
    ]


def _sort_interface_entries(entries: list[dict]) -> list[dict]:
    """Sort in place and return: primary IPs first per interface, then by name."""
    entries.sort(key=lambda e: (e["name"], e["is_alias"], e["ip"]))
    return entries


def get_network_interfaces(include_excluded: bool = False) -> list[dict]:
    """Get all network interfaces with their IPs and subnets.

    Args:
        include_excluded: keep the interfaces ``EXCLUDED_INTERFACE_PREFIXES``
            normally hides. That list exists to keep docker0 and friends out
            of the Virtual Printer's bind dropdown; a caller asking about an
            address the kernel has already chosen needs the real answer.

    Returns:
        List of dicts with name, ip, netmask, subnet, broadcast
    """
    # Only Linux has the SIOCGIFADDR/SIOCGIFNETMASK ioctls + sockaddr layout the
    # path below relies on. Windows lacks fcntl entirely; macOS/BSD have fcntl but
    # different ioctl numbers, so the ioctl path there fails per-interface and
    # returns an empty list (breaking the VP bind-interface dropdown on macOS).
    # Route everything non-Linux to the cross-platform psutil path.
    if not sys.platform.startswith("linux"):
        return _get_network_interfaces_psutil()

    interfaces = []

    try:
        import fcntl

        for iface in socket.if_nameindex():
            name = iface[1]

            # Skip excluded interfaces
            if not include_excluded and _is_excluded(name):
                continue

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

                # Get IP address
                ip_bytes = fcntl.ioctl(
                    s.fileno(),
                    0x8915,  # SIOCGIFADDR
                    struct.pack("256s", name[:15].encode()),
                )[20:24]
                ip = socket.inet_ntoa(ip_bytes)

                # Get netmask
                netmask_bytes = fcntl.ioctl(
                    s.fileno(),
                    0x891B,  # SIOCGIFNETMASK
                    struct.pack("256s", name[:15].encode()),
                )[20:24]
                netmask = socket.inet_ntoa(netmask_bytes)

                # Calculate subnet
                network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)

                interfaces.append(
                    {
                        "name": name,
                        "ip": ip,
                        "netmask": netmask,
                        "subnet": str(network),
                    }
                )

                s.close()
            except OSError:
                # Interface doesn't have an IP or other error
                pass
            except Exception as e:
                logger.debug("Error getting info for interface %s: %s", name, e)

    except ImportError:
        # fcntl not available (Windows)
        logger.warning("fcntl not available, interface detection limited")
    except Exception as e:
        logger.error("Error enumerating interfaces: %s", e)

    return interfaces


def get_all_interface_ips(include_excluded: bool = False) -> list[dict]:
    """Get all IPs (primary + aliases) for every interface, minus the excluded ones.

    Uses `ip -j addr show` to see secondary/alias IPs that ioctl misses.
    Falls back to :func:`_fallback_get_all_ips` wherever `ip` isn't there to
    ask — which is every non-Linux host.

    Args:
        include_excluded: see :func:`get_network_interfaces`.

    Returns:
        List of dicts with name, ip, netmask, subnet, is_alias, label
    """
    # Windows and macOS have no `ip`, so there is nothing to try first. Going
    # straight to psutil is what lets a Windows NIC carrying three IPs offer
    # three bind targets instead of one (#3121).
    if not sys.platform.startswith("linux") or not _IP_CMD:
        logger.debug("ip command unavailable on this platform, enumerating via psutil")
        return _fallback_get_all_ips(include_excluded)

    try:
        result = subprocess.run(
            [_IP_CMD, "-j", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning("ip addr show failed: %s", result.stderr)
            return _fallback_get_all_ips(include_excluded)

        interfaces_data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning("Failed to run ip -j addr show: %s", e)
        return _fallback_get_all_ips(include_excluded)

    entries = []
    for iface in interfaces_data:
        ifname = iface.get("ifname", "")
        if not include_excluded and _is_excluded(ifname):
            continue

        ipv4_count = 0
        for addr_info in iface.get("addr_info", []):
            if addr_info.get("family") != "inet":
                continue

            ip = addr_info.get("local", "")
            prefix = addr_info.get("prefixlen", 24)
            label = addr_info.get("label", ifname)

            try:
                network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
                netmask = str(network.netmask)
            except ValueError:
                continue

            # An alias has ":" in label (e.g. eth0:vp1) or is not the first IPv4
            is_alias = ":" in label or ipv4_count > 0

            entries.append(
                {
                    "name": ifname,
                    "ip": ip,
                    "netmask": netmask,
                    "subnet": str(network),
                    "is_alias": is_alias,
                    "label": label,
                }
            )
            ipv4_count += 1

    return _sort_interface_entries(entries)


def _fallback_get_all_ips(include_excluded: bool = False) -> list[dict]:
    """Enumerate without iproute2: psutil first, ioctl only if it finds nothing.

    psutil is the better answer because it reports secondary addresses, so a
    host with no `ip` command still gets one bind target per IP instead of per
    interface. The ioctl wrap below is what such a host used to get (minus the
    aliases it never saw) and is kept for the one case psutil can't serve: a
    hand-rolled venv missing the dependency. It only ever runs on Linux, since
    the ioctl path returns nothing anywhere else.
    """
    # EXCLUDED_INTERFACE_PREFIXES are Linux device names; see _psutil_ipv4_entries.
    exclude_by_name = sys.platform.startswith("linux") and not include_excluded
    entries = _psutil_ipv4_entries(exclude_by_name=exclude_by_name)
    if entries:
        # Deliberately not sorted. psutil's adapter order is what this path has
        # always returned, and find_interface_for_ip() answers with the first
        # entry whose subnet holds the target -- which the MQTT bridge uses as
        # the source IP for the #1429 rewrite and the SSDP proxy as its local
        # interface. Re-ordering it would quietly re-pick those on a host with
        # two adapters on one subnet. The iproute2 path sorts because it always
        # has; only Linux sees that order.
        return entries

    return [
        {
            **iface,
            "is_alias": False,
            "label": iface["name"],
        }
        for iface in get_network_interfaces(include_excluded)
    ]


def find_local_ipv4_network(local_ip: str) -> ipaddress.IPv4Network | None:
    """The IPv4 network configured on the local interface holding ``local_ip``.

    An IPv4 address carries no prefix length, so the only way to know how far
    a LAN reaches is to read the prefix off the interface that owns the
    address. ``None`` means no local interface claims it, which is the honest
    answer whenever the platform gives us no interface data at all.

    Nothing is filtered: ``local_ip`` is an address the kernel already picked
    as a route source, so answering "unknown" because it happens to sit on a
    bridge named ``br-something`` would be a worse answer than the truth.
    """
    try:
        address = ipaddress.IPv4Address(local_ip)
    except ValueError:
        return None

    for iface in get_all_interface_ips(include_excluded=True):
        if iface.get("ip") != str(address):
            continue
        try:
            return ipaddress.IPv4Network(iface["subnet"], strict=False)
        except (KeyError, TypeError, ValueError):
            logger.debug("Interface %s has an unusable subnet %r", iface.get("name"), iface.get("subnet"))
            return None

    return None


def find_interface_for_ip(target_ip: str) -> dict | None:
    """Find which interface is on the same subnet as the target IP.

    Args:
        target_ip: IP address to find the matching interface for

    Returns:
        Interface dict or None if not found
    """
    try:
        target = ipaddress.IPv4Address(target_ip)
    except ValueError:
        logger.error("Invalid target IP: %s", target_ip)
        return None

    interfaces = get_all_interface_ips()

    for iface in interfaces:
        if iface.get("is_alias"):
            continue
        try:
            network = ipaddress.IPv4Network(iface["subnet"], strict=False)
            if target in network:
                logger.debug("Found interface %s (%s) for target %s", iface["name"], iface["ip"], target_ip)
                return iface
        except ValueError:
            continue

    logger.warning("No interface found for target IP %s", target_ip)
    return None


def get_other_interfaces(exclude_ip: str) -> list[dict]:
    """Get all interfaces except the one with the given IP.

    Args:
        exclude_ip: IP address of interface to exclude

    Returns:
        List of interface dicts
    """
    interfaces = get_network_interfaces()
    return [iface for iface in interfaces if iface["ip"] != exclude_ip]

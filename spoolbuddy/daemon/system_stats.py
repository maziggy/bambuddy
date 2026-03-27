"""Collect OS-level system stats from the Raspberry Pi using stdlib only."""

import os
import platform


def _read_file(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _cpu_temp() -> float | None:
    raw = _read_file("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return None
    try:
        return round(int(raw) / 1000, 1)
    except (ValueError, TypeError):
        return None


def _memory_info() -> dict | None:
    raw = _read_file("/proc/meminfo")
    if raw is None:
        return None
    info: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            key = parts[0][:-1]
            try:
                info[key] = int(parts[1])  # kB
            except ValueError:
                continue
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    if total == 0:
        return None
    return {
        "total_mb": round(total / 1024),
        "available_mb": round(available / 1024),
        "used_mb": round((total - available) / 1024),
        "percent": round((total - available) / total * 100, 1),
    }


def _disk_info() -> dict | None:
    try:
        st = os.statvfs("/")
    except OSError:
        return None
    total = st.f_frsize * st.f_blocks
    free = st.f_frsize * st.f_bavail
    used = total - free
    if total == 0:
        return None
    return {
        "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1),
        "free_gb": round(free / (1024**3), 1),
        "percent": round(used / total * 100, 1),
    }


def _load_avg() -> list[float] | None:
    try:
        load = os.getloadavg()
        return [round(x, 2) for x in load]
    except OSError:
        return None


def _cpu_count() -> int | None:
    return os.cpu_count()


def _os_info() -> dict:
    uname = platform.uname()
    os_release = _read_file("/etc/os-release")
    pretty_name = None
    if os_release:
        for line in os_release.splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty_name = line.split("=", 1)[1].strip().strip('"')
                break
    return {
        "os": pretty_name or f"{uname.system} {uname.release}",
        "kernel": uname.release,
        "arch": uname.machine,
        "python": platform.python_version(),
    }


def _system_uptime() -> int | None:
    raw = _read_file("/proc/uptime")
    if raw is None:
        return None
    try:
        return int(float(raw.split()[0]))
    except (ValueError, IndexError):
        return None


def collect() -> dict:
    """Collect all system stats. Returns a flat dict safe for JSON serialization."""
    stats: dict = {}

    stats["os"] = _os_info()

    temp = _cpu_temp()
    if temp is not None:
        stats["cpu_temp_c"] = temp

    cpu_count = _cpu_count()
    if cpu_count is not None:
        stats["cpu_count"] = cpu_count

    load = _load_avg()
    if load is not None:
        stats["load_avg"] = load

    mem = _memory_info()
    if mem is not None:
        stats["memory"] = mem

    disk = _disk_info()
    if disk is not None:
        stats["disk"] = disk

    uptime = _system_uptime()
    if uptime is not None:
        stats["system_uptime_s"] = uptime

    return stats

"""requirements.lock covers requirements.txt.

Docker, the installers and CI install the pinned versions in requirements.lock;
requirements.txt only holds the lower bounds it is compiled from. A dependency
added or raised in requirements.txt without regenerating the lock would ship
without it. Checked here statically (no network), so it fails in a local test
run too, not only in CI.
"""

import re
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parents[3]


def _requirements() -> list[Requirement]:
    reqs = []
    for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            reqs.append(Requirement(line))
    return reqs


def _pins() -> dict[str, list[tuple[Version, Marker | None]]]:
    pins: dict[str, list[tuple[Version, Marker | None]]] = {}
    for line in (REPO / "requirements.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\[\]-]+)==([^\s;]+)\s*(?:;\s*(.+))?$", line)
        if m:
            name, version, marker = m.groups()
            name = name.split("[", 1)[0]
            pins.setdefault(canonicalize_name(name), []).append((Version(version), Marker(marker) if marker else None))
    return pins


def test_every_requirement_is_pinned_in_the_lock():
    pins = _pins()
    problems = []
    for req in _requirements():
        if req.marker is not None and not req.marker.evaluate():
            continue  # not for this platform/Python
        candidates = [
            v for v, marker in pins.get(canonicalize_name(req.name), []) if marker is None or marker.evaluate()
        ]
        if not candidates:
            problems.append(f"{req.name}: not in requirements.lock")
        elif not any(req.specifier.contains(v, prereleases=True) for v in candidates):
            problems.append(f"{req}: lock pins {', '.join(map(str, candidates))}")
    assert not problems, (
        "requirements.lock does not cover requirements.txt; regenerate it (see CONTRIBUTING.md):\n  "
        + "\n  ".join(problems)
    )


def test_the_lock_is_universal():
    """The check above only evaluates markers for the machine it runs on. A lock
    compiled for Linux alone passes it but has no tzdata and an unconditional
    uvloop, which breaks the Windows installer, a build only release tags run."""
    header = (REPO / "requirements.lock").read_text(encoding="utf-8").split("\n", 3)[:3]
    assert any("uv pip compile" in line and "--universal" in line for line in header), (
        "requirements.lock was not compiled with --universal; regenerate it (see CONTRIBUTING.md)"
    )

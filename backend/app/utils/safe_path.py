"""Containment-checked path joining.

Single source of truth for joining a user-controlled string under a trusted
parent directory. The two-vector arbitrary-file-write reported against
``backend/app/api/routes/projects.py::import_project_file`` traced to plain
``Path / user_string`` arithmetic with no resolve + containment check —
attacker passed an absolute path, ``Path("/lib") / "/etc"`` collapsed to
``Path("/etc")``, and the next ``write_bytes`` landed wherever the attacker
chose. This module is the answer.

Every site that joins a path component coming from a request body, a ZIP
``namelist()``, an ``UploadFile.filename``, or any other attacker-controlled
source MUST route through ``safe_join_under``. Sites that join trusted
constants (settings paths, hardcoded subdirs) are not in scope — those should
carry a ``# SEC-PATH-OK: <reason>`` marker so the CI backstop knows.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


class PathTraversalError(ValueError):
    """Raised when a join attempt would escape the trusted parent.

    Callers in API-route context catch this and translate to ``HTTPException``
    via ``safe_join_under`` (which already raises HTTPException directly when
    invoked with ``http=True``). Non-route callers can catch the
    ``PathTraversalError`` and decide their own response shape.
    """


def safe_join_under(parent: Path, *parts: str, http: bool = True) -> Path:
    """Join *parts* under *parent* and assert the result stays under it.

    Rejects:
    - empty / None / non-str parts;
    - parts containing NUL (``\\x00``);
    - parts starting with ``/`` or ``\\`` (absolute paths;
      ``Path("/lib") / "/etc"`` discards ``/lib``);
    - any sequence whose resolved form is not a descendant of *parent*'s
      resolved form (defeats ``..`` traversal even when the literal join
      doesn't look suspicious).

    Returns the resolved absolute path on success.

    When ``http=True`` (default; suitable for FastAPI routes), failures raise
    ``HTTPException(400, "Invalid path in upload")``. Set ``http=False`` to
    raise ``PathTraversalError`` instead — for non-route callers that need
    finer control over the response.
    """
    if not parts:
        _fail("safe_join_under called with no parts", http)

    for part in parts:
        if not isinstance(part, str):
            _fail(f"Path part has type {type(part).__name__}, expected str", http)
        if not part:
            _fail("Empty path part", http)
        if "\x00" in part:
            _fail("NUL byte in path part", http)
        # Reject literal absolute markers: pathlib collapses ``Path("/a") /
        # "/b"`` to ``Path("/b")`` so the catch-after-resolve below would also
        # fire, but rejecting up-front gives a clearer error and avoids
        # touching the filesystem.
        if part.startswith("/") or part.startswith("\\"):
            _fail("Absolute path part not allowed", http)

    parent_resolved = parent.resolve()
    candidate = parent
    for part in parts:
        candidate = candidate / part
    candidate_resolved = candidate.resolve()

    if not _is_relative_to(candidate_resolved, parent_resolved):
        _fail("Path escapes the parent directory", http)

    return candidate_resolved


def assert_under(parent: Path, candidate: Path, *, http: bool = True) -> Path:
    """Assert that an already-joined *candidate* path is under *parent*.

    Use when you have an existing ``Path`` (e.g. from another helper that
    builds the path itself) and need a containment check before writing or
    deleting. Equivalent to ``safe_join_under`` minus the per-part input
    validation.
    """
    parent_resolved = parent.resolve()
    candidate_resolved = candidate.resolve()
    if not _is_relative_to(candidate_resolved, parent_resolved):
        _fail("Path escapes the parent directory", http)
    return candidate_resolved


def _is_relative_to(child: Path, parent: Path) -> bool:
    # ``Path.is_relative_to`` exists in Python 3.9+. Bambuddy targets 3.11+
    # (per pyproject and the bug-report system info) so this is safe.
    try:
        return child.is_relative_to(parent)
    except AttributeError:  # pragma: no cover - defensive
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


def _fail(reason: str, http: bool) -> None:
    if http:
        raise HTTPException(status_code=400, detail="Invalid path in upload")
    raise PathTraversalError(reason)

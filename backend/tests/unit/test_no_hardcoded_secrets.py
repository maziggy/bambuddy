"""GHSA-gc24-px2r-5qmf backstop: no hardcoded fallback secrets in source.

The first half of GHSA-gc24-px2r-5qmf (CVSS 9.8) was a literal
``bambuddy-secret-key-change-in-production`` string used as the JWT
signing key when ``JWT_SECRET_KEY`` was unset. Production Docker images
shipped with that exact string — meaning anyone who pulled the image
could forge admin tokens for any Bambuddy instance running unmodified.

This test walks every source file in ``backend/app/`` at parse time and
flags string literals that look like credential fallbacks. It is
deliberately stricter than the actual exploit: any
``*-change-in-production`` / ``change-me`` / ``your-secret-here``
shaped string is a code smell at a security boundary, regardless of
whether the call site happens to enforce env-var presence today. The
goal is to keep that string class out of the codebase entirely so
future code paths cannot re-introduce the same vulnerability shape.

If you need one of these strings as a test input (e.g. asserting that
a forged token signed with the old leaked secret is *rejected*), use
the ``ALLOWED_TEST_INPUT_PATTERNS`` allowlist below — never the
production source.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Substring patterns that should never appear in production source as
# string literals. Case-insensitive substring match.
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "change-in-production",
    "change-me-in-production",
    "your-secret-here",
    "your-secret-key",
    "default-secret-key",
    "insecure-default",
    "placeholder-secret",
    "replace-this-secret",
    # The exact leaked value from GHSA-gc24 — keep as a regression marker
    # so any reintroduction is caught loudly with the CVE number attached.
    "bambuddy-secret-key-change-in-production",
)

# Production-source files where these patterns are TOLERATED because they
# document the historical leak (CHANGELOG / migration notes / security
# advisory references) rather than being used as a credential fallback.
# Add an entry with a `# reason: ...` comment, never silently.
ALLOWED_PRODUCTION_FILES: frozenset[Path] = frozenset()


def _python_files_under(root: Path) -> list[Path]:
    """Yield every .py file under ``root`` excluding caches and virtualenvs."""
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts and "venv" not in p.parts
    ]


def _string_literals_in(file_path: Path) -> list[tuple[int, str]]:
    """Return (lineno, value) for every string literal in ``file_path``.

    Uses ``ast`` to avoid false positives from comments / docstrings;
    docstrings are ``ast.Constant`` too but we explicitly include them
    because a docstring is not a safe place to put a credential either.
    Returns an empty list on syntax-error files rather than crashing —
    a parse failure means the file has a separate bug and we don't want
    this test to mask it.
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


@pytest.mark.unit
def test_no_hardcoded_secrets_in_production_source() -> None:
    """SEC-AUTH-3 (SECURITY.md): no credential-shaped fallback strings in backend/app/.

    Walks every Python source file under ``backend/app/``. Flags string
    literals matching any pattern in ``FORBIDDEN_PATTERNS``. Allowlisted
    files (e.g. tests asserting we reject the leaked GHSA-gc24 token)
    are exempt via ``ALLOWED_PRODUCTION_FILES``.

    Failure here means a code change has reintroduced the GHSA-gc24
    failure mode: a string literal that production code could fall
    back to as a credential, defeating the env-var-or-fail design of
    ``_get_jwt_secret()``.
    """
    repo_root = Path(__file__).resolve().parents[3]
    production_root = repo_root / "backend" / "app"
    assert production_root.is_dir(), f"Expected backend/app/ at {production_root}"

    findings: list[str] = []
    for src in _python_files_under(production_root):
        relative = src.relative_to(repo_root)
        if relative in ALLOWED_PRODUCTION_FILES:
            continue
        for lineno, literal in _string_literals_in(src):
            literal_lower = literal.lower()
            for pattern in FORBIDDEN_PATTERNS:
                if pattern in literal_lower:
                    findings.append(f"  {relative}:{lineno} contains forbidden pattern '{pattern}': {literal!r}")
                    break

    assert not findings, (
        "Hardcoded credential-shaped strings found in production source — "
        "this is the GHSA-gc24-px2r-5qmf shape (CVSS 9.8 hardcoded JWT secret). "
        "See SECURITY.md rule 3 'No hardcoded fallback secrets'.\n\n" + "\n".join(findings)
    )


@pytest.mark.unit
def test_jwt_secret_loader_has_no_hardcoded_fallback() -> None:
    """SEC-AUTH-3 (SECURITY.md): _get_jwt_secret never returns a literal string.

    The post-GHSA-gc24 design of ``_get_jwt_secret`` reads from env, then
    file, then generates a random value via ``secrets.token_urlsafe``.
    No code path returns a string literal. This test asserts that
    structural property by walking the function's AST and confirming
    every ``return`` statement returns either a Name (variable) or a
    Call (function result), never an ast.Constant string literal.

    If this test fails, ``_get_jwt_secret`` has been modified to return
    a hardcoded value somewhere — likely as a "convenience default" that
    will end up in a shipped Docker image, which is exactly how the
    original GHSA-gc24 advisory happened.
    """
    repo_root = Path(__file__).resolve().parents[3]
    auth_module = repo_root / "backend" / "app" / "core" / "auth.py"
    tree = ast.parse(auth_module.read_text(encoding="utf-8"))

    loader: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_get_jwt_secret":
            loader = node
            break

    assert loader is not None, "_get_jwt_secret() not found in backend/app/core/auth.py — has it been renamed?"

    literal_returns: list[tuple[int, str]] = []
    for node in ast.walk(loader):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            literal_returns.append((node.lineno, node.value.value))

    assert not literal_returns, (
        "_get_jwt_secret() has a string-literal return — this is the GHSA-gc24 vulnerability shape. "
        "Use os.environ + file storage + secrets.token_urlsafe; never return a hardcoded string.\n"
        + "\n".join(f"  auth.py:{ln}: returns {val!r}" for ln, val in literal_returns)
    )

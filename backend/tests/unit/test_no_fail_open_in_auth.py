"""GHSA-6mf4-q26m-47pv backstop: no fail-open ``except Exception`` in auth code.

The advisory's root cause was a single ``except Exception:`` block in the
auth probe path that returned ``False`` (treated as "auth disabled, allow
everything") when the DB raised an error during the check. CVSS 9.8.
Other Python ecosystems would call this CWE-636 ("Not Failing
Securely").

The fundamental problem is that ``except Exception:`` is too broad to
audit at review time — the reviewer cannot tell, just from the except
clause, whether the handler re-raises, denies, or silently returns a
permissive value. So every such block in auth-sensitive code must be
explicitly tagged with the reviewer's audit conclusion using the
``# SEC-AUTH-EXC: <reason>`` marker. Untagged blocks fail this
test.

The tag forces three things every time an ``except Exception:`` lands
in scope:
1. A reviewer has read the handler body and confirmed fail-closed semantics.
2. The reasoning is captured at the exact line so future readers can verify.
3. ``grep SEC-AUTH-EXC`` enumerates every audited exception path for spot-checks.

Scope (where this rule applies, mirrors SECURITY.md rule 2):
- backend/app/core/auth.py
- backend/app/core/permissions.py
- backend/app/api/routes/auth.py

To add a new ``except Exception:`` block in scope, append a comment
``# SEC-AUTH-EXC: <short reason>`` on the same line as the
``except`` keyword. The reason should describe what makes the handler
safe (e.g. "rollback + raise 500", "returns None which caller treats
as invalid → 401", "logged only, no access decision made here").
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Files in scope for the lint. Adding a file here widens the safety net;
# removing one weakens it. Either decision belongs in a PR description.
IN_SCOPE: tuple[Path, ...] = (
    REPO_ROOT / "backend" / "app" / "core" / "auth.py",
    REPO_ROOT / "backend" / "app" / "core" / "permissions.py",
    REPO_ROOT / "backend" / "app" / "api" / "routes" / "auth.py",
)

TAG_MARKER = "# SEC-AUTH-EXC:"


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    """Return True if ``handler`` catches Exception or bare ``except:``.

    Excludes narrower catches like ``except (OperationalError, ProgrammingError):``
    or ``except JWTError:`` which are explicit about what they handle and
    not the GHSA-6mf4 shape.
    """
    if handler.type is None:
        return True  # bare `except:`
    # Single Name catching Exception
    if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
        return True
    # Tuple catching Exception alongside other types — e.g. `except (Exception, OSError):`
    if isinstance(handler.type, ast.Tuple):
        return any(isinstance(elt, ast.Name) and elt.id == "Exception" for elt in handler.type.elts)
    return False


@pytest.mark.unit
def test_no_fail_open_in_auth_modules() -> None:
    """SEC-AUTH-2 (SECURITY.md): every broad except in auth modules must carry SEC-AUTH-EXC tag.

    Walks the AST of each in-scope module, finds every ``except Exception:``
    (or bare ``except:``) block, and asserts the source line containing
    the ``except`` keyword has a ``# SEC-AUTH-EXC: <reason>`` tag.

    The tag is the reviewer's signed-off audit conclusion. Without it,
    the broad except is indistinguishable from the GHSA-6mf4 shape.
    """
    findings: list[str] = []
    for source_path in IN_SCOPE:
        assert source_path.is_file(), f"Expected in-scope file at {source_path}"
        source = source_path.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        tree = ast.parse(source)
        relative = source_path.relative_to(REPO_ROOT)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not _is_broad_except(node):
                continue

            # The ``except`` keyword is on node.lineno. Comment must appear
            # on that line (1-indexed in ast, 0-indexed in our list).
            line_text = source_lines[node.lineno - 1]
            if TAG_MARKER not in line_text:
                # Show enough context for the operator to find the block.
                handler_preview = line_text.strip()
                findings.append(
                    f"  {relative}:{node.lineno}  {handler_preview}\n"
                    f"      → add `{TAG_MARKER} <reason>` describing why this is fail-closed"
                )

    assert not findings, (
        "Untagged ``except Exception:`` (or bare ``except:``) blocks found in auth modules. "
        "Each one is indistinguishable at review time from the GHSA-6mf4-q26m-47pv shape (CVSS 9.8). "
        "Either narrow the catch to the specific exception type you handle, or tag the line with "
        "`# SEC-AUTH-EXC: <reason>` documenting what makes the handler fail-closed. "
        "See SECURITY.md rule 2 'Fail-closed in auth code'.\n\n" + "\n".join(findings)
    )

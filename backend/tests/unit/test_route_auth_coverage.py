"""GHSA-gc24 + GHSA-r2qv backstop: every route has an explicit auth dep.

The "second half" of GHSA-gc24-px2r-5qmf was that 77 endpoints out of 117
responded to anonymous requests with full payloads. The fix at the time
was retroactive — auth deps were added route by route. This test makes
the requirement structural: every FastAPI route at the app-level (HTTP
and WebSocket) is walked, and each one either has an auth dependency or
is in the ``PUBLIC_ROUTES`` allowlist with a justification comment.

Adding an unauthenticated route now requires touching the allowlist.
The diff makes the intent visible in code review and the entry-with-
reason format documents *why* this is safe (login itself, status
heartbeat, etc.). Drift catches the same failure mode that surfaced
the original advisory.

The audit also covers WebSocket routes — the proactive sweep that
surfaced finding C1 (`/api/v1/ws` was fully unauthenticated) showed
that an APIRoute-only walk has a blind spot for the very route shape
that produced the most severe disclosure.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute

from backend.app.main import app

# Substring patterns identifying auth-bearing callable qualnames in the
# resolved Depends() tree. Inner functions returned by factories carry
# the outer factory's name in their qualname (e.g.
# ``require_permission.<locals>.permission_checker``), so a substring
# check is enough; we don't have to enumerate the inner names.
_AUTH_QUALNAME_PATTERNS: tuple[str, ...] = (
    "require_",  # require_permission, require_permission_if_auth_enabled, require_role, require_admin_*, require_auth_*, require_any_*, require_ownership_*, require_camera_stream_token_*, require_energy_cost_update
    "cloud_caller",  # cloud.py route-level dep
    "_cloud_api_key_gate",  # cloud.py router-level dep
    "resolve_api_key_cloud_owner",  # used by slicer routes that need the API key's owner
    "get_current_user",  # JWT identity resolution
    "get_current_active_user",  # JWT identity resolution
    "get_api_key",  # webhook routes use this directly
    "verify_websocket_token",  # WebSocket route inline check (GHSA-r2qv I-WS)
)

# Routes that are intentionally accessible without an auth dependency.
# Each entry MUST be (method, path) tuple — the path is matched against
# ``route.path`` literally. To add an entry: include a justification on
# the line above explaining why anonymous access is safe.
_PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # ---- HTTP API: auth bootstrap (pre-credential or token-self-validated) ----
        # First-run setup — runs before any user exists. Idempotent once setup_completed is true.
        ("POST", "/api/v1/auth/setup"),
        # Login itself — credentials in the request body ARE the auth.
        ("POST", "/api/v1/auth/login"),
        # Logout — clears server-side JTI revocation; degraded behaviour on bad token is acceptable.
        ("POST", "/api/v1/auth/logout"),
        # Status heartbeat — used by the login UI to decide whether to show login form.
        ("GET", "/api/v1/auth/status"),
        # Advanced-auth status (whether 2FA / OIDC / LDAP are configured) — read by login form.
        ("GET", "/api/v1/auth/advanced-auth/status"),
        # LDAP status (whether LDAP login is configured) — read by login form.
        ("GET", "/api/v1/auth/ldap/status"),
        # OIDC discovery — login form needs the list of providers + their icons before user picks one.
        ("GET", "/api/v1/auth/oidc/providers"),
        ("GET", "/api/v1/auth/oidc/providers/{provider_id}/icon"),
        # OIDC authorize / callback / exchange — protocol-level handshakes that validate state nonces inline.
        ("GET", "/api/v1/auth/oidc/authorize/{provider_id}"),
        ("GET", "/api/v1/auth/oidc/callback"),
        ("POST", "/api/v1/auth/oidc/exchange"),
        # 2FA send + verify — issued after password check; pre-auth token in cookie is the auth.
        ("POST", "/api/v1/auth/2fa/email/send"),
        ("POST", "/api/v1/auth/2fa/verify"),
        # Forgot-password (anonymous request) + confirm (signed token in the URL).
        ("POST", "/api/v1/auth/forgot-password"),
        ("POST", "/api/v1/auth/forgot-password/confirm"),
        # ---- HTTP API: signed-URL routes (token in path is the auth) ----
        # Signed download URLs — token in path validated by the handler.
        ("GET", "/api/v1/archives/{archive_id}/dl/{token}/{filename}"),
        ("GET", "/api/v1/archives/{archive_id}/source-dl/{token}/{filename}"),
        ("GET", "/api/v1/library/files/{file_id}/dl/{token}/{filename}"),
        # Obico cached frame — one-time nonce embedded in <img> tags.
        ("GET", "/api/v1/obico/cached-frame/{nonce}"),
        # MakerWorld thumbnail proxy — fetches external URL; no Bambuddy data exposed.
        ("GET", "/api/v1/makerworld/thumbnail"),
        # ---- HTTP API: operational + UI-bootstrap (no sensitive data) ----
        # Operational liveness probe — minimal payload, used by container orchestrators.
        ("GET", "/health"),
        # Prometheus metrics — gated by its own bearer token check (constant-time post-I2).
        ("GET", "/api/v1/metrics"),
        # UI bootstrap — defaults for sidebar order and ui-preferences are public defaults that ship with the app.
        ("GET", "/api/v1/settings/default-sidebar-order"),
        ("GET", "/api/v1/settings/ui-preferences"),
        # Appliance locale defaults — read by the i18n bootstrap BEFORE auth might be set up.
        # Contents are user-set hostname/timezone/locale from the firstboot wizard (no secrets);
        # the file is absent on non-appliance installs, in which case every field is null.
        ("GET", "/api/v1/system/appliance"),
        # Slicer printer-models — static catalog, no user data.
        ("GET", "/api/v1/slicer/printer-models"),
        # Current Bambuddy version — public info (already visible in HTTP response headers + Docker tags).
        ("GET", "/api/v1/updates/version"),
        # Webhook routes — auth lives inside the handler via get_api_key() + check_permission(), not as a Depends.
        # Once they all migrate to standard auth deps these entries come out; for now exempting the file.
        ("GET", "/api/v1/webhook/printer/{printer_id}/status"),
        ("GET", "/api/v1/webhook/queue"),
        ("POST", "/api/v1/webhook/printer/{printer_id}/cancel"),
        ("POST", "/api/v1/webhook/printer/{printer_id}/start"),
        ("POST", "/api/v1/webhook/printer/{printer_id}/stop"),
        ("POST", "/api/v1/webhook/queue/add"),
        # ---- Static / SPA routes (not user data) ----
        ("GET", "/"),
        ("GET", "/manifest.json"),
        ("GET", "/sw-register.js"),
        ("GET", "/sw.js"),
        ("GET", "/gcode-viewer/"),
        ("GET", "/gcode-viewer/{file_path:path}"),
        # SPA catch-all — serves index.html for client-side routing. No backend data path.
        ("GET", "/{full_path:path}"),
        # ---- WebSocket routes ----
        # /ws performs an inline ``verify_websocket_token`` check before
        # ``accept()`` (GHSA-r2qv WS fix). The qualname matches one of the
        # auth-bearing patterns above, so this entry is informational — the
        # walker recognises the inline check as auth.
    }
)


def _walk_dependant_qualnames(dependant) -> list[str]:
    """Flatten the dependant tree to a list of callable qualnames."""
    names: list[str] = []
    if dependant is None:
        return names
    if dependant.call:
        names.append(getattr(dependant.call, "__qualname__", "?"))
    for sub in dependant.dependencies:
        names.extend(_walk_dependant_qualnames(sub))
    return names


def _has_auth_dep(dependant) -> bool:
    """True if any callable in the dependant tree matches an auth pattern."""
    return any(any(p in qn for p in _AUTH_QUALNAME_PATTERNS) for qn in _walk_dependant_qualnames(dependant))


def _ws_endpoint_does_inline_token_check(route: APIWebSocketRoute) -> bool:
    """True if the websocket endpoint reads its source uses ``verify_websocket_token``.

    WebSocket routes don't pass auth via the standard Depends machinery
    (the WebSocket handshake doesn't carry headers), so the auth check
    lives inline in the endpoint body. We confirm by inspecting the
    endpoint function's source text — looking for an actual call to
    ``verify_websocket_token``. A docstring-only mention would NOT
    satisfy this check (we look for a call-shaped pattern, not a
    substring).
    """
    import inspect

    try:
        source = inspect.getsource(route.endpoint)
    except (OSError, TypeError):
        return False
    return bool(re.search(r"\bverify_websocket_token\s*\(", source))


@pytest.mark.unit
def test_routes_have_explicit_auth_deps() -> None:
    """SEC-AUTH-1 (SECURITY.md): every API route has an auth dep or is in the public allowlist.

    Walks both ``APIRoute`` (HTTP) and ``APIWebSocketRoute`` (WS)
    objects on the live FastAPI app. For each, asserts that at least
    one of the resolved Depends in the dependant tree matches an auth-
    bearing qualname, OR that the (method, path) pair is in the
    explicit public-route allowlist, OR (for WebSocket routes) that
    the endpoint performs an inline ``verify_websocket_token`` check.

    Failure means a new route is reachable anonymously without being
    documented as such — the GHSA-gc24 / GHSA-r2qv shape.
    """
    failures: list[str] = []

    for route in app.routes:
        if isinstance(route, APIRoute):
            method = sorted(route.methods)[0] if route.methods else "GET"
            if _has_auth_dep(route.dependant):
                continue
            if (method, route.path) in _PUBLIC_ROUTES:
                continue
            failures.append(f"  {method:7} {route.path}  → no auth dep, not in _PUBLIC_ROUTES allowlist")
        elif isinstance(route, APIWebSocketRoute):
            if _has_auth_dep(route.dependant):
                continue
            if _ws_endpoint_does_inline_token_check(route):
                continue
            if ("WS", route.path) in _PUBLIC_ROUTES:
                continue
            failures.append(f"  WS      {route.path}  → no auth dep, no inline token check, not in _PUBLIC_ROUTES")

    assert not failures, (
        "Routes without an auth dependency that aren't in the public allowlist. "
        "Either add a ``Depends(require_*)`` to the route OR add the (method, path) "
        "to ``_PUBLIC_ROUTES`` with a comment justifying why anonymous access is safe. "
        "See SECURITY.md rule 1 'Allowlist over denylist' (route allowlist sub-section).\n\n" + "\n".join(failures)
    )


@pytest.mark.unit
def test_public_routes_allowlist_matches_real_routes() -> None:
    """Drift-detection: every (method, path) in ``_PUBLIC_ROUTES`` must exist on the app.

    If a route is renamed or removed, the entry for it in the allowlist
    becomes dead — a residual rubber-stamp that does nothing but leaves
    the impression that the route still has anonymous access. This test
    flags those.
    """
    real_routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            method = sorted(route.methods)[0] if route.methods else "GET"
            real_routes.add((method, route.path))
        elif isinstance(route, APIWebSocketRoute):
            real_routes.add(("WS", route.path))

    stale = sorted(_PUBLIC_ROUTES - real_routes)
    assert not stale, (
        "_PUBLIC_ROUTES contains entries that no longer match any real route. "
        "Remove these stale entries (the route was renamed, removed, or its method changed).\n\n"
        + "\n".join(f"  {m:7} {p}" for m, p in stale)
    )

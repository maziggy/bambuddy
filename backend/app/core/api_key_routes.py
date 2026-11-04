"""Fail-closed route selection for the opt-in API-key principal mode.

Auth-disabled deployments also expose public setup and sign-in operations.
Passing a valid key must not turn those anonymous routes into an alternative
way around the key's scopes. A keyed request may reach only a route with an
existing authorization dependency or a small set of safe bootstrap responses.
"""

from fastapi.routing import APIRoute
from starlette.routing import Match

_SAFE_PUBLIC_KEY_ROUTES = frozenset(
    {
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/auth/status"),
        ("GET", "/api/v1/auth/advanced-auth/status"),
        ("GET", "/api/v1/auth/ldap/status"),
        ("GET", "/api/v1/auth/oidc/providers"),
        ("GET", "/api/v1/settings/default-sidebar-order"),
        ("GET", "/api/v1/settings/ui-preferences"),
        ("GET", "/api/v1/slicer/printer-models"),
        ("GET", "/api/v1/system/appliance"),
        ("GET", "/api/v1/updates/version"),
        # These authenticate without a generic permission dependency. Their
        # route bodies apply their own authorization or return non-secret UI
        # metadata. Keep this list explicit so a new auth-only route cannot
        # accidentally grant an API key authority it did not have before.
        ("GET", "/api/v1/announcements"),
        ("POST", "/api/v1/announcements/{public_id}/read"),
        ("GET", "/api/v1/finance/cost-centers/mine"),
        ("GET", "/api/v1/finance/cost-centers"),
        ("GET", "/api/v1/finance/cost-centers/{cost_center_id}"),
        ("GET", "/api/v1/inventory/colors/map"),
        ("GET", "/api/v1/inventory/colors/by-material"),
        ("GET", "/api/v1/settings/ui-flags"),
        ("POST", "/api/v1/makerworld/resolve"),
        ("POST", "/api/v1/makerworld/import"),
    }
)

# These are authorization dependencies, not optional identity resolvers.
# Match only the core auth module so a route named get_api_key cannot itself be
# mistaken for the authentication dependency of the same name.
_KEY_AUTH_DEPENDENCIES = frozenset(
    {
        "get_api_key",
        "get_current_active_user",
        "get_current_user",
        "require_admin_if_auth_enabled",
        "require_any_permission_if_auth_enabled",
        "require_api_key_scope",
        "require_camera_stream_token_if_auth_enabled",
        "require_camwall_token_if_auth_enabled",
        "require_media_token_ownership",
        "require_media_token_permission",
        "require_media_token_printer_permission",
        "require_overlay_token_any_printer_if_auth_enabled",
        "require_overlay_token_if_auth_enabled",
        "require_ownership_permission",
        "require_permission_if_auth_enabled",
        "require_printer_permission_if_auth_enabled",
    }
)


def _has_authorization_dependency(dependant) -> bool:
    call = dependant.call
    if call is not None and getattr(call, "__module__", "") == "backend.app.core.auth":
        outer_name = getattr(call, "__qualname__", "").split(".", 1)[0]
        if outer_name in _KEY_AUTH_DEPENDENCIES:
            return True
    return any(_has_authorization_dependency(child) for child in dependant.dependencies)


def api_key_route_allowed(routes, scope) -> bool:
    """Allow only explicitly protected routes and selected public reads.

    Use the same ASGI scope and route ordering as the application's router,
    so method and path parameters are resolved the same way as dispatch.
    Unknown or unclassified routes fail closed for key-bearing requests.
    """
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        match, _ = route.matches(scope)
        if match is Match.FULL:
            if (scope["method"], route.path) in _SAFE_PUBLIC_KEY_ROUTES:
                return True
            return any(_has_authorization_dependency(dep) for dep in route.dependant.dependencies)
    return False

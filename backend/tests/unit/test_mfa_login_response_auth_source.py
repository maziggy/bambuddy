"""A 2FA or SSO login reports the user's real auth source (PC-C9).

mfa.py kept its own copy of the user-response builder, and the copy left out
``auth_source``: after a 2FA or OIDC login the schema default "local" reached
the page, which then offered an LDAP or OIDC user "Change Password".
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.app.api.routes import mfa


@pytest.mark.parametrize("source", ["ldap", "oidc", "local"])
def test_the_login_response_carries_the_auth_source(source):
    user = SimpleNamespace(
        id=3,
        username="ana",
        email=None,
        role="user",
        is_active=True,
        is_admin=False,
        auth_source=source,
        groups=[],
        get_permissions=lambda: set(),
        created_at=datetime(2026, 10, 1),
    )

    assert mfa._user_to_response(user).auth_source == source

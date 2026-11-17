"""Who a maintenance reset is logged as (#3557).

The user, an API key's owner, or, for a legacy key without an owner, the key
by its name. Nobody when authentication is off.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.app.api.routes.maintenance import _actor_identity
from backend.app.core.auth import ApiKeyActor


def _key_actor(name: str, owner: SimpleNamespace | None) -> ApiKeyActor:
    actor = ApiKeyActor.__new__(ApiKeyActor)
    actor.api_key = SimpleNamespace(name=name)
    actor.owner = owner
    actor.id = owner.id if owner else None
    actor.username = owner.username if owner else None
    return actor


def test_auth_off_logs_nobody():
    assert _actor_identity(None) == (None, None)


def test_a_user_is_logged_by_id_and_name():
    assert _actor_identity(SimpleNamespace(id=7, username="mechanic")) == (7, "mechanic")


def test_a_key_is_logged_as_its_owner():
    owner = SimpleNamespace(id=3, username="farm-admin")

    assert _actor_identity(_key_actor("Home Assistant", owner)) == (3, "farm-admin")


def test_a_legacy_key_without_an_owner_is_logged_by_its_name():
    assert _actor_identity(_key_actor("Home Assistant", None)) == (None, "API key: Home Assistant")


def test_a_long_key_name_fits_the_column():
    _, name = _actor_identity(_key_actor("x" * 200, None))

    assert name is not None and len(name) == 100

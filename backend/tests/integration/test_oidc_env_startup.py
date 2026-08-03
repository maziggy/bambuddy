"""The env provider is applied on startup, not merely appliable (#2593).

test_oidc_env_apply.py calls apply_env_oidc_provider() directly, so it stays
green even if nothing ever calls it -- deleting the lifespan call would leave
the feature dead with a fully passing suite. These tests pin the call site.

They read the lifespan's source rather than running it: the function is ~460
lines and starts printer connections, MQTT and schedulers, so executing it
here would test everything except the one line in question. That makes this a
wiring check, not a behavioural one -- it proves the call exists and runs
after migrations, and deliberately proves nothing about what it does. The
behaviour is covered by test_oidc_env_apply.py.
"""

from __future__ import annotations

import inspect

from backend.app.main import lifespan


def _lifespan_source() -> str:
    return inspect.getsource(lifespan)


def test_lifespan_applies_the_env_oidc_provider():
    assert "apply_env_oidc_provider(" in _lifespan_source()


def test_it_runs_after_the_migrations():
    """is_env_managed does not exist until run_migrations has added it, so an
    upsert before init_db() would fail on every existing installation."""
    source = _lifespan_source()
    assert source.index("await init_db()") < source.index("apply_env_oidc_provider(")


def test_the_apply_call_is_awaited():
    """apply_env_oidc_provider is a coroutine; calling it without await would
    return an un-awaited coroutine and silently apply nothing."""
    source = _lifespan_source()
    call = source.index("apply_env_oidc_provider(")
    line_start = source.rindex("\n", 0, call) + 1
    assert source[line_start:call].strip().endswith("await")

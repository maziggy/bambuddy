"""Bambuddy says so when it finds itself on a loop it is not shipped on (#3001).

Every unit file in this repo pins ``--loop asyncio``, added for #1896 because
uvloop's SSL layer can truncate a Virtual Printer FTP upload. Two populations
run units we do not write and so do not have the flag: the Proxmox VE
Helper-Scripts LXC, which composes its own ``ExecStart``, and native installs
created before 2026-07-05, since ``install/update.sh`` never rewrites the unit
file. #3001 -- every RTSP camera failing at once -- is how we found out those
installs exist. A truncated upload gives no such signal, so the loop now
announces itself instead of waiting for the next visible symptom.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from backend.app.core.asyncio_handlers import running_on_uvloop, warn_if_running_on_uvloop


class _FakeUvloopLoop:
    """Stands in for ``uvloop.Loop`` by module name, which is what is matched.

    Lets the detection be tested on hosts without uvloop, and keeps the two
    tests below honest about *how* they identify the loop: by asking the loop
    what it is, not by asking whether uvloop imports.
    """

    __module__ = "uvloop.loop"


async def test_the_default_loop_is_not_flagged(caplog):
    """The loop every shipped unit file selects must stay silent."""
    with caplog.at_level(logging.WARNING):
        assert running_on_uvloop() is False
        assert warn_if_running_on_uvloop() is False
    assert "uvloop" not in caplog.text


def test_uvloop_is_detected_and_warned_about(caplog):
    """The warning has to name the flag, or it is not actionable."""
    loop = _FakeUvloopLoop()

    assert running_on_uvloop(loop) is True  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING):
        assert warn_if_running_on_uvloop(loop) is True  # type: ignore[arg-type]

    assert "--loop asyncio" in caplog.text, "the warning must state the fix, not just the diagnosis"
    assert "#1896" in caplog.text, "the truncation risk is the reason this matters; cite it"
    assert caplog.records[-1].levelno == logging.WARNING


def test_uvloop_is_detected_on_a_real_uvloop_loop():
    """The stand-in above is only worth having if it matches the real thing.

    Not an async test on purpose: an async test inherits the session's selector
    loop, which is the loop this is trying not to be.
    """
    uvloop = pytest.importorskip("uvloop", reason="uvloop is a uvicorn[standard] extra; Linux only")

    async def scenario() -> tuple[bool, bool]:
        return running_on_uvloop(), warn_if_running_on_uvloop()

    detected, warned = uvloop.run(scenario())
    assert detected is True
    assert warned is True


def test_no_running_loop_is_not_uvloop():
    """Called outside a loop -- ``asyncio.get_running_loop`` raises -- this must
    answer False rather than propagate, since it runs during startup."""
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
    assert running_on_uvloop() is False
    assert warn_if_running_on_uvloop() is False

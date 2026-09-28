"""The RTSPS proxy must survive whichever event loop production actually runs (#3001).

1.2.5.4 shipped ``server._bambuddy_proxy_handlers = handlers`` at the end of
``create_tls_proxy``. That is legal on ``asyncio.base_events.Server``, which
carries a ``__dict__``, and an outright ``AttributeError`` on
``uvloop.loop.Server``, a Cython cdef class that does not::

    AttributeError: 'uvloop.loop.Server' object has no attribute
    '_bambuddy_proxy_handlers' and no __dict__ for setting new attributes

Which loop you get is decided by the launch command, not by anything in the
app. Every unit file this repo ships pins ``--loop asyncio`` (added for #1896),
so none of them could hit this -- but ``requirements.txt`` pins
``uvicorn[standard]``, which installs uvloop on Linux, so any launcher without
that flag gets uvloop from ``--loop auto``. The Proxmox VE Helper-Scripts LXC
writes its own unit with no loop pinned, and native installs predating the
#1896 pin never gained it, because ``update.sh`` does not rewrite unit files.
So the loop this code runs on is not ours to assume, which is the whole reason
these tests exist. The proxy raised before opening a socket, which is why the
in-app diagnostic reported
``capture_exception`` at 0 ms while network reachability passed at 1 ms, and
why live view, snapshots and timelapse frames all went at once on every RTSP
model (X1, H2*, P2*). A1/P1 use the chamber-image protocol and return before
the proxy, so they were untouched.

The suite could not see any of it: ``conftest.event_loop`` builds its loop from
the default policy, so every async test in the repo runs on the selector loop
-- the one loop where that assignment works. Hence two tests here. The first
drives the real function on a real uvloop loop. The second states the
underlying contract without needing uvloop installed at all: the proxy must not
store anything *on* the server object, because the server it gets is not
guaranteed to accept attributes.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.services.camera import _proxy_handlers, close_tls_proxy, create_tls_proxy


def test_create_tls_proxy_works_on_a_uvloop_loop():
    """The regression itself, on the loop that production uses.

    Deliberately not an async test: the point is the loop implementation, and
    an async test would inherit the session's selector loop and prove nothing.
    ``uvloop.run`` owns its loop start to finish.
    """
    uvloop = pytest.importorskip("uvloop", reason="uvloop is a uvicorn[standard] extra; Linux only")

    async def scenario() -> int:
        # Port 322 is never dialled -- create_tls_proxy only binds the local
        # listener; the upstream connection is opened per client handler.
        port, server = await create_tls_proxy("127.0.0.1", 322)
        try:
            assert port > 0
            assert server in _proxy_handlers, (
                "handler set must be reachable from the registry, or close_tls_proxy has nothing to cancel"
            )
        finally:
            await close_tls_proxy(server)
        assert server not in _proxy_handlers, "close_tls_proxy must drop its registry entry"
        return port

    assert uvloop.run(scenario()) > 0


async def test_create_tls_proxy_stores_nothing_on_the_server(monkeypatch):
    """Runs everywhere, including where uvloop is not installed.

    A stand-in ``Server`` with ``__slots__`` reproduces uvloop's constraint --
    no ``__dict__``, so any attribute the proxy tries to attach raises. If this
    fails, the code has gone back to writing on the server object.
    """

    class SlottedServer:
        """Minimum of ``asyncio.Server`` that ``create_tls_proxy`` touches."""

        __slots__ = ("sockets", "__weakref__")

        def __init__(self, sockets):
            self.sockets = sockets

        def close(self):
            pass

        async def wait_closed(self):
            pass

    real_start_server = asyncio.start_server
    created: list[asyncio.Server] = []

    async def fake_start_server(*args, **kwargs):
        """Bind for real -- the port has to be usable -- then hide the Server."""
        real = await real_start_server(*args, **kwargs)
        created.append(real)
        return SlottedServer(real.sockets)

    monkeypatch.setattr(asyncio, "start_server", fake_start_server)
    try:
        port, server = await create_tls_proxy("127.0.0.1", 322)
        assert port > 0
        assert _proxy_handlers.get(server) == set(), "a fresh proxy has no handlers yet, but must have an entry"
        await close_tls_proxy(server)
        assert server not in _proxy_handlers, "close_tls_proxy must drop its registry entry"
    finally:
        for real in created:
            real.close()
            await real.wait_closed()

"""Letting go of a paho MQTT client without waiting for its network thread.

`Client.loop_stop()` is two statements: set `_thread_terminate`, then `join()`
the network thread with no timeout. That thread only reads the flag between
iterations of `loop_forever`, so it cannot read it while parked inside
`reconnect()` -> `_ssl_wrap_socket()` -> `do_handshake()`. paho gives that
handshake the connection's keepalive as its socket timeout -- 30s for a printer
-- and a socket timeout is per operation, renewed by every byte the peer sends.
A broker that still answers on its port but never finishes the handshake
therefore holds the join open for as long as it keeps trickling; a silent one
still holds it 30s.

Whoever called `loop_stop()` waits that out, and in Bambuddy that caller is the
asyncio thread. #3068: a printer 38 hours offline, still answering on 8883, was
picked up by the connection watchdog exactly as intended; the rebuild ended in
that join and the process stopped serving HTTP -- UI, API and health check --
while staying alive, so the container's `restart: unless-stopped` never fired.
#1445 was the same join reached from the add-printer probe.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# How long a retirement may take before it is worth a line in the support
# bundle. A healthy paho thread exits in well under a second.
_RETIRE_SLOW_SECONDS = 5.0

# The callbacks Bambuddy's three MQTT services set between them. Anything else
# paho offers is already None because nobody here assigns it.
_CALLBACKS = ("on_connect", "on_disconnect", "on_subscribe", "on_message")


def retire_paho_client(client, label: str) -> None:
    """Shut *client* down on a thread of its own and return immediately.

    *label* names the connection in logs and in the retirement thread's name,
    which is where a thread dump from the next stuck one will be read.

    Two things happen inline rather than on that thread:

    - The callbacks are cleared here. Blocking until the network thread was
      gone is what used to guarantee a client we had let go of could no longer
      touch our state; with the teardown detached, a zombie that finishes its
      handshake would auto-reconnect and report itself connected behind its
      replacement's back.
    - Nothing else -- not even `disconnect()`, which is cheap enough to run
      here (it queues a packet and returns) but would be one more thing
      between the caller and its return for no gain, since the thread starts
      within microseconds. It still happens and still matters: it is what
      stops paho's auto-reconnect, and with it the chance of an unacked
      `project_file` replaying onto a revived session (#1136).
    """
    for attr in _CALLBACKS:
        try:
            setattr(client, attr, None)
        except Exception:  # pragma: no cover - paho always allows this
            pass

    def _teardown() -> None:
        started = time.monotonic()
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass
        waited = time.monotonic() - started
        if waited >= _RETIRE_SLOW_SECONDS:
            # The stall that used to be the event loop's. Worth saying out
            # loud: it means this connection is wedged somewhere paho cannot
            # interrupt, and the next report of it should not have to be
            # diagnosed from a thread dump again.
            logger.warning(
                "[%s] Retiring the old MQTT client took %.0fs (paho's network thread would "
                "not stop). The connection was replaced anyway.",
                label,
                waited,
            )

    try:
        threading.Thread(target=_teardown, name=f"mqtt-retire-{label}", daemon=True).start()
    except RuntimeError as e:
        # Out of threads entirely, which means the process has larger problems.
        # Send the DISCONNECT inline anyway -- it is what keeps the abandoned
        # session from reconnecting and replaying (#1136) -- and leave the
        # network thread to paho.
        logger.error("[%s] Could not start the MQTT teardown thread: %s", label, e)
        try:
            client.disconnect()
        except Exception:
            pass

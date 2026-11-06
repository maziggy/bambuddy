"""Lift the open-file soft limit to the hard limit at startup (#2883).

Docker, systemd and most native installs start Bambuddy with a soft
``RLIMIT_NOFILE`` of 1024 and a far higher hard limit. A process may raise its
own soft limit up to the hard one without privileges, so doing it here reaches
every install through the code. A ``ulimits`` block in docker-compose.yml would
not: existing installs keep their own compose file, and on a host whose hard
limit is lower than the block asks for, the container would not start.

At 1024, running out of descriptors is what turned #2883 into a corrupted
database: every new SQLite connection failed with "disk I/O error" for 44
hours. Above 1024 open descriptors the failure is milder. paho-mqtt waits on
its socket with ``select()``, which cannot take a descriptor numbered 1024 or
higher; it reports that as a lost connection and reconnects, so printer
connections drop while the database keeps working.
"""

import logging

logger = logging.getLogger(__name__)

# What startup found and did, for the support bundle. None until it has run, or
# on a platform without RLIMIT_NOFILE (Windows).
startup_status: dict | None = None

# Tried in order when the hard limit is unlimited. macOS reports RLIM_INFINITY
# but refuses a soft limit above kern.maxfilesperproc, so a finite value has to
# be picked; 10240 is the usual OPEN_MAX there.
_UNLIMITED_HARD_TARGETS = (65536, 10240)


def _describe(value: int, infinity: int) -> int | str:
    return "unlimited" if value == infinity else value


def raise_open_file_limit() -> dict | None:
    """Raise the soft open-file limit as far as the hard limit allows.

    Never lowers it, and never raises: a limit that cannot be changed is logged
    and startup carries on with it. Returns what was found and done, which is
    also kept in ``startup_status``.
    """
    global startup_status
    try:
        import resource
    except ImportError:
        return None

    infinity = resource.RLIM_INFINITY
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError) as e:
        logger.warning("Could not read the open-file limit: %s", e)
        return None

    status: dict = {
        "soft_at_start": _describe(soft, infinity),
        "hard": _describe(hard, infinity),
        "soft": _describe(soft, infinity),
        "raised": False,
    }
    startup_status = status

    targets = _UNLIMITED_HARD_TARGETS if hard == infinity else (hard,)
    targets = tuple(t for t in targets if soft != infinity and t > soft)
    if not targets:
        logger.info("Open-file limit: %s (hard limit %s)", status["soft"], status["hard"])
        return status

    error: Exception | None = None
    for target in targets:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (OSError, ValueError) as e:
            error = e
            continue
        status["soft"] = target
        status["raised"] = True
        logger.info("Raised the open-file limit from %s to %s (hard limit %s)", soft, target, status["hard"])
        return status

    status["error"] = str(error)
    logger.warning(
        "Could not raise the open-file limit above %s (hard limit %s): %s",
        soft,
        status["hard"],
        error,
    )
    return status

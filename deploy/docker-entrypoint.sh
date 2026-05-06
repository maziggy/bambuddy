#!/bin/sh
# Bambuddy container entrypoint.
#
# Runs as root (the image leaves USER unset, so containers start as
# root by default), chowns /app/data and /app/logs to PUID:PGID, then
# drops to PUID:PGID via gosu and execs the application. This fixes the
# class of "Permission denied" errors that bit users when:
#
#   - a Docker named volume was first created with root ownership and
#     the container was running with `user: 1000:1000` (named volumes
#     created by the daemon take its ownership; Dockerfile chmod hacks
#     cover the parent path but not subdirs created at runtime).
#   - a bind-mount source path didn't exist on the host yet, so dockerd
#     created it as root before the container started, leaving it
#     unwritable by uid 1000 inside the container — see #1211 / #668
#     for the virtual_printer bind-mount case the shipped compose
#     template ships uncommented.
#
# If the container is started with an explicit `user:` directive
# (compose `user:` or `docker run --user`), the entrypoint runs as that
# user instead of root and chown isn't possible. The script falls
# through to direct exec without modifying ownership — preserving the
# previous behavior for users who pin a specific uid via compose.

set -eu

# Default to 1000:1000 to match the legacy `user: "1000:1000"` default
# in our previously-shipped compose template; overridable via env so
# users who run docker as a different uid can match their host without
# editing the compose user: directive.
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# If we're not root, we can't chown anything. Exec the original command
# and trust that the user has set up host-side ownership themselves.
if [ "$(id -u)" -ne 0 ]; then
    exec "$@"
fi

# `chown -R` is gated behind a top-level ownership check so a correctly-
# owned directory isn't traversed on every container start. A user with
# a multi-GB archive directory would otherwise pay seconds-to-minutes
# of chown traversal at every restart.
chown_if_needed() {
    target="$1"
    [ -d "$target" ] || mkdir -p "$target"
    current="$(stat -c '%u:%g' "$target" 2>/dev/null || echo '')"
    if [ "$current" != "$PUID:$PGID" ]; then
        echo "[entrypoint] chown -R ${PUID}:${PGID} ${target}"
        chown -R "${PUID}:${PGID}" "$target" || true
    fi
}

chown_if_needed /app/data
chown_if_needed /app/logs

# Bind-mount-source path needs the same treatment when present. dockerd
# creates missing bind-mount sources as root on the host before the
# container starts; the chown here propagates through the bind mount to
# the host-side directory and fixes the issue once and for all.
if [ -d /app/data/virtual_printer ]; then
    chown_if_needed /app/data/virtual_printer
fi

# Drop privileges and run the application. python's file capabilities
# (cap_net_bind_service=+ep, set in the Dockerfile) survive the uid
# switch, so binding to :322 / :990 still works post-drop.
exec gosu "${PUID}:${PGID}" "$@"

#!/bin/bash
# SpoolBuddy kiosk display idle watchdog.
#
# Powers the HDMI output off via wlopm after the configured inactivity
# timeout, driven by swayidle inside the labwc Wayland session. The timeout
# value is fetched once from the Bambuddy backend on startup so it matches
# whatever the user picked in SpoolBuddy Settings → Display. Changes made
# in the UI take effect on the next reboot / kiosk restart.
#
# Runs in labwc's autostart file as the kiosk user — needs access to
# WAYLAND_DISPLAY, which it inherits from the parent labwc process.

set -u

LOG_FILE="${SPOOLBUDDY_IDLE_LOG:-$HOME/.cache/spoolbuddy-idle.log}"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
exec >>"$LOG_FILE" 2>&1
echo "=== $(date -Is) spoolbuddy-idle starting (pid=$$) ==="
echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-<unset>}"
echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-<unset>}"
echo "PATH=$PATH"

DEFAULT_TIMEOUT=300
ENV_FILE="${SPOOLBUDDY_ENV_FILE:-/opt/bambuddy/spoolbuddy/.env}"
OUTPUT="${SPOOLBUDDY_DISPLAY_OUTPUT:-HDMI-A-1}"

# Wait for labwc to actually bring up its Wayland socket. Autostart fires
# before labwc finishes exporting WAYLAND_DISPLAY on some systems, which
# makes swayidle exit immediately.
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    for _ in $(seq 1 20); do
        sock=$(ls -1 "$XDG_RUNTIME_DIR"/wayland-* 2>/dev/null | grep -v '\.lock$' | head -n1 || true)
        if [ -n "$sock" ]; then
            WAYLAND_DISPLAY="$(basename "$sock")"
            export WAYLAND_DISPLAY
            echo "auto-detected WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
            break
        fi
        sleep 0.5
    done
fi
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export XDG_RUNTIME_DIR
    echo "defaulted XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
fi

if [ -r "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

BACKEND_URL="${SPOOLBUDDY_BACKEND_URL:-}"
API_KEY="${SPOOLBUDDY_API_KEY:-}"
DEVICE_ID="${SPOOLBUDDY_DEVICE_ID:-}"

# Derive device_id from the first non-loopback NIC MAC address, the same
# algorithm daemon/config.py uses so installs without an explicit
# SPOOLBUDDY_DEVICE_ID still match.
if [ -z "$DEVICE_ID" ]; then
    for iface in $(ls -1 /sys/class/net/ 2>/dev/null | sort); do
        [ "$iface" = "lo" ] && continue
        addr_file="/sys/class/net/$iface/address"
        [ -r "$addr_file" ] || continue
        mac=$(tr -d ':' < "$addr_file" 2>/dev/null)
        if [ -n "$mac" ] && [ "$mac" != "000000000000" ]; then
            DEVICE_ID="sb-$mac"
            break
        fi
    done
fi

TIMEOUT="$DEFAULT_TIMEOUT"
if [ -n "$BACKEND_URL" ] && [ -n "$API_KEY" ] && [ -n "$DEVICE_ID" ]; then
    response=$(curl -fsS --max-time 10 \
        -H "Authorization: Bearer $API_KEY" \
        "$BACKEND_URL/api/v1/spoolbuddy/devices/$DEVICE_ID/display" 2>/dev/null || true)
    if [ -n "$response" ]; then
        fetched=$(printf '%s' "$response" | jq -r '.blank_timeout // empty' 2>/dev/null || true)
        if [ -n "$fetched" ] && [ "$fetched" -eq "$fetched" ] 2>/dev/null; then
            TIMEOUT="$fetched"
        fi
    fi
fi

# FIFO for the SpoolBuddy daemon to request display wake from outside the
# Wayland session (NFC tag scan, scale weight change).  The daemon writes
# "wake\n" to this pipe; the monitor loop below calls wlopm --on.
WAKE_FIFO="/tmp/spoolbuddy-wake"
rm -f "$WAKE_FIFO"
mkfifo -m 622 "$WAKE_FIFO"
echo "wake FIFO created at $WAKE_FIFO"

if [ "$TIMEOUT" -le 0 ]; then
    # Blanking explicitly disabled — just monitor the wake FIFO so NFC/scale
    # wake still works even without swayidle.
    echo "timeout<=0, monitoring wake FIFO only (no swayidle)"
    while read -r _ < "$WAKE_FIFO"; do
        wlopm --on "$OUTPUT" 2>/dev/null || true
    done
    exit 0
fi

echo "starting swayidle with timeout=$TIMEOUT output=$OUTPUT"
swayidle -w \
    timeout "$TIMEOUT" "wlopm --off $OUTPUT" \
    resume "wlopm --on $OUTPUT" &
SWAYIDLE_PID=$!

# Monitor wake FIFO — when the daemon writes to it, turn the display on
# and schedule a re-blank after TIMEOUT seconds (swayidle doesn't know about
# FIFO wakes so it won't re-blank on its own).
REBLANK_PID=""
while read -r _ < "$WAKE_FIFO"; do
    wlopm --on "$OUTPUT" 2>/dev/null || true
    # Cancel any pending re-blank timer, then start a new one
    [ -n "$REBLANK_PID" ] && kill "$REBLANK_PID" 2>/dev/null || true
    (sleep "$TIMEOUT" && wlopm --off "$OUTPUT" 2>/dev/null) &
    REBLANK_PID=$!
done &
FIFO_PID=$!

# If either process exits, clean up and exit.
wait -n "$SWAYIDLE_PID" "$FIFO_PID" 2>/dev/null
echo "child exited, cleaning up"
kill "$SWAYIDLE_PID" "$FIFO_PID" 2>/dev/null || true
rm -f "$WAKE_FIFO"

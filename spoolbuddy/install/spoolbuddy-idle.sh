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

DEFAULT_TIMEOUT=300
ENV_FILE="${SPOOLBUDDY_ENV_FILE:-/opt/bambuddy/spoolbuddy/.env}"
OUTPUT="${SPOOLBUDDY_DISPLAY_OUTPUT:-HDMI-A-1}"

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

if [ "$TIMEOUT" -le 0 ]; then
    # Blanking explicitly disabled — don't launch swayidle at all.
    exec sleep infinity
fi

exec swayidle -w \
    timeout "$TIMEOUT" "wlopm --off $OUTPUT" \
    resume "wlopm --on $OUTPUT"

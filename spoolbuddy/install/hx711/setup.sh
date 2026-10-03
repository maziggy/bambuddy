#!/bin/bash
set -euo pipefail
# Installed root-owned; never source the daemon's user-writable environment.
marker=/run/spoolbuddy-hx711-overlay
node=/sys/firmware/devicetree/base/spoolbuddy-hx711
case "${1:-}" in
    start)
        if [[ -e "$node" ]]; then
            echo 'HX711 overlay already exists; inspect existing hardware setup.' >&2
            exit 1
        fi
        modprobe hx711
        # Own cleanup before applying the overlay, including failed starts.
        touch "$marker"
        dtoverlay -d /usr/local/lib/spoolbuddy-hx711 spoolbuddy-hx711
        # A failed kernel read can itself wait for conversion/reset. Bound the
        # whole probe period instead of repeating 50 potentially slow reads.
        deadline=$((SECONDS + 5))
        while (( SECONDS < deadline )); do
            for raw in /sys/bus/platform/devices/spoolbuddy-hx711/iio:device*/in_voltage0_raw; do
                if [[ -r "$raw" ]] && cat "$raw" >/dev/null; then
                    exit 0
                fi
            done
            sleep 0.1
        done
        echo 'HX711 produced no reading; check scale wiring and power.' >&2
        exit 1
        ;;
    stop)
        if [[ -e "$marker" ]]; then
            if [[ -e "$node" ]]; then
                dtoverlay -r spoolbuddy-hx711
            fi
            rm -f -- "$marker"
        fi
        ;;
    *) echo 'Usage: setup.sh start|stop' >&2; exit 2 ;;
esac

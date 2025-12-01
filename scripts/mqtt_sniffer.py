#!/usr/bin/env python3
"""MQTT Sniffer for Bambu Lab printers.

Connects to a printer and logs all MQTT messages to capture the exact
command format used by OrcaSlicer or Bambu Studio.

Usage:
    python mqtt_sniffer.py <printer_ip> <serial_number> <access_code>

Example:
    python mqtt_sniffer.py 192.168.1.100 0948BB540200427 12345678
"""

import json
import ssl
import sys
import time
from datetime import datetime

import paho.mqtt.client as mqtt


def on_connect(client, userdata, flags, rc):
    """Called when connected to the MQTT broker."""
    if rc == 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Connected to printer!")
        serial = userdata["serial"]
        # Subscribe to all topics for this printer
        topic_report = f"device/{serial}/report"
        client.subscribe(topic_report)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Subscribed to: {topic_report}")
        print("-" * 80)
        print("Listening for MQTT messages... Press Ctrl+C to stop.")
        print("Now use OrcaSlicer to add a K-profile and the command will be logged here.")
        print("-" * 80)
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Connection failed with code: {rc}")


def on_message(client, userdata, msg):
    """Called when a message is received."""
    try:
        payload = json.loads(msg.payload.decode("utf-8"))

        # Check if this is an extrusion_cali related message
        is_cali_msg = False
        command = None

        if "print" in payload:
            command = payload["print"].get("command", "")
            if "extrusion_cali" in command:
                is_cali_msg = True

        # Always log calibration messages with full detail
        if is_cali_msg:
            print(f"\n{'='*80}")
            print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] *** CALIBRATION COMMAND: {command} ***")
            print(f"Topic: {msg.topic}")
            print(f"Full payload:")
            print(json.dumps(payload, indent=2))
            print(f"{'='*80}\n")
        else:
            # For other messages, just show a brief summary
            if "print" in payload:
                cmd = payload["print"].get("command", "unknown")
                # Skip noisy status messages
                if cmd not in ["push_status"]:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Command: {cmd}")

    except json.JSONDecodeError:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Non-JSON message on {msg.topic}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error processing message: {e}")


def on_disconnect(client, userdata, rc):
    """Called when disconnected from the MQTT broker."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Disconnected with code: {rc}")


def main():
    if len(sys.argv) != 4:
        print("Usage: python mqtt_sniffer.py <printer_ip> <serial_number> <access_code>")
        print("\nExample:")
        print("  python mqtt_sniffer.py 192.168.1.100 0948BB540200427 12345678")
        sys.exit(1)

    printer_ip = sys.argv[1]
    serial_number = sys.argv[2]
    access_code = sys.argv[3]

    print(f"Connecting to printer at {printer_ip}...")
    print(f"Serial: {serial_number}")

    # Create MQTT client
    client = mqtt.Client(userdata={"serial": serial_number})
    client.username_pw_set("bblp", access_code)

    # Configure TLS
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(ssl_context)

    # Set callbacks
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(printer_ip, 8883, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n\nStopping sniffer...")
        client.disconnect()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

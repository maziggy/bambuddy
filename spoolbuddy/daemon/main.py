#!/usr/bin/env python3
"""SpoolBuddy daemon — reads NFC tags and scale, pushes events to Bambuddy backend."""

import asyncio
import logging
import socket
import time

from .api_client import APIClient
from .config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("spoolbuddy")


def _get_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


async def nfc_poll_loop(config: Config, api: APIClient):
    """Continuous NFC polling loop — runs in asyncio with blocking reads offloaded."""
    from .nfc_reader import NFCReader

    nfc = NFCReader()
    if not nfc.ok:
        logger.warning("NFC reader not available, skipping NFC polling")
        return

    try:
        while True:
            event_type, event_data = await asyncio.to_thread(nfc.poll)

            if event_type == "tag_detected":
                await api.tag_scanned(
                    device_id=config.device_id,
                    tag_uid=event_data["tag_uid"],
                    tray_uuid=event_data.get("tray_uuid"),
                    sak=event_data.get("sak"),
                    tag_type=event_data.get("tag_type"),
                )
            elif event_type == "tag_removed":
                await api.tag_removed(
                    device_id=config.device_id,
                    tag_uid=event_data["tag_uid"],
                )

            await asyncio.sleep(config.nfc_poll_interval)
    finally:
        nfc.close()


async def scale_poll_loop(config: Config, api: APIClient):
    """Continuous scale reading loop — reads at 100ms, reports at 1s intervals."""
    from .scale_reader import ScaleReader

    scale = ScaleReader(
        tare_offset=config.tare_offset,
        calibration_factor=config.calibration_factor,
    )
    if not scale.ok:
        logger.warning("Scale not available, skipping scale polling")
        return

    last_report = 0.0
    try:
        while True:
            result = await asyncio.to_thread(scale.read)

            if result is not None:
                grams, stable, raw_adc = result
                now = time.monotonic()

                if now - last_report >= config.scale_report_interval:
                    await api.scale_reading(
                        device_id=config.device_id,
                        weight_grams=grams,
                        stable=stable,
                        raw_adc=raw_adc,
                    )
                    last_report = now

            await asyncio.sleep(config.scale_read_interval)
    finally:
        scale.close()


async def heartbeat_loop(config: Config, api: APIClient, start_time: float):
    """Periodic heartbeat to keep device registered and pick up commands."""

    ip = _get_ip()

    while True:
        await asyncio.sleep(config.heartbeat_interval)

        uptime = int(time.monotonic() - start_time)
        result = await api.heartbeat(
            device_id=config.device_id,
            nfc_ok=True,
            scale_ok=True,
            uptime_s=uptime,
            ip_address=ip,
        )

        if result:
            cmd = result.get("pending_command")
            if cmd == "tare":
                logger.info("Tare command received from backend")
                # Tare is handled by scale_reader — need cross-task communication
                # For now, update calibration from backend response
            tare = result.get("tare_offset", config.tare_offset)
            cal = result.get("calibration_factor", config.calibration_factor)
            if tare != config.tare_offset or cal != config.calibration_factor:
                config.tare_offset = tare
                config.calibration_factor = cal
                logger.info("Calibration updated from backend: tare=%d, factor=%.6f", tare, cal)


async def main():
    config = Config.load()
    logger.info("SpoolBuddy daemon starting (device=%s, backend=%s)", config.device_id, config.backend_url)

    api = APIClient(config.backend_url, config.api_key)
    ip = _get_ip()
    start_time = time.monotonic()

    # Register with backend (retries until success)
    reg = await api.register_device(
        device_id=config.device_id,
        hostname=config.hostname,
        ip_address=ip,
        has_nfc=True,
        has_scale=True,
        tare_offset=config.tare_offset,
        calibration_factor=config.calibration_factor,
    )

    # Use server-side calibration if available
    if reg:
        config.tare_offset = reg.get("tare_offset", config.tare_offset)
        config.calibration_factor = reg.get("calibration_factor", config.calibration_factor)

    logger.info("Device registered, starting poll loops")

    try:
        await asyncio.gather(
            nfc_poll_loop(config, api),
            scale_poll_loop(config, api),
            heartbeat_loop(config, api, start_time),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())

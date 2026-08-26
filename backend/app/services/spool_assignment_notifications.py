import logging

from backend.app.core.database import async_session
from backend.app.core.websocket import ws_manager
from backend.app.models.printer import Printer
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.inventory_mode import spoolman_owns_assignments
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import printer_manager


def _global_tray_from_assignment(ams_id: int, tray_id: int) -> int:
    """Convert an assignment tuple to Bambuddy global tray ID."""
    if ams_id in (254, 255):
        return 254 + tray_id
    if ams_id >= 128:
        return ams_id
    return ams_id * 4 + tray_id


def _slot_label_from_global_tray(global_tray_id: int) -> str:
    """Return a human-readable slot label from a global tray ID."""
    if global_tray_id == 254:
        return "Ext-L"
    if global_tray_id == 255:
        return "Ext-R"
    if global_tray_id >= 128:
        return f"HT-{chr(65 + (global_tray_id - 128))}"
    # 24-27 = A2L AMS-Lite (normalised unit 6); see a2l-am-unit-16.
    if 24 <= global_tray_id <= 27:
        return f"Lite-{(global_tray_id % 4) + 1}"
    ams_id = global_tray_id // 4
    tray_id = global_tray_id % 4
    return f"{chr(65 + ams_id)}{tray_id + 1}"


def _tray_profile_and_color_for_global_id(state: PrinterState | None, global_tray_id: int) -> tuple[str, str]:
    """Resolve expected tray material/profile and color for a global tray ID from current printer state."""
    if not state or not state.raw_data:
        return ("Unknown", "Unknown")

    ams_raw = state.raw_data.get("ams", {})
    ams_units = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []

    vt_trays = state.raw_data.get("vt_tray", [])
    if not isinstance(vt_trays, list):
        vt_trays = []

    for tray in vt_trays:
        if not isinstance(tray, dict):
            continue
        if int(tray.get("id", -1)) == global_tray_id:
            profile = tray.get("tray_sub_brands") or tray.get("tray_type") or "Unknown"
            color = tray.get("tray_color") or "Unknown"
            return (profile, color)

    for ams in ams_units:
        if not isinstance(ams, dict):
            continue
        ams_id = int(ams.get("id", -1))
        trays = ams.get("tray", [])
        if not isinstance(trays, list):
            continue
        for tray in trays:
            if not isinstance(tray, dict):
                continue
            tray_id = int(tray.get("id", -1))
            candidate = ams_id if ams_id >= 128 else (ams_id * 4 + tray_id)
            if candidate == global_tray_id:
                profile = tray.get("tray_sub_brands") or tray.get("tray_type") or "Unknown"
                color = tray.get("tray_color") or "Unknown"
                return (profile, color)

    return ("Unknown", "Unknown")


def _decode_mqtt_mapping_to_global_trays(mapping_raw: object) -> list[int]:
    """Decode printer MQTT mapping values into Bambuddy global tray IDs."""
    if not isinstance(mapping_raw, list) or not mapping_raw:
        return []

    decoded: list[int] = []
    for value in mapping_raw:
        try:
            if isinstance(value, int):
                encoded = value
            elif isinstance(value, str):
                encoded = int(value, 10)
            else:
                continue
        except ValueError:
            continue

        if encoded >= 65535:
            continue

        ams_hw_id = (encoded >> 8) & 0xFF
        slot = encoded & 0xFF

        if 0 <= ams_hw_id <= 3:
            decoded.append(ams_hw_id * 4 + (slot & 0x03))
        elif 128 <= ams_hw_id <= 135:
            decoded.append(ams_hw_id)
        elif ams_hw_id in (254, 255):
            decoded.append(255 if slot == 255 else 254)

    return decoded


async def notify_missing_spool_assignments_on_print_start(
    printer_id: int,
    data: dict,
    logger: logging.Logger,
) -> None:
    """Send notification when print-start mapping references unassigned trays."""
    explicit_mapping = data.get("ams_mapping")
    explicit_values = (
        [value for value in explicit_mapping if isinstance(value, int)] if isinstance(explicit_mapping, list) else []
    )
    raw_mapping = data.get("raw_data", {}).get("mapping") if isinstance(data.get("raw_data"), dict) else None
    decoded_values = _decode_mqtt_mapping_to_global_trays(raw_mapping)
    mapping_values = explicit_values if explicit_values else decoded_values

    used_global_trays = {value for value in mapping_values if value >= 0}
    if not used_global_trays:
        return

    try:
        async with async_session() as db:
            printer = await db.get(Printer, printer_id)
            printer_name = printer.name if printer else f"Printer {printer_id}"

            # A tray is "assigned" if it has a row in the table the current
            # mode uses. Both expose printer_id / ams_id / tray_id in the same
            # shape, so _global_tray_from_assignment works on either.
            #
            # This read both tables and unioned them until #2812. That was
            # correct while the inactive table was emptied on every mode
            # toggle -- it is how #1473 was fixed, where querying only the
            # legacy table flagged every tray as missing on a Spoolman print.
            # Nothing is emptied now, so a union would let a leftover row in
            # the mode you are *not* using vouch for a tray that has no
            # assignment in the mode you are, and this notification exists
            # precisely to catch that tray.
            table = SpoolmanSlotAssignment if await spoolman_owns_assignments(db) else SpoolAssignment
            rows = (await db.execute(table.__table__.select().where(table.printer_id == printer_id))).fetchall()
            assigned_global_trays = {_global_tray_from_assignment(row.ams_id, row.tray_id) for row in rows}

            missing_global = sorted(used_global_trays - assigned_global_trays)
            if not missing_global:
                return

            await _send_missing_assignment_notification(printer_id, printer_name, missing_global, db)
    except Exception as e:
        logger.warning("Missing spool-assignment notification failed: %s", e)


async def _send_missing_assignment_notification(
    printer_id: int,
    printer_name: str,
    missing_global: list[int],
    db,
) -> None:
    """Describe the unassigned trays and push them to the UI and the providers."""
    state = printer_manager.get_status(printer_id)
    missing_slots = []
    for global_id in missing_global:
        profile, color = _tray_profile_and_color_for_global_id(state, global_id)
        missing_slots.append(
            {
                "slot": _slot_label_from_global_tray(global_id),
                "profile": profile,
                "color": color,
            }
        )

    await ws_manager.send_missing_spool_assignment(
        printer_id=printer_id,
        printer_name=printer_name,
        missing_slots=missing_slots,
    )
    await notification_service.on_print_missing_spool_assignment(
        printer_id=printer_id,
        printer_name=printer_name,
        missing_slots=missing_slots,
        db=db,
    )


async def notify_missing_spool_assignments_on_print_complete(
    printer_id: int,
    missing_global_trays: list[int],
    db,
    logger: logging.Logger,
) -> None:
    """Say so when a finished print could not debit a tray it drew from (#2812).

    The print-start check above is predictive: it reads the mapping before the
    job runs and warns about trays that have no assignment yet. It cannot cover
    an assignment that disappears *during* a print, and nothing re-checked
    afterwards -- so a print whose assignments existed at print start, and were
    gone by the time it finished, resolved its 3MF, resolved its grams,
    resolved its tray, skipped the debit at INFO, and reported success. The
    reporter lost 65.49 g that way and only noticed because a spool's remaining
    weight looked wrong.

    This fires on realized loss rather than risk: the trays passed here are the
    ones a completed print actually tried to charge and could not. A print that
    was already warned at start will notify twice, which is the right trade --
    the first says the weight may not be tracked, the second says it was not.

    Takes the caller's session: this runs inside ``on_print_complete``'s
    transaction, and opening a second one to read the printer's name would
    deadlock against it on SQLite.
    """
    if not missing_global_trays:
        return
    try:
        printer = await db.get(Printer, printer_id)
        printer_name = printer.name if printer else f"Printer {printer_id}"
        await _send_missing_assignment_notification(printer_id, printer_name, sorted(set(missing_global_trays)), db)
    except Exception as e:  # noqa: BLE001 — a notification must not fail a completed print
        logger.warning("Missing spool-assignment completion notification failed: %s", e)

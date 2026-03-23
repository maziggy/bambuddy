import logging

from backend.app.core.database import async_session
from backend.app.services.bambu_mqtt import PrinterState
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
    ams_id = global_tray_id // 4
    tray_id = global_tray_id % 4
    return f"{chr(65 + ams_id)}{tray_id + 1}"


def _tray_profile_for_global_id(state: PrinterState | None, global_tray_id: int) -> str:
    """Resolve expected tray material/profile for a global tray ID from current printer state."""
    if not state or not state.raw_data:
        return "Unknown"

    ams_raw = state.raw_data.get("ams", {})
    ams_units = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []

    vt_trays = state.raw_data.get("vt_tray", [])
    if not isinstance(vt_trays, list):
        vt_trays = []

    for tray in vt_trays:
        if not isinstance(tray, dict):
            continue
        if int(tray.get("id", -1)) == global_tray_id:
            return tray.get("tray_sub_brands") or tray.get("tray_type") or "Unknown"

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
                return tray.get("tray_sub_brands") or tray.get("tray_type") or "Unknown"

    return "Unknown"


def _tray_color_for_global_id(state: PrinterState | None, global_tray_id: int) -> str:
    """Resolve expected tray color for a global tray ID from current printer state."""
    if not state or not state.raw_data:
        return "Unknown"

    ams_raw = state.raw_data.get("ams", {})
    ams_units = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []

    vt_trays = state.raw_data.get("vt_tray", [])
    if not isinstance(vt_trays, list):
        vt_trays = []

    for tray in vt_trays:
        if not isinstance(tray, dict):
            continue
        if int(tray.get("id", -1)) == global_tray_id:
            return tray.get("tray_color") or "Unknown"

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
                return tray.get("tray_color") or "Unknown"

    return "Unknown"


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
        [int(value) for value in explicit_mapping if isinstance(value, int)]
        if isinstance(explicit_mapping, list)
        else []
    )
    raw_mapping = data.get("raw_data", {}).get("mapping") if isinstance(data.get("raw_data"), dict) else None
    decoded_values = _decode_mqtt_mapping_to_global_trays(raw_mapping)
    mapping_values = explicit_values if explicit_values else decoded_values

    used_global_trays = {value for value in mapping_values if value >= 0 and value != 255}
    if not used_global_trays:
        return

    try:
        async with async_session() as db:
            from backend.app.models.printer import Printer
            from backend.app.models.spool_assignment import SpoolAssignment

            printer = await db.get(Printer, printer_id)
            printer_name = printer.name if printer else f"Printer {printer_id}"

            assignments_result = await db.execute(
                SpoolAssignment.__table__.select().where(SpoolAssignment.printer_id == printer_id)
            )
            assignments = assignments_result.fetchall()
            assigned_global_trays = {
                _global_tray_from_assignment(assignment.ams_id, assignment.tray_id) for assignment in assignments
            }

            missing_global = sorted(used_global_trays - assigned_global_trays)
            if not missing_global:
                return

            state = printer_manager.get_status(printer_id)
            missing_slots = []
            for global_id in missing_global:
                missing_slots.append(
                    {
                        "slot": _slot_label_from_global_tray(global_id),
                        "profile": _tray_profile_for_global_id(state, global_id),
                        "color": _tray_color_for_global_id(state, global_id),
                    }
                )

            await notification_service.on_print_missing_spool_assignment(
                printer_id=printer_id,
                printer_name=printer_name,
                missing_slots=missing_slots,
                db=db,
            )
    except Exception as e:
        logger.warning("Missing spool-assignment notification failed: %s", e)

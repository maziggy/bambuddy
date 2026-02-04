"""Prometheus metrics endpoint for external monitoring."""

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.printer_manager import printer_manager, supports_chamber_temp

router = APIRouter(tags=["metrics"])


async def get_prometheus_settings(db: AsyncSession) -> tuple[bool, str]:
    """Get Prometheus settings from database."""
    result = await db.execute(select(Settings).where(Settings.key.in_(["prometheus_enabled", "prometheus_token"])))
    settings_dict = {s.key: s.value for s in result.scalars().all()}

    enabled = settings_dict.get("prometheus_enabled", "false").lower() == "true"
    token = settings_dict.get("prometheus_token", "")
    return enabled, token


def format_labels(**labels: str) -> str:
    """Format label key-value pairs for Prometheus."""
    if not labels:
        return ""
    pairs = [f'{k}="{v}"' for k, v in labels.items() if v is not None]
    return "{" + ",".join(pairs) + "}"


def state_to_numeric(state: str) -> int:
    """Convert printer state string to numeric value."""
    state_map = {
        "unknown": 0,
        "IDLE": 1,
        "RUNNING": 2,
        "PAUSE": 3,
        "FINISH": 4,
        "FAILED": 5,
        "PREPARE": 6,
        "SLICING": 7,
    }
    return state_map.get(state, 0)


@router.get("/metrics", response_class=Response)
async def get_metrics(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
):
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus text exposition format.
    Requires prometheus_enabled setting to be true.
    If prometheus_token is set, requires Bearer token authentication.
    """
    # Check if enabled
    enabled, token = await get_prometheus_settings(db)

    if not enabled:
        raise HTTPException(status_code=404, detail="Prometheus metrics not enabled")

    # Check authentication if token is set
    if token:
        if not authorization:
            raise HTTPException(status_code=401, detail="Authorization required")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Bearer token required")
        provided_token = authorization[7:]  # Remove "Bearer " prefix
        if provided_token != token:
            raise HTTPException(status_code=401, detail="Invalid token")

    lines: list[str] = []

    # =========================================================================
    # Printer metrics
    # =========================================================================

    # Get all printers from DB
    result = await db.execute(select(Printer).where(Printer.is_active == True))  # noqa: E712
    printers = list(result.scalars().all())

    # Build lookup for printer info
    printer_info = {p.id: p for p in printers}

    # Get all connected printer statuses
    all_statuses = printer_manager.get_all_statuses()

    # Printer connection status
    lines.append("# HELP bambuddy_printer_connected Printer connection status (1=connected, 0=disconnected)")
    lines.append("# TYPE bambuddy_printer_connected gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        connected = 1 if status and status.connected else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
            model=printer.model or "unknown",
        )
        lines.append(f"bambuddy_printer_connected{labels} {connected}")

    # Printer state
    lines.append("")
    lines.append(
        "# HELP bambuddy_printer_state Printer state (0=unknown, 1=idle, 2=running, 3=pause, 4=finish, 5=failed, 6=prepare, 7=slicing)"
    )
    lines.append("# TYPE bambuddy_printer_state gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        state_val = state_to_numeric(status.state) if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_printer_state{labels} {state_val}")

    # Print progress
    lines.append("")
    lines.append("# HELP bambuddy_print_progress Current print progress (0-100)")
    lines.append("# TYPE bambuddy_print_progress gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        progress = status.progress if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_print_progress{labels} {progress:.1f}")

    # Remaining time
    lines.append("")
    lines.append("# HELP bambuddy_print_remaining_seconds Estimated remaining print time in seconds")
    lines.append("# TYPE bambuddy_print_remaining_seconds gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        remaining = status.remaining_time * 60 if status else 0  # Convert minutes to seconds
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_print_remaining_seconds{labels} {remaining}")

    # Layer progress
    lines.append("")
    lines.append("# HELP bambuddy_print_layer_current Current layer number")
    lines.append("# TYPE bambuddy_print_layer_current gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        layer = status.layer_num if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_print_layer_current{labels} {layer}")

    lines.append("")
    lines.append("# HELP bambuddy_print_layer_total Total layers in current print")
    lines.append("# TYPE bambuddy_print_layer_total gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        total = status.total_layers if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_print_layer_total{labels} {total}")

    # =========================================================================
    # Temperature metrics
    # =========================================================================

    lines.append("")
    lines.append("# HELP bambuddy_bed_temp_celsius Current bed temperature")
    lines.append("# TYPE bambuddy_bed_temp_celsius gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        temp = status.temperatures.get("bed", 0) if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_bed_temp_celsius{labels} {temp:.1f}")

    lines.append("")
    lines.append("# HELP bambuddy_bed_target_celsius Target bed temperature")
    lines.append("# TYPE bambuddy_bed_target_celsius gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        temp = status.temperatures.get("bed_target", 0) if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_bed_target_celsius{labels} {temp:.1f}")

    lines.append("")
    lines.append("# HELP bambuddy_nozzle_temp_celsius Current nozzle temperature")
    lines.append("# TYPE bambuddy_nozzle_temp_celsius gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        # Primary nozzle
        temp = status.temperatures.get("nozzle", 0) if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
            nozzle="0",
        )
        lines.append(f"bambuddy_nozzle_temp_celsius{labels} {temp:.1f}")
        # Second nozzle if present
        if status and "nozzle_2" in status.temperatures:
            temp2 = status.temperatures.get("nozzle_2", 0)
            labels2 = format_labels(
                printer_id=str(printer.id),
                printer_name=printer.name,
                serial=printer.serial_number,
                nozzle="1",
            )
            lines.append(f"bambuddy_nozzle_temp_celsius{labels2} {temp2:.1f}")

    lines.append("")
    lines.append("# HELP bambuddy_nozzle_target_celsius Target nozzle temperature")
    lines.append("# TYPE bambuddy_nozzle_target_celsius gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        temp = status.temperatures.get("nozzle_target", 0) if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
            nozzle="0",
        )
        lines.append(f"bambuddy_nozzle_target_celsius{labels} {temp:.1f}")
        if status and "nozzle_2_target" in status.temperatures:
            temp2 = status.temperatures.get("nozzle_2_target", 0)
            labels2 = format_labels(
                printer_id=str(printer.id),
                printer_name=printer.name,
                serial=printer.serial_number,
                nozzle="1",
            )
            lines.append(f"bambuddy_nozzle_target_celsius{labels2} {temp2:.1f}")

    lines.append("")
    lines.append(
        "# HELP bambuddy_chamber_temp_celsius Current chamber temperature (only for models with chamber sensor)"
    )
    lines.append("# TYPE bambuddy_chamber_temp_celsius gauge")
    for printer in printers:
        # Only report chamber temp for models that have a real sensor
        if not supports_chamber_temp(printer.model):
            continue
        status = all_statuses.get(printer.id)
        temp = status.temperatures.get("chamber", 0) if status else 0
        labels = format_labels(
            printer_id=str(printer.id),
            printer_name=printer.name,
            serial=printer.serial_number,
        )
        lines.append(f"bambuddy_chamber_temp_celsius{labels} {temp:.1f}")

    # =========================================================================
    # Fan speeds
    # =========================================================================

    lines.append("")
    lines.append("# HELP bambuddy_fan_speed_percent Fan speed percentage")
    lines.append("# TYPE bambuddy_fan_speed_percent gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        if not status:
            continue
        # Part cooling fan
        if "part_fan" in status.temperatures:
            val = status.temperatures["part_fan"]
            labels = format_labels(
                printer_id=str(printer.id),
                printer_name=printer.name,
                serial=printer.serial_number,
                fan="part",
            )
            lines.append(f"bambuddy_fan_speed_percent{labels} {val:.1f}")
        # Aux fan
        if "aux_fan" in status.temperatures:
            val = status.temperatures["aux_fan"]
            labels = format_labels(
                printer_id=str(printer.id),
                printer_name=printer.name,
                serial=printer.serial_number,
                fan="aux",
            )
            lines.append(f"bambuddy_fan_speed_percent{labels} {val:.1f}")
        # Chamber fan
        if "chamber_fan" in status.temperatures:
            val = status.temperatures["chamber_fan"]
            labels = format_labels(
                printer_id=str(printer.id),
                printer_name=printer.name,
                serial=printer.serial_number,
                fan="chamber",
            )
            lines.append(f"bambuddy_fan_speed_percent{labels} {val:.1f}")

    # =========================================================================
    # WiFi signal
    # =========================================================================

    lines.append("")
    lines.append("# HELP bambuddy_wifi_signal_dbm WiFi signal strength in dBm")
    lines.append("# TYPE bambuddy_wifi_signal_dbm gauge")
    for printer in printers:
        status = all_statuses.get(printer.id)
        if status and status.wifi_signal is not None:
            labels = format_labels(
                printer_id=str(printer.id),
                printer_name=printer.name,
                serial=printer.serial_number,
            )
            lines.append(f"bambuddy_wifi_signal_dbm{labels} {status.wifi_signal}")

    # =========================================================================
    # Print statistics (from database)
    # =========================================================================

    # Total prints by status
    lines.append("")
    lines.append("# HELP bambuddy_prints_total Total number of prints by result")
    lines.append("# TYPE bambuddy_prints_total counter")
    result = await db.execute(select(PrintArchive.status, func.count(PrintArchive.id)).group_by(PrintArchive.status))
    for print_result, count in result.all():
        result_label = print_result or "unknown"
        labels = format_labels(result=result_label)
        lines.append(f"bambuddy_prints_total{labels} {count}")

    # Total prints per printer
    lines.append("")
    lines.append("# HELP bambuddy_printer_prints_total Total prints per printer")
    lines.append("# TYPE bambuddy_printer_prints_total counter")
    result = await db.execute(
        select(PrintArchive.printer_id, func.count(PrintArchive.id)).group_by(PrintArchive.printer_id)
    )
    for printer_id, count in result.all():
        if printer_id and printer_id in printer_info:
            p = printer_info[printer_id]
            labels = format_labels(
                printer_id=str(printer_id),
                printer_name=p.name,
                serial=p.serial_number,
            )
            lines.append(f"bambuddy_printer_prints_total{labels} {count}")

    # Total filament used - filament_used_grams already contains the total for each print job
    lines.append("")
    lines.append("# HELP bambuddy_filament_used_grams Total filament used in grams")
    lines.append("# TYPE bambuddy_filament_used_grams counter")
    result = await db.execute(select(func.coalesce(func.sum(PrintArchive.filament_used_grams), 0)))
    total_filament = result.scalar() or 0
    lines.append(f"bambuddy_filament_used_grams {total_filament:.1f}")

    # Total print time
    lines.append("")
    lines.append("# HELP bambuddy_print_time_seconds Total print time in seconds")
    lines.append("# TYPE bambuddy_print_time_seconds counter")
    result = await db.execute(select(func.coalesce(func.sum(PrintArchive.print_time_seconds), 0)))
    total_time = result.scalar() or 0
    lines.append(f"bambuddy_print_time_seconds {total_time}")

    # =========================================================================
    # Queue metrics
    # =========================================================================

    lines.append("")
    lines.append("# HELP bambuddy_queue_pending Number of pending queue items")
    lines.append("# TYPE bambuddy_queue_pending gauge")
    result = await db.execute(select(func.count(PrintQueueItem.id)).where(PrintQueueItem.status == "pending"))
    pending_count = result.scalar() or 0
    lines.append(f"bambuddy_queue_pending {pending_count}")

    lines.append("")
    lines.append("# HELP bambuddy_queue_printing Number of currently printing queue items")
    lines.append("# TYPE bambuddy_queue_printing gauge")
    result = await db.execute(select(func.count(PrintQueueItem.id)).where(PrintQueueItem.status == "printing"))
    printing_count = result.scalar() or 0
    lines.append(f"bambuddy_queue_printing {printing_count}")

    # =========================================================================
    # System metrics
    # =========================================================================

    lines.append("")
    lines.append("# HELP bambuddy_printers_connected Number of connected printers")
    lines.append("# TYPE bambuddy_printers_connected gauge")
    connected_count = sum(1 for s in all_statuses.values() if s.connected)
    lines.append(f"bambuddy_printers_connected {connected_count}")

    lines.append("")
    lines.append("# HELP bambuddy_printers_total Total number of configured printers")
    lines.append("# TYPE bambuddy_printers_total gauge")
    lines.append(f"bambuddy_printers_total {len(printers)}")

    # Add trailing newline
    lines.append("")

    content = "\n".join(lines)
    return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")

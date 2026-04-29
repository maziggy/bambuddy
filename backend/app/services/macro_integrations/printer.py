"""Macro functions: printer control commands."""

import asyncio

from backend.app.services.macro_functions import ArgSpec, FunctionContext, FunctionResult, macro_function


@macro_function(
    name="PRINTER_PAUSE",
    description="Pause the current print.",
    requires_printer=True,
    allowed_in_embed=False,
)
async def _printer_pause(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(ctx.printer_id)
    if not client:
        msg = "[PRINTER_PAUSE] error: printer not connected\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    ok = client.pause_print()
    if ok:
        msg = "[PRINTER_PAUSE] ok: pause command sent\n"
    else:
        msg = "[PRINTER_PAUSE] error: command rejected by printer (no active print?)\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=ok, message=msg)


@macro_function(
    name="PRINTER_RESUME",
    description="Resume a paused print.",
    requires_printer=True,
    allowed_in_embed=False,
)
async def _printer_resume(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(ctx.printer_id)
    if not client:
        msg = "[PRINTER_RESUME] error: printer not connected\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    ok = client.resume_print()
    if ok:
        msg = "[PRINTER_RESUME] ok: resume command sent\n"
    else:
        msg = "[PRINTER_RESUME] error: command rejected by printer (not paused?)\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=ok, message=msg)


@macro_function(
    name="PRINTER_STOP",
    description="Stop the current print.",
    requires_printer=True,
    allowed_in_embed=False,
)
async def _printer_stop(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(ctx.printer_id)
    if not client:
        msg = "[PRINTER_STOP] error: printer not connected\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    ok = client.stop_print()
    if ok:
        msg = "[PRINTER_STOP] ok: stop command sent\n"
    else:
        msg = "[PRINTER_STOP] error: command rejected by printer (no active print?)\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=ok, message=msg)


@macro_function(
    name="AMS_DRYING",
    description="Start AMS filament drying for a specific slot.",
    args={
        "ams": ArgSpec("AMS unit index (0-3)", default="0"),
        "temp": ArgSpec("Drying temperature °C (20-90)", default="45"),
        "duration": ArgSpec("Duration in hours (1-12)", default="4"),
    },
    requires_printer=True,
    allowed_in_embed=False,
)
async def _ams_drying(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.printer_manager import printer_manager

    try:
        ams_id = int(ctx.flags.get("ams", ctx.flags.get("a", "0")))
        temp = int(ctx.flags.get("temp", ctx.flags.get("t", "45")))
        duration = int(ctx.flags.get("duration", ctx.flags.get("d", "4")))
    except ValueError as exc:
        msg = f"[AMS_DRYING] invalid args: {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    if not (0 <= ams_id <= 3):
        msg = f"[AMS_DRYING] invalid --ams={ams_id} (use 0-3)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    if not (20 <= temp <= 90):
        msg = f"[AMS_DRYING] invalid --temp={temp} (use 20-90°C)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    if not (1 <= duration <= 12):
        msg = f"[AMS_DRYING] invalid --duration={duration} (use 1-12 hours)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    client = printer_manager.get_client(ctx.printer_id)
    if not client:
        msg = "[AMS_DRYING] error: printer not connected\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    ok = printer_manager.send_drying_command(
        ctx.printer_id, ams_id, temp, duration, mode=1, filament=None, rotate_tray=False
    )
    if ok:
        msg = f"[AMS_DRYING] ok: AMS {ams_id} drying at {temp}°C for {duration}h\n"
    else:
        msg = f"[AMS_DRYING] error: command rejected (AMS {ams_id} not available?)\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=ok, message=msg)


@macro_function(
    name="WAIT_FOR_TEMP",
    description="Wait until the nozzle reaches the target temperature.",
    args={
        "target": ArgSpec("Target nozzle temperature °C (0-350)", required=True),
        "tolerance": ArgSpec("Acceptable deviation in °C (>0)", default="5"),
        "max_wait": ArgSpec("Timeout in seconds (>0, max 600)", default="300"),
    },
    requires_printer=True,
    allowed_in_embed=False,
)
async def _wait_for_temp(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.printer_manager import printer_manager

    try:
        target = float(ctx.flags.get("target", "0"))
        tolerance = float(ctx.flags.get("tolerance", "5"))
        max_wait = float(ctx.flags.get("max_wait", "300"))
    except ValueError as exc:
        msg = f"[WAIT_FOR_TEMP] invalid args: {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    if not (0 <= target <= 350):
        msg = f"[WAIT_FOR_TEMP] invalid --target={target} (use 0-350°C)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    if tolerance <= 0:
        msg = f"[WAIT_FOR_TEMP] invalid --tolerance={tolerance} (must be > 0)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    if max_wait <= 0:
        msg = f"[WAIT_FOR_TEMP] invalid --max_wait={max_wait} (must be > 0)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    max_wait = min(max_wait, 600)

    await ctx.log(ctx.run_id, f"[WAIT_FOR_TEMP] waiting for {target}°C ±{tolerance}°C (timeout {max_wait}s)\n")
    elapsed = 0.0
    while elapsed < max_wait:
        client = printer_manager.get_client(ctx.printer_id)
        if client:
            nozzle = client.state.temperatures.get("nozzle", 0.0)
            if abs(nozzle - target) <= tolerance:
                msg = f"[WAIT_FOR_TEMP] ok: reached {nozzle:.1f}°C after {elapsed:.0f}s\n"
                await ctx.log(ctx.run_id, msg)
                return FunctionResult(ok=True, message=msg)
        await asyncio.sleep(2)
        elapsed += 2

    msg = f"[WAIT_FOR_TEMP] error: timeout after {max_wait:.0f}s (target {target}°C not reached)\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=False, message=msg)

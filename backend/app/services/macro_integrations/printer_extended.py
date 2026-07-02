"""Macro integrations: extended printer context and print control."""

from backend.app.services.macro_functions import ArgSpec, FunctionContext, FunctionResult, macro_function


@macro_function(
    name="CLEAR_HMS_ERRORS",
    description="Clear active HMS/print errors on the printer.",
    requires_printer=True,
    allowed_in_embed=False,
)
async def _clear_hms_errors(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(ctx.printer_id)
    if not client:
        msg = "[CLEAR_HMS_ERRORS] error: printer not connected\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    active_count = len(client.state.hms_errors)
    if active_count == 0:
        msg = "[CLEAR_HMS_ERRORS] ok: no active errors to clear\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=True, message=msg)

    ok = client.clear_hms_errors()
    if ok:
        msg = f"[CLEAR_HMS_ERRORS] ok: cleared {active_count} error(s)\n"
    else:
        msg = "[CLEAR_HMS_ERRORS] error: command rejected by printer\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=ok, message=msg)


@macro_function(
    name="PRINT_QUEUE_ADD",
    description="Add a library file to the print queue.",
    args={
        "file_id": ArgSpec("Library file ID to enqueue", required=True),
        "plate": ArgSpec("Plate number for multi-plate 3MF (>=1, default: 1)", default="1"),
    },
    requires_printer=True,
    allowed_in_embed=False,
)
async def _print_queue_add(ctx: FunctionContext) -> FunctionResult:
    from sqlalchemy import select

    from backend.app.core.database import async_session
    from backend.app.models.library import LibraryFile
    from backend.app.models.print_queue import PrintQueueItem

    try:
        file_id = int(ctx.flags.get("file_id", "0"))
        plate_id = int(ctx.flags.get("plate", "1"))
    except ValueError as exc:
        msg = f"[PRINT_QUEUE_ADD] invalid args: {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    if file_id <= 0:
        msg = f"[PRINT_QUEUE_ADD] invalid --file_id={file_id} (must be > 0)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    if plate_id < 1:
        msg = f"[PRINT_QUEUE_ADD] invalid --plate={plate_id} (must be >= 1)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    async with async_session() as db:
        result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
        lib_file = result.scalar_one_or_none()
        if not lib_file:
            msg = f"[PRINT_QUEUE_ADD] error: library file {file_id} not found\n"
            await ctx.log(ctx.run_id, msg)
            return FunctionResult(ok=False, message=msg)

        result = await db.execute(select(PrintQueueItem).order_by(PrintQueueItem.position.desc()).limit(1))
        last = result.scalar_one_or_none()
        next_pos = (last.position + 1) if last else 0

        item = PrintQueueItem(
            library_file_id=file_id,
            printer_id=ctx.printer_id,
            plate_id=plate_id if plate_id != 1 else None,
            position=next_pos,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)

    display = lib_file.display_name or lib_file.filename
    msg = f"[PRINT_QUEUE_ADD] ok: '{display}' added at position {next_pos} (queue item {item.id})\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=True, message=msg)


@macro_function(
    name="hms_errors",
    description="Inject active HMS error list into Jinja2 context.",
    context_var="hms_errors",
    requires_printer=True,
    allowed_in_embed=True,
)
async def _hms_errors_ctx(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(ctx.printer_id)
    if not client:
        return FunctionResult(ok=True, value=[])

    errors = [
        {
            "code": e.code,
            "severity": e.severity,
            "message": e.message,
            "module": e.module,
        }
        for e in client.state.hms_errors
    ]
    return FunctionResult(ok=True, value=errors)

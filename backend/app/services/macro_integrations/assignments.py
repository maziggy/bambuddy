"""Macro integrations: spool assignment management."""

from backend.app.services.macro_functions import ArgSpec, FunctionContext, FunctionResult, macro_function


@macro_function(
    name="ASSIGN_SPOOL",
    description="Assign a spool to an AMS slot on the target printer.",
    args={
        "spool_id": ArgSpec("Spool ID from inventory", required=True),
        "ams": ArgSpec("AMS unit index (0-3, 255=external)", required=True),
        "tray": ArgSpec("Tray slot index (0-3)", required=True),
    },
    requires_printer=True,
    allowed_in_embed=False,
)
async def _assign_spool(ctx: FunctionContext) -> FunctionResult:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from backend.app.core.database import async_session
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment
    from backend.app.services.printer_manager import printer_manager

    try:
        spool_id = int(ctx.flags.get("spool_id", "0"))
        ams_id = int(ctx.flags.get("ams", "0"))
        tray_id = int(ctx.flags.get("tray", "0"))
    except ValueError as exc:
        msg = f"[ASSIGN_SPOOL] invalid args: {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    if ctx.printer_id is None:
        msg = "[ASSIGN_SPOOL] no printer target — set printer: in the macro cfg\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    if not (0 <= ams_id <= 3 or ams_id == 255 or ams_id >= 128):
        msg = f"[ASSIGN_SPOOL] invalid ams_id={ams_id} (use 0-3 for regular AMS, 128+ for AMS-HT, 255 for external)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    if not (0 <= tray_id <= 3):
        msg = f"[ASSIGN_SPOOL] invalid tray_id={tray_id} (use 0-3)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    # Capture fingerprint from live MQTT state (outside DB session)
    fingerprint_color = None
    fingerprint_type = None
    state = printer_manager.get_status(ctx.printer_id)
    if state and state.raw_data:
        ams_data = state.raw_data.get("ams", {})
        ams_list = (
            ams_data.get("ams", []) if isinstance(ams_data, dict) else ams_data if isinstance(ams_data, list) else []
        )
        for unit in ams_list:
            if not isinstance(unit, dict):
                continue
            if int(unit.get("id", -1)) != ams_id:
                continue
            for tray in unit.get("tray", []):
                if isinstance(tray, dict) and int(tray.get("id", -1)) == tray_id:
                    fingerprint_color = tray.get("tray_color")
                    fingerprint_type = tray.get("tray_type")
                    break

    try:
        async with async_session() as db:
            result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
            spool = result.scalar_one_or_none()
            if not spool:
                msg = f"[ASSIGN_SPOOL] spool {spool_id} not found\n"
                await ctx.log(ctx.run_id, msg)
                return FunctionResult(ok=False, message=msg)
            if spool.archived_at:
                msg = f"[ASSIGN_SPOOL] spool {spool_id} is archived\n"
                await ctx.log(ctx.run_id, msg)
                return FunctionResult(ok=False, message=msg)

            # Upsert: remove any existing assignment for this slot
            existing = await db.execute(
                select(SpoolAssignment).where(
                    SpoolAssignment.printer_id == ctx.printer_id,
                    SpoolAssignment.ams_id == ams_id,
                    SpoolAssignment.tray_id == tray_id,
                )
            )
            old = existing.scalar_one_or_none()
            if old:
                await db.delete(old)
                await db.flush()

            assignment = SpoolAssignment(
                spool_id=spool_id,
                printer_id=ctx.printer_id,
                ams_id=ams_id,
                tray_id=tray_id,
                fingerprint_color=fingerprint_color,
                fingerprint_type=fingerprint_type,
            )
            db.add(assignment)
            await db.commit()
    except Exception as exc:
        msg = f"[ASSIGN_SPOOL] DB error: {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    try:
        from backend.app.core.websocket import ws_manager

        await ws_manager.broadcast(
            {
                "type": "spool_assignment_changed",
                "printer_id": ctx.printer_id,
                "ams_id": ams_id,
                "tray_id": tray_id,
            }
        )
    except Exception:  # noqa: BLE001
        pass

    msg = f"[ASSIGN_SPOOL] spool {spool_id} ({spool.material}) → AMS {ams_id} tray {tray_id}\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=True, message=msg)


@macro_function(
    name="UNASSIGN_SPOOL",
    description="Remove the spool assignment from an AMS slot.",
    args={
        "ams": ArgSpec("AMS unit index (0-3, 255=external)", required=True),
        "tray": ArgSpec("Tray slot index (0-3)", required=True),
    },
    requires_printer=True,
    allowed_in_embed=False,
)
async def _unassign_spool(ctx: FunctionContext) -> FunctionResult:
    from sqlalchemy import select

    from backend.app.core.database import async_session
    from backend.app.models.spool_assignment import SpoolAssignment

    try:
        ams_id = int(ctx.flags.get("ams", "0"))
        tray_id = int(ctx.flags.get("tray", "0"))
    except ValueError as exc:
        msg = f"[UNASSIGN_SPOOL] invalid args: {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    if not (0 <= ams_id <= 3 or ams_id == 255 or ams_id >= 128):
        msg = f"[UNASSIGN_SPOOL] invalid --ams={ams_id} (use 0-3 for regular AMS, 128+ for AMS-HT, 255 for external)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    if not (0 <= tray_id <= 3):
        msg = f"[UNASSIGN_SPOOL] invalid --tray={tray_id} (use 0-3)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    async with async_session() as db:
        result = await db.execute(
            select(SpoolAssignment).where(
                SpoolAssignment.printer_id == ctx.printer_id,
                SpoolAssignment.ams_id == ams_id,
                SpoolAssignment.tray_id == tray_id,
            )
        )
        assignment = result.scalar_one_or_none()
        if not assignment:
            msg = f"[UNASSIGN_SPOOL] error: no assignment found for AMS {ams_id} tray {tray_id}\n"
            await ctx.log(ctx.run_id, msg)
            return FunctionResult(ok=False, message=msg)

        await db.delete(assignment)
        await db.commit()

    try:
        from backend.app.core.websocket import ws_manager

        await ws_manager.broadcast(
            {
                "type": "spool_assignment_changed",
                "printer_id": ctx.printer_id,
                "ams_id": ams_id,
                "tray_id": tray_id,
            }
        )
    except Exception:  # noqa: BLE001
        pass

    msg = f"[UNASSIGN_SPOOL] ok: cleared AMS {ams_id} tray {tray_id}\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=True, message=msg)


@macro_function(
    name="assignments",
    description="Inject current AMS spool assignments for the target printer into Jinja2 context.",
    context_var="assignments",
    requires_printer=True,
    allowed_in_embed=True,
)
async def _assignments_ctx(ctx: FunctionContext) -> FunctionResult:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from backend.app.core.database import async_session
    from backend.app.models.spool_assignment import SpoolAssignment

    async with async_session() as db:
        result = await db.execute(
            select(SpoolAssignment)
            .options(selectinload(SpoolAssignment.spool))
            .where(SpoolAssignment.printer_id == ctx.printer_id)
        )
        rows = list(result.scalars().all())

    value = [
        {
            "ams_id": a.ams_id,
            "tray_id": a.tray_id,
            "spool_id": a.spool_id,
            "material": a.spool.material if a.spool else None,
            "color": a.spool.rgba if a.spool else None,
            "brand": a.spool.brand if a.spool else None,
        }
        for a in rows
    ]
    return FunctionResult(ok=True, value=value)

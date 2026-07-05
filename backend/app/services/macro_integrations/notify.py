"""Macro functions: notifications and timing utilities."""

import asyncio

from backend.app.services.macro_functions import ArgSpec, FunctionContext, FunctionResult, macro_function


@macro_function(
    name="NOTIFY",
    description="Send a notification via all configured providers.",
    args={
        "message": ArgSpec("Text to send", required=True),
    },
    allowed_in_embed=True,
)
async def _notify(ctx: FunctionContext) -> FunctionResult:
    from sqlalchemy import select as sa_select

    from backend.app.core.database import async_session
    from backend.app.models.notification import NotificationProvider
    from backend.app.services.notification_service import notification_service

    message = ctx.flags.get("message", ctx.flags.get("m", "")).strip("'\"")
    if not message:
        msg = "[NOTIFY] error: --message is required\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    try:
        async with async_session() as db:
            result = await db.execute(sa_select(NotificationProvider).where(NotificationProvider.enabled.is_(True)))
            providers = list(result.scalars().all())
            if not providers:
                msg = "[NOTIFY] ok: no enabled providers configured (notification skipped)\n"
                await ctx.log(ctx.run_id, msg)
                return FunctionResult(ok=True, message=msg)

            await notification_service._send_to_providers(
                providers,
                title="Macro Notification",
                message=message,
                db=db,
                event_type="macro_notify",
            )
    except Exception as exc:  # noqa: BLE001
        msg = f"[NOTIFY] error: dispatch failed — {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    msg = f"[NOTIFY] ok: sent to {len(providers)} provider(s) — {message!r}\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=True, message=msg)


@macro_function(
    name="WAIT",
    description="Pause execution for N seconds (max 300).",
    args={
        "seconds": ArgSpec("Duration in seconds (>0, max 300)", required=True, default="1"),
    },
    allowed_in_embed=True,
)
async def _wait(ctx: FunctionContext) -> FunctionResult:
    try:
        seconds = float(ctx.flags.get("seconds", ctx.flags.get("s", "1")))
    except ValueError as exc:
        msg = f"[WAIT] invalid args: {exc}\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    if seconds <= 0:
        msg = f"[WAIT] invalid --seconds={seconds} (must be > 0)\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    seconds = min(seconds, 300)
    await ctx.log(ctx.run_id, f"[WAIT] sleeping {seconds}s\n")
    await asyncio.sleep(seconds)
    msg = f"[WAIT] ok: resumed after {seconds}s\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=True, message=msg)

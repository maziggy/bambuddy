"""Macro integrations: sub-macro invocation via MACRO command."""

from backend.app.services.macro_functions import ArgSpec, FunctionContext, FunctionResult, macro_function


@macro_function(
    name="MACRO",
    description="Run another macro by name as an inline sub-macro.",
    args={
        "name": ArgSpec("Name of the macro to run", required=True),
    },
    requires_printer=False,
    allowed_in_embed=False,
)
async def _run_macro_command(ctx: FunctionContext) -> FunctionResult:
    name = ctx.flags.get("name", "").strip("'\"")
    if not name:
        msg = "[MACRO] error: --name is required\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    runner = ctx._runner
    if runner is None:
        msg = "[MACRO] error: sub-macro calls are not available in this context\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    call_stack = ctx._call_stack
    if name in call_stack:
        msg = f"[MACRO] error: cycle detected — {name} is already in the call stack\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    await runner._run_sub_macro(
        name,
        ctx.printer_id,
        call_stack | frozenset({name}),
        run_id=ctx.run_id,
        log_fn=ctx.log,
        allow_printer_commands=ctx.allow_printer_commands,
    )
    return FunctionResult(ok=True, message=f"[MACRO] ok: ran sub-macro '{name}'\n")

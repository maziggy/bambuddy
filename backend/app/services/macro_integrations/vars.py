"""Macro integrations: persistent variable storage (SET_VAR / DELETE_VAR / vars context)."""

from backend.app.services.macro_functions import ArgSpec, FunctionContext, FunctionResult, macro_function


@macro_function(
    name="SET_VAR",
    description="Persist a value under a key. Survives across macro runs and restarts.",
    args={
        "key": ArgSpec("Variable name", required=True),
        "value": ArgSpec("Value to store (any JSON-serialisable type)", required=True),
        "ttl": ArgSpec("Time-to-live in seconds (omit for permanent)", default=None),
        "scope": ArgSpec(
            "'macro' to isolate per-macro, 'global' to share across all macros (default: global)", default="global"
        ),
    },
    allowed_in_embed=True,
)
async def _set_var(ctx: FunctionContext) -> FunctionResult:
    import json
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from backend.app.core.database import async_session
    from backend.app.models.macro_var import MacroVar

    key = ctx.flags.get("key", "").strip("'\"")
    raw_value = ctx.flags.get("value", "").strip("'\"")
    ttl_raw = ctx.flags.get("ttl")
    scope = ctx.flags.get("scope", "global").strip("'\"").lower()

    if not key:
        msg = "[SET_VAR] --key is required\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    # Encode value: try JSON parse first so numbers/bools/lists work, else store as string
    try:
        value_json = json.dumps(json.loads(raw_value))
    except (json.JSONDecodeError, ValueError):
        value_json = json.dumps(raw_value)

    expires_at = None
    if ttl_raw:
        try:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(ttl_raw))
        except ValueError:
            msg = f"[SET_VAR] invalid --ttl value: {ttl_raw!r}\n"
            await ctx.log(ctx.run_id, msg)
            return FunctionResult(ok=False, message=msg)

    macro_id: int | None = None
    if scope == "macro":
        # Pull macro_id from the MacroRun row if we have a run_id
        if ctx.run_id:
            async with async_session() as db:
                from backend.app.models.macro import MacroRun

                result = await db.execute(select(MacroRun).where(MacroRun.id == ctx.run_id))
                run = result.scalar_one_or_none()
                macro_id = run.macro_id if run else None

    async with async_session() as db:
        # Upsert: find existing row with same key + scope
        q = select(MacroVar).where(MacroVar.key == key)
        if macro_id is not None:
            q = q.where(MacroVar.macro_id == macro_id)
        else:
            q = q.where(MacroVar.macro_id.is_(None))

        result = await db.execute(q)
        existing = result.scalar_one_or_none()

        if existing:
            existing.value_json = value_json
            existing.expires_at = expires_at
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(
                MacroVar(
                    key=key,
                    value_json=value_json,
                    macro_id=macro_id,
                    expires_at=expires_at,
                )
            )

        await db.commit()

    ttl_str = f" (ttl={ttl_raw}s)" if ttl_raw else ""
    scope_str = f"macro:{macro_id}" if macro_id else "global"
    msg = f"[SET_VAR] {key}={raw_value!r} [{scope_str}]{ttl_str}\n"
    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=True, message=msg)


@macro_function(
    name="DELETE_VAR",
    description="Delete a persisted variable by key.",
    args={
        "key": ArgSpec("Variable name to delete", required=True),
        "scope": ArgSpec("'macro' or 'global' — must match the scope used when setting", default="global"),
    },
    allowed_in_embed=True,
)
async def _delete_var(ctx: FunctionContext) -> FunctionResult:
    from sqlalchemy import select

    from backend.app.core.database import async_session
    from backend.app.models.macro_var import MacroVar

    key = ctx.flags.get("key", "").strip("'\"")
    scope = ctx.flags.get("scope", "global").strip("'\"").lower()

    if not key:
        msg = "[DELETE_VAR] --key is required\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    macro_id: int | None = None
    if scope == "macro" and ctx.run_id:
        from sqlalchemy import select as _sel

        async with async_session() as db:
            from backend.app.models.macro import MacroRun

            result = await db.execute(_sel(MacroRun).where(MacroRun.id == ctx.run_id))
            run = result.scalar_one_or_none()
            macro_id = run.macro_id if run else None

    async with async_session() as db:
        q = select(MacroVar).where(MacroVar.key == key)
        q = q.where(MacroVar.macro_id == macro_id) if macro_id is not None else q.where(MacroVar.macro_id.is_(None))
        result = await db.execute(q)
        var = result.scalar_one_or_none()
        if var:
            await db.delete(var)
            await db.commit()
            msg = f"[DELETE_VAR] deleted {key!r}\n"
        else:
            msg = f"[DELETE_VAR] key {key!r} not found (no-op)\n"

    await ctx.log(ctx.run_id, msg)
    return FunctionResult(ok=True, message=msg)


@macro_function(
    name="vars",
    description="Inject all non-expired macro vars into Jinja2 context as a dict.",
    context_var="vars",
    allowed_in_embed=True,
)
async def _vars_ctx(ctx: FunctionContext) -> FunctionResult:
    import json
    from datetime import datetime, timezone

    from sqlalchemy import or_, select

    from backend.app.core.database import async_session
    from backend.app.models.macro_var import MacroVar

    now = datetime.now(timezone.utc)

    # Resolve macro_id for scoped vars
    macro_id: int | None = None
    if ctx.run_id:
        async with async_session() as db:
            from backend.app.models.macro import MacroRun

            result = await db.execute(select(MacroRun).where(MacroRun.id == ctx.run_id))
            run = result.scalar_one_or_none()
            macro_id = run.macro_id if run else None

    async with async_session() as db:
        # Load global vars + this macro's scoped vars
        q = (
            select(MacroVar)
            .where(
                or_(MacroVar.macro_id.is_(None), MacroVar.macro_id == macro_id)
                if macro_id
                else MacroVar.macro_id.is_(None)
            )
            .where(or_(MacroVar.expires_at.is_(None), MacroVar.expires_at > now))
        )
        result = await db.execute(q)
        rows = result.scalars().all()

    # Scoped vars shadow global vars with the same key
    merged: dict[str, object] = {}
    globals_: dict[str, object] = {}
    scoped: dict[str, object] = {}
    for row in rows:
        try:
            val = json.loads(row.value_json)
        except (json.JSONDecodeError, ValueError):
            val = row.value_json
        if row.macro_id is None:
            globals_[row.key] = val
        else:
            scoped[row.key] = val

    merged = {**globals_, **scoped}
    return FunctionResult(ok=True, value=merged)

"""Variant groups — one job, several sliced files (#671 / #2570).

A user with more than one printer model slices the same job once per model. The
files are unrelated as far as the library is concerned: different names,
different metadata, often uploaded separately after being sliced in Bambu Studio.
A variant group is the user telling Bambuddy that they are interchangeable.

Two features consume that statement from opposite ends:

* the print queue picks the printer and needs the matching file (#671)
* the File Manager's print action has the printer already and needs the same
  match (#2570)

The group itself stores no model information. Each member's target model comes
from its own ``sliced_for_model``, parsed out of the 3MF, so a group can never
disagree with the files in it. A legacy file that declares no model may name one
explicitly, because there is nothing else to go on.

Invariants enforced here rather than in the database, because they are about
meaning rather than shape:

* **Two members minimum.** A group of one expresses no choice. Removing members
  down to one dissolves the group rather than leaving a stub that does nothing.
* **One member per model.** Two files sliced for the same printer are not
  alternatives — the resolver would have no basis to prefer one, so an
  arbitrary pick would look like a bug the first time the wrong quality preset
  came out.
* **Members must be sliced and must resolve to a model.** An unsliced .3mf can
  never be dispatched, so it cannot be a candidate.
* **A file belongs to at most one group**, which the schema already guarantees;
  this layer turns the resulting overwrite into an explicit 409.

Permissions follow library_tags.py: mutations need LIBRARY_UPDATE_ALL /
LIBRARY_UPDATE_OWN, reads need LIBRARY_READ_ALL / LIBRARY_READ_OWN, and an
``*_OWN`` caller only ever sees or touches files they created.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_ownership_permission
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.library import FileVariantGroup, LibraryFile
from backend.app.models.user import User
from backend.app.schemas.library import (
    VariantGroupCreate,
    VariantGroupMemberRequest,
    VariantGroupMemberResponse,
    VariantGroupResponse,
    VariantGroupUpdate,
)
from backend.app.utils.printer_models import normalize_printer_model, normalize_printer_model_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library/variant-groups", tags=["library-variants"])

# File types that can actually be sent to a printer. A source .3mf or an .stl
# has no G-code and no sliced_for_model, so it is never a dispatch candidate.
_PRINTABLE_TYPES = ("gcode.3mf", "gcode")


def normalize_model_name(raw: str | None) -> str | None:
    """Normalize any spelling of a printer model to its short name.

    Internal codes are resolved **first**. ``normalize_printer_model`` returns
    unknown input unchanged rather than None, so an ``x or y`` chain in the other
    order never reaches the code map and leaves "O1C" as "O1C" — which then
    matches no printer row and leaves the job waiting forever. Running the code
    map first is a no-op for every non-code input.
    """
    if not raw:
        return None
    return normalize_printer_model(normalize_printer_model_id(raw) or raw) or raw


def resolve_variant_model(lib_file: LibraryFile, explicit: str | None = None) -> str | None:
    """Normalized model a file will be dispatched to, or None if unknowable.

    Precedence: the caller's explicit choice for this request, then the durable
    override stored on the file, then what the 3MF itself declares. The override
    exists because a file imported before Bambuddy parsed ``sliced_for_model``
    declares nothing, and without a way to say so it could never be grouped.
    It is kept separate from ``file_metadata`` so a user's assertion is never
    mistaken for something parsed out of the file.
    """
    raw = explicit or lib_file.variant_target_model or (lib_file.file_metadata or {}).get("sliced_for_model")
    return normalize_model_name(raw)


async def _load_files(
    db: AsyncSession,
    file_ids: list[int],
    user: User | None,
    can_access_all: bool,
) -> dict[int, LibraryFile]:
    """Fetch the caller's visible, untrashed files by id."""
    query = LibraryFile.active().where(LibraryFile.id.in_(file_ids))
    if user is not None and not can_access_all:
        query = query.where(LibraryFile.created_by_id == user.id)
    rows = (await db.execute(query)).scalars().all()
    return {f.id: f for f in rows}


def _validate_member(lib_file: LibraryFile, explicit_model: str | None) -> str:
    """Return the member's model, or raise the reason it cannot be one."""
    if lib_file.file_type not in _PRINTABLE_TYPES:
        raise HTTPException(
            400,
            f"{lib_file.filename} is not a sliced file — only sliced output can be a print variant",
        )
    model = resolve_variant_model(lib_file, explicit_model)
    if not model:
        raise HTTPException(
            400,
            f"{lib_file.filename} does not say which printer it was sliced for — set its target model explicitly",
        )
    if explicit_model:
        # Persist the user's answer, normalized. The group stores no model data
        # of its own, so without this the choice would last exactly one request
        # and the member would read back with no model at all.
        lib_file.variant_target_model = model
    return model


async def _group_response(db: AsyncSession, group: FileVariantGroup) -> VariantGroupResponse:
    members = (
        (
            await db.execute(
                LibraryFile.active()
                .where(LibraryFile.variant_group_id == group.id)
                .order_by(LibraryFile.variant_position, LibraryFile.id)
            )
        )
        .scalars()
        .all()
    )
    return VariantGroupResponse(
        id=group.id,
        name=group.name,
        members=[
            VariantGroupMemberResponse(
                library_file_id=f.id,
                filename=f.filename,
                # Members were validated on the way in, but a file whose metadata
                # was rewritten since then should not blow up a read.
                target_model=resolve_variant_model(f) or "",
                position=f.variant_position,
            )
            for f in members
        ],
    )


async def _get_group_or_404(db: AsyncSession, group_id: int) -> FileVariantGroup:
    group = (await db.execute(select(FileVariantGroup).where(FileVariantGroup.id == group_id))).scalar_one_or_none()
    if not group:
        raise HTTPException(404, "Variant group not found")
    return group


async def _dissolve_if_too_small(db: AsyncSession, group: FileVariantGroup) -> bool:
    """Delete the group when fewer than two members remain.

    A one-member group is not a choice, and leaving one behind would let the
    queue create a cross-model item with a single candidate that silently
    behaves like an ordinary job. Returns True when the group was dissolved.
    """
    remaining = (await db.execute(select(LibraryFile).where(LibraryFile.variant_group_id == group.id))).scalars().all()
    if len(remaining) >= 2:
        return False
    for lib_file in remaining:
        lib_file.variant_group_id = None
        lib_file.variant_position = 0
    await db.delete(group)
    return True


@router.post("", response_model=VariantGroupResponse, status_code=201)
@router.post("/", response_model=VariantGroupResponse, status_code=201)
async def create_variant_group(
    payload: VariantGroupCreate,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
) -> VariantGroupResponse:
    """Group files as variants of one job, in priority order."""
    user, can_update_all = auth_result

    file_ids = [m.library_file_id for m in payload.members]
    if len(set(file_ids)) != len(file_ids):
        raise HTTPException(400, "The same file cannot appear twice in a variant group")

    files = await _load_files(db, file_ids, user, can_update_all)
    missing = [fid for fid in file_ids if fid not in files]
    if missing:
        raise HTTPException(404, f"Library file not found: {missing[0]}")

    already_grouped = [files[fid].filename for fid in file_ids if files[fid].variant_group_id is not None]
    if already_grouped:
        raise HTTPException(409, f"{already_grouped[0]} already belongs to a variant group")

    models: dict[str, str] = {}
    for member in payload.members:
        lib_file = files[member.library_file_id]
        model = _validate_member(lib_file, member.target_model)
        if model in models:
            raise HTTPException(
                400,
                f"{lib_file.filename} and {models[model]} are both sliced for {model} — "
                "variants must target different printers",
            )
        models[model] = lib_file.filename

    group = FileVariantGroup(
        name=payload.name or files[file_ids[0]].filename,
        created_by_id=user.id if user else None,
    )
    db.add(group)
    await db.flush()

    for position, fid in enumerate(file_ids):
        files[fid].variant_group_id = group.id
        files[fid].variant_position = position

    await db.commit()
    logger.info("Created variant group %s with %d members", group.id, len(file_ids))
    return await _group_response(db, group)


@router.get("/by-file/{file_id}", response_model=VariantGroupResponse)
async def get_group_for_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
) -> VariantGroupResponse:
    """The group a file belongs to.

    Both consumers start from a file rather than a group id: the print modal
    knows which file the user clicked, and the queue-create flow knows which
    file was selected.
    """
    user, can_read_all = auth_result
    files = await _load_files(db, [file_id], user, can_read_all)
    lib_file = files.get(file_id)
    if not lib_file:
        raise HTTPException(404, "Library file not found")
    if lib_file.variant_group_id is None:
        raise HTTPException(404, "File is not part of a variant group")
    return await _group_response(db, await _get_group_or_404(db, lib_file.variant_group_id))


@router.get("/{group_id}", response_model=VariantGroupResponse)
async def get_variant_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
) -> VariantGroupResponse:
    return await _group_response(db, await _get_group_or_404(db, group_id))


@router.patch("/{group_id}", response_model=VariantGroupResponse)
async def update_variant_group(
    group_id: int,
    payload: VariantGroupUpdate,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
) -> VariantGroupResponse:
    """Rename the group, re-order its members, or both.

    Re-ordering is how the user says which printer they would rather have when
    both are free, so it must be an explicit full ordering — a partial list
    would leave the rest in an order nobody chose.
    """
    user, can_update_all = auth_result
    group = await _get_group_or_404(db, group_id)

    if payload.name is not None:
        group.name = payload.name

    if payload.member_file_ids is not None:
        current = (
            (await db.execute(select(LibraryFile).where(LibraryFile.variant_group_id == group.id))).scalars().all()
        )
        if set(payload.member_file_ids) != {f.id for f in current}:
            raise HTTPException(400, "member_file_ids must list exactly the group's current members")
        files = await _load_files(db, payload.member_file_ids, user, can_update_all)
        if len(files) != len(payload.member_file_ids):
            raise HTTPException(404, "Library file not found")
        for position, fid in enumerate(payload.member_file_ids):
            files[fid].variant_position = position

    await db.commit()
    return await _group_response(db, group)


@router.post("/{group_id}/members", response_model=VariantGroupResponse)
async def add_variant_group_member(
    payload: VariantGroupMemberRequest,
    group_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
) -> VariantGroupResponse:
    """Attach another slice to an existing group.

    This is the common real case: the H2S version was queued last week, the H2C
    version was sliced today.
    """
    user, can_update_all = auth_result
    group = await _get_group_or_404(db, group_id)

    files = await _load_files(db, [payload.library_file_id], user, can_update_all)
    lib_file = files.get(payload.library_file_id)
    if not lib_file:
        raise HTTPException(404, "Library file not found")
    if lib_file.variant_group_id == group.id:
        raise HTTPException(409, f"{lib_file.filename} is already in this group")
    if lib_file.variant_group_id is not None:
        raise HTTPException(409, f"{lib_file.filename} already belongs to a variant group")

    model = _validate_member(lib_file, payload.target_model)

    existing = (
        (
            await db.execute(
                LibraryFile.active()
                .where(LibraryFile.variant_group_id == group.id)
                .order_by(LibraryFile.variant_position, LibraryFile.id)
            )
        )
        .scalars()
        .all()
    )
    for other in existing:
        if resolve_variant_model(other) == model:
            raise HTTPException(
                400,
                f"{lib_file.filename} and {other.filename} are both sliced for {model} — "
                "variants must target different printers",
            )

    lib_file.variant_group_id = group.id
    lib_file.variant_position = len(existing)
    await db.commit()
    return await _group_response(db, group)


@router.delete("/{group_id}/members/{file_id}", response_model=None, status_code=204)
async def remove_variant_group_member(
    group_id: int,
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
) -> None:
    """Drop one file out of a group; the file itself is untouched."""
    user, can_update_all = auth_result
    group = await _get_group_or_404(db, group_id)

    files = await _load_files(db, [file_id], user, can_update_all)
    lib_file = files.get(file_id)
    if not lib_file or lib_file.variant_group_id != group.id:
        raise HTTPException(404, "File is not a member of this group")

    lib_file.variant_group_id = None
    lib_file.variant_position = 0
    await db.flush()
    await _dissolve_if_too_small(db, group)
    await db.commit()


@router.delete("/{group_id}", response_model=None, status_code=204)
async def delete_variant_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
) -> None:
    """Ungroup the files. The files themselves are kept — every one of them is
    independently printable, which is the whole reason they were grouped."""
    group = await _get_group_or_404(db, group_id)
    members = (await db.execute(select(LibraryFile).where(LibraryFile.variant_group_id == group.id))).scalars().all()
    for lib_file in members:
        lib_file.variant_group_id = None
        lib_file.variant_position = 0
    await db.delete(group)
    await db.commit()

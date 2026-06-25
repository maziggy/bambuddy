"""Library tag catalog + per-file assignment endpoints (#1268).

Tags are global cross-cutting labels for library files — one catalog per
install, no per-user partitioning. Designed as the orthogonal complement to
folders: folders express hierarchy, tags express attributes ("toy",
"kid-safe", "petg-only"). The reporter (#1268) and at least one upvoter
asked for them; the design decisions were locked with @maziggy:

* tags apply to files only (folders already express hierarchy)
* the tag filter on the file list intentionally IGNORES the selected folder
  so "show me every toy regardless of where it lives" works (multi-tag = AND)
* bulk-tagging from the multi-select toolbar ships in v1
* no auto-tags from 3MF metadata; user-authored only
* no color, no icon — label-only chips

Permission model:

* **Catalog mutations** (POST / PATCH / DELETE on ``/library/tags``) require
  :attr:`Permission.LIBRARY_UPDATE_ALL` because the catalog is global —
  ownership-aware update isn't meaningful for a row no user owns.
* **Bulk assignment** is gated by the existing
  :attr:`Permission.LIBRARY_UPDATE_ALL` / :attr:`Permission.LIBRARY_UPDATE_OWN`
  pair so a ``*_OWN`` user can only re-tag files they created.
* **GET** is gated by :attr:`Permission.LIBRARY_READ_ALL` /
  :attr:`Permission.LIBRARY_READ_OWN` — ``*_OWN`` callers see every catalog
  row (it's just labels), but ``file_count`` is filtered to their own files.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_ownership_permission, require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.library import LibraryFile, LibraryFileTag, LibraryTag
from backend.app.models.user import User
from backend.app.schemas.library import (
    TagBulkAssignRequest,
    TagBulkAssignResponse,
    TagCreate,
    TagResponse,
    TagUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library/tags", tags=["library-tags"])


def _name_key(name: str) -> str:
    """Case-insensitive uniqueness key — LOWER(TRIM(name)).

    Mirrors the same convention used by Locations (#1505) so the catalog
    can't end up with "Toys" + "toys" + " TOYS " as separate rows. Empty
    string after stripping is rejected by Pydantic min_length, so this
    helper trusts its input.
    """
    return name.strip().lower()


@router.get("", response_model=list[TagResponse])
@router.get("/", response_model=list[TagResponse])
async def list_tags(
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
) -> list[TagResponse]:
    """List every tag in the catalog with the count of files using it.

    Catalog rows are global, so a ``read_own`` caller still sees every tag
    name — that's just the chip set the rest of the UI offers. But the
    ``file_count`` projection is filtered to their own files so the number
    matches what they'd see when they filter the listing by that tag.
    """
    user, can_read_all = auth_result

    # Count distinct file_ids per tag via the association table joined back
    # to LibraryFile so soft-deleted (trashed) files don't inflate the chip
    # counts shown in the management modal.
    file_filter = LibraryFile.deleted_at.is_(None)
    if user is not None and not can_read_all:
        file_filter = file_filter & (LibraryFile.created_by_id == user.id)

    count_subq = (
        select(
            LibraryFileTag.tag_id.label("tag_id"),
            func.count(distinct(LibraryFile.id)).label("file_count"),
        )
        .join(LibraryFile, LibraryFile.id == LibraryFileTag.file_id)
        .where(file_filter)
        .group_by(LibraryFileTag.tag_id)
        .subquery()
    )

    query = (
        select(LibraryTag, func.coalesce(count_subq.c.file_count, 0))
        .outerjoin(count_subq, count_subq.c.tag_id == LibraryTag.id)
        .order_by(func.lower(LibraryTag.name))
    )
    rows = (await db.execute(query)).all()
    return [
        TagResponse(
            id=t.id,
            name=t.name,
            file_count=int(count),
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t, count in rows
    ]


@router.post("", response_model=TagResponse, status_code=201)
@router.post("/", response_model=TagResponse, status_code=201)
async def create_tag(
    payload: TagCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPDATE_ALL)),
) -> TagResponse:
    """Create a tag. Case-insensitive dup → 409."""
    key = _name_key(payload.name)
    tag = LibraryTag(name=payload.name.strip(), name_key=key)
    db.add(tag)
    try:
        await db.commit()
    except IntegrityError:
        # Race condition or actual dup — re-fetch the existing row so the
        # caller can recover by reading the id from the 409 detail string
        # if they want to. The body is consistent regardless of cause.
        await db.rollback()
        raise HTTPException(status_code=409, detail="Tag with this name already exists") from None
    await db.refresh(tag)
    return TagResponse(id=tag.id, name=tag.name, file_count=0, created_at=tag.created_at, updated_at=tag.updated_at)


@router.patch("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: int,
    payload: TagUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPDATE_ALL)),
) -> TagResponse:
    """Rename a tag. Case-insensitive dup → 409 (own-name no-op is allowed)."""
    tag = (await db.execute(select(LibraryTag).where(LibraryTag.id == tag_id))).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    new_key = _name_key(payload.name)
    if new_key != tag.name_key:
        # Pre-check so the user gets a clean 409 instead of an IntegrityError
        # that we'd then have to translate. The post-commit IntegrityError
        # branch still catches the concurrent-create race.
        existing = (await db.execute(select(LibraryTag).where(LibraryTag.name_key == new_key))).scalar_one_or_none()
        if existing is not None and existing.id != tag.id:
            raise HTTPException(status_code=409, detail="Tag with this name already exists")
    tag.name = payload.name.strip()
    tag.name_key = new_key
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Tag with this name already exists") from None
    await db.refresh(tag)

    # Re-count files for the projection so the caller's modal shows the
    # right number after the rename.
    file_count = (
        await db.execute(select(func.count(LibraryFileTag.file_id)).where(LibraryFileTag.tag_id == tag.id))
    ).scalar_one()
    return TagResponse(
        id=tag.id,
        name=tag.name,
        file_count=int(file_count or 0),
        created_at=tag.created_at,
        updated_at=tag.updated_at,
    )


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPDATE_ALL)),
) -> None:
    """Delete a tag. Association rows ON DELETE CASCADE — files are untouched."""
    tag = (await db.execute(select(LibraryTag).where(LibraryTag.id == tag_id))).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    await db.delete(tag)
    await db.commit()


@router.post("/bulk-assign", response_model=TagBulkAssignResponse)
async def bulk_assign(
    payload: TagBulkAssignRequest,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
) -> TagBulkAssignResponse:
    """Add / remove / replace tag assignments across multiple files.

    Implemented as set-style operations against the association table —
    cheaper than re-doing the M2M list per file and idempotent on retries.
    A caller without ``*_UPDATE_ALL`` can only modify files they created
    (per the existing ownership pair); silently-skipped files are
    excluded from the response counts so the UI can detect partial
    application.
    """
    user, can_update_all = auth_result

    # Resolve the file scope FIRST — anything not visible to the caller is
    # quietly dropped, so a malicious or buggy client can't tag files it
    # doesn't own. This is the same posture as bulk-delete in
    # library_trash.py.
    file_q = select(LibraryFile.id).where(
        LibraryFile.id.in_(payload.file_ids),
        LibraryFile.deleted_at.is_(None),
    )
    if user is not None and not can_update_all:
        file_q = file_q.where(LibraryFile.created_by_id == user.id)
    file_ids = list((await db.execute(file_q)).scalars().all())
    if not file_ids:
        return TagBulkAssignResponse(files_updated=0, associations_added=0, associations_removed=0)

    # Validate tag ids exist. Unknown tag_ids are silently dropped from
    # the operation rather than raising — matches the bulk-trash shape
    # and keeps a partial-success result usable.
    tag_ids: list[int] = []
    if payload.tag_ids:
        tag_ids = list(
            (await db.execute(select(LibraryTag.id).where(LibraryTag.id.in_(payload.tag_ids)))).scalars().all()
        )

    added = 0
    removed = 0

    if payload.action == "add":
        if not tag_ids:
            return TagBulkAssignResponse(files_updated=0, associations_added=0, associations_removed=0)
        # Insert (file_id, tag_id) for every pair that doesn't already exist.
        # We could use INSERT ... ON CONFLICT DO NOTHING for Postgres + SQLite
        # 3.24+ but the explicit pre-check keeps the SQLAlchemy core dialect
        # neutral and lets us count what actually got added.
        existing = set(
            (
                await db.execute(
                    select(LibraryFileTag.file_id, LibraryFileTag.tag_id).where(
                        LibraryFileTag.file_id.in_(file_ids),
                        LibraryFileTag.tag_id.in_(tag_ids),
                    )
                )
            ).all()
        )
        to_insert = [
            {"file_id": fid, "tag_id": tid} for fid in file_ids for tid in tag_ids if (fid, tid) not in existing
        ]
        if to_insert:
            await db.execute(LibraryFileTag.__table__.insert(), to_insert)
            added = len(to_insert)
    elif payload.action == "remove":
        if not tag_ids:
            return TagBulkAssignResponse(files_updated=0, associations_added=0, associations_removed=0)
        result = await db.execute(
            delete(LibraryFileTag).where(
                LibraryFileTag.file_id.in_(file_ids),
                LibraryFileTag.tag_id.in_(tag_ids),
            )
        )
        removed = int(result.rowcount or 0)
    elif payload.action == "replace":
        # Strip everything currently on these files, then INSERT the new set.
        del_result = await db.execute(delete(LibraryFileTag).where(LibraryFileTag.file_id.in_(file_ids)))
        removed = int(del_result.rowcount or 0)
        if tag_ids:
            await db.execute(
                LibraryFileTag.__table__.insert(),
                [{"file_id": fid, "tag_id": tid} for fid in file_ids for tid in tag_ids],
            )
            added = len(file_ids) * len(tag_ids)

    await db.commit()
    return TagBulkAssignResponse(
        files_updated=len(file_ids),
        associations_added=added,
        associations_removed=removed,
    )

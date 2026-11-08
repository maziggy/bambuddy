"""Saved custom label sheets (#3296).

A saved template is a named sheet layout, listed in the print dialog for
everyone on the install. Printing does not read this table: the dialog sends
the layout itself with the print request, so anyone who can print can use a
saved template or a one-off custom sheet, and only saving, editing and
deleting need ``inventory:update`` (the same as storage locations).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.label_template import LabelTemplate
from backend.app.models.user import User
from backend.app.schemas.label_template import (
    LabelTemplateIn,
    LabelTemplateResponse,
    SheetLayoutResponse,
)
from backend.app.utils.natural_sort import natural_sort_key

router = APIRouter(prefix="/label-templates", tags=["labels"])

DUPLICATE_NAME = "A label template with this name already exists"

_LAYOUT_COLUMNS = tuple(SheetLayoutResponse.model_fields)


def _to_response(template: LabelTemplate) -> LabelTemplateResponse:
    return LabelTemplateResponse(
        id=template.id,
        name=template.name,
        layout=SheetLayoutResponse(**{col: getattr(template, col) for col in _LAYOUT_COLUMNS}),
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _apply(template: LabelTemplate, data: LabelTemplateIn) -> None:
    template.name = data.name
    template.name_key = data.name_key
    for col, value in data.layout.model_dump().items():
        setattr(template, col, value)


async def _name_taken(db: AsyncSession, name_key: str, exclude_id: int | None = None) -> bool:
    query = select(LabelTemplate.id).where(LabelTemplate.name_key == name_key)
    if exclude_id is not None:
        query = query.where(LabelTemplate.id != exclude_id)
    return (await db.execute(query)).first() is not None


async def _commit(db: AsyncSession, template: LabelTemplate) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        # Two saves under the same name at once: the unique index catches the
        # one the lookup above could not.
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME) from exc
    await db.refresh(template)


@router.get("", response_model=list[LabelTemplateResponse])
async def list_label_templates(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """All saved label templates, in natural name order."""
    result = await db.execute(select(LabelTemplate))
    templates = sorted(result.scalars().all(), key=lambda t: natural_sort_key(t.name))
    return [_to_response(t) for t in templates]


@router.post("", response_model=LabelTemplateResponse, status_code=201)
async def create_label_template(
    data: LabelTemplateIn,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Save a custom sheet layout under a name."""
    if await _name_taken(db, data.name_key):
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME)
    template = LabelTemplate()
    _apply(template, data)
    db.add(template)
    await _commit(db, template)
    return _to_response(template)


@router.put("/{template_id}", response_model=LabelTemplateResponse)
async def replace_label_template(
    template_id: int,
    data: LabelTemplateIn,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Rename a saved template or change its layout. The body is the whole
    template, as the dialog's form always holds all of it."""
    template = await db.get(LabelTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Label template not found")
    if await _name_taken(db, data.name_key, exclude_id=template_id):
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME)
    _apply(template, data)
    await _commit(db, template)
    return _to_response(template)


@router.delete("/{template_id}", status_code=204)
async def delete_label_template(
    template_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Delete a saved template. Labels already printed with it are unaffected."""
    template = await db.get(LabelTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Label template not found")
    await db.delete(template)
    await db.commit()
    return Response(status_code=204)

"""API routes for external sidebar links."""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.external_link import ExternalLink
from backend.app.models.user import User
from backend.app.schemas.external_link import (
    ExternalLinkCreate,
    ExternalLinkReorder,
    ExternalLinkResponse,
    ExternalLinkUpdate,
)

# Directory for storing custom icons
ICONS_DIR = app_settings.base_dir / "icons"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external-links", tags=["external-links"])


@router.get("/", response_model=list[ExternalLinkResponse])
async def list_external_links(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_READ),
):
    """List all external links ordered by sort_order."""
    result = await db.execute(select(ExternalLink).order_by(ExternalLink.sort_order, ExternalLink.id))
    links = result.scalars().all()
    return links


@router.post("/", response_model=ExternalLinkResponse)
async def create_external_link(
    link_data: ExternalLinkCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_CREATE),
):
    """Create a new external link."""
    # Get the highest sort_order to place new link at end
    result = await db.execute(select(ExternalLink).order_by(ExternalLink.sort_order.desc()).limit(1))
    last_link = result.scalar_one_or_none()
    next_order = (last_link.sort_order + 1) if last_link else 0

    link = ExternalLink(
        name=link_data.name,
        url=link_data.url,
        icon=link_data.icon,
        sort_order=next_order,
    )

    db.add(link)
    await db.commit()
    await db.refresh(link)

    logger.info(f"Created external link: {link.name} -> {link.url}")

    return link


@router.get("/{link_id}", response_model=ExternalLinkResponse)
async def get_external_link(
    link_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_READ),
):
    """Get a specific external link."""
    result = await db.execute(select(ExternalLink).where(ExternalLink.id == link_id))
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="External link not found")

    return link


@router.patch("/{link_id}", response_model=ExternalLinkResponse)
async def update_external_link(
    link_id: int,
    update_data: ExternalLinkUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_UPDATE),
):
    """Update an external link."""
    result = await db.execute(select(ExternalLink).where(ExternalLink.id == link_id))
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="External link not found")

    # Update only provided fields
    update_dict = update_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(link, key, value)

    await db.commit()
    await db.refresh(link)

    logger.info(f"Updated external link: {link.name}")

    return link


@router.delete("/{link_id}")
async def delete_external_link(
    link_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_DELETE),
):
    """Delete an external link."""
    result = await db.execute(select(ExternalLink).where(ExternalLink.id == link_id))
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="External link not found")

    name = link.name
    await db.delete(link)
    await db.commit()

    logger.info(f"Deleted external link: {name}")

    return {"message": f"External link '{name}' deleted"}


@router.put("/reorder", response_model=list[ExternalLinkResponse])
async def reorder_external_links(
    reorder_data: ExternalLinkReorder,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_UPDATE),
):
    """Update the sort order of external links."""
    # Update sort_order for each link based on position in the list
    for index, link_id in enumerate(reorder_data.ids):
        result = await db.execute(select(ExternalLink).where(ExternalLink.id == link_id))
        link = result.scalar_one_or_none()
        if link:
            link.sort_order = index

    await db.commit()

    # Return updated list
    result = await db.execute(select(ExternalLink).order_by(ExternalLink.sort_order, ExternalLink.id))
    links = result.scalars().all()

    logger.info(f"Reordered {len(reorder_data.ids)} external links")

    return links


@router.post("/{link_id}/icon", response_model=ExternalLinkResponse)
async def upload_icon(
    link_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_UPDATE),
):
    """Upload a custom icon for an external link."""
    result = await db.execute(select(ExternalLink).where(ExternalLink.id == link_id))
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="External link not found")

    # Validate file extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    # Create icons directory if it doesn't exist
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    # Delete old custom icon if exists
    if link.custom_icon:
        old_path = ICONS_DIR / link.custom_icon
        if old_path.exists():
            old_path.unlink()

    # Generate unique filename
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = ICONS_DIR / filename

    # Save file
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # Update link
    link.custom_icon = filename
    await db.commit()
    await db.refresh(link)

    logger.info(f"Uploaded custom icon for link {link.name}: {filename}")

    return link


@router.delete("/{link_id}/icon", response_model=ExternalLinkResponse)
async def delete_icon(
    link_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_UPDATE),
):
    """Delete the custom icon for an external link."""
    result = await db.execute(select(ExternalLink).where(ExternalLink.id == link_id))
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="External link not found")

    if link.custom_icon:
        filepath = ICONS_DIR / link.custom_icon
        if filepath.exists():
            filepath.unlink()
        link.custom_icon = None
        await db.commit()
        await db.refresh(link)
        logger.info(f"Deleted custom icon for link {link.name}")

    return link


@router.get("/{link_id}/icon")
async def get_icon(
    link_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the custom icon for an external link.

    Note: Unauthenticated - loaded via <img> tags which can't send auth headers.
    """
    result = await db.execute(select(ExternalLink).where(ExternalLink.id == link_id))
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="External link not found")

    if not link.custom_icon:
        raise HTTPException(status_code=404, detail="No custom icon set")

    filepath = ICONS_DIR / link.custom_icon
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Icon file not found")

    return FileResponse(filepath)

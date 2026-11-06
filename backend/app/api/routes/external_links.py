"""API routes for external sidebar links."""

import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    RequirePermissionIfAuthEnabled,
    ScopedCaller,
    require_media_token_permission,
    require_notification_send,
)
from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.external_link import ExternalLink, ExternalLinkBadge
from backend.app.models.user import User
from backend.app.schemas.external_link import (
    ExternalLinkBadgeSet,
    ExternalLinkCreate,
    ExternalLinkReorder,
    ExternalLinkResponse,
    ExternalLinkUpdate,
)
from backend.app.utils.local_time import utcnow_naive

# Directory for storing custom icons
ICONS_DIR = app_settings.base_dir / "icons"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external-links", tags=["external-links"])

# An app that stopped reporting (stopped, crashed) leaves no old number behind.
BADGE_FRESH_FOR = timedelta(minutes=10)


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _same_place(url: str) -> tuple[str, str, int | None, str]:
    """Scheme, host, port and path of an address. A login in it, the default
    port written out and a trailing slash don't count."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = None
    return scheme, (parts.hostname or "").lower(), port, parts.path.rstrip("/")


@router.get("/", response_model=list[ExternalLinkResponse])
async def list_external_links(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.EXTERNAL_LINKS_READ),
):
    """List all external links ordered by sort_order."""
    result = await db.execute(select(ExternalLink).order_by(ExternalLink.sort_order, ExternalLink.id))
    links = result.scalars().all()
    fresh_since = utcnow_naive() - BADGE_FRESH_FOR
    badges = {
        b.link_id: b.count
        for b in (await db.execute(select(ExternalLinkBadge).where(ExternalLinkBadge.updated_at >= fresh_since)))
        .scalars()
        .all()
    }
    return [
        ExternalLinkResponse.model_validate(link).model_copy(update={"badge_count": badges.get(link.id)})
        for link in links
    ]


@router.put("/badge")
async def set_badge(
    data: ExternalLinkBadgeSet,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: ScopedCaller = Depends(require_notification_send()),
):
    """Set the count on the sidebar links pointing at an app, e.g. Bambuddy
    Orders' To do. For the app itself: an API key needs the "Send
    notifications" permission, like for its messages. The count shows while
    the app keeps reporting it."""
    wanted = _same_place(data.url)
    links = [
        link for link in (await db.execute(select(ExternalLink))).scalars().all() if _same_place(link.url) == wanted
    ]
    if not links:
        raise HTTPException(status_code=404, detail="No sidebar link points at this address")
    now = utcnow_naive()
    for link in links:
        values = {"count": data.count, "updated_at": now}
        changed = cast(
            CursorResult,
            await db.execute(update(ExternalLinkBadge).where(ExternalLinkBadge.link_id == link.id).values(**values)),
        )
        if changed.rowcount:
            continue
        try:
            async with db.begin_nested():
                db.add(ExternalLinkBadge(link_id=link.id, **values))
        except IntegrityError:
            # Another report made the row in the meantime: update that one.
            await db.execute(update(ExternalLinkBadge).where(ExternalLinkBadge.link_id == link.id).values(**values))
    await db.commit()
    return {"links": len(links)}


@router.post("/", response_model=ExternalLinkResponse)
async def create_external_link(
    link_data: ExternalLinkCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
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

    logger.info("Created external link: %s -> %s", link.name, link.url)

    return link


@router.get("/{link_id}", response_model=ExternalLinkResponse)
async def get_external_link(
    link_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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

    logger.info("Updated external link: %s", link.name)

    return link


@router.delete("/{link_id}")
async def delete_external_link(
    link_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
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

    logger.info("Deleted external link: %s", name)

    return {"message": f"External link '{name}' deleted"}


@router.put("/reorder", response_model=list[ExternalLinkResponse])
async def reorder_external_links(
    reorder_data: ExternalLinkReorder,
    db: AsyncSession = Depends(get_db, scope="function"),
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

    logger.info("Reordered %s external links", len(reorder_data.ids))

    return links


@router.post("/{link_id}/icon", response_model=ExternalLinkResponse)
async def upload_icon(
    link_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
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

    logger.info("Uploaded custom icon for link %s: %s", link.name, filename)

    return link


@router.delete("/{link_id}/icon", response_model=ExternalLinkResponse)
async def delete_icon(
    link_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
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
        logger.info("Deleted custom icon for link %s", link.name)

    return link


@router.get("/{link_id}/icon")
async def get_icon(
    link_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = Depends(require_media_token_permission(Permission.EXTERNAL_LINKS_READ)),
):
    """Get the custom icon for an external link.

    Requires a media token query param (?token=xxx) when auth is enabled, and
    the same ``external_links:read`` every other read on this router takes.
    Previously it took the camera-stream token, so a sidebar icon was visible
    only to users who could also watch the printer camera (#3025).
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

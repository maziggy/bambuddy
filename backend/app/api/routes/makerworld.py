"""MakerWorld integration routes.

User pastes a MakerWorld URL → Bambuddy resolves it → shows plate list →
one-click import/print. The URL-paste flow covers the actual discovery
pattern (Reddit/YouTube/shared links) without needing to replicate
MakerWorld's whole search UI.

Search/browse endpoints are intentionally NOT exposed: the public-facing
``design/search`` endpoint returns empty results from server-originated
requests (see memory/makerworld-integration.md for the investigation).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import get_stored_token
from backend.app.api.routes.library import save_3mf_bytes_to_library
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.user import User
from backend.app.schemas.makerworld import (
    MakerWorldImportRequest,
    MakerWorldImportResponse,
    MakerWorldResolvedModel,
    MakerWorldResolveRequest,
    MakerWorldStatus,
)
from backend.app.services.makerworld import (
    MakerWorldAuthError,
    MakerWorldError,
    MakerWorldForbiddenError,
    MakerWorldNotFoundError,
    MakerWorldService,
    MakerWorldUnavailableError,
    MakerWorldUrlError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/makerworld", tags=["makerworld"])

_SOURCE_TYPE = "makerworld"


async def _build_service(db: AsyncSession, user: User | None) -> MakerWorldService:
    """Construct a per-request MakerWorldService seeded with the caller's
    stored Bambu Cloud bearer token when available.

    Mirrors ``cloud.build_authenticated_cloud`` — the token is entirely
    optional; anonymous calls (metadata, URL resolution) still work.
    """
    token, _email, _region = await get_stored_token(db, user)
    return MakerWorldService(auth_token=token)


def _canonical_url(model_id: int) -> str:
    """Build a stable source_url we use for dedupe.

    MakerWorld URLs vary (``/en/models/``, ``/de/models/``, slug suffixes,
    ``#profileId-`` fragments), so we canonicalise to the locale-free
    ID-only form so all variants of the same model dedupe to a single
    library row.
    """
    return f"https://makerworld.com/models/{model_id}"


def _map_service_error(exc: MakerWorldError) -> HTTPException:
    """Translate service exceptions into HTTP responses."""
    if isinstance(exc, MakerWorldUrlError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, MakerWorldAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, MakerWorldForbiddenError):
        # 403 forwards MakerWorld's own refusal message (content-gated,
        # region-locked, requires points, etc.) — UI surfaces it verbatim.
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, MakerWorldNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MakerWorldUnavailableError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=f"MakerWorld error: {exc}")


@router.get("/thumbnail")
async def proxy_thumbnail(
    url: str = Query(..., description="MakerWorld CDN image URL (makerworld.bblmw.com or public-cdn.bblmw.com)"),
):
    """Proxy a MakerWorld CDN thumbnail.

    The SPA's ``img-src`` CSP only allows ``'self' data: blob:`` — hotlinking
    from makerworld.bblmw.com is blocked. This endpoint refetches the image
    server-side and returns it with a long cache window.

    **Unauthenticated on purpose**: ``<img>`` tags can't send Authorization
    headers, so requiring a Bearer token here would break the whole feature
    (browsers would get 401 on every image, rendering as broken-image
    placeholders). The thumbnails being proxied are MakerWorld's *public*
    CDN — any visitor to makerworld.com can fetch them without auth — so no
    data is exposed. The SSRF guard inside ``fetch_thumbnail`` restricts
    the upstream host to the MakerWorld CDN allowlist, so this can't be
    abused as a generic open proxy.

    URLs are content-addressable (filename contains a hash), so the
    aggressive ``immutable`` cache-control is safe.
    """
    service = MakerWorldService()
    try:
        payload, content_type = await service.fetch_thumbnail(url)
    except MakerWorldError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    return Response(
        content=payload,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )


@router.get("/status", response_model=MakerWorldStatus)
async def get_status(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.MAKERWORLD_VIEW),
):
    """Report whether the caller can import 3MFs (needs a Bambu Cloud token)."""
    token, _email, _region = await get_stored_token(db, current_user)
    has_token = bool(token)
    return MakerWorldStatus(has_cloud_token=has_token, can_download=has_token)


@router.post("/resolve", response_model=MakerWorldResolvedModel)
async def resolve_url(
    body: MakerWorldResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.MAKERWORLD_VIEW),
):
    """Resolve a MakerWorld URL to full model metadata + plate list.

    The response also tells the caller which (if any) LibraryFile rows already
    exist for the same model URL, so the UI can show an "Already imported"
    badge and skip a redundant download.
    """
    try:
        model_id, profile_id = MakerWorldService.parse_url(body.url)
    except MakerWorldError as exc:
        raise _map_service_error(exc) from exc

    service = await _build_service(db, current_user)
    try:
        design = await service.get_design(model_id)
        instances_envelope = await service.get_design_instances(model_id)
    except MakerWorldError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    # MakerWorld's instances payload is ``{"total": N, "hits": [...]}``; callers
    # only care about the hits, and we normalise the null case to an empty list
    # so the frontend doesn't have to handle null vs [] both ways.
    instances = instances_envelope.get("hits") or []
    if not isinstance(instances, list):
        instances = []

    canonical = _canonical_url(model_id)
    existing_q = await db.execute(select(LibraryFile.id).where(LibraryFile.source_url == canonical))
    already_imported = [row[0] for row in existing_q.all()]

    return MakerWorldResolvedModel(
        model_id=model_id,
        profile_id=profile_id,
        design=design,
        instances=instances,
        already_imported_library_ids=already_imported,
    )


@router.post("/import", response_model=MakerWorldImportResponse)
async def import_instance(
    body: MakerWorldImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.MAKERWORLD_IMPORT),
):
    """Download a specific MakerWorld instance (plate configuration) and save
    the 3MF into the library.

    De-duplicates by canonicalised source URL — if the same MakerWorld model
    was imported before (any plate), that existing LibraryFile is returned and
    no new download happens.
    """
    if body.folder_id is not None:
        folder_q = await db.execute(select(LibraryFolder).where(LibraryFolder.id == body.folder_id))
        target_folder = folder_q.scalar_one_or_none()
        if target_folder is None:
            raise HTTPException(status_code=404, detail="Folder not found")
        if target_folder.is_external and target_folder.external_readonly:
            raise HTTPException(
                status_code=403,
                detail="Cannot import into a read-only external folder",
            )

    service = await _build_service(db, current_user)
    try:
        manifest = await service.get_instance_download(body.instance_id)
    except MakerWorldError as exc:
        await service.close()
        raise _map_service_error(exc) from exc

    signed_url = manifest.get("url")
    suggested_name = manifest.get("name") or f"makerworld-{body.instance_id}.3mf"
    if not signed_url or not isinstance(signed_url, str):
        await service.close()
        raise HTTPException(status_code=502, detail="MakerWorld did not return a download URL")

    # Resolve the profile so we know which parent model this instance belongs
    # to — that's what goes into source_url (canonical, locale-free) so all
    # plates of the same model dedupe together.
    try:
        profile = await service.get_profile(body.instance_id)
    except MakerWorldNotFoundError:
        # Some instances don't have a profile endpoint hit; fall back to the
        # instance id for source_url.
        profile = {}
    except MakerWorldError as exc:
        await service.close()
        raise _map_service_error(exc) from exc

    design_id = profile.get("designId")
    source_url = _canonical_url(int(design_id)) if isinstance(design_id, int) else None

    # Dedupe check upfront so we don't burn bandwidth re-downloading.
    if source_url:
        existing_q = await db.execute(select(LibraryFile).where(LibraryFile.source_url == source_url).limit(1))
        existing_row = existing_q.scalar_one_or_none()
        if existing_row is not None:
            await service.close()
            return MakerWorldImportResponse(
                library_file_id=existing_row.id,
                filename=existing_row.filename,
                was_existing=True,
            )

    try:
        file_bytes, download_filename = await service.download_3mf(signed_url)
    except MakerWorldError as exc:
        await service.close()
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    # Prefer the server-provided human-readable filename; the signed URL's
    # path ends in a UUID that's not meaningful to users.
    filename = suggested_name if suggested_name.endswith(".3mf") else download_filename

    library_file, was_existing = await save_3mf_bytes_to_library(
        db,
        file_bytes=file_bytes,
        filename=filename,
        folder_id=body.folder_id,
        source_type=_SOURCE_TYPE,
        source_url=source_url,
        owner_id=current_user.id if current_user else None,
    )

    return MakerWorldImportResponse(
        library_file_id=library_file.id,
        filename=library_file.filename,
        was_existing=was_existing,
    )

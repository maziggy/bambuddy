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
import os
from urllib.parse import unquote

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
    MakerWorldRecentImport,
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


def _canonical_url(model_id: int, profile_id: int | None = None) -> str:
    """Build a stable source_url we use for dedupe.

    Dedupe is keyed per *plate* (profile) rather than per model, since the
    ``/iot-service/.../profile/{profileId}`` download returns a specific
    plate — not the full multi-plate zip — so two different plates of the
    same design should become two separate library entries. Canonical
    shape uses the locale-free path with the ``#profileId-`` fragment so
    all URL variants of the same plate still collapse (e.g. ``/en/models/
    123-slug?from=search#profileId-456`` and ``/de/models/123#profileId-
    456`` both map to ``https://makerworld.com/models/123#profileId-
    456``). Plate-less imports (legacy or whole-design) keep the old
    model-only shape for backwards compatibility with existing rows.
    """
    if profile_id:
        return f"https://makerworld.com/models/{model_id}#profileId-{profile_id}"
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

    # /instances/hits omits the per-instance printer compatibility info that
    # /design.instances[].extention.modelInfo carries (compatibility +
    # otherCompatibility). Merge it in so the frontend can show "this
    # instance was sliced for A1" + "also marked compatible with: H2D, P1S,
    # …" before the user picks one — without that, every instance row looks
    # identical in the UI and users blindly pick the first one regardless of
    # whether it matches their printer.
    design_instances = design.get("instances") or []
    if isinstance(design_instances, list):
        compat_by_id = {}
        for di in design_instances:
            if not isinstance(di, dict):
                continue
            iid = di.get("id")
            if iid is None:
                continue
            ext = (di.get("extention") or {}).get("modelInfo") or {}
            compat_by_id[iid] = {
                "compatibility": ext.get("compatibility"),
                "otherCompatibility": ext.get("otherCompatibility"),
            }
        for inst in instances:
            if not isinstance(inst, dict):
                continue
            iid = inst.get("id")
            extra = compat_by_id.get(iid)
            if extra:
                inst["compatibility"] = extra["compatibility"]
                inst["otherCompatibility"] = extra["otherCompatibility"]

    # Find every library row whose source_url is either the model-level
    # canonical URL (legacy whole-model imports) or any plate-level URL
    # (``...#profileId-{n}``) under this model. The frontend surfaces this
    # to mark imported plates in the instance picker.
    model_prefix = _canonical_url(model_id)
    existing_q = await db.execute(
        select(LibraryFile.id).where(
            (LibraryFile.source_url == model_prefix) | (LibraryFile.source_url.like(f"{model_prefix}#profileId-%")),
            LibraryFile.deleted_at.is_(None),
        )
    )
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
        effective_folder_id: int | None = body.folder_id
    else:
        # Default destination: a dedicated top-level "MakerWorld" folder. Keeps
        # imports out of the library root so power users can still organise
        # manually in subfolders, and auto-creates the folder on the first
        # import so users don't have to set it up themselves.
        mw_folder_q = await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.name == "MakerWorld",
                LibraryFolder.parent_id.is_(None),
                LibraryFolder.is_external.is_(False),
            )
        )
        mw_folder = mw_folder_q.scalar_one_or_none()
        if mw_folder is None:
            mw_folder = LibraryFolder(name="MakerWorld", parent_id=None)
            db.add(mw_folder)
            await db.flush()
        effective_folder_id = mw_folder.id

    service = await _build_service(db, current_user)

    # YASTL#51's iot-service endpoint needs the *alphanumeric* modelId
    # (e.g. "US2bb73b106683e5"), not the integer design id from /models/{N}.
    # Fetch design metadata to resolve it, and — in the same call — pick a
    # default profileId from the response if the frontend didn't specify one.
    try:
        design = await service.get_design(body.model_id)
    except MakerWorldError as exc:
        await service.close()
        raise _map_service_error(exc) from exc

    alphanumeric_model_id = design.get("modelId")
    if not isinstance(alphanumeric_model_id, str) or not alphanumeric_model_id:
        await service.close()
        raise HTTPException(
            status_code=502,
            detail="MakerWorld design metadata missing the modelId field",
        )

    profile_id = body.profile_id
    if profile_id is None:
        for instance in design.get("instances") or []:
            pid = instance.get("profileId")
            if isinstance(pid, int) and pid > 0:
                profile_id = pid
                break
        if profile_id is None:
            try:
                envelope = await service.get_design_instances(body.model_id)
            except MakerWorldError as exc:
                await service.close()
                raise _map_service_error(exc) from exc
            for hit in envelope.get("hits") or []:
                pid = hit.get("profileId")
                if isinstance(pid, int) and pid > 0:
                    profile_id = pid
                    break
        if profile_id is None:
            await service.close()
            raise HTTPException(
                status_code=502,
                detail="MakerWorld returned no instances for this model",
            )

    # Canonical URL includes profile_id so each plate gets its own library
    # entry (see ``_canonical_url`` docstring).
    source_url = _canonical_url(body.model_id, profile_id)

    try:
        manifest = await service.get_profile_download(profile_id, alphanumeric_model_id)
    except MakerWorldError as exc:
        await service.close()
        raise _map_service_error(exc) from exc

    signed_url = manifest.get("url")
    # Basename-strip any path components from the upstream filename so a
    # malicious response (``name: "../../evil.3mf"``) can't persist a suspect
    # string into the library row or the UI. On-disk storage uses a UUID
    # filename regardless (see library.py), so this is defence-in-depth.
    raw_name = manifest.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        # MakerWorld emits percent-encoded names (`%20` for spaces, etc.)
        # because the same string round-trips through HTTP URLs in the
        # CDN download path. Decode before persisting so the library
        # row, the slice toast, and every later UI surface show the
        # human-readable form.
        suggested_name = os.path.basename(unquote(raw_name.strip())) or f"makerworld-{body.model_id}.3mf"
    else:
        suggested_name = f"makerworld-{body.model_id}.3mf"
    if not signed_url or not isinstance(signed_url, str):
        await service.close()
        raise HTTPException(status_code=502, detail="MakerWorld did not return a download URL")

    # Dedupe check upfront so we don't burn bandwidth re-downloading.
    if source_url:
        existing_q = await db.execute(LibraryFile.active().where(LibraryFile.source_url == source_url).limit(1))
        existing_row = existing_q.scalar_one_or_none()
        if existing_row is not None:
            await service.close()
            return MakerWorldImportResponse(
                library_file_id=existing_row.id,
                filename=existing_row.filename,
                folder_id=existing_row.folder_id,
                profile_id=profile_id,
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
    # path ends in a UUID that's not meaningful to users. Decode the
    # fallback path-tail too — same percent-encoding round-trip applies
    # there as on the manifest-supplied name.
    filename = suggested_name if suggested_name.endswith(".3mf") else unquote(download_filename)

    library_file, was_existing = await save_3mf_bytes_to_library(
        db,
        file_bytes=file_bytes,
        filename=filename,
        folder_id=effective_folder_id,
        source_type=_SOURCE_TYPE,
        source_url=source_url,
        owner_id=current_user.id if current_user else None,
    )

    return MakerWorldImportResponse(
        library_file_id=library_file.id,
        filename=library_file.filename,
        folder_id=library_file.folder_id,
        profile_id=profile_id,
        was_existing=was_existing,
    )


@router.get("/recent-imports", response_model=list[MakerWorldRecentImport])
async def recent_imports(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.MAKERWORLD_VIEW),
):
    """Last N MakerWorld imports, newest first.

    Surfaces files whose ``source_type`` is ``"makerworld"`` so the MakerWorld
    page can show a 'Recent imports' sidebar that persists across resolves.
    ``limit`` is clamped to ``[1, 50]`` to keep payloads sensible.
    """
    _ = current_user  # permission gate only
    capped = max(1, min(50, int(limit)))
    result = await db.execute(
        LibraryFile.active()
        .where(LibraryFile.source_type == _SOURCE_TYPE)
        .order_by(LibraryFile.created_at.desc())
        .limit(capped)
    )
    rows = result.scalars().all()
    return [
        MakerWorldRecentImport(
            library_file_id=row.id,
            filename=row.filename,
            folder_id=row.folder_id,
            thumbnail_path=row.thumbnail_path,
            source_url=row.source_url,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]

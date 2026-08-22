"""MakerWorld integration routes.

User pastes a model URL (MakerWorld or other supported host) → Bambuddy resolves
it → shows plate list → one-click import/print. The URL-paste flow covers the
actual discovery pattern (Reddit/YouTube/shared links) without needing to
replicate the host's whole search UI.

Search/browse endpoints are intentionally NOT exposed: the public-facing
``design/search`` endpoint returns empty results from server-originated
requests (see memory/makerworld-integration.md for the investigation).

The route layer uses the :class:`ModelProviderRegistry` to select the
appropriate provider based on the pasted URL, so adding a new model host only
requires registering a new :class:`ModelProvider` — these route handlers
remain unchanged.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import resolve_api_key_cloud_owner
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
from backend.app.services.model_providers import makerworld_provider, registry
from backend.app.services.model_providers.base import (
    ModelProvider,
    ProviderAuthError,
    ProviderError,
    ProviderForbiddenError,
    ProviderNotFoundError,
    ProviderResourceRef,
    ProviderService,
    ProviderUnavailableError,
    ProviderUrlError,
)
from backend.app.services.model_providers.makerworld.service import MakerWorldService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/makerworld", tags=["makerworld"])


def _provider_for_url(url: str) -> ModelProvider:
    """Return the registered model provider that claims *url*.

    A pasted link for an unsupported host is a clean 400 — the registry is
    the routing seam, and "nobody supports this URL" is a client-input
    problem, not a server error.
    """
    provider = registry.find_for_url(url)
    if provider is None:
        msg = f"No registered model provider supports {url!r}"
        raise HTTPException(status_code=400, detail=msg)
    return provider


def _provider_for_source(source_type: str) -> ModelProvider:
    """Return the registered model provider with this ``source_type``.

    Import identifies a resource by numeric id, not by URL, so there is
    nothing to route on except the source type the caller names.
    """
    try:
        return registry.get(source_type)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _build_service(
    db: AsyncSession,
    provider: ModelProvider,
    current_user: User | None,
    api_key_cloud_owner: User | None = None,
) -> ProviderService:
    """Construct a per-request service via *provider*.

    Identity resolution (JWT user vs API-key owner vs anonymous) and
    credential seeding live inside ``provider.build_service`` — the single
    place every provider resolves them, so the routes never re-implement it.
    """
    return await provider.build_service(db=db, user=current_user, api_key_owner=api_key_cloud_owner)


def _canonical_url(
    provider: ModelProvider,
    model_id: int,
    profile_id: int | None = None,
) -> str:
    """Build the stable ``source_url`` key used for dedupe.

    The provider's own :meth:`ModelProvider.canonical_url` is used to generate
    the dedupe key, so each provider controls its own key shape.
    """
    ref = ProviderResourceRef(
        source_type=provider.source_type,
        external_id=str(model_id),
        sub_id=str(profile_id) if profile_id else None,
    )
    return provider.canonical_url(ref)


def _map_service_error(exc: ProviderError) -> HTTPException:
    """Translate provider service exceptions into HTTP responses."""
    if isinstance(exc, ProviderUrlError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ProviderAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, ProviderForbiddenError):
        # 403 forwards the provider's own refusal message (content-gated,
        # region-locked, requires points, etc.) — UI surfaces it verbatim.
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ProviderNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProviderUnavailableError):
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
    except ProviderError as exc:
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
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
):
    """Report whether the caller can import 3MFs (needs a Bambu Cloud token).

    API-keyed callers (which return None from ``current_user``) get the
    owner User via ``resolve_api_key_cloud_owner`` when the key carries the
    cloud-access scope, so ``has_cloud_token`` reflects the owning user's
    stored token rather than always reporting ``False`` (#1777, same shape
    as the cloud-presets fix in #1182).
    """
    service = await _build_service(db, makerworld_provider, current_user, api_key_cloud_owner)
    try:
        status = await service.get_status(db)
    finally:
        await service.close()
    return MakerWorldStatus(
        has_cloud_token=status.authenticated,
        can_download=status.can_download,
        # ``auth_error`` is set exactly when a stored token exists *and* was
        # rejected — the "sign-in expired" state. No token means there is no
        # sign-in to have expired.
        sign_in_expired=status.auth_error is not None,
    )


@router.post("/resolve", response_model=MakerWorldResolvedModel)
async def resolve_url(
    body: MakerWorldResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.MAKERWORLD_VIEW),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
):
    """Resolve a MakerWorld URL to full model metadata + plate list.

    The response also tells the caller which (if any) LibraryFile rows already
    exist for the same model URL, so the UI can show an "Already imported"
    badge and skip a redundant download.
    """
    # Strategy pattern: select provider based on URL instead of hardcoding.
    provider = _provider_for_url(body.url)
    try:
        ref = provider.parse_url(body.url)
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    model_id = int(ref.external_id)
    profile_id = int(ref.sub_id) if ref.sub_id else None

    service = await _build_service(db, provider, current_user, api_key_cloud_owner)
    try:
        resolved = await service.resolve(ref)
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    # Find every library row whose source_url is either the model-level
    # canonical URL (legacy whole-model imports) or any plate-level URL
    # (``...#profileId-{n}``) under this model. The frontend surfaces this
    # to mark imported plates in the instance picker.
    model_prefix = _canonical_url(provider, model_id)
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
        design=resolved.design,
        instances=resolved.instances,
        already_imported_library_ids=already_imported,
    )


@router.post("/import", response_model=MakerWorldImportResponse)
async def import_instance(
    body: MakerWorldImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.MAKERWORLD_IMPORT),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
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

    # Import identifies a model by numeric id, not by URL — the request names
    # the provider via ``source_type`` (default: MakerWorld).
    provider = _provider_for_source(body.source_type)
    service = await _build_service(db, provider, current_user, api_key_cloud_owner)

    # YASTL#51's iot-service endpoint needs the *alphanumeric* modelId
    # (e.g. "US2bb73b106683e5"), not the integer design id from /models/{N} —
    # resolving that, plus picking a default profile when the frontend didn't
    # specify one, lives inside ``get_download``. The route only orchestrates
    # dedupe + persistence so every provider shares those concerns here.
    ref = ProviderResourceRef(
        source_type=provider.source_type,
        external_id=str(body.model_id),
        sub_id=str(body.profile_id) if body.profile_id else None,
    )

    try:
        info = await service.get_download(ref)
        # The provider enriches ``sub_id`` with the actually-resolved profile
        # when the caller omitted one.
        resolved_profile_id = int(info.ref.sub_id) if info.ref.sub_id else None

        # Canonical URL includes profile_id so each plate gets its own library
        # entry (see ``_canonical_url`` docstring).
        source_url = provider.canonical_url(info.ref)

        # Dedupe check upfront so we don't burn bandwidth re-downloading.
        existing_q = await db.execute(LibraryFile.active().where(LibraryFile.source_url == source_url).limit(1))
        existing_row = existing_q.scalar_one_or_none()
        if existing_row is not None:
            return MakerWorldImportResponse(
                library_file_id=existing_row.id,
                filename=existing_row.filename,
                folder_id=existing_row.folder_id,
                profile_id=resolved_profile_id,
                was_existing=True,
            )

        download = await service.download(info)
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    # Basename-strip any path components from the upstream filename so a
    # malicious response (``name: "../../evil.3mf"``) can't persist a suspect
    # string into the library row or the UI. On-disk storage uses a UUID
    # filename regardless (see library.py), so this is defence-in-depth.
    raw_name = info.suggested_filename
    if isinstance(raw_name, str) and raw_name.strip():
        # MakerWorld emits percent-encoded names (`%20` for spaces, etc.)
        # because the same string round-trips through HTTP URLs in the
        # CDN download path. Decode before persisting so the library
        # row, the slice toast, and every later UI surface show the
        # human-readable form.
        suggested_name = os.path.basename(unquote(raw_name.strip())) or f"makerworld-{body.model_id}.3mf"
    else:
        suggested_name = f"makerworld-{body.model_id}.3mf"

    # Prefer the server-provided human-readable filename; the signed URL's
    # path ends in a UUID that's not meaningful to users. Decode the
    # fallback path-tail too — same percent-encoding round-trip applies
    # there as on the manifest-supplied name.
    filename = suggested_name if suggested_name.endswith(".3mf") else unquote(download.filename)

    # API-keyed callers carry identity on the key, not in current_user (#1777);
    # this collapse stays route-side solely so the library row is attributed
    # to the key's owner rather than NULL. Credential identity is resolved
    # inside the provider.
    cloud_token_user = current_user or api_key_cloud_owner
    library_file, was_existing = await save_3mf_bytes_to_library(
        db,
        file_bytes=download.file_bytes,
        filename=filename,
        folder_id=effective_folder_id,
        source_type=provider.source_type,
        source_url=source_url,
        owner_id=cloud_token_user.id if cloud_token_user else None,
    )

    return MakerWorldImportResponse(
        library_file_id=library_file.id,
        filename=library_file.filename,
        folder_id=library_file.folder_id,
        profile_id=resolved_profile_id,
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
    Widening this to all registered providers is a behaviour change that
    belongs with the provider that needs it.
    ``limit`` is clamped to ``[1, 50]`` to keep payloads sensible.
    """
    _ = current_user  # permission gate only
    capped = max(1, min(50, int(limit)))

    result = await db.execute(
        LibraryFile.active()
        .where(LibraryFile.source_type == makerworld_provider.source_type)
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

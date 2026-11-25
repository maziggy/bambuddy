"""Manyfold integration routes (#1471).

Browse and search a self-hosted Manyfold library and import its files into
the Bambuddy library, from where they are sliced and printed like any other
file. Files are only fetched when someone imports one; nothing is mirrored.

One connection serves the whole install: an admin stores the Manyfold URL and
an OAuth application's client ID and secret (``/manyfold/config``), and the
application's owner in Manyfold decides which models Bambuddy can see.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.library import save_3mf_bytes_to_library, validate_print_file_upload
from backend.app.api.routes.settings import set_setting
from backend.app.core.auth import ApiKeyActor, RequestActor, RequirePermissionIfAuthEnabled, require_actor_still_exists
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.row_visibility import owned_row_clauses
from backend.app.models.library import LibraryFile
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.manyfold import (
    ManyfoldConfigResponse,
    ManyfoldConfigUpdate,
    ManyfoldFile,
    ManyfoldImportRequest,
    ManyfoldImportResponse,
    ManyfoldLibraryRef,
    ManyfoldModel,
    ManyfoldModelList,
    ManyfoldStatus,
    ManyfoldTestRequest,
    ManyfoldTestResponse,
)
from backend.app.services.library_folder_access import default_import_folder, get_writable_folder
from backend.app.services.model_providers import manyfold_provider
from backend.app.services.model_providers.base import ProviderResourceRef
from backend.app.services.model_providers.manyfold.config import (
    CLIENT_ID_KEY,
    CLIENT_SECRET_KEY,
    CONFIG_KEYS,
    URL_KEY,
    ManyfoldConfig,
    load_config,
    normalize_url,
)
from backend.app.services.model_providers.manyfold.service import (
    ManyfoldError,
    ManyfoldNotFoundError,
    ManyfoldService,
    clear_token_cache,
    is_importable_filename,
    valid_id,
)
from backend.app.utils.filename import INVALID_FILENAME_CHARS, InvalidFilenameError, validate_print_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manyfold", tags=["manyfold"])


def _error(status_code: int, code: str, message: str) -> HTTPException:
    # Structured detail: the frontend shows its own translated text for the code.
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _map_error(exc: ManyfoldError) -> HTTPException:
    """Manyfold's refusals are not Bambuddy's: never answer them with 401 or 403."""
    if isinstance(exc, ManyfoldNotFoundError):
        status_code = 404
    elif exc.code == "manyfold_not_configured":
        status_code = 409
    else:
        status_code = 502
    return _error(status_code, exc.code, str(exc))


async def _service(db: AsyncSession) -> ManyfoldService:
    config = await load_config(db)
    if not config.configured:
        raise _error(409, "manyfold_not_configured", "Manyfold is not set up")
    return ManyfoldService(config)


def _id(value: str) -> str:
    try:
        return valid_id(value)
    except ManyfoldError as exc:
        raise _map_error(exc) from exc


def _library_filename(name: str, file_id: str) -> str:
    """The Manyfold file name, made safe for the printer's SD card."""
    cleaned = "".join("_" if ch in INVALID_FILENAME_CHARS or ord(ch) < 0x20 else ch for ch in name)
    cleaned = cleaned.rstrip(" .")
    try:
        validate_print_filename(cleaned)
    except InvalidFilenameError:
        cleaned = f"manyfold-{file_id}{os.path.splitext(name)[1].lower()}"
    return cleaned


# ---- connection ---------------------------------------------------------


def _config_response(config: ManyfoldConfig) -> ManyfoldConfigResponse:
    return ManyfoldConfigResponse(
        url=config.url,
        client_id=config.client_id,
        has_client_secret=bool(config.client_secret),
        configured=config.configured,
    )


@router.get("/config", response_model=ManyfoldConfigResponse)
async def get_config(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """The stored connection, without the secret."""
    return _config_response(await load_config(db))


@router.put("/config", response_model=ManyfoldConfigResponse)
async def update_config(
    body: ManyfoldConfigUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Store the connection. An empty secret keeps the stored one."""
    try:
        url = normalize_url(body.url)
    except ValueError as exc:
        raise _error(400, "manyfold_bad_url", str(exc)) from exc
    stored = await load_config(db)
    secret = (body.client_secret or "").strip() or stored.client_secret
    if not secret:
        raise _error(400, "manyfold_secret_required", "Enter the client secret")
    await set_setting(db, URL_KEY, url)
    await set_setting(db, CLIENT_ID_KEY, body.client_id.strip())
    await set_setting(db, CLIENT_SECRET_KEY, secret)
    await db.commit()
    clear_token_cache()
    return _config_response(await load_config(db))


@router.delete("/config", status_code=204)
async def delete_config(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Disconnect Manyfold. Files imported earlier stay in the library."""
    await db.execute(delete(Settings).where(Settings.key.in_(CONFIG_KEYS)))
    await db.commit()
    clear_token_cache()


@router.post("/config/test", response_model=ManyfoldTestResponse)
async def test_config(
    body: ManyfoldTestRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Sign in with the entered values and count the models Bambuddy can see.

    Nothing is stored. An empty secret tests the stored one, so an admin can
    re-test without typing it again.
    """
    try:
        url = normalize_url(body.url)
    except ValueError as exc:
        raise _error(400, "manyfold_bad_url", str(exc)) from exc
    secret = (body.client_secret or "").strip() or (await load_config(db)).client_secret
    if not secret:
        raise _error(400, "manyfold_secret_required", "Enter the client secret")
    service = ManyfoldService(ManyfoldConfig(url=url, client_id=body.client_id.strip(), client_secret=secret))
    try:
        return ManyfoldTestResponse(model_count=await service.check())
    except ManyfoldError as exc:
        raise _map_error(exc) from exc
    finally:
        await service.close()


# ---- browsing -----------------------------------------------------------


@router.get("/status", response_model=ManyfoldStatus)
async def get_status(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.MANYFOLD_VIEW),
):
    """Whether Manyfold is set up, and where it is."""
    config = await load_config(db)
    return ManyfoldStatus(configured=config.configured, url=config.url if config.configured else "")


@router.get("/models", response_model=ManyfoldModelList)
async def list_models(
    q: str = "",
    page: int = 1,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.MANYFOLD_VIEW),
):
    """One page of Manyfold's models, optionally searched.

    ``q`` takes Manyfold's own search syntax; the page size is the
    application owner's Manyfold setting.
    """
    service = await _service(db)
    try:
        return ManyfoldModelList(**await service.list_models(query=q[:200], page=max(1, min(page, 100000))))
    except ManyfoldError as exc:
        raise _map_error(exc) from exc
    finally:
        await service.close()


@router.get("/models/{model_id}", response_model=ManyfoldModel)
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.MANYFOLD_VIEW),
    actor: User | ApiKeyActor | None = RequestActor,
):
    """A model's details and files, with the library file of each earlier import
    the caller may open."""
    model_id = _id(model_id)
    service = await _service(db)
    try:
        model = await service.get_model(model_id)
    except ManyfoldError as exc:
        raise _map_error(exc) from exc
    finally:
        await service.close()

    imported: dict[str, ManyfoldLibraryRef] = {}
    readable = owned_row_clauses(LibraryFile, actor, Permission.LIBRARY_READ_ALL, Permission.LIBRARY_READ_OWN)
    rows = (
        await db.execute(
            LibraryFile.active()
            .where(manyfold_provider.source_url_filter(LibraryFile.source_url, model_id), *readable)
            .order_by(LibraryFile.id)
        )
        if readable is not None
        else None
    )
    for row in rows.scalars().all() if rows is not None else []:
        file_id = (row.source_url or "").rsplit("/", 1)[-1]
        imported.setdefault(file_id, ManyfoldLibraryRef(id=row.id, filename=row.filename, folder_id=row.folder_id))

    return ManyfoldModel(
        id=model["id"],
        name=model["name"],
        caption=model["caption"],
        description=model["description"],
        license=model["license"],
        tags=model["tags"],
        url=model["url"],
        has_preview=model["preview_file_id"] is not None,
        files=[ManyfoldFile(**file, library_file=imported.get(file["id"])) for file in model["files"]],
    )


@router.get("/models/{model_id}/preview")
async def get_preview(
    model_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.MANYFOLD_VIEW),
):
    """The model's preview image, fetched from Manyfold.

    The browser can't send Manyfold's token, and the page's CSP only allows
    images from Bambuddy itself, so the image comes through here. The page
    fetches it rather than pointing an ``<img>`` at it: a model without a
    preview answers 404, and a failing ``<img>`` on a protected URL makes the
    app renew its media token.
    """
    model_id = _id(model_id)
    service = await _service(db)
    try:
        data, content_type = await service.fetch_preview(model_id)
    except ManyfoldError as exc:
        raise _map_error(exc) from exc
    finally:
        await service.close()
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


# ---- import -------------------------------------------------------------


async def _import_folder_id(db: AsyncSession, folder_id: int | None, user: User | ApiKeyActor | None) -> int | None:
    """The chosen folder, or the top-level "Manyfold" folder (created on first use)."""
    if folder_id is not None:
        # Only into a folder the user may write to (#3201).
        folder = await get_writable_folder(db, folder_id, user)
        if folder.is_external and folder.external_readonly:
            raise HTTPException(status_code=403, detail="Cannot import into a read-only external folder")
        return folder_id
    folder = await default_import_folder(db, manyfold_provider.default_folder_name, user)
    return folder.id


@router.post("/import", response_model=ManyfoldImportResponse)
async def import_file(
    body: ManyfoldImportRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.MANYFOLD_IMPORT),
    actor: User | ApiKeyActor | None = RequestActor,
):
    """Download one Manyfold file into the library.

    A file imported before, and still in the library, is returned as it is
    rather than downloaded again.
    """
    model_id, file_id = _id(body.model_id), _id(body.file_id)
    source_url = manyfold_provider.canonical_url(
        ProviderResourceRef(source_type=manyfold_provider.source_type, external_id=model_id, sub_id=file_id)
    )
    # A copy the caller cannot open is no copy for them: they get their own.
    readable = owned_row_clauses(LibraryFile, actor, Permission.LIBRARY_READ_ALL, Permission.LIBRARY_READ_OWN)
    existing = (
        (
            await db.execute(LibraryFile.active().where(LibraryFile.source_url == source_url, *readable).limit(1))
        ).scalar_one_or_none()
        if readable is not None
        else None
    )
    if existing is not None:
        return ManyfoldImportResponse(
            library_file_id=existing.id, filename=existing.filename, folder_id=existing.folder_id, was_existing=True
        )

    service = await _service(db)
    try:
        file = await service.get_file(model_id, file_id)
        if not is_importable_filename(file["filename"]):
            raise _error(
                400,
                "manyfold_not_importable",
                "Only 3MF, STL and STEP files can be imported; Bambuddy can't slice or print the others",
            )
        data = await service.download_file(file)
    except ManyfoldError as exc:
        raise _map_error(exc) from exc
    finally:
        await service.close()

    filename = _library_filename(file["filename"], file_id)
    validate_print_file_upload(filename, data)
    # Not saved for a user deleted during the download (SQLite reuses the id).
    await require_actor_still_exists(db, actor)
    folder_id = await _import_folder_id(db, body.folder_id, actor)
    library_file, was_existing = await save_3mf_bytes_to_library(
        db,
        file_bytes=data,
        filename=filename,
        folder_id=folder_id,
        source_type=manyfold_provider.source_type,
        source_url=source_url,
        owner_id=actor.id if actor else None,
        dedupe_within=readable,
    )
    logger.info(
        "[MANYFOLD] Imported %s (model %s, file %s) as library file %s", filename, model_id, file_id, library_file.id
    )
    return ManyfoldImportResponse(
        library_file_id=library_file.id,
        filename=library_file.filename,
        folder_id=library_file.folder_id,
        was_existing=was_existing,
    )

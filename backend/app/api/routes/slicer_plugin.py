"""API for the Bambuddy slicer plugin (OrcaSlicer).

The plugin runs inside the slicer on the user's machine and sends a sliced
file here together with a manifest: what the user wants done with it (library
only, or queue plate N x copies on a printer) and where it came from (slicer,
presets, a fingerprint of the model). The file's own contents -- per-plate
grams, time, the model it was sliced for -- are read from the file by the same
parser every library upload goes through, not taken from the manifest, so the
two can never disagree.

The work is delegated: storing the file is ``ingest_library_upload`` and
queueing it is ``POST /queue/`` itself, so a file sent from the slicer passes
exactly the checks one added through the UI does.
"""

import asyncio
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.library import ingest_library_upload
from backend.app.api.routes.print_queue import add_to_queue
from backend.app.core.auth import _validate_api_key, require_permission_if_auth_enabled, security
from backend.app.core.config import APP_VERSION
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.slicer_plugin_upload import SlicerPluginUpload
from backend.app.models.user import User
from backend.app.schemas.print_queue import PrintQueueItemCreate
from backend.app.schemas.slicer_plugin import (
    SlicerPluginInfoResponse,
    SlicerPluginLibraryFile,
    SlicerPluginManifest,
    SlicerPluginQueueResult,
    SlicerPluginUploadResponse,
)
from backend.app.utils.printer_models import is_gcode_compatible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slicer-plugin", tags=["slicer-plugin"])

# Manifest versions this server understands. A plugin reads them from /info and
# sends the newest one both sides know.
MANIFEST_VERSIONS = (1,)

# A claim still "processing" after this long belongs to a request that died
# without releasing it (a killed worker, a cancelled task). Past it, a retry
# with the same upload_id may take the claim over instead of being refused
# forever. Far longer than any upload plus queue insert takes.
_STALE_CLAIM = timedelta(minutes=10)

_MAX_MANIFEST_CHARS = 64_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _may_queue(credentials: HTTPAuthorizationCredentials | None, x_api_key: str | None) -> bool:
    """Whether the caller may create queue items, decided by the queue's own gate.

    Asks the dependency ``POST /queue/`` is guarded by rather than restating
    its rules, so a key or account that could not queue from the UI cannot
    queue from the slicer either.
    """
    try:
        await require_permission_if_auth_enabled(Permission.QUEUE_CREATE)(credentials=credentials, x_api_key=x_api_key)
    except HTTPException as e:
        if e.status_code == 403:
            return False
        raise
    return True


async def _caller_id(
    db: AsyncSession,
    user: User | None,
    credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> str:
    """Name the caller, so an upload_id replays only to whoever used it first."""
    if user is not None:
        return f"user:{user.id}"
    key_value = x_api_key
    if key_value is None and credentials is not None and credentials.credentials.startswith("bb_"):
        key_value = credentials.credentials
    if key_value:
        api_key = await _validate_api_key(db, key_value)
        if api_key is not None:
            return f"apikey:{api_key.id}"
    return "anonymous"


def _check_plate(content: bytes, plate_id: int) -> str | None:
    """Return why ``plate_id`` cannot be printed from this file, or None if it can."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return "plate_id needs a sliced 3MF; this file is not one"
    if f"Metadata/plate_{plate_id}.gcode" not in names:
        return f"This file has no sliced plate {plate_id}"
    return None


def _replay(existing: SlicerPluginUpload, caller: str) -> SlicerPluginUploadResponse:
    """Answer a repeated upload_id with what the first request returned."""
    # Anyone else presenting the id gets nothing about it: a 409 that is the
    # same whether or not the stored upload is still in progress.
    if existing.caller != caller:
        raise HTTPException(409, "upload_id has already been used")
    if existing.status != "done" or not existing.response:
        raise HTTPException(409, "An upload with this upload_id is still being processed")
    data = json.loads(existing.response)
    data["replayed"] = True
    return SlicerPluginUploadResponse.model_validate(data)


@router.get("/info", response_model=SlicerPluginInfoResponse)
async def plugin_info(
    current_user: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    """Handshake for the plugin's connect dialog.

    Needs the same permission an upload does, so "connected" means the plugin
    can actually send. ``can_queue`` tells it whether to offer queueing at all.
    """
    return SlicerPluginInfoResponse(
        bambuddy_version=APP_VERSION,
        manifest_versions=list(MANIFEST_VERSIONS),
        can_upload=True,
        can_queue=await _may_queue(credentials, x_api_key),
    )


@router.post("/uploads", response_model=SlicerPluginUploadResponse)
async def upload_from_slicer(
    file: UploadFile = File(...),
    manifest: str = Form(..., max_length=_MAX_MANIFEST_CHARS),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    """Store a sliced file from the slicer plugin, and queue it if asked.

    Idempotent on ``manifest.upload_id``: a retry of a send whose response was
    lost gets the first response back (``replayed: true``) instead of a second
    upload -- and, with action "queue", instead of a second print.
    """
    try:
        m = SlicerPluginManifest.model_validate_json(manifest)
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors(include_url=False, include_context=False, include_input=False))
    if m.schema_version not in MANIFEST_VERSIONS:
        raise HTTPException(
            400,
            f"Manifest version {m.schema_version} is not supported; this server reads {list(MANIFEST_VERSIONS)}",
        )
    if not file.filename:
        raise HTTPException(400, "Filename is required")

    intent = m.intent
    caller = await _caller_id(db, current_user, credentials, x_api_key)

    # A repeat is answered before anything is checked or read, so a retry of an
    # upload that succeeded returns its answer even if the caller has since
    # lost a permission it had then.
    existing = (
        await db.execute(select(SlicerPluginUpload).where(SlicerPluginUpload.upload_id == m.upload_id))
    ).scalar_one_or_none()
    if existing is not None:
        abandoned = (
            existing.status != "done" and existing.caller == caller and existing.created_at < _utcnow() - _STALE_CLAIM
        )
        if not abandoned:
            return _replay(existing, caller)
        await db.delete(existing)
        await db.commit()

    # Refused before anything is written: a plugin told "uploaded, but you may
    # not queue" would leave a file in the library the user never asked to keep.
    if intent.action == "queue" and not await _may_queue(credentials, x_api_key):
        raise HTTPException(403, "This account or API key may upload files but not queue them")

    content = await file.read()
    if intent.action == "queue" and intent.plate_id is not None:
        plate_problem = await asyncio.to_thread(_check_plate, content, intent.plate_id)
        if plate_problem:
            raise HTTPException(400, plate_problem)

    # Claim the id. The unique constraint decides between two requests that
    # both got past the lookup above -- the plugin retrying a send that timed
    # out while the first attempt was still working.
    source = m.source
    claim = SlicerPluginUpload(
        upload_id=m.upload_id,
        caller=caller,
        status="processing",
        slicer=source.slicer,
        slicer_version=source.slicer_version,
        plugin_version=source.plugin_version,
        project_name=source.project_name,
        model_fingerprint=source.model_fingerprint,
        presets=source.presets.model_dump_json() if source.presets else None,
        created_at=_utcnow(),
    )
    db.add(claim)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = (
            await db.execute(select(SlicerPluginUpload).where(SlicerPluginUpload.upload_id == m.upload_id))
        ).scalar_one()
        return _replay(winner, caller)
    claim_id = claim.id

    try:
        response = await _store_and_queue(db, m, file.filename, content, current_user)
    except Exception:
        # Release the claim, so the plugin's retry runs again rather than
        # being told the failed attempt is still in progress.
        await db.rollback()
        await db.execute(delete(SlicerPluginUpload).where(SlicerPluginUpload.id == claim_id))
        await db.commit()
        raise

    claim = await db.get(SlicerPluginUpload, claim_id)
    claim.status = "done"
    claim.library_file_id = response.library_file.id
    claim.response = response.model_dump_json()
    await db.commit()
    return response


async def _store_and_queue(
    db: AsyncSession,
    m: SlicerPluginManifest,
    filename: str,
    content: bytes,
    current_user: User | None,
) -> SlicerPluginUploadResponse:
    intent = m.intent
    library_file, duplicate_of = await ingest_library_upload(
        db,
        filename=filename,
        content=content,
        folder_id=intent.folder_id,
        created_by_id=current_user.id if current_user else None,
    )
    stored = SlicerPluginLibraryFile(
        id=library_file.id,
        filename=library_file.filename,
        file_type=library_file.file_type,
        file_size=library_file.file_size,
        thumbnail_path=library_file.thumbnail_path,
        duplicate_of=duplicate_of,
    )
    sliced_for = (library_file.file_metadata or {}).get("sliced_for_model")

    warnings: list[str] = []
    if duplicate_of is not None:
        warnings.append(f"The library already holds this exact file (#{duplicate_of}).")

    if intent.action != "queue":
        return SlicerPluginUploadResponse(upload_id=m.upload_id, library_file=stored, warnings=warnings)

    # POST /queue/ refuses a target_model the file was not sliced for, but a
    # named printer is the user's own pick and is only flagged -- the same
    # latitude the print dialog gives.
    if intent.printer_id is not None:
        printer = await db.get(Printer, intent.printer_id)
        if printer is not None and not is_gcode_compatible(sliced_for, printer.model):
            warnings.append(f"Sliced for {sliced_for}, but {printer.name} is a {printer.model}.")

    try:
        item = await add_to_queue(
            PrintQueueItemCreate(
                library_file_id=library_file.id,
                printer_id=intent.printer_id,
                target_model=intent.target_model,
                plate_id=intent.plate_id,
                quantity=intent.copies,
                manual_start=intent.manual_start,
                cost_center_id=intent.cost_center_id,
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        # The queue route may have flushed a batch row before refusing (the
        # budget check runs after it); none of that may outlive the refusal.
        await db.rollback()
        queue_error = e.detail if isinstance(e.detail, str) else json.dumps(e.detail)
        return SlicerPluginUploadResponse(
            upload_id=m.upload_id, library_file=stored, queue_error=queue_error, warnings=warnings
        )

    if item.batch_id is None:
        item_ids = [item.id]
    else:
        item_ids = list(
            (
                await db.execute(
                    select(PrintQueueItem.id)
                    .where(PrintQueueItem.batch_id == item.batch_id)
                    .order_by(PrintQueueItem.position)
                )
            ).scalars()
        )
    return SlicerPluginUploadResponse(
        upload_id=m.upload_id,
        library_file=stored,
        queue=SlicerPluginQueueResult(queue_item_ids=item_ids, batch_id=item.batch_id),
        warnings=warnings,
    )

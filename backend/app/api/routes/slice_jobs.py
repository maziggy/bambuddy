"""Polling endpoint for the in-memory slice-job dispatcher.

POST /library/files/{id}/slice and POST /archives/{id}/slice return a
job_id and a status_url pointing here. The frontend polls this until
status flips to `completed` or `failed`.
"""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.core.auth import require_ownership_permission
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.services.slice_dispatch import slice_dispatch

router = APIRouter(prefix="/slice-jobs", tags=["slice-jobs"])


@router.get("/{job_id}")
async def get_slice_job(
    job_id: int,
    # Job IDs are sequential integers and the body leaks source filenames
    # plus the resulting library_file_id / archive_id. Gate on the library
    # read permission family (own/all). NOTE: SliceJob is in-memory with no
    # owner field, so we cannot per-row scope; callers with either OWN or
    # ALL can poll any job_id. Adding owner_id to SliceJob is the proper
    # follow-up (out of scope for the IDOR fix train).
    _: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    job = slice_dispatch.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Slice job not found or expired")
    body: dict = {
        "job_id": job.id,
        "status": job.status,
        "kind": job.kind,
        "source_id": job.source_id,
        "source_name": job.source_name,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        # Live progress fed by the sidecar's --pipe channel. Null when
        # the slicer hasn't emitted yet (early "Initializing" phase) or
        # the sidecar doesn't support progress (older versions).
        "progress": job.progress,
    }
    if job.status == "completed":
        body["result"] = job.result
    elif job.status == "failed":
        body["error_status"] = job.error_status
        body["error_detail"] = job.error_detail
    return body

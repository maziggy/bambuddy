"""Polling endpoint for the in-memory slice-job dispatcher.

POST /library/files/{id}/slice and POST /archives/{id}/slice return a
job_id and a status_url pointing here. The frontend polls this until
status flips to `completed` or `failed`.
"""

from fastapi import APIRouter, HTTPException

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.services.slice_dispatch import slice_dispatch

router = APIRouter(prefix="/slice-jobs", tags=["slice-jobs"])


@router.get("/{job_id}")
async def get_slice_job(
    job_id: int,
    # Job IDs are sequential integers and the body leaks source filenames
    # plus the resulting library_file_id / archive_id. Gate on LIBRARY_READ
    # — same baseline a user needs to see slice sources or results.
    _: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_READ),
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
    }
    if job.status == "completed":
        body["result"] = job.result
    elif job.status == "failed":
        body["error_status"] = job.error_status
        body["error_detail"] = job.error_detail
    return body

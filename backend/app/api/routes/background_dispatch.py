from fastapi import APIRouter, HTTPException

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.services.background_dispatch import background_dispatch

router = APIRouter(prefix="/background-dispatch", tags=["background-dispatch"])


@router.delete("/{job_id}")
async def cancel_dispatch_job(
    job_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
):
    """Cancel a background-dispatch job.

    Queued jobs are cancelled immediately. Active jobs are marked for
    cooperative cancellation and will stop at the next cancellation checkpoint.
    """
    result = await background_dispatch.cancel_job(job_id)

    if not result["cancelled"]:
        raise HTTPException(status_code=404, detail="Dispatch job not found")

    return {
        "status": "cancelling" if result.get("pending") else "cancelled",
        "job_id": result["job_id"],
        "source_name": result["source_name"],
        "printer_id": result["printer_id"],
        "printer_name": result["printer_name"],
    }

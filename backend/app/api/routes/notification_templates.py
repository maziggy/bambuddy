"""API routes for notification template management."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.schemas.notification_template import (
    EVENT_VARIABLES,
    SAMPLE_DATA,
    EventVariablesResponse,
    NotificationTemplateResponse,
    NotificationTemplateUpdate,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
)
from backend.app.services.notification_service import notification_service

router = APIRouter(prefix="/notification-templates", tags=["notification-templates"])


# Event type display names
EVENT_NAMES = {
    "print_start": "Print Started",
    "print_complete": "Print Completed",
    "print_failed": "Print Failed",
    "print_stopped": "Print Stopped",
    "print_progress": "Print Progress",
    "printer_offline": "Printer Offline",
    "printer_error": "Printer Error",
    "filament_low": "Filament Low",
    "maintenance_due": "Maintenance Due",
    "test": "Test Notification",
    # Queue notifications
    "queue_job_added": "Queue Job Added",
    "queue_job_assigned": "Queue Job Assigned",
    "queue_job_started": "Queue Job Started",
    "queue_job_waiting": "Queue Job Waiting",
    "queue_job_skipped": "Queue Job Skipped",
    "queue_job_failed": "Queue Job Failed",
    "queue_completed": "Queue Completed",
}


@router.get("", response_model=list[NotificationTemplateResponse])
@router.get("/", response_model=list[NotificationTemplateResponse])
async def get_templates(db: AsyncSession = Depends(get_db)):
    """Get all notification templates."""
    result = await db.execute(select(NotificationTemplate).order_by(NotificationTemplate.id))
    return result.scalars().all()


@router.get("/variables", response_model=list[EventVariablesResponse])
async def get_variables():
    """Get available variables for each event type."""
    return [
        EventVariablesResponse(
            event_type=event_type,
            event_name=EVENT_NAMES.get(event_type, event_type),
            variables=variables,
        )
        for event_type, variables in EVENT_VARIABLES.items()
    ]


@router.get("/{template_id}", response_model=NotificationTemplateResponse)
async def get_template(template_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single notification template."""
    result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.put("/{template_id}", response_model=NotificationTemplateResponse)
async def update_template(
    template_id: int,
    update: NotificationTemplateUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a notification template."""
    result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if update.title_template is not None:
        template.title_template = update.title_template
    if update.body_template is not None:
        template.body_template = update.body_template

    await db.commit()
    await db.refresh(template)

    # Clear template cache so changes take effect immediately
    notification_service.clear_template_cache()

    return template


@router.post("/{template_id}/reset", response_model=NotificationTemplateResponse)
async def reset_template(template_id: int, db: AsyncSession = Depends(get_db)):
    """Reset a notification template to its default values."""
    result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Find the default template
    default = next(
        (t for t in DEFAULT_TEMPLATES if t["event_type"] == template.event_type),
        None,
    )
    if not default:
        raise HTTPException(status_code=500, detail="Default template not found")

    template.title_template = default["title_template"]
    template.body_template = default["body_template"]

    await db.commit()
    await db.refresh(template)

    # Clear template cache so changes take effect immediately
    notification_service.clear_template_cache()

    return template


@router.post("/preview", response_model=TemplatePreviewResponse)
async def preview_template(request: TemplatePreviewRequest):
    """Preview a template with sample data."""
    sample = SAMPLE_DATA.get(request.event_type, {})

    # Safe template rendering - replace missing vars with empty string
    def safe_format(template: str, data: dict) -> str:
        result = template
        for key, value in data.items():
            result = result.replace("{" + key + "}", str(value))
        # Remove any remaining unreplaced placeholders
        import re

        result = re.sub(r"\{[a-z_]+\}", "", result)
        return result

    return TemplatePreviewResponse(
        title=safe_format(request.title_template, sample),
        body=safe_format(request.body_template, sample),
    )

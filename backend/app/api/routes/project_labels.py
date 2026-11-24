"""Project label routes (#3655): a label for the bag or box a project's parts go in.

- ``POST /projects/labels``          — labels for one or more projects
- ``POST /projects/labels/preview``  — one label as a PNG, for the dialog

The request is the spool labels' (``labels.py``): a built-in template or a
custom sheet, monochrome, the ``fields`` to print, PDF or PNG at a dpi, and a
starting position on a sheet. Instead of spool ids it takes ``items``, one per
label, each a project with what the user typed for that bag: how many parts
are in it and a free-text line ("Order #1042", "Bag 2 of 3").

The QR code on each label deep-links to ``/projects/<id>``.
"""

from __future__ import annotations

from datetime import timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from backend.app.api.routes.labels import (
    MAX_LABELS_PER_REQUEST,
    LabelLayoutOptions,
    label_file_response,
    label_preview_response,
    render_pdf_in_worker,
    resolve_deeplink_base,
    sheet_request_problem,
)
from backend.app.api.routes.projects import COMPLETED_PARTS_RUN
from backend.app.core.auth import ApiKeyActor, RequestActor, RequestPrinterScope, RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import PrinterScope
from backend.app.core.row_visibility import owned_row_clauses
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.project import Project
from backend.app.models.user import User
from backend.app.services.label_renderer import render_label_preview_pdf, render_labels
from backend.app.services.project_label_renderer import (
    ALL_PROJECT_LABEL_FIELDS,
    DEFAULT_PROJECT_LABEL_FIELDS,
    MAX_NOTE_LENGTH,
    ProjectLabelData,
    ProjectLabelField,
    draw_project_label,
)
from backend.app.utils.local_time import local_zone

router = APIRouter(tags=["labels"])

# Parts in one bag. Bounds a typo, not a real bag.
MAX_QUANTITY = 100_000


class ProjectLabelItem(BaseModel):
    """One label: the project, and what the user typed for this bag."""

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0, le=2**31 - 1)  # PostgreSQL INTEGER primary key
    quantity: int | None = Field(default=None, ge=1, le=MAX_QUANTITY)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

    @field_validator("note")
    @classmethod
    def blank_note_is_none(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class ProjectLabelOptions(LabelLayoutOptions):
    model_config = ConfigDict(extra="forbid")

    # The lines to print. Omitted: DEFAULT_PROJECT_LABEL_FIELDS.
    fields: list[ProjectLabelField] | None = Field(default=None, max_length=len(ALL_PROJECT_LABEL_FIELDS))

    def field_set(self) -> frozenset[ProjectLabelField]:
        return DEFAULT_PROJECT_LABEL_FIELDS if self.fields is None else frozenset(self.fields)


class ProjectLabelRequest(ProjectLabelOptions):
    items: list[ProjectLabelItem] = Field(..., min_length=1, max_length=MAX_LABELS_PER_REQUEST)
    starting_position: int = Field(default=1, ge=1)
    format: Literal["pdf", "png"] = "pdf"
    dpi: Literal[203, 300, 600] = 300

    @model_validator(mode="after")
    def validate_sheet(self) -> ProjectLabelRequest:
        if (self.template == "custom") != (self.layout is not None):
            return self  # layout_only_for_custom reports this one
        problem = sheet_request_problem(
            self.resolved_template(), len(self.items), self.starting_position, self.format, self.dpi, self.template
        )
        if problem:
            raise ValueError(problem)
        return self


class ProjectLabelPreviewRequest(ProjectLabelOptions, ProjectLabelItem):
    pass


async def _project_label_data(
    items: list[ProjectLabelItem],
    fields: frozenset[ProjectLabelField],
    request: Request,
    db: AsyncSession,
    printer_scope: PrinterScope,
    actor: User | ApiKeyActor | None,
) -> list[ProjectLabelData]:
    """The label data for each item, in the order given. A project named twice
    (several bags) is loaded once."""
    ids = sorted({item.project_id for item in items})
    rows = await db.scalars(
        select(Project)
        .where(Project.id.in_(ids))
        .options(
            load_only(
                Project.id, Project.name, Project.color, Project.parent_id, Project.due_date, Project.target_parts_count
            )
        )
    )
    projects = {p.id: p for p in rows}
    missing = [pid for pid in ids if pid not in projects]
    if missing:
        raise HTTPException(404, f"Project(s) not found: {missing}")

    parent_ids = {p.parent_id for p in projects.values() if p.parent_id}
    parents: dict[int, str] = {}
    if "parent" in fields and parent_ids:
        parent_rows = await db.execute(select(Project.id, Project.name).where(Project.id.in_(parent_ids)))
        parents = dict(parent_rows.tuples().all())

    # Counts are project-wide on the Projects page. A print date describes
    # an individual run, so apply archive ownership and the run's printer scope.
    good_runs = (
        select(PrintArchive.project_id)
        .join(PrintLogEntry, PrintLogEntry.archive_id == PrintArchive.id)
        .where(PrintArchive.project_id.in_(ids), PrintArchive.deleted_at.is_(None), COMPLETED_PARTS_RUN)
        .group_by(PrintArchive.project_id)
    )
    completed = {}
    if "parts" in fields:
        counts = await db.execute(good_runs.add_columns(func.sum(PrintArchive.quantity)))
        completed = dict(counts.tuples().all())

    printed = {}
    if "printed" in fields:
        visible = owned_row_clauses(PrintArchive, actor, Permission.ARCHIVES_READ_ALL, Permission.ARCHIVES_READ_OWN)
        if visible is not None:
            printer = printer_scope.where_strict(PrintLogEntry.printer_id)
            dates = good_runs.add_columns(func.max(PrintLogEntry.completed_at)).where(*visible)
            if printer is not None:
                dates = dates.where(printer)
            zone = local_zone()
            for pid, when in (await db.execute(dates)).all():
                if when is not None:
                    printed[pid] = when.replace(tzinfo=timezone.utc).astimezone(zone).date()

    base = await resolve_deeplink_base(request, db) if "qr" in fields else None
    out: list[ProjectLabelData] = []
    for item in items:
        project = projects[item.project_id]
        out.append(
            ProjectLabelData(
                project_id=project.id,
                name=project.name,
                deeplink_url=f"{base}/projects/{project.id}" if base else "",
                color=project.color,
                parent_name=parents.get(project.parent_id) if project.parent_id else None,
                due_date=project.due_date.date() if project.due_date else None,
                printed=printed.get(project.id),
                completed_parts=completed.get(project.id, 0),
                target_parts=project.target_parts_count,
                quantity=item.quantity,
                note=item.note,
            )
        )
    return out


@router.post("/projects/labels")
async def render_project_labels(
    body: ProjectLabelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    actor: User | ApiKeyActor | None = RequestActor,
) -> Response:
    """Render labels for projects' finished parts, one per item."""
    fields = body.field_set()
    data_list = await _project_label_data(body.items, fields, request, db, printer_scope, actor)
    template = body.resolved_template()
    pdf = await render_pdf_in_worker(
        render_labels,
        template,
        data_list,
        monochrome=body.monochrome,
        starting_position=body.starting_position,
        fields=fields,
        draw=draw_project_label,
        subject="project",
    )
    names = [f"project-{d.project_id}-{n}.png" for n, d in enumerate(data_list, start=1)]
    return await label_file_response(
        pdf, template, f"bambuddy-project-labels-{body.template}", body.format, body.dpi, names
    )


@router.post("/projects/labels/preview")
async def preview_project_label(
    body: ProjectLabelPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    actor: User | ApiKeyActor | None = RequestActor,
) -> Response:
    """One project label, as a PNG, for the dialog's preview."""
    item = ProjectLabelItem(project_id=body.project_id, quantity=body.quantity, note=body.note)
    fields = body.field_set()
    data = (await _project_label_data([item], fields, request, db, printer_scope, actor))[0]
    pdf = await render_pdf_in_worker(
        render_label_preview_pdf,
        body.resolved_template(),
        data,
        monochrome=body.monochrome,
        fields=fields,
        draw=draw_project_label,
    )
    return await label_preview_response(pdf)

"""Pydantic schemas for PipelineRun + eligibility (#1425 PR B + PR C)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class EligibilityIssueResponse(BaseModel):
    """Single eligibility issue — see ``services/pipeline_eligibility.py`` for
    the full list of ``kind`` values and what each means."""

    kind: Literal[
        "printer_not_set",
        "printer_not_found",
        "printer_disabled",
        "printer_offline",
        "filament_type_mismatch",
        "filament_color_mismatch",
        "ams_slot_missing",
        "filament_unverified",
        "no_class_matches",  # PR C: target_kind='printer_class' and zero printers in the install match the model
        "class_not_set",  # PR C: target_kind='printer_class' with no target_model_class
    ]
    slot_index: int | None = None
    expected: str | None = None
    actual: str | None = None


class PerPrinterReport(BaseModel):
    """One row of class-targeting eligibility — per matching printer.

    PR C extends the top-level report with this list so the confirmation modal
    can show ``3 of 5 X1Cs eligible`` plus a per-printer breakdown of why each
    candidate is or isn't usable.
    """

    printer_id: int
    printer_name: str
    ok: bool
    issues: list[EligibilityIssueResponse] = []


class EligibilityReportResponse(BaseModel):
    """Returned by both ``POST /check-eligibility`` and (on 409) ``POST /run``
    so the frontend can render the same modal in either flow.

    ``ok`` semantics:
      - ``target_kind='specific_printer'``: ``ok`` mirrors that single
        printer's eligibility (no blocking issues).
      - ``target_kind='printer_class'``: ``ok`` is True iff **at least one**
        matching printer passes — the run can dispatch even if some
        candidates in the class are offline / filament-mismatched, because
        the scheduler will pick any eligible one. The per-printer list lives
        on ``printer_reports`` so the operator sees the full picture.

    ``issues`` carries class-level issues only (``no_class_matches``,
    ``class_not_set``) — per-printer detail moves to ``printer_reports``.
    """

    ok: bool
    target_kind: Literal["specific_printer", "printer_class"] = "specific_printer"
    target_printer_id: int | None = None
    target_printer_name: str | None = None
    target_model_class: str | None = None
    issues: list[EligibilityIssueResponse] = []
    printer_reports: list[PerPrinterReport] = []


class CheckEligibilityRequest(BaseModel):
    """Exactly one of ``source_library_file_id`` / ``source_archive_id`` must
    be set."""

    source_library_file_id: int | None = None
    source_archive_id: int | None = None
    force: bool = Field(default=False)

    @model_validator(mode="after")
    def exactly_one_source(self) -> "CheckEligibilityRequest":
        if (self.source_library_file_id is None) == (self.source_archive_id is None):
            raise ValueError("exactly one of source_library_file_id or source_archive_id must be set")
        return self


class PipelineRunCreateRequest(BaseModel):
    """``copies`` defaults to 1 (PR B parity). The route handler enforces the
    ``pipeline_max_copies`` setting on top of the schema's lower bound."""

    source_library_file_id: int | None = None
    source_archive_id: int | None = None
    copies: int = Field(default=1, ge=1, le=1000)
    force: bool = Field(
        default=False,
        description=(
            "When False (default), the route returns 409 with the eligibility "
            "report if any blocking issue exists. When True, the run starts "
            "even when issues exist — recorded on PipelineRun.eligibility_overridden."
        ),
    )

    @model_validator(mode="after")
    def exactly_one_source(self) -> "PipelineRunCreateRequest":
        if (self.source_library_file_id is None) == (self.source_archive_id is None):
            raise ValueError("exactly one of source_library_file_id or source_archive_id must be set")
        return self


class PipelineJobResponse(BaseModel):
    id: int
    pipeline_run_id: int
    copy_index: int
    assigned_printer_id: int | None
    assigned_printer_name: str | None = None
    queue_entry_id: int | None
    status: Literal[
        "pending",
        "awaiting_printer",
        "queued",
        "printing",
        "completed",
        "failed",
        "cancelled",
    ]
    error_message: str | None = None
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None


class PipelineRunResponse(BaseModel):
    id: int
    pipeline_id: int | None
    pipeline_name: str | None = None
    source_library_file_id: int | None
    source_archive_id: int | None = None
    source_filename: str | None = None
    parent_run_id: int | None = None
    copies: int
    # Roll-up counts used by the dashboard's per-row summary. Computed at read
    # time from the per-job statuses so they always match the live state.
    copies_completed: int = 0
    copies_failed: int = 0
    copies_cancelled: int = 0
    copies_in_progress: int = 0
    status: Literal[
        "queued",
        "slicing",
        "dispatching",
        "in_progress",
        "completed",
        "failed",
        "partial_failure",  # PR C: some copies succeeded, some failed/cancelled
        "cancelled",
    ]
    slice_job_id: int | None
    sliced_library_file_id: int | None
    eligibility_overridden: bool
    error_message: str | None = None
    created_by: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    jobs: list[PipelineJobResponse] = []
    # Pipeline target snapshot — copied onto the response so the dashboard
    # doesn't need a second query to display "Run on X1C class" per row.
    target_kind: Literal["specific_printer", "printer_class"] | None = None
    target_printer_id: int | None = None
    target_model_class: str | None = None
    fanout_strategy: Literal["max_parallel", "fill_one_first", "round_robin"] | None = None


class PipelineRunListResponse(BaseModel):
    runs: list[PipelineRunResponse] = []
    total: int = 0  # PR C: for the dashboard's paginator

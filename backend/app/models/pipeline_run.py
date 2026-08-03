"""Models for a Slicer Pipeline run (#1425 PR B).

A PipelineRun is one "Run pipeline" click: slice the source file once with the
pipeline's four preset slots, then enqueue a single print on the pipeline's
pinned target printer (PR B = single-target dispatch). PR C extends this with
copies > 1 and class targeting + fanout strategies.

Status on a PipelineRun is mostly COMPUTED from the underlying slice_job
(in-memory) + the linked queue_entry's state at read time — see
``api/routes/pipeline_runs.py`` ``_compute_run_status`` for the rules. The
``status`` column is the persisted snapshot used as a fallback / for filtering
in list queries; it's updated on terminal transitions (slice failure, cancel,
or queue-entry completion).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PipelineRun(Base):
    """One run-pipeline invocation. PR B always carries exactly one
    PipelineJob (copies=1); PR C will allow N."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Pipeline + source. ``ondelete='SET NULL'`` on both so run history survives
    # the user soft-deleting a pipeline or removing the source library file.
    pipeline_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("slicer_pipelines.id", ondelete="SET NULL"))
    source_library_file_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("library_files.id", ondelete="SET NULL")
    )
    # Mutually exclusive with source_library_file_id. When set, the orchestrator
    # reads ``archive.source_3mf_path`` (falling back to ``file_path``) for the
    # slice input. Lets ArchiveCard's "Run with pipeline" reuse the same /run
    # endpoint instead of growing a second route.
    source_archive_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("print_archives.id", ondelete="SET NULL"))

    # Set when this run was created by ``POST /pipeline-runs/{parent}/retry-failed``.
    # Chains the new run back to the run whose failed copies it re-attempts so
    # the dashboard can show "Retry of run #N" inline. ``SET NULL`` so cleaning
    # up old runs doesn't dangle retries.
    parent_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pipeline_runs.id", ondelete="SET NULL"))

    copies: Mapped[int] = mapped_column(Integer, default=1)

    # Snapshot status — terminal transitions are persisted here, in-flight
    # reads compute from slice_job + queue_entry. Values:
    #   'queued', 'slicing', 'dispatching', 'in_progress',
    #   'completed', 'failed', 'cancelled'
    status: Mapped[str] = mapped_column(String(20), default="queued")

    # Slice integration. slice_job_id is the in-memory slice_dispatch id (so
    # it's a plain int, not an FK). sliced_library_file_id is the produced
    # gcode.3mf row.
    slice_job_id: Mapped[int | None] = mapped_column(Integer)
    sliced_library_file_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("library_files.id", ondelete="SET NULL")
    )

    # True when the operator chose to "Run anyway" past eligibility issues
    # (filament mismatch, etc.). Surfaced in run history so the audit log
    # shows which runs bypassed the pre-flight.
    eligibility_overridden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    error_message: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    jobs: Mapped[list["PipelineJob"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="PipelineJob.copy_index",
    )


class PipelineJob(Base):
    """One copy within a PipelineRun. PR B: always exactly one per run.

    Each job binds the run to one queue entry (``queue_entry_id``). The
    queue entry's status drives this job's status; this row mostly carries
    the run-side narrative (dispatch timestamps, error message) so deleting
    the queue entry later doesn't lose the audit trail.
    """

    __tablename__ = "pipeline_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    copy_index: Mapped[int] = mapped_column(Integer, default=0)

    assigned_printer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("printers.id", ondelete="SET NULL"))
    queue_entry_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("print_queue.id", ondelete="SET NULL"))

    # Values: 'pending', 'awaiting_printer', 'queued', 'printing',
    #         'completed', 'failed', 'cancelled'
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)

    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    run: Mapped["PipelineRun"] = relationship(back_populates="jobs")

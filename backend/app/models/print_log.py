from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, event, func
from sqlalchemy.orm import Mapped, ORMExecuteState, Session, mapped_column, with_loader_criteria

from backend.app.core.database import Base

# The status of a run that has started and not ended. Such a run is written
# when its print starts (archive redesign) and is not a print that happened
# yet: statistics, the print log and everything else that reads runs leave it
# out, without each having to say so (see _hide_open_runs below).
RUN_OPEN = "printing"
# Execution option for the few that need open runs too: the print's own start
# and end, which find and fill in the run.
INCLUDE_OPEN_RUNS = "include_open_runs"


class PrintLogEntry(Base):
    """Independent print log entry. Written when print events occur.

    This is a separate table from archives/queue — clearing the log
    never touches archives or queue items.

    archive_id is a nullable FK so log entries survive archive deletion (ON
    DELETE SET NULL). Aggregating runs per archive — for the per-archive
    "Print Log" view and for statistics that should not double-count
    overwritten archives (#1378) — is done via WHERE archive_id = X.
    """

    __tablename__ = "print_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    archive_id: Mapped[int | None] = mapped_column(
        ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Which queue item produced this run, when one did. Printer-initiated
    # prints have none. Batch cost/energy roll-up joins on this (#342): the
    # archive alone can't attribute a run to an order because several orders
    # — and plain reprints — share one archive.
    queue_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("print_queue.id", ondelete="SET NULL"), nullable=True, index=True
    )
    print_name: Mapped[str | None] = mapped_column(String(255))
    # Which plate of a multi-plate 3MF this run printed (1-based). The archive
    # holds one plate, its latest run's, and every run of a file shares that
    # archive, so a run's own plate is kept here. NULL when not known.
    plate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    printer_name: Mapped[str | None] = mapped_column(String(255))
    printer_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # completed, failed, stopped, cancelled, skipped
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    filament_type: Mapped[str | None] = mapped_column(String(50))
    filament_color: Mapped[str | None] = mapped_column(String(50))
    filament_used_grams: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    energy_kwh: Mapped[float | None] = mapped_column(Float)
    energy_cost: Mapped[float | None] = mapped_column(Float)
    # Printer wear for this run: duration at the printer's hourly wear cost (#694).
    wear_cost: Mapped[float | None] = mapped_column(Float)
    # True when the run's times were not measured: a print closed at reconnect
    # (#3310), whose end is its start plus the slicer's estimate when it
    # completed, or the reconnect (duration 0). Counted in
    # the totals, left out of the time accuracy. NULL for a measured run.
    duration_unmeasured: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(100))
    # User quality verdict ('good' / 'reject'), mirrored from the archive on
    # PATCH exactly like status/failure_reason (#1444 mirror) so verdict-aware
    # statistics can stay on this table (#1898).
    user_verdict: Mapped[str | None] = mapped_column(String(10), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_username: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # What describes one print and today still lives on the archive, which
    # the archive redesign moves here. Written alongside the archive's own
    # columns for now; read from the archive until each part moves over.
    subtask_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    billing_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cost_center_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wallet_charge_skipped: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    energy_start_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    energy_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    energy_start_plug_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    energy_start_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_verdict_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    user_verdict_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirm_requested: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confirm_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    confirm_token_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    timelapse_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    timelapse_baseline: Mapped[list | None] = mapped_column(JSON, nullable=True)
    timelapse_baseline_unread: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bambuddy_forced_timelapse: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Ended by a stop that could not reach the printer: logged without word
    # from it. The printer's real end, if it still comes, replaces it.
    ended_offline: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


@event.listens_for(Session, "do_orm_execute")
def _hide_open_runs(state: ORMExecuteState) -> None:
    """Every ORM read, update and delete of runs leaves open runs out, unless
    it asks for them with ``execution_options(include_open_runs=True)``.

    Fail-closed on purpose: some 25 places read runs -- statistics, failure
    analysis, metrics, project and batch totals, the print log, repricing,
    purge -- and a run that has not ended counted in any of them is a print
    that did not happen. A new reader is covered without knowing about it.
    """
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get(INCLUDE_OPEN_RUNS, False):
        return
    state.statement = state.statement.options(_FINISHED_RUNS_ONLY)


# Built once: a new option per statement doubled the listener's cost.
_FINISHED_RUNS_ONLY = with_loader_criteria(PrintLogEntry, PrintLogEntry.status != RUN_OPEN, include_aliases=True)

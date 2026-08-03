from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrintQueueItem(Base):
    """Print queue item for scheduled/queued prints."""

    __tablename__ = "print_queue"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Links
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), nullable=True)
    # Target printer model for model-based assignment (mutually exclusive with printer_id)
    # When set, scheduler assigns to any idle printer of matching model
    target_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Target location filter for model-based assignment (only used with target_model)
    # When set, only printers in this location are considered
    target_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Required filament types for model-based assignment (JSON array, e.g., '["PLA", "PETG"]')
    # Used by scheduler to validate printer has compatible filaments loaded
    required_filament_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Waiting reason - explains why a model-based job hasn't started yet
    # Set by scheduler when no matching printer is available
    waiting_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Either archive_id OR library_file_id must be set (archive created at print start from library file)
    archive_id: Mapped[int | None] = mapped_column(ForeignKey("print_archives.id", ondelete="CASCADE"), nullable=True)
    library_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_files.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("print_batches.id", ondelete="SET NULL"), nullable=True)

    # Scheduling
    position: Mapped[int] = mapped_column(Integer, default=0)  # Queue order
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = ASAP
    manual_start: Mapped[bool] = mapped_column(Boolean, default=False)  # Requires manual trigger to start

    # Conditions
    require_previous_success: Mapped[bool] = mapped_column(Boolean, default=False)

    # Power management
    auto_off_after: Mapped[bool] = mapped_column(Boolean, default=False)  # Power off printer after print

    # AMS mapping: JSON array of global tray IDs for each filament slot
    # Format: "[5, -1, 2, -1]" where position = slot_id-1, value = global tray ID (-1 = unused)
    ams_mapping: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Filament overrides for model-based assignment: JSON array of override objects
    # Format: '[{"slot_id": 1, "type": "PLA", "color": "#FFFFFF"}]'
    # Only slots with overrides are included (sparse). null = use original 3MF values.
    filament_overrides: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Plate ID for multi-plate 3MF files (1-indexed, None = auto-detect/plate 1)
    plate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Shortest-job-first scheduling
    print_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Cached from archive/library
    been_jumped: Mapped[bool] = mapped_column(Boolean, default=False)  # Starvation guard for SJF

    # Auto-print G-code injection (#422)
    gcode_injection: Mapped[bool] = mapped_column(Boolean, default=False)

    # How many times the start-watchdog has reverted this item from 'printing'
    # back to 'pending' (#2555). A printer that accepts project_file but never
    # starts (#1678) used to be retried forever: upload, wait out the watchdog,
    # revert, upload again — burning a full 3MF transfer per cycle and, with
    # the queue dispatching serially, dragging every other printer's start time
    # out with it. The counter bounds that loop; see DISPATCH_MAX_ATTEMPTS.
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # H2C dual-nozzle-rack slicer pick preservation (#1780). BambuStudio's
    # project_file MQTT command for rack-swap-capable models (O1C2 today)
    # carries per-filament physical nozzle position IDs in `nozzle_mapping`,
    # forwarded verbatim through the queue and replayed by the dispatcher so
    # the firmware honours the user's pick instead of falling back to
    # "last matching nozzle type" auto-pick. Stored as opaque JSON string
    # (list[int]); NULL on every other model. `nozzles_info` is a deprecated
    # column from the original #1780 attempt — kept nullable so old rows still
    # load; never written to or read from.
    nozzle_mapping: Mapped[str | None] = mapped_column(Text, nullable=True)
    nozzles_info: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Printer-card direct uploads create transient library rows. When this is
    # true, the scheduler deletes the source row/files after archiving a copy.
    cleanup_library_after_dispatch: Mapped[bool] = mapped_column(Boolean, default=False)

    # Print options. bed_levelling / flow_cali / nozzle_offset_cali are tri-state
    # strings (off/on/auto) matching BambuStudio; "auto" = skip if recently done.
    # The remaining three stay boolean (BambuStudio exposes no auto for them).
    bed_levelling: Mapped[str] = mapped_column(String(8), default="auto")
    flow_cali: Mapped[str] = mapped_column(String(8), default="auto")
    vibration_cali: Mapped[bool] = mapped_column(Boolean, default=True)
    layer_inspect: Mapped[bool] = mapped_column(Boolean, default=False)
    timelapse: Mapped[bool] = mapped_column(Boolean, default=False)
    use_ams: Mapped[bool] = mapped_column(Boolean, default=True)
    # Nozzle offset calibration — dual-nozzle printers only, MQTT-gated (#1682)
    nozzle_offset_cali: Mapped[str] = mapped_column(String(8), default="auto")

    # Preheat / heat-soak override (#1468). 'inherit' uses the global
    # preheat_enabled setting; 'on' / 'off' force the per-item decision. The
    # chamber target falls through: per-item override → max(filament-map[loaded
    # tray type]) → 0 (skips chamber phase). 'inherit' + global off + override
    # null = no preheat. Default 'inherit' so existing queue items behave
    # exactly as before the migration.
    preheat_override: Mapped[str] = mapped_column(String(10), default="inherit")
    preheat_chamber_target_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Status: pending, printing, completed, failed, skipped, cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # Dispatch claim (#2615). Set atomically by the scheduler the moment it
    # begins dispatching this row and cleared when dispatch ends. The row stays
    # `status='pending'` throughout the (slow) FTP upload, which left a window
    # where a concurrent PATCH could reassign printer_id mid-upload and split the
    # queue row from the archive/expected-print/physical command. While this is
    # set the edit routes reject changes (409) and the scheduler won't re-select
    # the row. Startup reconciliation clears any left over by a crash mid-dispatch
    # (no coroutine survives a restart), so a stale claim never wedges an item.
    dispatching_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Cleared by the per-printer "Resume after failure" action (#1818) so the
    # scheduler's `_check_previous_success` lookback skips this row. Without
    # this, a single `failed` or `aborted` print poisoned every later
    # `require_previous_success` item on the same printer forever — the
    # lookback excluded `skipped` but had no way to dismiss the originating
    # failure. The flag is per-item, not per-printer, so a fresh failure
    # after a resume re-gates downstream items independently.
    gate_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)

    # Set by the dispatch scheduler when the assigned spool can't satisfy
    # this print's per-slot filament weight (#1496). Display-only flag — the
    # actual deficit is recomputed live every time the user clicks ▶, so
    # swapping a spool to a fuller one between flag and dispatch clears the
    # block automatically.
    filament_short: Mapped[bool] = mapped_column(Boolean, default=False)

    # User has acknowledged the filament-shortage warning for this item
    # ("Print Anyway"). Set by the start route when the user passes
    # skip_filament_check=true, or at queue-creation time if PrintModal's
    # frontend deficit warning was acknowledged. Survives scheduler ticks so
    # the dispatch no longer bounces between "user said anyway" and
    # "scheduler re-flagged" (#1698-followup).
    skip_filament_check: Mapped[bool] = mapped_column(Boolean, default=False)

    # Tracking
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # User tracking (who added this to the queue)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    printer: Mapped["Printer"] = relationship()
    archive: Mapped["PrintArchive | None"] = relationship()
    library_file: Mapped["LibraryFile | None"] = relationship()
    project: Mapped["Project | None"] = relationship(back_populates="queue_items")
    batch: Mapped["PrintBatch | None"] = relationship(back_populates="queue_items")
    created_by: Mapped["User | None"] = relationship()
    variants: Mapped[list["PrintQueueVariant"]] = relationship(
        back_populates="queue_item",
        cascade="all, delete-orphan",
        order_by="PrintQueueVariant.position",
    )


class PrintQueueVariant(Base):
    """One candidate file for a queue item that may print on several models (#671).

    A user with an H2S and an H2C slices the same job twice and does not care
    which machine runs it. Each slice becomes a variant; the scheduler walks them
    in ``position`` order and takes the first whose model has an idle printer.

    **This is a snapshot, not a pointer.** The candidate list is copied from the
    library's variant group when the item is queued, and every per-file setting
    the dispatcher needs is copied with it. Two reasons:

    - Editing the library group afterwards must not silently change a job that is
      already waiting in the queue.
    - The per-file settings genuinely differ between candidates and are choices
      the user made for *this* job, not properties of the file. An H2C slice is
      dual-nozzle and will not have the same slot count, AMS mapping or nozzle
      mapping as the H2S slice of the same model.

    On a match the winning variant's fields are written onto the queue row before
    the dispatch commit, so everything downstream — upload, archive creation,
    print history, reprint — sees an ordinary single-file item and needs no
    knowledge that variants exist.

    Variants reference library files only. An archive records a print that already
    happened, of one specific file, so it is never a candidate for "which of these
    should we run".
    """

    __tablename__ = "print_queue_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    queue_item_id: Mapped[int] = mapped_column(
        ForeignKey("print_queue.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # User's priority order. When two printers are idle in the same scheduler
    # pass, the lowest position wins — so the choice is reproducible instead of
    # depending on which match the matcher happened to find first.
    position: Mapped[int] = mapped_column(Integer, default=0)

    # CASCADE: deleting the file drops this candidate but leaves the item and its
    # other candidates alone. Losing the *last* candidate is handled by the
    # resolver, which holds the item pending with an explicit waiting_reason
    # rather than letting it sit there looking dispatchable forever.
    library_file_id: Mapped[int] = mapped_column(ForeignKey("library_files.id", ondelete="CASCADE"), nullable=False)
    # Normalized short name ("H2S"), taken from the file's own sliced_for_model
    # at creation, or picked by the user for a legacy file that declares none.
    target_model: Mapped[str] = mapped_column(String(50), nullable=False)

    # Per-file dispatch settings, same semantics as the identically named columns
    # on PrintQueueItem — see there for the formats.
    plate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ams_mapping: Mapped[str | None] = mapped_column(Text, nullable=True)
    nozzle_mapping: Mapped[str | None] = mapped_column(Text, nullable=True)
    filament_overrides: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_filament_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    print_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # How many times this candidate has been dispatched and bounced back to
    # pending by the start-watchdog. The resolver tries least-attempted first, so
    # a printer that accepts the file and never starts (#1678) hands the job to
    # the other machine on the next lap instead of burning the item's whole
    # DISPATCH_MAX_ATTEMPTS budget against the same wedged printer — which is the
    # entire reason the user queued an alternative.
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    queue_item: Mapped["PrintQueueItem"] = relationship(back_populates="variants")
    library_file: Mapped["LibraryFile"] = relationship()


from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.library import LibraryFile  # noqa: E402
from backend.app.models.print_batch import PrintBatch  # noqa: E402
from backend.app.models.printer import Printer  # noqa: E402
from backend.app.models.project import Project  # noqa: E402
from backend.app.models.user import User  # noqa: E402

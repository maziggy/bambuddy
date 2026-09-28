from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrintBatch(Base):
    """Batch grouping for multiple queue items created from the same file.

    A batch carries the *intent* — how many of each plate are wanted — in its
    :class:`PrintBatchPlate` rows, while the queue items it spawned carry what
    was actually dispatched. Keeping the two apart is what lets a failed print
    still count as owed work: the plate row's ``quantity_target`` stays put
    while the failed item lands in the "failed" bucket, so ``remaining`` goes
    back up instead of the order silently under-delivering (#342).

    Batches created before plate rows existed simply have none; every consumer
    falls back to deriving progress from the queue items alone.
    """

    __tablename__ = "print_batches"
    __table_args__ = (Index("uq_print_batches_external", "external_source", "external_ref", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))

    # Source file (one of these)
    archive_id: Mapped[int | None] = mapped_column(ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True)
    library_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_files.id", ondelete="SET NULL"), nullable=True
    )

    # Total requested quantity (for display — actual items may differ if cancelled)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    # Status: active, completed, cancelled
    status: Mapped[str] = mapped_column(String(20), default="active")

    # Optional link to a Project, which owns the heavier planning metadata
    # (BOM, attachments, tags). The batch keeps only the two fields that are
    # useless without it — a date and free text — so an order doesn't force
    # the user to create a Project first.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Link to the record in another system that asked for this batch, e.g. a
    # shop order an integration turned into prints. ``external_ref`` identifies
    # this batch within ``external_source`` and is unique there, so a client
    # that retries a create after a lost response gets a 409 instead of a
    # second batch printing the same order twice. Both set or both null.
    external_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # User tracking
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    archive: Mapped["PrintArchive | None"] = relationship()
    library_file: Mapped["LibraryFile | None"] = relationship()
    created_by: Mapped["User | None"] = relationship()
    queue_items: Mapped[list["PrintQueueItem"]] = relationship(back_populates="batch")
    plates: Mapped[list["PrintBatchPlate"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="PrintBatchPlate.sort_order",
    )


class PrintBatchPlate(Base):
    """How many runs of one plate a batch still owes.

    ``plate_id`` is the plate index within the source 3MF, or NULL for a
    single-plate file / whole-file print — the same convention
    ``PrintQueueItem.plate_id`` uses, so progress can be derived by grouping
    the batch's items on that column.
    """

    __tablename__ = "print_batch_plates"
    __table_args__ = (UniqueConstraint("batch_id", "plate_id", name="uq_batch_plate"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("print_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )

    plate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plate_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # How many runs of this plate the order wants. Zero is legal — a plate the
    # user explicitly marked "not required" keeps its row so it can be raised
    # later without re-creating the order.
    quantity_target: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Display order; mirrors the plate order in the source file.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    batch: Mapped["PrintBatch"] = relationship(back_populates="plates")


from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.library import LibraryFile  # noqa: E402
from backend.app.models.print_queue import PrintQueueItem  # noqa: E402
from backend.app.models.user import User  # noqa: E402

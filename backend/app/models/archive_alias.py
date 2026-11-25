from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ArchiveAlias(Base):
    """An archive merged into another (archive redesign P2): its id still
    finds the archive it went into, so old links, bookmarks and API calls
    keep working. The merged row stays, soft-deleted, so the money booked
    against it (wallet charges, budget reservations) is never rewritten.

    Flattened: an archive merged on later passes its aliases to the one it
    went into, so an id resolves in one step.
    """

    __tablename__ = "archive_aliases"

    # The merged archive's id. Not a foreign key: the merged row may be purged
    # with the other soft-deleted archives, and its id must keep resolving.
    merged_archive_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archive_id: Mapped[int] = mapped_column(ForeignKey("print_archives.id", ondelete="CASCADE"), index=True)
    merged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

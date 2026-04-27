from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class MacroCfgFile(Base):
    """A .cfg file on disk that contains one or more macro definitions."""

    __tablename__ = "macro_cfg_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)  # display name = filename stem
    file_path: Mapped[str] = mapped_column(String(500))  # relative path inside macros_dir
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # last parse error, if any
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    macros: Mapped[list["Macro"]] = relationship(back_populates="cfg_file", cascade="all, delete-orphan")


class Macro(Base):
    __tablename__ = "macros"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which .cfg file this macro was parsed from
    cfg_file_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("macro_cfg_files.id", ondelete="CASCADE"), nullable=True
    )

    # active   — block exists in the .cfg file and parsed cleanly
    # orphaned — block was removed from the file (run history preserved)
    # error    — block exists but failed to parse
    status: Mapped[str] = mapped_column(String(20), default="active")

    trigger_type: Mapped[str] = mapped_column(String(20), default="manual")  # manual|webhook|schedule
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    printer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    cfg_file: Mapped["MacroCfgFile | None"] = relationship(back_populates="macros")
    runs: Mapped[list["MacroRun"]] = relationship(back_populates="macro", cascade="all, delete-orphan")


class MacroRun(Base):
    __tablename__ = "macro_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    macro_id: Mapped[int] = mapped_column(Integer, ForeignKey("macros.id", ondelete="CASCADE"))
    printer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|success|error
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual|webhook|schedule|gcode_embed|terminal
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    log: Mapped[str] = mapped_column(Text, default="")

    macro: Mapped["Macro"] = relationship(back_populates="runs")

"""SQLAlchemy model for tracking installed plugins."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from backend.app.core.database import Base


class PluginRecord(Base):
    __tablename__ = "plugins"

    id = Column(Integer, primary_key=True, index=True)
    plugin_key = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    version = Column(String(50), nullable=False, default="0.0.1")
    description = Column(Text, nullable=True)
    author = Column(String(200), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    settings = Column(Text, nullable=True, default="{}")  # JSON blob
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

from backend.app.schemas.printer import (
    PrinterBase,
    PrinterCreate,
    PrinterUpdate,
    PrinterResponse,
    PrinterStatus,
)
from backend.app.schemas.archive import (
    ArchiveBase,
    ArchiveUpdate,
    ArchiveResponse,
    ProjectPageResponse,
    ProjectPageImage,
)
from backend.app.schemas.smart_plug import (
    SmartPlugBase,
    SmartPlugCreate,
    SmartPlugUpdate,
    SmartPlugResponse,
    SmartPlugControl,
    SmartPlugStatus,
    SmartPlugTestConnection,
)

__all__ = [
    "PrinterBase",
    "PrinterCreate",
    "PrinterUpdate",
    "PrinterResponse",
    "PrinterStatus",
    "ArchiveBase",
    "ArchiveUpdate",
    "ArchiveResponse",
    "ProjectPageResponse",
    "ProjectPageImage",
    "SmartPlugBase",
    "SmartPlugCreate",
    "SmartPlugUpdate",
    "SmartPlugResponse",
    "SmartPlugControl",
    "SmartPlugStatus",
    "SmartPlugTestConnection",
]

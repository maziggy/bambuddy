"""Virtual printer services for slicer integration."""

from backend.app.services.virtual_printer.manager import (
    DEFAULT_VIRTUAL_PRINTER_MODEL,
    VIRTUAL_PRINTER_MODELS,
    virtual_printer_manager,
)

__all__ = [
    "virtual_printer_manager",
    "VIRTUAL_PRINTER_MODELS",
    "DEFAULT_VIRTUAL_PRINTER_MODEL",
]

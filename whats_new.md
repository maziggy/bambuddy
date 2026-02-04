# What's New: 3D File Preview Updates

## Overview
- Expanded 3D previews in the Library File Manager and Printer File Manager.
- Added STL support (interactive 3D view) alongside existing 3MF/G-code previews.
- Improved multi-plate handling for 3MF files in the printer file manager.

## Library File Manager (Files Page)
- 3D preview now supports `.3mf`, `.gcode`, and `.stl` files.
- STL files open in the same viewer modal used by 3MF files.
- Plate-aware selection continues to work for 3MF files with multiple plates.

## Printer File Manager (Printers → File Manager)
- Added a 3D View action for `.3mf`, `.gcode`, and `.stl` files on the printer.
- New printer-side 3MF plate endpoints:
  - Plate list and metadata retrieval.
  - Plate thumbnail retrieval.
- Added a printer-side gcode preview endpoint for 3MF and `.gcode` files.
- STL models are centered on the build sheet in the 3D viewer.

## Model Viewer Enhancements
- Added STL rendering support using `STLLoader`.
- Viewer now selects the correct rendering pipeline based on file type.
- STL models auto-center on the build plate for a consistent viewing experience.

## Affected Areas
- Frontend: File Manager modals and 3D viewer.
- Backend: Printer file preview endpoints for plates, plate thumbnails, and gcode.

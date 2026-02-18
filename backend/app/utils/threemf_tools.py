"""3MF file parsing utilities for filament tracking.

This module provides functions to parse Bambu Lab 3MF files and extract
per-layer filament usage data from the embedded G-code. This enables
accurate partial usage reporting for multi-material prints.
"""

import json
import math
import re
import zipfile
from pathlib import Path

import defusedxml.ElementTree as ET

# Default filament properties
DEFAULT_FILAMENT_DIAMETER = 1.75  # mm
DEFAULT_FILAMENT_DENSITY = 1.24  # g/cm³ (PLA)


def parse_gcode_layer_filament_usage(gcode_content: str) -> dict[int, dict[int, float]]:
    """Parse G-code to extract per-layer, per-filament cumulative extrusion in mm.

    This function tracks filament extrusion across layers and tool changes,
    building a cumulative usage map that can be used to calculate partial
    usage at any layer.

    Args:
        gcode_content: The raw G-code content as a string

    Returns:
        A nested dictionary mapping layer numbers to filament usage:
        {layer: {filament_id: cumulative_mm}, ...}

    Example:
        {0: {0: 125.5}, 1: {0: 250.0, 1: 50.0}, 2: {0: 375.0, 1: 150.0}}

        This shows:
        - Layer 0: filament 0 used 125.5mm cumulative
        - Layer 1: filament 0 used 250mm cumulative, filament 1 used 50mm
        - Layer 2: filament 0 used 375mm cumulative, filament 1 used 150mm

    G-code commands parsed:
        - M73 L<layer>: Layer change marker
        - M620 S<filament>: Filament/tool change (S255 = unload)
        - G0/G1/G2/G3 E<amount>: Extrusion moves
    """
    layer_filaments: dict[int, dict[int, float]] = {}
    current_layer = 0
    active_filament: int | None = None
    cumulative_extrusion: dict[int, float] = {}  # filament_id -> total mm

    for line in gcode_content.splitlines():
        line = line.strip()
        if not line:
            continue

        # Handle comments - skip but check for layer markers
        if line.startswith(";"):
            # Some slicers use comment-based layer markers
            # e.g., "; CHANGE_LAYER" or ";LAYER_CHANGE"
            continue

        # Split line into command and inline comment
        if ";" in line:
            line = line.split(";")[0].strip()

        # Extract command and parameters
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0].upper()

        # Layer change: M73 L<layer>
        # Bambu printers use M73 with L parameter for layer indication
        if cmd == "M73":
            for part in parts[1:]:
                part_upper = part.upper()
                if part_upper.startswith("L"):
                    try:
                        new_layer = int(part[1:])
                        # Save current state before layer change
                        if cumulative_extrusion:
                            layer_filaments[current_layer] = cumulative_extrusion.copy()
                        current_layer = new_layer
                    except ValueError:
                        pass  # Skip G-code lines with unparseable layer numbers

        # Filament change: M620 S<filament>
        # Bambu uses M620 for AMS filament switching
        # S255 means full unload (no active filament)
        elif cmd == "M620":
            for part in parts[1:]:
                part_upper = part.upper()
                if part_upper.startswith("S"):
                    filament_str = part[1:]
                    if filament_str == "255":
                        # Full unload - no active filament
                        active_filament = None
                    else:
                        try:
                            # Extract digits (e.g., "0A" -> 0, "1" -> 1)
                            match = re.match(r"(\d+)", filament_str)
                            if match:
                                active_filament = int(match.group(1))
                        except (ValueError, AttributeError):
                            pass  # Skip unparseable filament switch commands

        # Extrusion moves: G0/G1/G2/G3 with E parameter
        # Only G1 typically has extrusion, but check all for safety
        elif cmd in ("G0", "G1", "G2", "G3"):
            if active_filament is None:
                continue
            for part in parts[1:]:
                part_upper = part.upper()
                if part_upper.startswith("E"):
                    try:
                        extrusion = float(part[1:])
                        # Only count positive extrusion (not retractions)
                        if extrusion > 0:
                            current = cumulative_extrusion.get(active_filament, 0)
                            cumulative_extrusion[active_filament] = current + extrusion
                    except ValueError:
                        pass  # Skip G-code lines with unparseable extrusion values

    # Save final layer state
    if cumulative_extrusion:
        layer_filaments[current_layer] = cumulative_extrusion.copy()

    return layer_filaments


def mm_to_grams(
    length_mm: float,
    diameter_mm: float = DEFAULT_FILAMENT_DIAMETER,
    density_g_cm3: float = DEFAULT_FILAMENT_DENSITY,
) -> float:
    """Convert filament length in mm to weight in grams.

    Uses the formula: mass = volume × density
    where volume = π × r² × length

    Args:
        length_mm: Length of filament in millimeters
        diameter_mm: Filament diameter in millimeters (default: 1.75)
        density_g_cm3: Material density in g/cm³ (default: 1.24 for PLA)

    Returns:
        Weight in grams
    """
    radius_cm = (diameter_mm / 2) / 10  # Convert mm to cm
    length_cm = length_mm / 10  # Convert mm to cm
    volume_cm3 = math.pi * radius_cm * radius_cm * length_cm
    return volume_cm3 * density_g_cm3


def extract_layer_filament_usage_from_3mf(file_path: Path) -> dict[int, dict[int, float]] | None:
    """Extract per-layer filament usage from a 3MF file's embedded G-code.

    Args:
        file_path: Path to the 3MF file

    Returns:
        Dictionary mapping layers to filament usage, or None if parsing fails.
        Format: {layer: {filament_id: cumulative_mm}, ...}
    """
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # Find G-code file(s) - usually plate_1.gcode or Metadata/plate_1.gcode
            gcode_files = [f for f in zf.namelist() if f.endswith(".gcode")]
            if not gcode_files:
                return None

            # Use the first G-code file (typically only one per 3MF export)
            gcode_path = gcode_files[0]
            gcode_content = zf.read(gcode_path).decode("utf-8", errors="ignore")

            return parse_gcode_layer_filament_usage(gcode_content)
    except Exception:
        return None


def get_cumulative_usage_at_layer(
    layer_usage: dict[int, dict[int, float]],
    target_layer: int,
) -> dict[int, float]:
    """Get cumulative filament usage (in mm) up to and including target_layer.

    Args:
        layer_usage: The output from parse_gcode_layer_filament_usage()
        target_layer: The layer number to get usage for

    Returns:
        Dictionary of {filament_id: cumulative_mm} for each filament used
        up to target_layer. Returns empty dict if no data available.
    """
    if not layer_usage:
        return {}

    # Find the highest recorded layer <= target_layer
    # (we store snapshots at layer changes, so we need the closest one)
    relevant_layers = [layer for layer in layer_usage if layer <= target_layer]
    if not relevant_layers:
        return {}

    max_layer = max(relevant_layers)
    return layer_usage.get(max_layer, {})


def extract_filament_properties_from_3mf(file_path: Path) -> dict[int, dict]:
    """Extract filament properties (density, diameter, type) from 3MF metadata.

    Args:
        file_path: Path to the 3MF file

    Returns:
        Dictionary mapping filament IDs to their properties:
        {filament_id: {"diameter": 1.75, "density": 1.24, "type": "PLA"}, ...}

        Note: filament_id is 1-based (matches slot_id in slice_info.config)
    """
    properties: dict[int, dict] = {}
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # Try slice_info.config first for filament types
            if "Metadata/slice_info.config" in zf.namelist():
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)
                for f in root.findall(".//filament"):
                    try:
                        # id is 1-based in slice_info.config
                        fid = int(f.get("id", 0))
                        properties[fid] = {
                            "type": f.get("type", "PLA"),
                            "diameter": DEFAULT_FILAMENT_DIAMETER,
                            "density": DEFAULT_FILAMENT_DENSITY,
                        }
                    except ValueError:
                        pass  # Skip filament entries with unparseable IDs

            # Try project_settings.config for density values
            if "Metadata/project_settings.config" in zf.namelist():
                content = zf.read("Metadata/project_settings.config").decode()
                try:
                    data = json.loads(content)
                    densities = data.get("filament_density", [])
                    for i, density in enumerate(densities):
                        # project_settings uses 0-based indexing, convert to 1-based
                        fid = i + 1
                        if fid not in properties:
                            properties[fid] = {
                                "type": "",
                                "diameter": DEFAULT_FILAMENT_DIAMETER,
                            }
                        try:
                            properties[fid]["density"] = float(density)
                        except (ValueError, TypeError):
                            properties[fid]["density"] = DEFAULT_FILAMENT_DENSITY
                except json.JSONDecodeError:
                    pass  # Skip malformed project_settings.config JSON
    except Exception:
        pass  # Return whatever properties were collected before the error

    return properties


def extract_nozzle_mapping_from_3mf(zf: zipfile.ZipFile) -> dict[int, int] | None:
    """Extract per-slot nozzle/extruder mapping from a 3MF file.

    On dual-nozzle printers (H2D, H2D Pro), each filament slot is assigned to a
    specific nozzle. The slicer may override user preferences when using "Auto For
    Flush" mode, so the actual assignment comes from slice_info.config group_id
    attributes, not from the user's filament_nozzle_map preference.

    Priority:
        1. group_id on <filament> elements in slice_info.config (actual assignment)
        2. filament_nozzle_map in project_settings.config (user preference fallback)

    Both are mapped through physical_extruder_map to get MQTT extruder IDs (0=right, 1=left).

    Args:
        zf: An open ZipFile of the 3MF archive

    Returns:
        Dictionary mapping {slot_id: extruder_id} for dual-nozzle files,
        or None if single-nozzle, missing data, or parse error.
    """
    try:
        if "Metadata/project_settings.config" not in zf.namelist():
            return None

        content = zf.read("Metadata/project_settings.config").decode()
        data = json.loads(content)

        physical_extruder_map = data.get("physical_extruder_map")
        if not physical_extruder_map or len(physical_extruder_map) <= 1:
            return None  # Single-nozzle printer

        # Priority 1: Use group_id from slice_info filament elements.
        # This reflects the actual slicer assignment (respects "Auto For Flush").
        nozzle_mapping: dict[int, int] = {}
        if "Metadata/slice_info.config" in zf.namelist():
            si_content = zf.read("Metadata/slice_info.config").decode()
            si_root = ET.fromstring(si_content)
            for filament_elem in si_root.findall(".//filament"):
                group_id_str = filament_elem.get("group_id")
                filament_id_str = filament_elem.get("id")
                if group_id_str is not None and filament_id_str:
                    try:
                        group_id = int(group_id_str)
                        slot_id = int(filament_id_str)
                        if group_id < len(physical_extruder_map):
                            nozzle_mapping[slot_id] = int(physical_extruder_map[group_id])
                    except (ValueError, TypeError, IndexError):
                        pass

        if nozzle_mapping:
            return nozzle_mapping

        # Priority 2: Fall back to filament_nozzle_map (user preference).
        # This is correct when the user manually assigned nozzles, but may be
        # wrong when the slicer overrides via "Auto For Flush".
        filament_nozzle_map = data.get("filament_nozzle_map")
        if not filament_nozzle_map:
            return None

        for i, slicer_ext_str in enumerate(filament_nozzle_map):
            slot_id = i + 1
            try:
                slicer_ext = int(slicer_ext_str)
                if slicer_ext < len(physical_extruder_map):
                    nozzle_mapping[slot_id] = int(physical_extruder_map[slicer_ext])
            except (ValueError, TypeError, IndexError):
                pass

        return nozzle_mapping if nozzle_mapping else None
    except Exception:
        return None


def extract_filament_usage_from_3mf(file_path: Path, plate_id: int | None = None) -> list[dict]:
    """Extract per-filament total usage from 3MF slice_info.config.

    This extracts the slicer-estimated total usage per filament slot,
    not the per-layer breakdown.

    Args:
        file_path: Path to the 3MF file
        plate_id: Optional plate index to filter for (for multi-plate files)

    Returns:
        List of filament usage dictionaries:
        [{"slot_id": 1, "used_g": 50.5, "type": "PLA", "color": "#FF0000"}, ...]
    """
    filament_usage = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "Metadata/slice_info.config" not in zf.namelist():
                return []

            content = zf.read("Metadata/slice_info.config").decode()
            root = ET.fromstring(content)

            if plate_id is not None:
                # Find the plate element with matching index
                for plate_elem in root.findall(".//plate"):
                    plate_index = None
                    for meta in plate_elem.findall("metadata"):
                        if meta.get("key") == "index":
                            try:
                                plate_index = int(meta.get("value", "0"))
                            except ValueError:
                                pass
                            break

                    if plate_index == plate_id:
                        for f in plate_elem.findall("filament"):
                            filament_id = f.get("id")
                            used_g = f.get("used_g", "0")
                            try:
                                used_amount = float(used_g)
                                if filament_id:
                                    filament_usage.append(
                                        {
                                            "slot_id": int(filament_id),
                                            "used_g": used_amount,
                                            "type": f.get("type", ""),
                                            "color": f.get("color", ""),
                                        }
                                    )
                            except (ValueError, TypeError):
                                pass
                        break
            else:
                # No plate_id specified - extract all filaments
                for f in root.findall(".//filament"):
                    filament_id = f.get("id")
                    used_g = f.get("used_g", "0")
                    try:
                        used_amount = float(used_g)
                        if filament_id:
                            filament_usage.append(
                                {
                                    "slot_id": int(filament_id),
                                    "used_g": used_amount,
                                    "type": f.get("type", ""),
                                    "color": f.get("color", ""),
                                }
                            )
                    except (ValueError, TypeError):
                        pass  # Skip filament entries with unparseable usage values

    except Exception:
        pass  # Return whatever usage data was collected before the error

    return filament_usage

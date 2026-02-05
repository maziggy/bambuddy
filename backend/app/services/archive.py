import hashlib
import json
import logging
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from defusedxml import ElementTree as ET
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.models.archive import PrintArchive
from backend.app.models.filament import Filament
from backend.app.models.printer import Printer

logger = logging.getLogger(__name__)


class ThreeMFParser:
    """Parser for Bambu Lab 3MF files."""

    def __init__(self, file_path: Path, plate_number: int | None = None):
        self.file_path = file_path
        self.plate_number = plate_number  # Which plate was printed (1, 2, 3, etc.)
        self.metadata: dict = {}

    def parse(self) -> dict:
        """Extract metadata from 3MF file."""
        try:
            with zipfile.ZipFile(self.file_path, "r") as zf:
                self._parse_slice_info(zf)  # Now sets self.plate_number from slice_info
                self._parse_project_settings(zf)
                self._parse_gcode_header(zf)
                self._parse_3dmodel(zf)
                self._extract_thumbnail(zf)  # Uses correct plate_number for thumbnail

                # Enhance print_name with plate info if this is a multi-plate export
                plate_index = self.metadata.get("_plate_index")
                if plate_index and plate_index > 1:
                    # Append plate number to distinguish from other plates
                    existing_name = self.metadata.get("print_name", "")
                    if existing_name and f"Plate {plate_index}" not in existing_name:
                        self.metadata["print_name"] = f"{existing_name} - Plate {plate_index}"

                # ALWAYS prefer slice_info values - they contain ONLY filaments actually used in print
                # project_settings contains ALL configured filaments (AMS slots), not just used ones
                if self.metadata.get("_slice_filament_type"):
                    self.metadata["filament_type"] = self.metadata["_slice_filament_type"]
                if self.metadata.get("_slice_filament_color"):
                    self.metadata["filament_color"] = self.metadata["_slice_filament_color"]

                # Clean up internal keys
                self.metadata.pop("_slice_filament_type", None)
                self.metadata.pop("_slice_filament_color", None)
                self.metadata.pop("_plate_index", None)
        except Exception:
            pass
        return self.metadata

    def _parse_slice_info(self, zf: zipfile.ZipFile):
        """Parse slice_info.config for print settings and printable objects."""
        try:
            if "Metadata/slice_info.config" in zf.namelist():
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                # Extract printer_model_id from plate metadata
                # Format: <plate><metadata key="printer_model_id" value="C11" /></plate>
                for meta in root.findall(".//metadata"):
                    key = meta.get("key")
                    value = meta.get("value")
                    if key == "printer_model_id" and value:
                        from backend.app.utils.printer_models import normalize_printer_model_id

                        normalized = normalize_printer_model_id(value)
                        if normalized:
                            self.metadata["sliced_for_model"] = normalized
                        break

                # Find the plate element (single-plate exports only have one plate)
                plate = root.find(".//plate")

                if plate is not None:
                    # Extract metadata from plate element
                    for meta in plate.findall("metadata"):
                        key = meta.get("key")
                        value = meta.get("value")
                        if key == "index" and value:
                            # Extract plate index - this tells us which plate was exported
                            try:
                                extracted_index = int(value)
                                # Set plate_number if not already set from filename
                                if not self.plate_number:
                                    self.plate_number = extracted_index
                                # Store in metadata for print_name generation
                                self.metadata["_plate_index"] = extracted_index
                            except ValueError:
                                pass
                        elif key == "prediction" and value:
                            self.metadata["print_time_seconds"] = int(value)
                        elif key == "weight" and value:
                            self.metadata["filament_used_grams"] = float(value)

                    # Extract printable objects for skip object functionality
                    # Objects are stored as <object identify_id="123" name="Part1" skipped="false" />
                    printable_objects = {}
                    for obj in plate.findall("object"):
                        identify_id = obj.get("identify_id")
                        name = obj.get("name")
                        skipped = obj.get("skipped", "false")

                        # Only include objects that are not pre-skipped
                        if identify_id and name and skipped.lower() != "true":
                            try:
                                printable_objects[int(identify_id)] = name
                            except ValueError:
                                pass

                    if printable_objects:
                        self.metadata["printable_objects"] = printable_objects

                # Get filament info from filaments ACTUALLY USED in the print
                # slice_info has <filament id="1" type="PLA" color="#FFFFFF" used_g="100" />
                # Only include filaments where used_g > 0
                filaments = root.findall(".//filament")
                if filaments:
                    # Collect unique filament types and colors for filaments that are actually used
                    types = []
                    colors = []
                    for f in filaments:
                        # Check if this filament is actually used in the print
                        used_g = f.get("used_g", "0")
                        try:
                            used_amount = float(used_g)
                        except (ValueError, TypeError):
                            used_amount = 0

                        # Only include if used_g > 0 (filament is actually consumed)
                        if used_amount > 0:
                            ftype = f.get("type")
                            fcolor = f.get("color")
                            if ftype and ftype not in types:
                                types.append(ftype)
                            if fcolor and fcolor not in colors:
                                colors.append(fcolor)

                    if types:
                        self.metadata["_slice_filament_type"] = ", ".join(types)
                    if colors:
                        self.metadata["_slice_filament_color"] = ",".join(colors)
        except Exception:
            pass

    def _parse_project_settings(self, zf: zipfile.ZipFile):
        """Parse project settings for print configuration."""
        try:
            if "Metadata/project_settings.config" in zf.namelist():
                content = zf.read("Metadata/project_settings.config").decode()
                try:
                    data = json.loads(content)
                    self._extract_filament_info(data)
                    self._extract_print_settings(data)
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass

    def _parse_gcode_header(self, zf: zipfile.ZipFile):
        """Parse G-code file header for total layer count and printer model."""
        import re

        try:
            # Look for plate_1.gcode or similar
            gcode_files = [f for f in zf.namelist() if f.endswith(".gcode")]
            if not gcode_files:
                return

            # Read first 4KB of G-code (header contains metadata)
            gcode_path = gcode_files[0]
            with zf.open(gcode_path) as f:
                header = f.read(4096).decode("utf-8", errors="ignore")

            # Look for "; total layer number: XX" pattern
            match = re.search(r";\s*total\s+layer\s+number[:\s]+(\d+)", header, re.IGNORECASE)
            if match:
                self.metadata["total_layers"] = int(match.group(1))

            # Look for printer_model in gcode header (fallback if not found in slice_info)
            # Format: "; printer_model = Bambu Lab X1 Carbon" or "; printer_model = X1C"
            if "sliced_for_model" not in self.metadata:
                match = re.search(r";\s*printer_model\s*=\s*(.+)", header, re.IGNORECASE)
                if match:
                    from backend.app.utils.printer_models import normalize_printer_model

                    raw_model = match.group(1).strip()
                    self.metadata["sliced_for_model"] = normalize_printer_model(raw_model)
        except Exception:
            pass

    def _extract_filament_info(self, data: dict):
        """Extract filament info, preferring non-support filaments."""
        try:
            filament_types = data.get("filament_type", [])
            filament_colors = data.get("filament_colour", [])
            filament_is_support = data.get("filament_is_support", [])

            if not filament_types:
                return

            # Collect all non-support filaments
            non_support_types = []
            non_support_colors = []

            for i, ftype in enumerate(filament_types):
                is_support = filament_is_support[i] if i < len(filament_is_support) else "0"
                if is_support == "0":
                    if ftype and ftype not in non_support_types:
                        non_support_types.append(ftype)
                    if i < len(filament_colors) and filament_colors[i]:
                        color = filament_colors[i]
                        if color not in non_support_colors:
                            non_support_colors.append(color)

            # Fallback to first filament if all are support
            if not non_support_types and filament_types:
                non_support_types = [filament_types[0]]
            if not non_support_colors and filament_colors:
                non_support_colors = [filament_colors[0]]

            # Store filament type(s)
            if non_support_types:
                self.metadata["filament_type"] = ", ".join(non_support_types)

            # Store all colors as comma-separated (for multi-color display)
            if non_support_colors:
                self.metadata["filament_color"] = ",".join(non_support_colors)

        except Exception:
            pass

    def _extract_print_settings(self, data: dict):
        """Extract print settings from JSON config."""
        try:
            # Layer height - usually an array, get first value
            if "layer_height" in data:
                val = data["layer_height"]
                if isinstance(val, list) and val:
                    self.metadata["layer_height"] = float(val[0])
                elif isinstance(val, (int, float, str)):
                    self.metadata["layer_height"] = float(val)

            # Nozzle diameter
            if "nozzle_diameter" in data:
                val = data["nozzle_diameter"]
                if isinstance(val, list) and val:
                    self.metadata["nozzle_diameter"] = float(val[0])
                elif isinstance(val, (int, float, str)):
                    self.metadata["nozzle_diameter"] = float(val)

            # Bed temperature - first layer or regular
            for key in ["bed_temperature_initial_layer", "bed_temperature"]:
                if key in data:
                    val = data[key]
                    if isinstance(val, list) and val:
                        self.metadata["bed_temperature"] = int(float(val[0]))
                    elif isinstance(val, (int, float, str)):
                        self.metadata["bed_temperature"] = int(float(val))
                    break

            # Nozzle temperature
            for key in ["nozzle_temperature_initial_layer", "nozzle_temperature"]:
                if key in data:
                    val = data[key]
                    if isinstance(val, list) and val:
                        self.metadata["nozzle_temperature"] = int(float(val[0]))
                    elif isinstance(val, (int, float, str)):
                        self.metadata["nozzle_temperature"] = int(float(val))
                    break

            # Printer model (extract and normalize)
            if "printer_model" in data:
                from backend.app.utils.printer_models import normalize_printer_model

                self.metadata["sliced_for_model"] = normalize_printer_model(data["printer_model"])
        except Exception:
            pass

    def _extract_settings_from_content(self, content: str):
        """Extract print settings from config content."""
        settings_map = {
            "layer_height": ("layer_height", float),
            "nozzle_diameter": ("nozzle_diameter", float),
            "bed_temperature": ("bed_temperature", int),
            "nozzle_temperature": ("nozzle_temperature", int),
        }

        for key, (search_key, converter) in settings_map.items():
            if key not in self.metadata:
                try:
                    # Try JSON format
                    if f'"{search_key}"' in content:
                        start = content.find(f'"{search_key}"')
                        value_start = content.find(":", start) + 1
                        value_end = content.find(",", value_start)
                        if value_end == -1:
                            value_end = content.find("}", value_start)
                        value = content[value_start:value_end].strip().strip('"')
                        self.metadata[key] = converter(value)
                except Exception:
                    pass

    def _parse_3dmodel(self, zf: zipfile.ZipFile):
        """Parse 3D/3dmodel.model for MakerWorld metadata."""
        import re

        try:
            model_path = "3D/3dmodel.model"
            if model_path not in zf.namelist():
                return

            content = zf.read(model_path).decode("utf-8", errors="ignore")

            # Parse XML metadata elements
            # MakerWorld adds metadata like: <metadata name="Designer">username</metadata>
            metadata_pattern = r'<metadata\s+name="([^"]+)"[^>]*>([^<]*)</metadata>'
            matches = re.findall(metadata_pattern, content)

            makerworld_fields = {}
            for name, value in matches:
                makerworld_fields[name] = value.strip()

            # Check for direct MakerWorld URL in content
            url_pattern = r'https?://makerworld\.com/[^\s<>"\']+/models/(\d+)'
            url_match = re.search(url_pattern, content)
            if url_match:
                self.metadata["makerworld_url"] = url_match.group(0)
                self.metadata["makerworld_model_id"] = url_match.group(1)

            # Extract model ID from DSM reference in image URLs
            # Format: https://makerworld.bblmw.com/makerworld/model/DSM00000001275614/...
            # The numeric part (1275614) is the MakerWorld model ID
            if "makerworld_url" not in self.metadata:
                dsm_pattern = r"DSM0+(\d+)"
                dsm_match = re.search(dsm_pattern, content)
                if dsm_match:
                    model_id = dsm_match.group(1)
                    self.metadata["makerworld_url"] = f"https://makerworld.com/en/models/{model_id}"
                    self.metadata["makerworld_model_id"] = model_id

            # Store designer info
            if "Designer" in makerworld_fields:
                self.metadata["designer"] = makerworld_fields["Designer"]
            if "Title" in makerworld_fields:
                self.metadata["print_name"] = makerworld_fields["Title"]

        except Exception:
            pass

    def _extract_thumbnail(self, zf: zipfile.ZipFile):
        """Extract thumbnail image from 3MF.

        If a plate_number was specified, try to use that plate's thumbnail first.
        """
        thumbnail_paths = []

        # If a specific plate was printed, try that thumbnail first
        if self.plate_number:
            thumbnail_paths.append(f"Metadata/plate_{self.plate_number}.png")

        # Fallback to default paths
        thumbnail_paths.extend(
            [
                "Metadata/plate_1.png",
                "Metadata/thumbnail.png",
                "Metadata/model_thumbnail.png",
            ]
        )

        for thumb_path in thumbnail_paths:
            if thumb_path in zf.namelist():
                self.metadata["_thumbnail_data"] = zf.read(thumb_path)
                self.metadata["_thumbnail_ext"] = ".png"
                break


def extract_printable_objects_from_3mf(
    data: bytes, plate_number: int | None = None, include_positions: bool = False
) -> dict[int, str] | dict[int, dict] | tuple[dict[int, dict], list | None]:
    """Extract printable objects from 3MF file bytes.

    This is a lightweight function used during print start to get the list
    of objects that can be skipped.

    Args:
        data: Raw bytes of the 3MF file
        plate_number: Which plate was printed (1-based), or None for first plate
        include_positions: If True, return tuple of (objects dict, bbox_all)

    Returns:
        If include_positions=False: Dictionary mapping identify_id (int) to object name (str)
        If include_positions=True: Tuple of (dict mapping identify_id to {name, x, y}, bbox_all list or None)
    """
    import json
    from io import BytesIO

    printable_objects: dict = {}
    bbox_all: list | None = None

    try:
        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            if "Metadata/slice_info.config" not in zf.namelist():
                return printable_objects

            content = zf.read("Metadata/slice_info.config").decode()
            root = ET.fromstring(content)

            # Find the correct plate
            if plate_number:
                plate = root.find(f".//plate[@plate_idx='{plate_number}']")
                if plate is None:
                    plate = root.find(".//plate")
            else:
                plate = root.find(".//plate")

            if plate is None:
                return printable_objects

            # Get actual plate index from metadata (sliced files only have one plate)
            plate_idx = plate_number or 1
            for meta in plate.findall("metadata"):
                if meta.get("key") == "index":
                    try:
                        plate_idx = int(meta.get("value", "1"))
                    except ValueError:
                        pass
                    break

            # Load position data from plate_N.json if we need positions
            # Build a lookup by name - use list to handle duplicate names
            bbox_by_name: dict[str, list[list]] = {}
            if include_positions:
                plate_json_path = f"Metadata/plate_{plate_idx}.json"
                if plate_json_path in zf.namelist():
                    try:
                        plate_json = json.loads(zf.read(plate_json_path).decode())
                        # Get bbox_all - the bounding box of all objects (used for image bounds)
                        bbox_all = plate_json.get("bbox_all")
                        for bbox_obj in plate_json.get("bbox_objects", []):
                            obj_name = bbox_obj.get("name")
                            bbox = bbox_obj.get("bbox", [])
                            if obj_name and len(bbox) >= 4:
                                if obj_name not in bbox_by_name:
                                    bbox_by_name[obj_name] = []
                                bbox_by_name[obj_name].append(bbox)
                    except (json.JSONDecodeError, KeyError):
                        pass

            # Extract objects from slice_info.config
            for obj in plate.findall("object"):
                identify_id = obj.get("identify_id")
                name = obj.get("name")
                skipped = obj.get("skipped", "false")

                if identify_id and name and skipped.lower() != "true":
                    try:
                        obj_id = int(identify_id)
                        if include_positions:
                            x, y = None, None
                            # Match by name - pop first bbox to handle duplicates
                            bboxes = bbox_by_name.get(name)
                            if bboxes:
                                bbox = bboxes.pop(0)
                                # Calculate center from bbox [x_min, y_min, x_max, y_max]
                                x = (bbox[0] + bbox[2]) / 2
                                y = (bbox[1] + bbox[3]) / 2
                            printable_objects[obj_id] = {"name": name, "x": x, "y": y}
                        else:
                            printable_objects[obj_id] = name
                    except ValueError:
                        pass

    except Exception:
        pass

    if include_positions:
        return printable_objects, bbox_all
    return printable_objects


class ProjectPageParser:
    """Parser for extracting project page data from Bambu Lab 3MF files."""

    def __init__(self, file_path: Path):
        self.file_path = file_path

    def parse(self, archive_id: int) -> dict:
        """Extract project page metadata and images from 3MF file."""
        import html
        import re

        result = {
            "title": None,
            "description": None,
            "designer": None,
            "designer_user_id": None,
            "license": None,
            "copyright": None,
            "creation_date": None,
            "modification_date": None,
            "origin": None,
            "profile_title": None,
            "profile_description": None,
            "profile_cover": None,
            "profile_user_id": None,
            "profile_user_name": None,
            "design_model_id": None,
            "design_profile_id": None,
            "design_region": None,
            "model_pictures": [],
            "profile_pictures": [],
            "thumbnails": [],
        }

        try:
            with zipfile.ZipFile(self.file_path, "r") as zf:
                # Parse 3D/3dmodel.model for metadata
                model_path = "3D/3dmodel.model"
                if model_path in zf.namelist():
                    content = zf.read(model_path).decode("utf-8", errors="ignore")

                    # Extract metadata elements using regex
                    # Format: <metadata name="Key">Value</metadata> or <metadata name="Key" />
                    metadata_pattern = r'<metadata\s+name="([^"]+)"[^>]*>([^<]*)</metadata>'
                    matches = re.findall(metadata_pattern, content)

                    field_mapping = {
                        "Title": "title",
                        "Description": "description",
                        "Designer": "designer",
                        "DesignerUserId": "designer_user_id",
                        "License": "license",
                        "Copyright": "copyright",
                        "CreationDate": "creation_date",
                        "ModificationDate": "modification_date",
                        "Origin": "origin",
                        "ProfileTitle": "profile_title",
                        "ProfileDescription": "profile_description",
                        "ProfileCover": "profile_cover",
                        "ProfileUserId": "profile_user_id",
                        "ProfileUserName": "profile_user_name",
                        "DesignModelId": "design_model_id",
                        "DesignProfileId": "design_profile_id",
                        "DesignRegion": "design_region",
                    }

                    for name, value in matches:
                        if name in field_mapping:
                            # Decode HTML entities multiple times (content is often triple-encoded)
                            decoded = value.strip()
                            prev = None
                            while prev != decoded:
                                prev = decoded
                                decoded = html.unescape(decoded)
                            # Normalize non-breaking spaces to regular spaces
                            decoded = decoded.replace("\xa0", " ")
                            result[field_mapping[name]] = decoded if decoded else None

                # List images in Auxiliaries folder
                from urllib.parse import quote

                for name in zf.namelist():
                    if name.startswith("Auxiliaries/Model Pictures/"):
                        filename = name.split("/")[-1]
                        if filename:
                            result["model_pictures"].append(
                                {
                                    "name": filename,
                                    "path": name,
                                    "url": f"/api/v1/archives/{archive_id}/project-image/{quote(name, safe='')}",
                                }
                            )
                    elif name.startswith("Auxiliaries/Profile Pictures/"):
                        filename = name.split("/")[-1]
                        if filename:
                            result["profile_pictures"].append(
                                {
                                    "name": filename,
                                    "path": name,
                                    "url": f"/api/v1/archives/{archive_id}/project-image/{quote(name, safe='')}",
                                }
                            )
                    elif name.startswith("Auxiliaries/.thumbnails/"):
                        filename = name.split("/")[-1]
                        if filename:
                            result["thumbnails"].append(
                                {
                                    "name": filename,
                                    "path": name,
                                    "url": f"/api/v1/archives/{archive_id}/project-image/{quote(name, safe='')}",
                                }
                            )

        except Exception as e:
            result["_error"] = str(e)

        return result

    def get_image(self, image_path: str) -> tuple[bytes, str] | None:
        """Extract an image from the 3MF file.

        Returns tuple of (image_data, content_type) or None if not found.
        """
        try:
            with zipfile.ZipFile(self.file_path, "r") as zf:
                if image_path in zf.namelist():
                    data = zf.read(image_path)
                    # Determine content type from extension
                    ext = image_path.lower().split(".")[-1]
                    content_types = {
                        "png": "image/png",
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "webp": "image/webp",
                        "gif": "image/gif",
                    }
                    content_type = content_types.get(ext, "application/octet-stream")
                    return (data, content_type)
        except Exception:
            pass
        return None

    def update_metadata(self, updates: dict) -> bool:
        """Update project page metadata in the 3MF file.

        Args:
            updates: Dict with fields to update (title, description, designer, etc.)

        Returns:
            True if successful, False otherwise.
        """
        import html
        import re
        import tempfile

        try:
            # Read the 3MF file
            with zipfile.ZipFile(self.file_path, "r") as zf_read:
                # Find and read the 3dmodel.model file
                model_path = "3D/3dmodel.model"
                if model_path not in zf_read.namelist():
                    return False

                content = zf_read.read(model_path).decode("utf-8")

                # Update metadata fields
                field_mapping = {
                    "title": "Title",
                    "description": "Description",
                    "designer": "Designer",
                    "license": "License",
                    "copyright": "Copyright",
                    "profile_title": "ProfileTitle",
                    "profile_description": "ProfileDescription",
                }

                for field, xml_name in field_mapping.items():
                    if field in updates and updates[field] is not None:
                        new_value = html.escape(updates[field])
                        # Replace existing metadata or we'd need to add it
                        pattern = rf'(<metadata\s+name="{xml_name}"[^>]*>)[^<]*(</metadata>)'
                        replacement = rf"\g<1>{new_value}\g<2>"
                        content = re.sub(pattern, replacement, content)

                # Write to a temporary file first
                with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
                    tmp_path = Path(tmp.name)

                # Create new zip with updated content
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf_write:
                    for item in zf_read.namelist():
                        if item == model_path:
                            zf_write.writestr(item, content.encode("utf-8"))
                        else:
                            zf_write.writestr(item, zf_read.read(item))

            # Replace original file with updated one
            shutil.move(tmp_path, self.file_path)
            return True

        except Exception:
            # Clean up temp file if it exists
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
            return False


class ArchiveService:
    """Service for archiving print jobs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """Compute SHA256 hash of a file for duplicate detection."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read in chunks to handle large files
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    async def get_duplicate_hashes(self) -> set[str]:
        """Get all content hashes that appear more than once.

        Returns a set of hashes that have duplicates.
        """
        from sqlalchemy import func

        result = await self.db.execute(
            select(PrintArchive.content_hash)
            .where(PrintArchive.content_hash.isnot(None))
            .group_by(PrintArchive.content_hash)
            .having(func.count(PrintArchive.id) > 1)
        )
        return {row[0] for row in result.all()}

    async def find_duplicates(
        self,
        archive_id: int,
        content_hash: str | None = None,
        print_name: str | None = None,
        makerworld_model_id: str | None = None,
    ) -> list[dict]:
        """Find duplicate archives based on hash or name matching.

        Returns list of dicts with id, print_name, created_at, match_type.
        """
        duplicates = []

        # First, find exact matches by content hash
        if content_hash:
            result = await self.db.execute(
                select(PrintArchive)
                .where(
                    and_(
                        PrintArchive.content_hash == content_hash,
                        PrintArchive.id != archive_id,
                    )
                )
                .order_by(PrintArchive.created_at.desc())
                .limit(10)
            )
            for archive in result.scalars().all():
                duplicates.append(
                    {
                        "id": archive.id,
                        "print_name": archive.print_name,
                        "created_at": archive.created_at,
                        "match_type": "exact",
                    }
                )

        # Then, find similar matches by print name or MakerWorld ID
        if print_name or makerworld_model_id:
            conditions = [PrintArchive.id != archive_id]

            name_conditions = []
            if print_name:
                # Match if print names are similar (ignoring case)
                name_conditions.append(PrintArchive.print_name.ilike(print_name))
            if makerworld_model_id:
                # Match by MakerWorld model ID stored in extra_data
                # Use json_extract for SQLite compatibility (astext is PostgreSQL-only)
                from sqlalchemy import func

                name_conditions.append(
                    func.json_extract(PrintArchive.extra_data, "$.makerworld_model_id") == str(makerworld_model_id)
                )

            if name_conditions:
                conditions.append(or_(*name_conditions))

                result = await self.db.execute(
                    select(PrintArchive).where(and_(*conditions)).order_by(PrintArchive.created_at.desc()).limit(10)
                )
                for archive in result.scalars().all():
                    # Don't add if already in duplicates (exact match)
                    if not any(d["id"] == archive.id for d in duplicates):
                        duplicates.append(
                            {
                                "id": archive.id,
                                "print_name": archive.print_name,
                                "created_at": archive.created_at,
                                "match_type": "similar",
                            }
                        )

        return duplicates

    async def archive_print(
        self,
        printer_id: int | None,
        source_file: Path,
        print_data: dict | None = None,
        created_by_id: int | None = None,
    ) -> PrintArchive | None:
        """Archive a 3MF file with metadata.

        Args:
            printer_id: ID of the printer (optional)
            source_file: Path to the 3MF file
            print_data: Print data from MQTT (optional)
            created_by_id: User ID who created this archive (optional, for user tracking)
        """
        # Verify printer exists if specified
        if printer_id is not None:
            result = await self.db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            if not printer:
                return None

        # Create archive directory structure
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{timestamp}_{source_file.stem}"
        # Use "unassigned" folder for archives without a printer
        printer_folder = str(printer_id) if printer_id is not None else "unassigned"
        archive_dir = settings.archive_dir / printer_folder / archive_name
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Copy 3MF file
        dest_file = archive_dir / source_file.name
        shutil.copy2(source_file, dest_file)

        # Compute content hash for duplicate detection
        content_hash = self.compute_file_hash(dest_file)

        # Extract plate number from filename (e.g., "plate_5" from "/data/Metadata/plate_5.gcode")
        plate_number = None
        if print_data:
            filename = print_data.get("filename", "")
            match = re.search(r"plate_(\d+)", filename)
            if match:
                plate_number = int(match.group(1))

        # Parse 3MF metadata
        parser = ThreeMFParser(dest_file, plate_number=plate_number)
        metadata = parser.parse()

        # Save thumbnail if present
        thumbnail_path = None
        if "_thumbnail_data" in metadata:
            thumb_file = archive_dir / f"thumbnail{metadata['_thumbnail_ext']}"
            thumb_file.write_bytes(metadata["_thumbnail_data"])
            thumbnail_path = str(thumb_file.relative_to(settings.base_dir))
            del metadata["_thumbnail_data"]
            del metadata["_thumbnail_ext"]

        # Merge with print data from MQTT
        if print_data:
            metadata["_print_data"] = print_data

        # Determine status and timestamps
        status = print_data.get("status", "completed") if print_data else "archived"
        started_at = datetime.now() if status == "printing" else None
        completed_at = datetime.now() if status in ("completed", "failed", "archived") else None

        # Calculate cost based on filament usage and type
        cost = None
        filament_grams = metadata.get("filament_used_grams")
        filament_type = metadata.get("filament_type")
        if filament_grams and filament_type:
            # For multi-material prints, use the first filament type for cost calculation
            primary_type = filament_type.split(",")[0].strip()
            # Look up filament cost_per_kg from database
            filament_result = await self.db.execute(select(Filament).where(Filament.type == primary_type).limit(1))
            filament = filament_result.scalar_one_or_none()
            if filament:
                cost = round((filament_grams / 1000) * filament.cost_per_kg, 2)
            else:
                # Use default filament cost from settings
                from backend.app.api.routes.settings import get_setting

                default_cost_setting = await get_setting(self.db, "default_filament_cost")
                default_cost_per_kg = float(default_cost_setting) if default_cost_setting else 25.0
                cost = round((filament_grams / 1000) * default_cost_per_kg, 2)

        # Calculate quantity from printable objects count
        # printable_objects is a dict of {identify_id: name} for non-skipped objects
        quantity = 1  # Default to 1
        printable_objects = metadata.get("printable_objects")
        if printable_objects and isinstance(printable_objects, dict):
            quantity = len(printable_objects)
            logger.debug(f"Auto-detected {quantity} parts from 3MF printable objects")

        # Create archive record
        archive = PrintArchive(
            printer_id=printer_id,
            filename=source_file.name,
            file_path=str(dest_file.relative_to(settings.base_dir)),
            file_size=dest_file.stat().st_size,
            content_hash=content_hash,
            thumbnail_path=thumbnail_path,
            print_name=metadata.get("print_name") or source_file.stem,
            print_time_seconds=metadata.get("print_time_seconds"),
            filament_used_grams=metadata.get("filament_used_grams"),
            filament_type=metadata.get("filament_type"),
            filament_color=metadata.get("filament_color"),
            layer_height=metadata.get("layer_height"),
            total_layers=metadata.get("total_layers"),
            nozzle_diameter=metadata.get("nozzle_diameter"),
            bed_temperature=metadata.get("bed_temperature"),
            nozzle_temperature=metadata.get("nozzle_temperature"),
            sliced_for_model=metadata.get("sliced_for_model"),
            makerworld_url=metadata.get("makerworld_url"),
            designer=metadata.get("designer"),
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            cost=cost,
            quantity=quantity,
            extra_data=metadata,
            created_by_id=created_by_id,
        )

        self.db.add(archive)
        await self.db.commit()
        await self.db.refresh(archive)

        return archive

    async def get_archive(self, archive_id: int) -> PrintArchive | None:
        """Get an archive by ID with relationships loaded."""
        from sqlalchemy.orm import selectinload

        result = await self.db.execute(
            select(PrintArchive)
            .options(selectinload(PrintArchive.created_by), selectinload(PrintArchive.project))
            .where(PrintArchive.id == archive_id)
        )
        return result.scalar_one_or_none()

    async def update_archive_status(
        self,
        archive_id: int,
        status: str,
        completed_at: datetime | None = None,
        failure_reason: str | None = None,
    ) -> bool:
        """Update the status of an archive."""
        archive = await self.get_archive(archive_id)
        if not archive:
            return False

        archive.status = status
        if completed_at:
            archive.completed_at = completed_at
        if failure_reason:
            archive.failure_reason = failure_reason

        await self.db.commit()
        return True

    async def add_reprint_cost(self, archive_id: int) -> bool:
        """Add cost for a reprint to the existing archive cost."""
        archive = await self.get_archive(archive_id)
        if not archive:
            return False

        if not archive.filament_used_grams or not archive.filament_type:
            return False

        # Calculate cost based on filament type or default
        from backend.app.api.routes.settings import get_setting

        primary_type = archive.filament_type.split(",")[0].strip()

        # Look up filament cost_per_kg from database
        filament_result = await self.db.execute(select(Filament).where(Filament.type == primary_type).limit(1))
        filament = filament_result.scalar_one_or_none()

        if filament:
            cost_per_kg = filament.cost_per_kg
        else:
            # Use default filament cost from settings
            default_cost_setting = await get_setting(self.db, "default_filament_cost")
            cost_per_kg = float(default_cost_setting) if default_cost_setting else 25.0

        additional_cost = round((archive.filament_used_grams / 1000) * cost_per_kg, 2)

        # Add to existing cost (or set if None)
        if archive.cost is None:
            archive.cost = additional_cost
        else:
            archive.cost = round(archive.cost + additional_cost, 2)

        await self.db.commit()
        logger.info(f"Added reprint cost {additional_cost} to archive {archive_id}, new total: {archive.cost}")
        return True

    async def list_archives(
        self,
        printer_id: int | None = None,
        project_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PrintArchive]:
        """List archives with optional filtering."""
        from sqlalchemy.orm import selectinload

        query = (
            select(PrintArchive)
            .options(selectinload(PrintArchive.project), selectinload(PrintArchive.created_by))
            .order_by(PrintArchive.created_at.desc())
        )

        if printer_id:
            query = query.where(PrintArchive.printer_id == printer_id)

        if project_id:
            query = query.where(PrintArchive.project_id == project_id)

        query = query.limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def delete_archive(self, archive_id: int) -> bool:
        """Delete an archive and its files."""
        archive = await self.get_archive(archive_id)
        if not archive:
            return False

        # Delete files - with CRITICAL safety checks to prevent accidental deletion
        # of parent directories (e.g., /opt) if file_path is empty/malformed
        if archive.file_path and archive.file_path.strip():
            file_path = settings.base_dir / archive.file_path
            if file_path.exists():
                archive_dir = file_path.parent

                # Safety check 1: archive_dir must be inside archive_dir
                try:
                    archive_dir.resolve().relative_to(settings.archive_dir.resolve())
                except ValueError:
                    logger.error(
                        f"SECURITY: Refusing to delete archive {archive_id} - "
                        f"path {archive_dir} is outside archive directory {settings.archive_dir}"
                    )
                    # Still delete the database record, just not the files
                    await self.db.delete(archive)
                    await self.db.commit()
                    return True

                # Safety check 2: archive_dir must be at least 1 level deep inside archive_dir
                # (should be archive_dir/uuid/file.3mf, so parent should be archive_dir/uuid)
                try:
                    relative_path = archive_dir.resolve().relative_to(settings.archive_dir.resolve())
                    if len(relative_path.parts) < 1:
                        logger.error(
                            f"SECURITY: Refusing to delete archive {archive_id} - "
                            f"path {archive_dir} is not deep enough inside archive directory"
                        )
                        await self.db.delete(archive)
                        await self.db.commit()
                        return True
                except ValueError:
                    pass  # Already handled above

                shutil.rmtree(archive_dir, ignore_errors=True)
        else:
            logger.error(
                f"SECURITY: Refusing to delete files for archive {archive_id} - "
                f"file_path is empty or invalid: '{archive.file_path}'"
            )

        # Delete database record
        await self.db.delete(archive)
        await self.db.commit()
        return True

    async def attach_timelapse(
        self,
        archive_id: int,
        timelapse_data: bytes,
        filename: str = "timelapse.mp4",
    ) -> bool:
        """Attach a timelapse video to an archive."""
        import asyncio

        archive = await self.get_archive(archive_id)
        if not archive:
            return False

        # Get archive directory
        file_path = settings.base_dir / archive.file_path
        archive_dir = file_path.parent

        # Save timelapse - use thread pool to avoid blocking event loop
        # (timelapse files can be 100MB+, sync write blocks for seconds)
        timelapse_file = archive_dir / filename
        await asyncio.to_thread(timelapse_file.write_bytes, timelapse_data)

        # Update archive record
        archive.timelapse_path = str(timelapse_file.relative_to(settings.base_dir))
        await self.db.commit()

        return True

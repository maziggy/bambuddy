import json
import zipfile
import shutil
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer


class ThreeMFParser:
    """Parser for Bambu Lab 3MF files."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.metadata: dict = {}

    def parse(self) -> dict:
        """Extract metadata from 3MF file."""
        try:
            with zipfile.ZipFile(self.file_path, "r") as zf:
                self._parse_slice_info(zf)
                self._parse_project_settings(zf)
                self._parse_3dmodel(zf)
                self._extract_thumbnail(zf)

                # Prefer slice_info for colors (shows ALL filaments actually used in print)
                # project_settings may filter out "support" filaments incorrectly
                if self.metadata.get("_slice_filament_color"):
                    self.metadata["filament_color"] = self.metadata["_slice_filament_color"]
                if not self.metadata.get("filament_type") and self.metadata.get("_slice_filament_type"):
                    self.metadata["filament_type"] = self.metadata["_slice_filament_type"]

                # Clean up internal keys
                self.metadata.pop("_slice_filament_type", None)
                self.metadata.pop("_slice_filament_color", None)
        except Exception:
            pass
        return self.metadata

    def _parse_slice_info(self, zf: zipfile.ZipFile):
        """Parse slice_info.config for print settings."""
        try:
            if "Metadata/slice_info.config" in zf.namelist():
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                # Get first plate's metadata
                plate = root.find(".//plate")
                if plate is not None:
                    # Get prediction and weight from metadata elements
                    for meta in plate.findall("metadata"):
                        key = meta.get("key")
                        value = meta.get("value")
                        if key == "prediction" and value:
                            self.metadata["print_time_seconds"] = int(value)
                        elif key == "weight" and value:
                            self.metadata["filament_used_grams"] = float(value)

                # Get filament info from ALL filaments actually used in the print
                # slice_info has <filament id="1" type="PLA" color="#FFFFFF" used_g="100" />
                filaments = root.findall(".//filament")
                if filaments:
                    # Collect all unique filament types and colors
                    types = []
                    colors = []
                    for f in filaments:
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
                is_support = filament_is_support[i] if i < len(filament_is_support) else '0'
                if is_support == '0':
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
                dsm_pattern = r'DSM0+(\d+)'
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
        """Extract thumbnail image from 3MF."""
        thumbnail_paths = [
            "Metadata/plate_1.png",
            "Metadata/thumbnail.png",
            "Metadata/model_thumbnail.png",
        ]
        for thumb_path in thumbnail_paths:
            if thumb_path in zf.namelist():
                self.metadata["_thumbnail_data"] = zf.read(thumb_path)
                self.metadata["_thumbnail_ext"] = ".png"
                break


class ArchiveService:
    """Service for archiving print jobs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def archive_print(
        self,
        printer_id: int | None,
        source_file: Path,
        print_data: dict | None = None,
    ) -> PrintArchive | None:
        """Archive a 3MF file with metadata."""
        # Verify printer exists if specified
        if printer_id is not None:
            result = await self.db.execute(
                select(Printer).where(Printer.id == printer_id)
            )
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

        # Parse 3MF metadata
        parser = ThreeMFParser(dest_file)
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

        # Create archive record
        archive = PrintArchive(
            printer_id=printer_id,
            filename=source_file.name,
            file_path=str(dest_file.relative_to(settings.base_dir)),
            file_size=dest_file.stat().st_size,
            thumbnail_path=thumbnail_path,
            print_name=metadata.get("print_name") or source_file.stem,
            print_time_seconds=metadata.get("print_time_seconds"),
            filament_used_grams=metadata.get("filament_used_grams"),
            filament_type=metadata.get("filament_type"),
            filament_color=metadata.get("filament_color"),
            layer_height=metadata.get("layer_height"),
            nozzle_diameter=metadata.get("nozzle_diameter"),
            bed_temperature=metadata.get("bed_temperature"),
            nozzle_temperature=metadata.get("nozzle_temperature"),
            makerworld_url=metadata.get("makerworld_url"),
            designer=metadata.get("designer"),
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            extra_data=metadata,
        )

        self.db.add(archive)
        await self.db.commit()
        await self.db.refresh(archive)

        return archive

    async def get_archive(self, archive_id: int) -> PrintArchive | None:
        """Get an archive by ID."""
        result = await self.db.execute(
            select(PrintArchive).where(PrintArchive.id == archive_id)
        )
        return result.scalar_one_or_none()

    async def update_archive_status(
        self,
        archive_id: int,
        status: str,
        completed_at: datetime | None = None,
    ) -> bool:
        """Update the status of an archive."""
        archive = await self.get_archive(archive_id)
        if not archive:
            return False

        archive.status = status
        if completed_at:
            archive.completed_at = completed_at

        await self.db.commit()
        return True

    async def list_archives(
        self,
        printer_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PrintArchive]:
        """List archives with optional filtering."""
        query = select(PrintArchive).order_by(PrintArchive.created_at.desc())

        if printer_id:
            query = query.where(PrintArchive.printer_id == printer_id)

        query = query.limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def delete_archive(self, archive_id: int) -> bool:
        """Delete an archive and its files."""
        archive = await self.get_archive(archive_id)
        if not archive:
            return False

        # Delete files
        file_path = settings.base_dir / archive.file_path
        if file_path.exists():
            archive_dir = file_path.parent
            shutil.rmtree(archive_dir, ignore_errors=True)

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
        archive = await self.get_archive(archive_id)
        if not archive:
            return False

        # Get archive directory
        file_path = settings.base_dir / archive.file_path
        archive_dir = file_path.parent

        # Save timelapse
        timelapse_file = archive_dir / filename
        timelapse_file.write_bytes(timelapse_data)

        # Update archive record
        archive.timelapse_path = str(timelapse_file.relative_to(settings.base_dir))
        await self.db.commit()

        return True

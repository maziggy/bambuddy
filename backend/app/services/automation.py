"""
Service for GCODE/3MF automation: rewriting files with printer-specific automation data,
creating temporary files for upload, and cleaning up after upload.
"""

import tempfile
import shutil
import hashlib
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from backend.app.models.automation import Automation
from backend.app.core.database import async_session

async def get_automation_by_printer_id(printer_id: int) -> Optional[Automation]:
    """Retrieve automation data for a given printer id."""
    async with async_session() as db:
        result = await db.execute(
            Automation.__table__.select().where(Automation.printer_id == printer_id)
        )
        return result.scalar_one_or_none()

def detect_and_alter_gcode_content(
    content: str,
    automation: Automation,
    detect_word: Optional[str] = None,
) -> str:
    """
    Alter GCODE content with automation data. Abort if detect_word is present.
    """
    lines = content.splitlines(keepends=True)
    new_lines = list(lines)

    # START CODE LOGIC
    start_inserted = False
    if automation.start_code:
        # If detect line is present, do not add start code
        if automation.start_code_detect and any(automation.start_code_detect in l for l in lines):
            pass  # skip insertion
        else:
            # Find after line for start code
            if automation.start_code_after:
                for idx, l in enumerate(lines):
                    if automation.start_code_after in l:
                        new_lines.insert(idx + 1, automation.start_code + '\n')
                        start_inserted = True
                        break
            if not start_inserted:
                new_lines.insert(0, automation.start_code + '\n')

    # END CODE LOGIC
    end_inserted = False
    if automation.end_code:
        # If detect word is present, do not add end code
        if automation.end_code_detect and any(automation.end_code_detect in l for l in lines):
            pass  # skip insertion
        else:
            # Find after line for end code (search from end)
            if automation.end_code_after:
                for idx in range(len(new_lines) - 1, -1, -1):
                    if automation.end_code_after in new_lines[idx]:
                        new_lines.insert(idx, automation.end_code + '\n')
                        end_inserted = True
                        break
            if not end_inserted:
                new_lines.append('\n' + automation.end_code)

    return ''.join(new_lines)

def compute_file_hash(file_path: Path, algo: str = "sha256") -> str:
    """Compute hash of a file (default sha256)."""
    h = hashlib.new(algo)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def create_temp_gcode_with_automation(
    original_path: Path,
    printer_id: int,
    detect_word: Optional[str] = None,
) -> Tuple[Path, str, dict]:
    """
    Create a temporary GCODE or 3MF file with automation data for the given printer.
    Returns (temp_path, overall_hash, {gcode_filename: md5}) of the altered file.
    """
    import asyncio
    automation = asyncio.run(get_automation_by_printer_id(printer_id))
    if not automation:
        raise ValueError(f"No automation data found for printer id {printer_id}")

    suffix = original_path.suffix.lower()
    if suffix == ".3mf":
        # Handle 3MF: open as zip, alter all .gcode files inside, and write .gcode.md5 files
        gcode_md5s = {}
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
            tmp_path = Path(tmp.name)
        with zipfile.ZipFile(original_path, "r") as zf_read, \
             zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf_write:
            for item in zf_read.namelist():
                if item.endswith(".gcode"):
                    content = zf_read.read(item).decode("utf-8", errors="ignore")
                    altered = detect_and_alter_gcode_content(content, automation, detect_word)
                    zf_write.writestr(item, altered.encode("utf-8"))
                    # Compute MD5 for this altered gcode
                    md5 = hashlib.md5(altered.encode("utf-8")).hexdigest()
                    gcode_md5s[item] = md5
                    # Write the .gcode.md5 file (same folder as .gcode)
                    md5_filename = item + ".md5"
                    zf_write.writestr(md5_filename, md5.encode("utf-8"))
                else:
                    # Don't copy old .gcode.md5 files, they will be regenerated
                    if item.endswith(".gcode.md5"):
                        continue
                    zf_write.writestr(item, zf_read.read(item))
        file_hash = compute_file_hash(tmp_path)
        return tmp_path, file_hash, gcode_md5s
    elif suffix == ".gcode":
        # Handle plain GCODE
        with open(original_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        altered = detect_and_alter_gcode_content(content, automation, detect_word)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gcode", mode="w", encoding="utf-8") as tmp:
            tmp.write(altered)
            tmp_path = Path(tmp.name)
        file_hash = compute_file_hash(tmp_path)
        md5 = hashlib.md5(altered.encode("utf-8")).hexdigest()
        return tmp_path, file_hash, {original_path.name: md5}
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

def cleanup_temp_file(temp_path: Path):
    """Remove the temporary file if it exists."""
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass

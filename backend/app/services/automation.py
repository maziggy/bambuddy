"""
Service for GCODE/3MF automation: rewriting files with printer-specific automation data,
creating temporary files for upload, and cleaning up after upload.
"""

import logging
import tempfile
import hashlib
import zipfile
from pathlib import Path
from typing import Optional, Tuple
from sqlalchemy import select

from backend.app.models.automation import Automation
from backend.app.core.database import async_session

logger = logging.getLogger(__name__)


async def get_automation_by_printer_id(printer_id: int) -> Optional[Automation]:
    """Retrieve automation data for a given printer id."""
    async with async_session() as db:
        result = await db.execute(
            select(Automation).where(Automation.printer_id == printer_id)
        )
        return result.scalar_one_or_none()


async def needs_gcode_modification(printer_id: int) -> bool:
    """Check if a printer has automation configured and needs modification.
    
    Returns True only if automation exists AND has at least one code to inject.
    """
    automation = await get_automation_by_printer_id(printer_id)
    if not automation:
        return False
    return bool(automation.start_code or automation.end_code)

def detect_and_alter_gcode_content(
    content: str,
    automation: Automation,
    detect_word: Optional[str] = None,
) -> str:
    """
    Alter GCODE content with automation data. Abort if detect_word is present.
    
    Args:
        content: Original G-code text
        automation: Automation configuration with start/end codes
        detect_word: Optional word to skip modification if found
        
    Returns:
        Modified G-code content as string
    """
    lines = content.splitlines(keepends=True)
    new_lines = list(lines)

    # START CODE LOGIC
    start_inserted = False
    if automation.start_code:
        # If detect line is present, do not add start code
        if automation.start_code_detect and any(automation.start_code_detect in l for l in lines):
            logger.debug(
                "Skipping start code injection: detection string '%s' found",
                automation.start_code_detect,
            )
        else:
            # Find after line for start code
            if automation.start_code_after:
                for idx, l in enumerate(lines):
                    if automation.start_code_after in l:
                        new_lines.insert(idx + 1, automation.start_code + '\n')
                        start_inserted = True
                        logger.debug(
                            "Inserted start code after anchor: '%s' at line %d",
                            automation.start_code_after,
                            idx,
                        )
                        break
            if not start_inserted:
                new_lines.insert(0, automation.start_code + '\n')
                logger.debug("Inserted start code at beginning")

    # END CODE LOGIC
    end_inserted = False
    if automation.end_code:
        # If detect word is present, do not add end code
        if automation.end_code_detect and any(automation.end_code_detect in l for l in lines):
            logger.debug(
                "Skipping end code injection: detection string '%s' found",
                automation.end_code_detect,
            )
        else:
            # Find after line for end code (search from end)
            if automation.end_code_after:
                for idx in range(len(new_lines) - 1, -1, -1):
                    if automation.end_code_after in new_lines[idx]:
                        new_lines.insert(idx, automation.end_code + '\n')
                        end_inserted = True
                        logger.debug(
                            "Inserted end code before anchor: '%s' at line %d",
                            automation.end_code_after,
                            idx,
                        )
                        break
            if not end_inserted:
                new_lines.append('\n' + automation.end_code)
                logger.debug("Inserted end code at end")

    return ''.join(new_lines)

def compute_file_hash(file_path: Path, algo: str = "sha256") -> str:
    """Compute hash of a file (default sha256).
    
    Args:
        file_path: Path to file
        algo: Hash algorithm (default: sha256)
        
    Returns:
        Hex-encoded hash string
    """
    h = hashlib.new(algo)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def create_temp_gcode_with_automation(
    original_path: Path,
    printer_id: int,
    detect_word: Optional[str] = None,
) -> Tuple[Path, str, dict]:
    """
    Create a temporary GCODE or 3MF file with automation data for the given printer.
    
    **IMPORTANT: Caller is responsible for cleanup!** Use try/finally or context manager.
    
    Args:
        original_path: Path to original .gcode or .3mf file
        printer_id: Printer ID to fetch automation config for
        detect_word: Optional detection string to skip modification
        
    Returns:
        Tuple of:
        - temp_path: Path to created temporary file
        - overall_hash: SHA256 hash of modified file
        - gcode_hashes: Dict mapping {gcode_filename: md5_hash}
        
    Raises:
        ValueError: If printer has no automation or unsupported file type
        Exception: If file operation fails
    """
    automation = await get_automation_by_printer_id(printer_id)
    if not automation:
        raise ValueError(f"No automation data found for printer id {printer_id}")

    suffix = original_path.suffix.lower()
    
    if suffix == ".3mf":
        # Handle 3MF: open as zip, alter all .gcode files inside
        gcode_md5s = {}
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
            tmp_path = Path(tmp.name)
            
        try:
            with (
                zipfile.ZipFile(original_path, "r") as zf_read,
                zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf_write,
            ):
                for item in zf_read.namelist():
                    if item.endswith(".gcode"):
                        # Modify GCODE file
                        content = zf_read.read(item).decode("utf-8", errors="ignore")
                        altered = detect_and_alter_gcode_content(
                            content, automation, detect_word
                        )
                        zf_write.writestr(item, altered.encode("utf-8"))
                        
                        # Compute MD5 for altered gcode
                        md5 = hashlib.md5(altered.encode("utf-8")).hexdigest()
                        gcode_md5s[item] = md5
                        
                        # Write .gcode.md5 file
                        md5_filename = item + ".md5"
                        zf_write.writestr(md5_filename, md5.encode("utf-8"))
                    elif item.endswith(".gcode.md5"):
                        # Skip old MD5 files - they'll be regenerated
                        continue
                    else:
                        # Copy non-gcode files as-is
                        zf_write.writestr(item, zf_read.read(item))
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to process 3MF file: {e}") from e
            
        file_hash = compute_file_hash(tmp_path)
        logger.info(
            "Created modified 3MF temp file for printer %d: %s (hash: %s)",
            printer_id,
            tmp_path,
            file_hash,
        )
        return tmp_path, file_hash, gcode_md5s
        
    elif suffix == ".gcode":
        # Handle plain GCODE
        try:
            with open(original_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            altered = detect_and_alter_gcode_content(content, automation, detect_word)
            
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".gcode", mode="w", encoding="utf-8"
            ) as tmp:
                tmp.write(altered)
                tmp_path = Path(tmp.name)
                
        except Exception as e:
            raise RuntimeError(f"Failed to process GCODE file: {e}") from e
            
        file_hash = compute_file_hash(tmp_path)
        md5 = hashlib.md5(altered.encode("utf-8")).hexdigest()
        logger.info(
            "Created modified GCODE temp file for printer %d: %s (hash: %s)",
            printer_id,
            tmp_path,
            file_hash,
        )
        return tmp_path, file_hash, {original_path.name: md5}
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Must be .gcode or .3mf")

def cleanup_temp_file(temp_path: Path):
    """Remove the temporary file if it exists.
    
    Args:
        temp_path: Path to temporary file to remove
        
    Note:
        Exceptions are silently caught and logged - this is safe to call
        in finally blocks without worrying about re-raising exceptions.
    """
    try:
        if temp_path.exists():
            temp_path.unlink()
            logger.debug("Cleaned up temp automation file: %s", temp_path)
    except Exception as e:
        logger.warning("Failed to cleanup temp file %s: %s", temp_path, e)

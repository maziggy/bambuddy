"""Parse Bambu Lab MIFARE Classic tag data blocks into structured metadata."""

import logging

logger = logging.getLogger(__name__)

# Bambu tag block layout (MIFARE Classic 1K):
# Block 1: material type (bytes 0-7), color info (bytes 8-15)
# Block 2: temperatures, weights
# Block 4: detailed filament type ("PLA Matte")
# Block 9: tray UUID (16 bytes), same on both tags, equals the AMS tray_uuid


def parse_bambu_blocks(blocks: dict[int, bytes]) -> dict:
    """Parse raw Bambu MIFARE Classic blocks into metadata dict.

    Args:
        blocks: Dict mapping block number -> 16 bytes

    Returns:
        Dict with tray_uuid, material_type, color, etc.
    """
    result = {}

    # Extract tray UUID from block 9
    if 9 in blocks:
        result["tray_uuid"] = blocks[9][:16].hex().upper()

    # Extract material info from block 1
    if 1 in blocks:
        data = blocks[1]
        # Material type is typically in the first few bytes
        material_bytes = data[:8]
        result["material_raw"] = material_bytes.hex().upper()

    # Extract block 2 data (temperatures, weights)
    if 2 in blocks:
        data = blocks[2]
        result["block2_raw"] = data.hex().upper()

    return result

"""Parse Bambu Lab MIFARE Classic tag data blocks into structured metadata."""

import logging

logger = logging.getLogger(__name__)

# Bambu tag block layout (MIFARE Classic 1K):
# Block 1: material type (bytes 0-7), color info (bytes 8-15)
# Block 2: temperatures, weights
# Block 4-5: tray UUID (32 hex chars across 2 blocks)


def parse_bambu_blocks(blocks: dict[int, bytes]) -> dict:
    """Parse raw Bambu MIFARE Classic blocks into metadata dict.

    Args:
        blocks: Dict mapping block number -> 16 bytes

    Returns:
        Dict with tray_uuid, material_type, color, etc.
    """
    result = {}

    # Extract tray UUID from blocks 4+5
    if 4 in blocks and 5 in blocks:
        uuid_raw = blocks[4] + blocks[5]
        result["tray_uuid"] = uuid_raw[:16].hex().upper()

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

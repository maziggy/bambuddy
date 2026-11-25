"""Decode the data blocks of a Bambu Lab filament RFID tag (#2685).

SpoolBuddy reads blocks 1, 2, 4, 5, 6 and 9 of a genuine Bambu tag (MIFARE
Classic 1K) and sends them as hex. The layout follows the community
RFID-Tag-Guide; numbers are little-endian. The result uses the field names of
an AMS tray report, so a roll SpoolBuddy read goes through the same mapping as
one the AMS reported (``spool_tag_matcher.resolve_tray_fields``).
"""

import math
import re
import struct

from sqlalchemy.ext.asyncio import AsyncSession

_HEX_BLOCK = re.compile(r"^[0-9A-Fa-f]{32}$")

# Without these the tag says nothing usable about the roll
REQUIRED_BLOCKS = (1, 2, 4, 5)


def _ascii(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def decode_bambu_tag_blocks(raw_blocks: dict[str, str] | None) -> dict | None:
    """The tag's contents as AMS tray fields, or None when a needed block is missing or unreadable.

    Block 6 (temperatures) and block 9 (tray UUID) are optional: their fields are
    left out when the block is absent.
    """
    if not raw_blocks:
        return None

    blocks: dict[int, bytes] = {}
    for key, value in raw_blocks.items():
        if str(key).isdigit() and isinstance(value, str) and _HEX_BLOCK.match(value):
            blocks[int(key)] = bytes.fromhex(value)
    if any(n not in blocks for n in REQUIRED_BLOCKS):
        return None

    tray_type = _ascii(blocks[2])
    if not tray_type:
        return None

    b1, b5 = blocks[1], blocks[5]
    tray: dict = {
        "tray_id_name": _ascii(b1[0:8]),  # variant, e.g. "A00-B08"
        "tray_info_idx": _ascii(b1[8:16]),  # material / slicer profile, e.g. "GFA00"
        "tray_type": tray_type,  # "PLA"
        "tray_sub_brands": _ascii(blocks[4]),  # "PLA Basic"
        "tray_color": b5[0:4].hex().upper(),  # RRGGBBAA
    }

    label_weight = _u16(b5, 4)
    if label_weight:
        tray["tray_weight"] = label_weight
    diameter = struct.unpack_from("<f", b5, 8)[0]
    if math.isfinite(diameter) and 0 < diameter < 5:
        tray["tray_diameter"] = round(diameter, 2)

    b6 = blocks.get(6)
    if b6 is not None:
        for name, offset in (
            ("drying_temp", 0),
            ("drying_time", 2),
            ("bed_temp", 6),
            ("nozzle_temp_max", 8),
            ("nozzle_temp_min", 10),
        ):
            value = _u16(b6, offset)
            if value:
                tray[name] = value

    b9 = blocks.get(9)
    if b9 is not None and any(b9):
        tray["tray_uuid"] = b9.hex().upper()

    return tray


async def spool_prefill_from_tag(db: AsyncSession, raw_blocks: dict[str, str] | None) -> dict | None:
    """The spool a scanned Bambu tag describes, for prefilling Quick Add, or None when the tag can't be decoded.

    Holds the ``Spool`` column values AMS auto-creation would set for the same
    roll, plus the tag's diameter and drying hints for display.
    """
    from backend.app.services.spool_tag_matcher import bambu_spool_core_weight, resolve_tray_fields

    tray = decode_bambu_tag_blocks(raw_blocks)
    if tray is None:
        return None

    prefill = await resolve_tray_fields(db, tray)
    prefill["core_weight"], prefill["core_weight_catalog_id"] = await bambu_spool_core_weight(db)
    prefill["variant_id"] = tray["tray_id_name"] or None
    prefill["diameter"] = tray.get("tray_diameter")
    prefill["drying_temp"] = tray.get("drying_temp")
    prefill["drying_time"] = tray.get("drying_time")
    return prefill

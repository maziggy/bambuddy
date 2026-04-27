"""OpenTag3D NDEF encoder for NTAG tags.

Encodes spool data as an OpenTag3D NDEF message ready to write to NTAG
starting at page 4 (after the manufacturer pages).

NDEF structure:
  [CC: E1 10 12 00]              - Capability Container (4 bytes, page 4)
  [TLV: 03 len]                  - NDEF Message TLV (2 bytes)
  [NDEF record header]           - D2 15 payload_len (3 bytes: MB|ME|SR, TNF=MIME, type_len=21)
  [Type: "application/opentag3d"] - 21 bytes
  [Payload: OpenTag3D fields]    - 102 bytes
  [Terminator: FE]               - 1 byte
"""

import logging
import struct

from backend.app.api.routes._spoolman_helpers import MappedSpoolFields
from backend.app.models.spool import Spool

logger = logging.getLogger(__name__)

OPENTAG3D_MIME_TYPE = b"application/opentag3d"
PAYLOAD_SIZE = 102
TAG_VERSION = 1000  # v1.000


def _build_payload_from_dict(data: dict) -> bytes:
    """Build 102-byte OpenTag3D core payload from a plain field dict.

    Accepted keys: material, subtype, brand, color_name, rgba,
    label_weight, nozzle_temp_min.  All are optional and default to
    safe zero/empty values when missing.
    """
    buf = bytearray(PAYLOAD_SIZE)

    # 0x00: Tag Version (2 bytes, big-endian)
    struct.pack_into(">H", buf, 0x00, TAG_VERSION)

    # 0x02: Base Material (5 bytes, UTF-8, space-padded)
    material = (data.get("material") or "")[:5].ljust(5)
    buf[0x02:0x07] = material.encode("utf-8")[:5]

    # 0x07: Material Modifiers (5 bytes, UTF-8, space-padded)
    modifiers = (data.get("subtype") or "")[:5].ljust(5)
    buf[0x07:0x0C] = modifiers.encode("utf-8")[:5]

    # 0x0C: Reserved (15 bytes, zero-fill) — already zero

    # 0x1B: Manufacturer (16 bytes, UTF-8, space-padded)
    brand = (data.get("brand") or "")[:16].ljust(16)
    buf[0x1B:0x2B] = brand.encode("utf-8")[:16]

    # 0x2B: Color Name (32 bytes, UTF-8, space-padded)
    color_name = (data.get("color_name") or "")[:32].ljust(32)
    buf[0x2B:0x4B] = color_name.encode("utf-8")[:32]

    # 0x4B: Color 1 RGBA (4 bytes)
    rgba_hex = data.get("rgba") or "00000000"
    try:
        rgba_bytes = bytes.fromhex(rgba_hex[:8].ljust(8, "0"))
    except ValueError:
        logger.warning("OpenTag3D encoder: invalid rgba value %r — encoding as transparent black", rgba_hex)
        rgba_bytes = b"\x00\x00\x00\x00"
    buf[0x4B:0x4F] = rgba_bytes[:4]

    # 0x4F: Colors 2-4 (12 bytes, zero-fill) — already zero

    # 0x5C: Target Diameter (2 bytes, big-endian) — 1750 = 1.75mm
    struct.pack_into(">H", buf, 0x5C, 1750)

    # 0x5E: Target Weight (2 bytes, big-endian) — clamped to uint16 (0–65535)
    label_weight = max(0, min(int(data.get("label_weight") or 0), 65535))
    struct.pack_into(">H", buf, 0x5E, label_weight)

    # 0x60: Print Temp (1 byte) — nozzle_temp_min / 5, clamped to 0–255
    buf[0x60] = max(0, min(int((data.get("nozzle_temp_min") or 0) // 5), 255))

    # 0x61: Bed Temp (1 byte) — not tracked
    # 0x62: Density (2 bytes) — not tracked
    # 0x64: Transmission Distance (2 bytes) — not tracked
    # All zero — already zero

    return bytes(buf)


def _build_payload(spool: Spool) -> bytes:
    """Build 102-byte OpenTag3D core payload from a Spool ORM object."""
    return _build_payload_from_dict(
        {
            "material": spool.material,
            "subtype": spool.subtype,
            "brand": spool.brand,
            "color_name": spool.color_name,
            "rgba": spool.rgba,
            "label_weight": spool.label_weight,
            "nozzle_temp_min": spool.nozzle_temp_min,
        }
    )


def _encode_ndef(payload: bytes) -> bytes:
    """Wrap a 102-byte payload in CC + TLV + NDEF record + terminator."""
    mime_type = OPENTAG3D_MIME_TYPE

    # NDEF record: MB|ME|SR (0xD0) | TNF=MIME (0x02) => 0xD2
    record_header = bytes([0xD2, len(mime_type), len(payload)])
    ndef_record = record_header + mime_type + payload

    # TLV: type=0x03 (NDEF Message), length
    ndef_len = len(ndef_record)
    if ndef_len < 0xFF:
        tlv = bytes([0x03, ndef_len])
    else:
        tlv = bytes([0x03, 0xFF, (ndef_len >> 8) & 0xFF, ndef_len & 0xFF])

    cc = bytes([0xE1, 0x10, 0x12, 0x00])
    terminator = bytes([0xFE])
    return cc + tlv + ndef_record + terminator


def encode_opentag3d(spool: Spool) -> bytes:
    """Encode spool ORM object as OpenTag3D NDEF message.

    Returns raw bytes ready to write to NTAG starting at page 4.
    """
    return _encode_ndef(_build_payload(spool))


def encode_opentag3d_from_mapped(mapped: MappedSpoolFields) -> bytes:
    """Encode a Spoolman-mapped spool dict as OpenTag3D NDEF message.

    Accepts the dict produced by ``_map_spoolman_spool`` (or any dict
    with the same field names).  Returns raw bytes ready to write to
    NTAG starting at page 4.
    """
    return _encode_ndef(_build_payload_from_dict(mapped))

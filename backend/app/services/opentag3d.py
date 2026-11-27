"""OpenTag3D NDEF encoder and decoder for NTAG tags.

Encodes spool data as an OpenTag3D NDEF message ready to write to NTAG
starting at page 4, the first page of the data area, and decodes the payload
of one read back (``decode_opentag3d``, #3657), in either layout.

The Capability Container is not part of it: on NTAG21x it is page 3, set at
the factory. A copy written from page 4 lands in the data area, where a Type 2
reader takes it for an unknown TLV, skips the NDEF message and reports an
empty tag (#3500).

NDEF structure:
  [TLV: 03 len]                  - NDEF Message TLV (2 bytes, page 4)
  [NDEF record header]           - D2 15 payload_len (3 bytes: MB|ME|SR, TNF=MIME, type_len=21)
  [Type: "application/opentag3d"] - 21 bytes
  [Payload: OpenTag3D fields]    - 102 bytes
  [Terminator: FE]               - 1 byte
"""

import logging
import re
import struct

from backend.app.api.routes._spoolman_helpers import MappedSpoolFields
from backend.app.models.spool import Spool
from backend.app.services import tag_values
from backend.app.utils.filament_types import is_printer_filament_type, printer_filament_type

logger = logging.getLogger(__name__)

OPENTAG3D_MIME_TYPE = b"application/opentag3d"
PAYLOAD_SIZE = 102
TAG_VERSION = 1000  # v1.000

# Base Material and Material Modifiers are 5 bytes each, in both layouts.
_FIELD_BYTES = 5
# Where a material name splits into its base and its suffix: "PETG-HS", "TPU 95A".
_MATERIAL_SEPARATOR = re.compile(r"[-\s]+")


def _fits(text: str) -> bool:
    return len(text.encode("utf-8")) <= _FIELD_BYTES


def _material_fields(material: str | None, subtype: str | None) -> tuple[str, str]:
    """What goes in Base Material and Material Modifiers (#3754).

    Both fields are 5 bytes. The spec's base is the material "excluding any
    modifiers", so a material that does not fit is split at its first "-" or
    space: PETG-HS is written as PETG / HS, TPU 95A as TPU / 95A, PLA Matte as
    PLA / Matte. Cutting it instead wrote "PETG-", which read back as a
    material nothing knows, and PETG-HS and PETG-CF as the same one.

    The suffix then takes the modifier field from the subtype: HS or CF says
    which material it is, Matte does not, and the field holds one of them.
    ``decode_opentag3d`` joins the two again where the result is a known type.

    A name that fits is written as before, so tags that read fine stay as they
    are. One with no usable split falls back to the printer type it names
    ("PolyTerra PLA" -> PLA), and is cut only when there is none.
    """
    text = (material or "").strip()
    if _fits(text):
        return text, subtype or ""
    parts = _MATERIAL_SEPARATOR.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1] and _fits(parts[0]):
        return parts[0], parts[1]
    reduced = printer_filament_type(text)
    if reduced != text and _fits(reduced):
        return reduced, subtype or ""
    return text, subtype or ""


def _build_payload_from_dict(data: dict) -> bytes:
    """Build 102-byte OpenTag3D core payload from a plain field dict.

    Accepted keys: material, subtype, brand, color_name, rgba,
    label_weight, nozzle_temp_min.  All are optional and default to
    safe zero/empty values when missing.
    """
    buf = bytearray(PAYLOAD_SIZE)

    # 0x00: Tag Version (2 bytes, big-endian)
    struct.pack_into(">H", buf, 0x00, TAG_VERSION)

    # 0x02: Base Material, 0x07: Material Modifiers (5 bytes each, UTF-8, space-padded)
    base, modifiers = _material_fields(data.get("material"), data.get("subtype"))
    _put_text(buf, 0x02, _FIELD_BYTES, base)
    _put_text(buf, 0x07, _FIELD_BYTES, modifiers)

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


# OpenTag3D 2.004 (https://opentag3d.info/spec). 2.000 moved every field and
# dropped NTAG213: this layout does not fit one, so v1 stays for those tags and
# the SpoolBuddy daemon picks by the tag's size (#3500).
V2_TAG_VERSION = 2004
# Up to the end of the last field written (empty spool weight, 0xA0-0xA1). The
# spec reads missing bytes as 0 (2.001), so the rest need not be written.
V2_PAYLOAD_SIZE = 0xA2


def _rgba_bytes(value: str | None) -> bytes:
    """4 RGBA bytes from 6- or 8-char hex; 6 chars means opaque."""
    text = (value or "").strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    try:
        return bytes.fromhex(text[:8].ljust(8, "0"))
    except ValueError:
        logger.warning("OpenTag3D encoder: invalid colour %r, encoding as transparent black", value)
        return b"\x00\x00\x00\x00"


def _put_text(buf: bytearray, offset: int, size: int, value: str | None) -> None:
    """UTF-8, cut to *size* bytes without splitting a character, space-padded."""
    raw = (value or "").encode("utf-8")[:size].decode("utf-8", "ignore").encode("utf-8")
    buf[offset : offset + size] = raw.ljust(size, b" ")


def _temp(celsius: int | None) -> int:
    """A temperature as the spec stores it: degrees C divided by 5, one byte."""
    try:
        return max(0, min(int(celsius or 0) // 5, 255))
    except (TypeError, ValueError):
        return 0


def _build_v2_payload_from_dict(data: dict) -> bytes:
    """OpenTag3D 2.004 payload from the same field dict as the v1 one.

    Written: material, modifiers, manufacturer, colour name, colours 1-4
    (2-4 from ``extra_colors``), diameter, print temperatures (target = the
    minimum, as v1 wrote it), target weight and empty spool weight. Bambuddy
    tracks no density or bed temperature, so those stay 0 (not set), and
    chamber temperature 0 means no heating.
    """
    buf = bytearray(V2_PAYLOAD_SIZE)
    struct.pack_into(">H", buf, 0x00, V2_TAG_VERSION)
    base, modifiers = _material_fields(data.get("material"), data.get("subtype"))
    _put_text(buf, 0x02, _FIELD_BYTES, base)
    _put_text(buf, 0x07, _FIELD_BYTES, modifiers)
    _put_text(buf, 0x0C, 16, data.get("brand"))
    _put_text(buf, 0x1C, 32, data.get("color_name"))
    buf[0x3C:0x40] = _rgba_bytes(data.get("rgba") or "00000000")
    extra = [tok for tok in (data.get("extra_colors") or "").split(",") if tok.strip()]
    for i, colour in enumerate(extra[:3]):
        offset = 0x40 + 4 * i
        buf[offset : offset + 4] = _rgba_bytes(colour)
    # 0x8C: diameter in micrometres
    struct.pack_into(">H", buf, 0x8C, 1750)
    buf[0x90] = _temp(data.get("nozzle_temp_min"))  # target print
    buf[0x91] = _temp(data.get("nozzle_temp_min"))  # minimum print
    buf[0x92] = _temp(data.get("nozzle_temp_max"))  # maximum print
    struct.pack_into(">H", buf, 0x9E, max(0, min(int(data.get("label_weight") or 0), 65535)))
    struct.pack_into(">H", buf, 0xA0, max(0, min(int(data.get("core_weight") or 0), 65535)))
    return bytes(buf)


def _spool_fields(spool: Spool) -> dict:
    return {
        "material": spool.material,
        "subtype": spool.subtype,
        "brand": spool.brand,
        "color_name": spool.color_name,
        "rgba": spool.rgba,
        "extra_colors": spool.extra_colors,
        "label_weight": spool.label_weight,
        "core_weight": spool.core_weight,
        "nozzle_temp_min": spool.nozzle_temp_min,
        "nozzle_temp_max": spool.nozzle_temp_max,
    }


def encode_opentag3d_v2(spool: Spool) -> bytes:
    """Encode a spool as an OpenTag3D 2.004 NDEF message, for NTAG215/216."""
    return _encode_ndef(_build_v2_payload_from_dict(_spool_fields(spool)))


def encode_opentag3d_v2_from_mapped(mapped: MappedSpoolFields) -> bytes:
    """:func:`encode_opentag3d_v2` for a Spoolman-mapped spool dict."""
    return _encode_ndef(_build_v2_payload_from_dict(dict(mapped)))


def _encode_ndef(payload: bytes) -> bytes:
    """Wrap a payload in TLV + NDEF record + terminator."""
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

    terminator = bytes([0xFE])
    return tlv + ndef_record + terminator


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


# --- Decoding (#3657) -----------------------------------------------------------

# Where each field sits, per layout: (offset, size) for text and colours, the
# offset for numbers. v1 is the layout _build_payload_from_dict writes; v2 the
# 2.x one _build_v2_payload_from_dict writes (2.000 moved every field).
_V1_LAYOUT = {
    "material": (0x02, 5),
    "subtype": (0x07, 5),
    "brand": (0x1B, 16),
    "color_name": (0x2B, 32),
    "rgba": (0x4B, 4),
    "extra_colors": (0x4F, 12),
    "diameter": 0x5C,
    "label_weight": 0x5E,
    "print_temp": 0x60,
}
_V2_LAYOUT = {
    "material": (0x02, 5),
    "subtype": (0x07, 5),
    "brand": (0x0C, 16),
    "color_name": (0x1C, 32),
    "rgba": (0x3C, 4),
    "extra_colors": (0x40, 12),
    "diameter": 0x8C,
    "label_weight": 0x9E,
    "print_temp": 0x90,  # target; 0x91 minimum, 0x92 maximum
}
# Beyond every field read here, in either layout
_DECODE_SIZE = 0xA2


def decode_opentag3d(payload: bytes) -> dict:
    """The spool fields an OpenTag3D payload holds, None where the tag leaves one out.

    Fields: material, subtype, brand, color_name, rgba, extra_colors,
    label_weight, core_weight, nozzle_temp_min, nozzle_temp_max, diameter,
    drying_temp, drying_time. Raises TagDecodeError for a version this reader
    must not parse, or a tag without a material.
    """
    if len(payload) < 2:
        raise tag_values.TagDecodeError(f"OpenTag3D payload of {len(payload)} bytes")
    version = struct.unpack_from(">H", payload, 0)[0]
    if 1000 <= version < 2000:
        layout = _V1_LAYOUT
    elif 2000 <= version < 3000:
        layout = _V2_LAYOUT
    else:
        # The spec: a reader must not parse a newer major version. Below 1.000
        # there is no layout at all.
        raise tag_values.TagDecodeError(f"OpenTag3D version {version // 1000}.{version % 1000:03d} not supported")
    # Bytes past the written length read as 0 (spec 2.001); the v2 encoder
    # stops at the empty spool weight.
    buf = payload.ljust(_DECODE_SIZE, b"\x00")

    def text(key: str, limit: int) -> str | None:
        offset, size = layout[key]
        return tag_values.text(buf[offset : offset + size].decode("utf-8", errors="ignore"), limit)

    def u16(offset: int) -> int:
        return struct.unpack_from(">H", buf, offset)[0]

    # A tag written before #3754 can hold a name cut after its hyphen ("PETG-"
    # of PETG-HS): the base before it is still the material.
    material = (text("material", tag_values.MAX_MATERIAL) or "").rstrip("-").strip()
    if not material:
        raise tag_values.TagDecodeError("OpenTag3D tag without a material")
    subtype = text("subtype", tag_values.MAX_SUBTYPE)
    # A modifier that completes a known type is part of the material: PETG +
    # CF is PETG-CF, PLA + AERO is PLA-AERO, the way the encoder splits a name
    # too long for the field (#3754). Anything else (HF, HS, Matte, Silk) stays
    # the subtype, since HF and CF are subtypes users pick on purpose (#1345).
    if subtype and is_printer_filament_type(f"{material}-{subtype}"):
        material, subtype = f"{material}-{subtype}".upper(), None

    offset, _ = layout["rgba"]
    rgba = tag_values.rgba_from_bytes(buf[offset : offset + 4])
    offset, size = layout["extra_colors"]
    extra = tag_values.extra_colors(
        [tag_values.rgba_from_bytes(buf[o : o + 4]) for o in range(offset, offset + size, 4)]
    )

    # Temperatures are stored in steps of 5 degrees. A tag with only the
    # target print temperature (all of v1, and v2 without the optional range)
    # gives it as the minimum and no maximum, rather than a made-up range.
    target = buf[layout["print_temp"]] * 5
    if layout is _V2_LAYOUT:
        temp_min = buf[0x91] * 5 or target
        temp_max = buf[0x92] * 5
        core_weight = tag_values.weight(u16(0xA0))
        drying_temp = tag_values.temperature(buf[0x9A] * 5)  # maximum drying temperature
        drying_time = buf[0x9B] or None  # hours, as on a Bambu tag
    else:
        temp_min, temp_max = target, 0
        core_weight = drying_temp = drying_time = None

    return {
        "material": material,
        "subtype": subtype,
        "brand": text("brand", tag_values.MAX_BRAND),
        "color_name": text("color_name", tag_values.MAX_COLOR_NAME),
        "rgba": rgba,
        "extra_colors": extra,
        "label_weight": tag_values.weight(u16(layout["label_weight"])),
        "core_weight": core_weight,
        "nozzle_temp_min": tag_values.temperature(temp_min),
        "nozzle_temp_max": tag_values.temperature(temp_max),
        "diameter": tag_values.diameter(u16(layout["diameter"]) / 1000),  # micrometres
        "drying_temp": drying_temp,
        "drying_time": drying_time,
    }

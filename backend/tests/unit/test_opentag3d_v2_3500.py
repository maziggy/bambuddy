"""OpenTag3D 2.004 encoding for tags with room for it (#3500).

The spec moved every field in 2.000 and dropped NTAG213, so SpoolBuddy writes
v2 on an NTAG215/216 and keeps v1 for an NTAG213. Offsets are from
https://opentag3d.info/spec (2.004).
"""

import struct
from types import SimpleNamespace

from backend.app.services.opentag3d import (
    OPENTAG3D_MIME_TYPE,
    V2_PAYLOAD_SIZE,
    encode_opentag3d,
    encode_opentag3d_v2,
    encode_opentag3d_v2_from_mapped,
)

NTAG213_DATA_AREA = 0x12 * 8  # 144
NTAG215_DATA_AREA = 0x3E * 8  # 496


def _spool(**overrides):
    fields = {
        "material": "PETG",
        "subtype": "HF",
        "brand": "Polymaker",
        "color_name": "Jade White",
        "rgba": "00AE42FF",
        "extra_colors": "ff0000,00ff0080",
        "label_weight": 1000,
        "core_weight": 250,
        "nozzle_temp_min": 230,
        "nozzle_temp_max": 260,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _payload(message: bytes) -> bytes:
    """The record's payload, after TLV(2) + header(3) + type(21)."""
    assert message[0] == 0x03
    assert message[2] == 0xD2
    assert message[5:26] == OPENTAG3D_MIME_TYPE
    length = message[4]
    return message[26 : 26 + length]


def test_the_fields_sit_at_the_2004_offsets():
    p = _payload(encode_opentag3d_v2(_spool()))

    assert struct.unpack_from(">H", p, 0x00)[0] == 2004
    assert p[0x02:0x07] == b"PETG "
    assert p[0x07:0x0C] == b"HF   "
    assert p[0x0C:0x1C] == b"Polymaker".ljust(16)
    assert p[0x1C:0x3C] == b"Jade White".ljust(32)
    assert p[0x3C:0x40] == bytes.fromhex("00AE42FF")
    assert struct.unpack_from(">H", p, 0x8C)[0] == 1750
    assert (p[0x90], p[0x91], p[0x92]) == (46, 46, 52)  # 230, 230, 260 C / 5
    assert struct.unpack_from(">H", p, 0x9E)[0] == 1000
    assert struct.unpack_from(">H", p, 0xA0)[0] == 250


def test_extra_colours_fill_colours_2_to_4():
    p = _payload(encode_opentag3d_v2(_spool(extra_colors="ff0000,00ff0080,0000ff,123456")))

    assert p[0x40:0x44] == bytes.fromhex("FF0000FF")  # 6 hex chars = opaque
    assert p[0x44:0x48] == bytes.fromhex("00FF0080")
    assert p[0x48:0x4C] == bytes.fromhex("0000FFFF")
    # Only three more colours fit; the fourth extra one is dropped.
    assert p[0x4C:0x50] == bytes(4)


def test_fields_bambuddy_does_not_track_stay_unset():
    p = _payload(encode_opentag3d_v2(_spool()))

    assert p[0x93] == 0  # chamber: no heating
    assert p[0x94] == 0  # bed
    assert struct.unpack_from(">H", p, 0x9C)[0] == 0  # density


def test_missing_values_encode_as_zero():
    p = _payload(
        encode_opentag3d_v2(
            _spool(extra_colors=None, nozzle_temp_min=None, nozzle_temp_max=None, core_weight=None, brand=None)
        )
    )

    assert p[0x0C:0x1C] == b" " * 16
    assert p[0x40:0x4C] == bytes(12)
    assert (p[0x90], p[0x91], p[0x92]) == (0, 0, 0)
    assert struct.unpack_from(">H", p, 0xA0)[0] == 0


def test_a_long_name_is_cut_without_splitting_a_character():
    p = _payload(encode_opentag3d_v2(_spool(color_name="Grün" * 10)))

    name = p[0x1C:0x3C]
    assert len(name) == 32
    name.decode("utf-8")  # no half character at the cut


def test_v2_fits_an_ntag215_but_not_an_ntag213():
    v2 = encode_opentag3d_v2(_spool())

    assert len(v2) == 2 + 3 + 21 + V2_PAYLOAD_SIZE + 1
    assert NTAG213_DATA_AREA < len(v2) <= NTAG215_DATA_AREA
    # v1 still fits the NTAG213 it is kept for.
    assert len(encode_opentag3d(_spool())) <= NTAG213_DATA_AREA


def test_the_spoolman_path_encodes_the_same():
    spool = _spool()

    assert encode_opentag3d_v2_from_mapped(vars(spool)) == encode_opentag3d_v2(spool)

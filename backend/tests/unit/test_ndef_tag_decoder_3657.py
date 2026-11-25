"""Decoding NTAG filament tags for SpoolBuddy's Quick Add prefill (#3657).

The three ``REAL_*`` fixtures are data areas (from page 4) read off real tags
with an ESP32 SpoolBuddy client: two written by NFC Tools on iOS, one by
Bambuddy itself. The rest are built here or with Bambuddy's own encoder.
"""

import json
import logging
import struct
from types import SimpleNamespace

import pytest

from backend.app.services.ndef_tag_decoder import NdefTag, decode_ndef_tag
from backend.app.services.opentag3d import encode_opentag3d, encode_opentag3d_v2

# OpenSpool README example, written by NFC Tools as a custom record. NFC Tools
# stores the MIME type as an external type (TNF 4), not a MIME record.
REAL_OPENSPOOL_NFC_TOOLS = bytes.fromhex(
    "0391d4107e6170706c69636174696f6e2f6a736f6e7b2270726f746f636f6c223a226f70656e73706f6f6c222c227665"
    "7273696f6e223a22312e30222c2274797065223a22504c41222c22636f6c6f725f686578223a22464641414242222c22"
    "6272616e64223a2247656e65726963222c226d696e5f74656d70223a22323230222c226d61785f74656d70223a223234"
    "30227dfe"
)
# FilaMan System with extended data, written the same way.
REAL_FILAMAN_SYSTEM_NFC_TOOLS = bytes.fromhex(
    "039fd4108c6170706c69636174696f6e2f6a736f6e7b2273706f6f6c5f6964223a223432222c2270726f746f636f6c22"
    "3a2266696c616d616e222c2276657273696f6e223a22312e30222c2274797065223a22504c41222c22636f6c6f725f68"
    "6578223a22314131413141222c226272616e64223a2247656e65726963222c226d696e5f74656d70223a22313930222c"
    "226d61785f74656d70223a22323330227dfe"
)
# OpenTag3D 2.004 written by SpoolBuddy for a Sunlu PETG Matte spool with no
# print temperatures set.
REAL_OPENTAG3D_V2_BAMBUDDY = bytes.fromhex(
    "03bad215a26170706c69636174696f6e2f6f70656e746167336407d450455447204d6174746553756e6c752020202020"
    "202020202020426c61636b2020202020202020202020202020202020202020202020202020203f4141ff000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000006d60000000000000000000000000000000003e800fafe"
)

# Capability Container copy that tags written before #3500 carry at page 4
CC_COPY = bytes([0xE1, 0x10, 0x3E, 0x00])


def _record(mime: str, payload: bytes, tnf: int = 0x02, mb: bool = True, me: bool = True) -> bytes:
    header = tnf
    if mb:
        header |= 0x80
    if me:
        header |= 0x40
    if len(payload) < 256:  # short record: 1-byte payload length
        return bytes([header | 0x10, len(mime), len(payload)]) + mime.encode() + payload
    return bytes([header, len(mime)]) + struct.pack(">I", len(payload)) + mime.encode() + payload


def _area(*records: bytes, before: bytes = b"") -> bytes:
    message = b"".join(records)
    if len(message) < 0xFF:
        tlv = bytes([0x03, len(message)])
    else:
        tlv = bytes([0x03, 0xFF]) + struct.pack(">H", len(message))
    return before + tlv + message + b"\xfe"


def _json_area(doc: dict, tnf: int = 0x02) -> bytes:
    return _area(_record("application/json", json.dumps(doc).encode(), tnf=tnf))


def _set(prefill: dict) -> dict:
    return {k: v for k, v in prefill.items() if v is not None}


def _spool(**overrides):
    fields = {
        "material": "PETG",
        "subtype": "Silk",
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


# --- Real tags -------------------------------------------------------------------


def test_real_openspool_tag_from_nfc_tools():
    tag = decode_ndef_tag(REAL_OPENSPOOL_NFC_TOOLS)
    assert tag is not None
    assert tag.tag_format == "openspool"
    assert _set(tag.prefill) == {
        "material": "PLA",
        "rgba": "FFAABBFF",
        "brand": "Generic",
        "nozzle_temp_min": 220,
        "nozzle_temp_max": 240,
    }


def test_real_filaman_system_tag_from_nfc_tools():
    tag = decode_ndef_tag(REAL_FILAMAN_SYSTEM_NFC_TOOLS)
    assert tag is not None
    assert tag.tag_format == "filaman_system"
    assert _set(tag.prefill) == {
        "material": "PLA",
        "rgba": "1A1A1AFF",
        "brand": "Generic",
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
    }


def test_real_opentag3d_tag_written_by_bambuddy():
    tag = decode_ndef_tag(REAL_OPENTAG3D_V2_BAMBUDDY)
    assert tag is not None
    assert tag.tag_format == "opentag3d"
    assert _set(tag.prefill) == {
        "material": "PETG",
        "subtype": "Matte",
        "effect_type": "matte",
        "brand": "Sunlu",
        "color_name": "Black",
        "rgba": "3F4141FF",
        "label_weight": 1000,
        "core_weight": 250,
        "diameter": 1.75,
    }


def test_prefill_has_every_quick_add_key():
    tag = decode_ndef_tag(REAL_OPENSPOOL_NFC_TOOLS)
    assert tag is not None
    assert set(tag.prefill) == {
        "material",
        "subtype",
        "color_name",
        "rgba",
        "extra_colors",
        "effect_type",
        "brand",
        "label_weight",
        "slicer_filament",
        "slicer_filament_name",
        "nozzle_temp_min",
        "nozzle_temp_max",
        "core_weight",
        "core_weight_catalog_id",
        "variant_id",
        "diameter",
        "drying_temp",
        "drying_time",
    }


# --- OpenTag3D round trip through Bambuddy's encoder ----------------------------


def test_opentag3d_v2_round_trip():
    tag = decode_ndef_tag(encode_opentag3d_v2(_spool()))
    assert tag is not None
    assert tag.tag_format == "opentag3d"
    assert _set(tag.prefill) == {
        "material": "PETG",
        "subtype": "Silk",
        "effect_type": "silk",
        "brand": "Polymaker",
        "color_name": "Jade White",
        "rgba": "00AE42FF",
        # the encoder makes a 6-digit stop opaque
        "extra_colors": "ff0000ff,00ff0080",
        "label_weight": 1000,
        "core_weight": 250,
        "nozzle_temp_min": 230,
        "nozzle_temp_max": 260,
        "diameter": 1.75,
    }


def test_opentag3d_v1_round_trip():
    tag = decode_ndef_tag(encode_opentag3d(_spool()))
    assert tag is not None
    assert tag.tag_format == "opentag3d"
    # v1 holds one print temperature (the minimum) and no extra colours or spool weight
    assert _set(tag.prefill) == {
        "material": "PETG",
        "subtype": "Silk",
        "effect_type": "silk",
        "brand": "Polymaker",
        "color_name": "Jade White",
        "rgba": "00AE42FF",
        "label_weight": 1000,
        "nozzle_temp_min": 230,
        "diameter": 1.75,
    }


def test_opentag3d_keeps_what_the_encoder_cut():
    # 5 bytes for material and modifiers, 16 for the brand, temperatures in steps of 5
    spool = _spool(subtype="Translucent", brand="A Very Long Brand Name", nozzle_temp_min=233, nozzle_temp_max=None)
    tag = decode_ndef_tag(encode_opentag3d_v2(spool))
    assert tag is not None
    assert tag.prefill["subtype"] == "Trans"
    assert tag.prefill["brand"] == "A Very Long Bran"
    assert tag.prefill["nozzle_temp_min"] == 230
    # no maximum written: none made up
    assert tag.prefill["nozzle_temp_max"] is None


def test_opentag3d_with_cc_copy_in_front():
    plain = decode_ndef_tag(encode_opentag3d(_spool()))
    with_cc = decode_ndef_tag(CC_COPY + encode_opentag3d(_spool()))
    assert with_cc == plain


def _ot3d_v2_payload(**bytes_at) -> bytes:
    buf = bytearray(0xA2)
    struct.pack_into(">H", buf, 0x00, 2004)
    buf[0x02:0x07] = b"PLA  "
    for offset, value in bytes_at.items():
        buf[int(offset[1:], 16)] = value
    return bytes(buf)


def test_opentag3d_v2_target_temperature_only():
    tag = decode_ndef_tag(_area(_record("application/opentag3d", _ot3d_v2_payload(o90=42))))
    assert tag is not None
    assert tag.prefill["nozzle_temp_min"] == 210
    assert tag.prefill["nozzle_temp_max"] is None


def test_opentag3d_v2_drying():
    tag = decode_ndef_tag(_area(_record("application/opentag3d", _ot3d_v2_payload(o9A=13, o9B=8))))
    assert tag is not None
    assert tag.prefill["drying_temp"] == 65
    assert tag.prefill["drying_time"] == 8


def test_opentag3d_short_payload_reads_as_zeros():
    # Only version and material written: the spec reads the rest as 0
    payload = struct.pack(">H", 2004) + b"PLA"
    tag = decode_ndef_tag(_area(_record("application/opentag3d", payload)))
    assert tag is not None
    assert _set(tag.prefill) == {"material": "PLA"}


def test_opentag3d_newer_major_version_is_refused(caplog):
    payload = bytearray(_ot3d_v2_payload())
    struct.pack_into(">H", payload, 0, 3000)
    with caplog.at_level(logging.INFO):
        assert decode_ndef_tag(_area(_record("application/opentag3d", bytes(payload))), "04AA") is None
    assert "version 3.000 not supported" in caplog.text


def test_opentag3d_without_material_is_not_decoded():
    payload = bytearray(_ot3d_v2_payload())
    payload[0x02:0x07] = b"     "
    assert decode_ndef_tag(_area(_record("application/opentag3d", bytes(payload)))) is None


# --- JSON formats ------------------------------------------------------------------


def test_openspool_numbers_hash_colour_and_u1_fields():
    doc = {
        "protocol": "openspool",
        "version": "1.0",
        "type": "PETG",
        "subtype": "Silk+",
        "color_hex": "#00ae42",
        "additional_color_hexes": ["#FF0000", "00FF0080"],
        "brand": "Elegoo",
        "min_temp": 230,
        "max_temp": "250",
        "weight": 1000,
        "diameter": 1.75,
    }
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert _set(tag.prefill) == {
        "material": "PETG",
        "subtype": "Silk+",
        "effect_type": "silk",
        "brand": "Elegoo",
        "rgba": "00AE42FF",
        "extra_colors": "ff0000ff,00ff0080",
        "nozzle_temp_min": 230,
        "nozzle_temp_max": 250,
        "label_weight": 1000,
        "diameter": 1.75,
    }


def test_openspool_mime_record():
    doc = {"protocol": "openspool", "type": "ABS", "color_hex": "FFFFFF", "brand": "Generic"}
    tag = decode_ndef_tag(_json_area(doc, tnf=0x02))
    assert tag is not None
    assert tag.tag_format == "openspool"


@pytest.mark.parametrize(
    "field, value",
    [
        ("color_hex", "nothex"),
        ("color_hex", "FFF"),
        ("min_temp", "hot"),
        ("min_temp", "0"),
        ("min_temp", 900),
        ("min_temp", True),
        ("weight", -5),
        ("diameter", "17.5"),
    ],
)
def test_openspool_garbage_values_are_dropped(field, value):
    doc = {"protocol": "openspool", "type": "PLA", field: value}
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert _set(tag.prefill) == {"material": "PLA"}


def test_json_text_is_trimmed_and_capped():
    doc = {"protocol": "openspool", "type": "  PLA  ", "brand": "B" * 300}
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert tag.prefill["material"] == "PLA"
    assert tag.prefill["brand"] == "B" * 100


@pytest.mark.parametrize(
    "doc",
    [
        {"protocol": "openspool", "brand": "Generic"},  # no type
        {"protocol": "something-else", "type": "PLA"},
        {"type": "PLA", "color_hex": "FFFFFF"},  # no protocol, no FilaMan shape
        {"sm_id": "0", "type": "PETG"},  # FilaMan spool tag without a Spoolman spool
        {"location": "Shelf A"},  # FilaMan location tag: not read
        {"sm_id": "123"},  # nothing but a link
    ],
)
def test_unknown_json_shapes_are_not_decoded(doc):
    assert decode_ndef_tag(_json_area(doc)) is None


def test_json_that_does_not_parse():
    assert decode_ndef_tag(_area(_record("application/json", b'{"protocol": "openspool",'))) is None


def test_json_that_is_not_an_object():
    assert decode_ndef_tag(_area(_record("application/json", b'["openspool"]'))) is None


# --- TLVs, records, blank tags ------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xfe",
        b"\x00\x00\xfe",
        b"\x03\x00\xfe",  # empty NDEF message, as a new NTAG often comes
        b"\x00" * 16,  # read ran out before any NDEF TLV
    ],
)
def test_blank_tags(data):
    assert decode_ndef_tag(data) == NdefTag("blank")


def test_tlvs_before_the_ndef_message_are_skipped():
    lock_control = bytes([0x01, 0x03, 0xA0, 0x10, 0x44])
    unknown_long = bytes([0xFD, 0xFF, 0x00, 0x02, 0xAA, 0xBB])  # 3-byte length on a non-NDEF TLV
    area = b"\x00" + lock_control + unknown_long + REAL_OPENSPOOL_NFC_TOOLS
    tag = decode_ndef_tag(area)
    assert tag is not None
    assert tag.tag_format == "openspool"


def test_three_byte_ndef_length():
    payload = encode_opentag3d_v2(_spool())[2:-1]  # the record itself
    big = _record("text/plain", b"x" * 200, mb=True, me=False) + payload[:1].replace(b"\xd2", b"\x52") + payload[1:]
    area = _area(big)
    assert area[1] == 0xFF
    tag = decode_ndef_tag(area)
    assert tag is not None
    assert tag.tag_format == "opentag3d"


def test_first_known_record_wins_after_others():
    uri = bytes([0x91, 0x01, 0x0C, 0x55, 0x04]) + b"example.com"  # well-known URI, MB set
    openspool = _record("application/json", b'{"protocol":"openspool","type":"PLA"}', mb=False, me=False)
    ot3d = _record("application/opentag3d", _ot3d_v2_payload(), mb=False, me=True)
    tag = decode_ndef_tag(_area(uri + openspool + ot3d))
    assert tag is not None
    assert tag.tag_format == "openspool"


def test_chunked_records_are_skipped():
    chunked = bytes([0xB2]) + _record("application/json", b'{"protocol":"openspool","type":"PLA"}')[1:]
    ot3d = _record("application/opentag3d", _ot3d_v2_payload(), mb=False, me=True)
    tag = decode_ndef_tag(_area(chunked + ot3d))
    assert tag is not None
    assert tag.tag_format == "opentag3d"


def test_truncated_read_is_not_decoded(caplog):
    with caplog.at_level(logging.INFO):
        assert decode_ndef_tag(REAL_OPENTAG3D_V2_BAMBUDDY[:64], "04BB") is None
    assert "NDEF on 04BB" in caplog.text


def test_record_longer_than_its_message_is_not_decoded():
    record = bytearray(_record("application/json", b'{"protocol":"openspool","type":"PLA"}'))
    record[2] = 0xF0  # claims more payload than there is
    assert decode_ndef_tag(_area(bytes(record))) is None


def test_unknown_mime_type_is_logged(caplog):
    area = _area(_record("application/vnd.example", b"\x01\x02\x03"))
    with caplog.at_level(logging.INFO):
        assert decode_ndef_tag(area, "04CC") is None
    assert "NDEF on 04CC: no known filament record ('application/vnd.example', 3 bytes)" in caplog.text


@pytest.mark.parametrize("data", [b"\x03", b"\x03\xff\x01", b"\x01"])
def test_cut_off_tlv_headers(data):
    assert decode_ndef_tag(data) is None


# --- Hostile or sloppy tags (review of #3657) --------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        bytes(range(0x20, 0x60)),  # ASCII
        b"\xff" * 64,
        b"Hello, this tag holds plain text and no NDEF at all",
        b"\x01\x03\xa0\x10\x44",  # a Lock Control TLV and then nothing
    ],
)
def test_data_that_is_not_ndef_is_not_blank(data):
    # "blank" would let a client offer to write over someone else's data
    assert decode_ndef_tag(data) is None


@pytest.mark.parametrize("colour", ["FF AA BB", "ff aa bb", "FFAA BB ", "FF\tAABB"])
def test_colours_with_whitespace_inside_are_dropped(colour):
    doc = {"protocol": "openspool", "type": "PLA", "color_hex": colour, "additional_color_hexes": [colour]}
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert tag.prefill["rgba"] is None
    assert tag.prefill["extra_colors"] is None


@pytest.mark.parametrize("value", [0.4, "0.4", "1_000", 10**400])
def test_numbers_that_round_to_zero_overflow_or_use_underscores_are_dropped(value):
    doc = {"protocol": "openspool", "type": "PLA", "weight": value, "min_temp": value}
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert tag.prefill["label_weight"] is None
    assert tag.prefill["nozzle_temp_min"] is None


def test_control_characters_are_dropped_from_text():
    doc = {"protocol": "openspool", "type": "P\nLA", "brand": "\x1b[2JEvil\u202e"}
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert tag.prefill["material"] == "PLA"
    assert tag.prefill["brand"] == "[2JEvil"


def test_tag_content_cannot_add_log_lines(caplog):
    area = _area(_record("text/x\nFAKE LOG LINE level=ERROR", b"\x00"))
    with caplog.at_level(logging.INFO):
        assert decode_ndef_tag(area, "04DD") is None
    assert len(caplog.records) == 1
    assert "\n" not in caplog.records[0].getMessage()


def test_long_protocol_is_cut_short_in_the_log(caplog):
    doc = {"protocol": "x" * 800, "type": "PLA"}
    with caplog.at_level(logging.INFO):
        assert decode_ndef_tag(_json_area(doc), "04EE") is None
    assert len(caplog.records[0].getMessage()) < 200


def test_opentag3d_version_below_1000_is_refused():
    payload = bytearray(_ot3d_v2_payload())
    struct.pack_into(">H", payload, 0, 0)
    assert decode_ndef_tag(_area(_record("application/opentag3d", bytes(payload)))) is None


@pytest.mark.parametrize(
    "area",
    [
        _json_area({"protocol": "openspool", "type": "PETG", "min_temp": "260", "max_temp": "190"}),
        _area(_record("application/opentag3d", _ot3d_v2_payload(o91=52, o92=38))),
    ],
)
def test_a_reversed_temperature_range_is_swapped(area):
    tag = decode_ndef_tag(area)
    assert tag is not None
    assert (tag.prefill["nozzle_temp_min"], tag.prefill["nozzle_temp_max"]) == (190, 260)


def test_json_with_a_byte_order_mark():
    payload = "\ufeff".encode() + b'{"protocol":"openspool","type":"PLA"}'
    tag = decode_ndef_tag(_area(_record("application/json", payload)))
    assert tag is not None
    assert tag.tag_format == "openspool"


def test_mime_type_with_parameters():
    tag = decode_ndef_tag(_area(_record("application/json; charset=utf-8", b'{"protocol":"openspool","type":"PLA"}')))
    assert tag is not None
    assert tag.tag_format == "openspool"


# --- FilaMan (no protocol) ---------------------------------------------------------

# From FilaMan's README_ManufacturerTags_EN.md
FILAMAN_MANUFACTURER = {
    "b": "Recycling Fabrik",
    "an": "FX1_PETG-S175-1000-DAEM00055",
    "t": "PETG",
    "c": "FF5733",
    "cn": "Vibrant Orange",
    "et": "230",
    "bt": "70",
    "di": "1.75",
    "de": "1.24",
    "sw": "180",
    "u": "https://www.recyclingfabrik.com/search?q=",
}


def test_filaman_manufacturer_tag():
    tag = decode_ndef_tag(_json_area(FILAMAN_MANUFACTURER))
    assert tag is not None
    assert tag.tag_format == "filaman_mfr"
    # bed temperature, density, article number and URL have no prefill field
    assert _set(tag.prefill) == {
        "material": "PETG",
        "brand": "Recycling Fabrik",
        "rgba": "FF5733FF",
        "color_name": "Vibrant Orange",
        "nozzle_temp_min": 230,
        "diameter": 1.75,
        "core_weight": 180,
    }


def test_filaman_unregistered_manufacturer_tag_with_sm_id_zero():
    tag = decode_ndef_tag(_json_area({"sm_id": "0", **FILAMAN_MANUFACTURER, "mc": "FF0000,00FF00,0000FF"}))
    assert tag is not None
    assert tag.tag_format == "filaman_mfr"
    assert tag.prefill["extra_colors"] == "ff0000ff,00ff00ff,0000ffff"


def test_filaman_spool_tag():
    # As FilaMan's web UI writes it (html/rfid.js), sm_id first
    doc = {
        "sm_id": "123",
        "color_hex": "FF5733",
        "type": "PETG",
        "min_temp": "230",
        "max_temp": "250",
        "brand": "Sunlu",
    }
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert tag.tag_format == "filaman"
    assert _set(tag.prefill) == {
        "material": "PETG",
        "brand": "Sunlu",
        "rgba": "FF5733FF",
        "nozzle_temp_min": 230,
        "nozzle_temp_max": 250,
    }


def test_filaman_placeholder_temperatures_are_dropped():
    # FilaMan writes 175/275 when Spoolman has no temperatures for the filament
    doc = {"sm_id": 7, "color_hex": "FFFFFF", "type": "PLA", "min_temp": "175", "max_temp": "275", "brand": "Generic"}
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert tag.prefill["nozzle_temp_min"] is None
    assert tag.prefill["nozzle_temp_max"] is None
    # white can't be told from a placeholder, so it stays
    assert tag.prefill["rgba"] == "FFFFFFFF"


def test_registered_filaman_tag_is_known_but_not_prefilled(caplog):
    # FilaMan rewrites a manufacturer tag once its spool is in Spoolman; cn is the article number there
    doc = {"sm_id": "123", "b": "Recycling Fabrik", "cn": "FX1_PETG-S175-1000-DAEM00055"}
    with caplog.at_level(logging.INFO):
        tag = decode_ndef_tag(_json_area(doc), "04FF")
    assert tag == NdefTag("filaman")
    assert "NDEF on 04FF: FilaMan tag linked to Spoolman spool '123', no filament data" in caplog.text


def test_filaman_system_is_still_read_with_its_protocol():
    doc = {"spool_id": "42", "protocol": "filaman", "type": "PLA", "sm_id": "9"}
    tag = decode_ndef_tag(_json_area(doc))
    assert tag is not None
    assert tag.tag_format == "filaman_system"

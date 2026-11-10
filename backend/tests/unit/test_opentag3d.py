"""Unit tests for OpenTag3D NDEF encoder."""

import struct
from unittest.mock import MagicMock

from backend.app.services.opentag3d import (
    OPENTAG3D_MIME_TYPE,
    PAYLOAD_SIZE,
    _build_payload,
    _build_payload_from_dict,
    encode_opentag3d,
    encode_opentag3d_from_mapped,
)


def _first_ndef_message(data: bytes) -> bytes | None:
    """Walk the data area from page 4 the way an NFC Forum Type 2 reader does.

    Returns the value of the first NDEF Message TLV (0x03), or None when the
    walk reaches the terminator (0xFE) or the end without one.
    """
    i = 0
    while i < len(data):
        tlv_type = data[i]
        if tlv_type == 0x00:  # NULL TLV, no length
            i += 1
            continue
        if tlv_type == 0xFE:  # Terminator
            return None
        if i + 1 >= len(data):
            return None
        length = data[i + 1]
        start = i + 2
        if length == 0xFF:
            length = int.from_bytes(data[i + 2 : i + 4], "big")
            start = i + 4
        if tlv_type == 0x03:
            return data[start : start + length]
        i = start + length
    return None


def _make_spool(**kwargs):
    """Create a mock Spool with default values."""
    defaults = {
        "material": "PLA",
        "subtype": "Matte",
        "brand": "Polymaker",
        "color_name": "Jade White",
        "rgba": "00AE42FF",
        "label_weight": 1000,
        "nozzle_temp_min": 220,
    }
    defaults.update(kwargs)
    spool = MagicMock()
    for k, v in defaults.items():
        setattr(spool, k, v)
    return spool


class TestBuildPayload:
    def test_payload_is_102_bytes(self):
        spool = _make_spool()
        payload = _build_payload(spool)
        assert len(payload) == PAYLOAD_SIZE

    def test_tag_version(self):
        payload = _build_payload(_make_spool())
        version = struct.unpack_from(">H", payload, 0x00)[0]
        assert version == 1000

    def test_material_field(self):
        payload = _build_payload(_make_spool(material="PETG"))
        material = payload[0x02:0x07].decode("utf-8")
        assert material == "PETG "

    def test_material_truncated(self):
        payload = _build_payload(_make_spool(material="SUPERLONG"))
        material = payload[0x02:0x07].decode("utf-8")
        assert material == "SUPER"

    def test_modifiers_field(self):
        payload = _build_payload(_make_spool(subtype="Silk"))
        mods = payload[0x07:0x0C].decode("utf-8")
        assert mods == "Silk "

    def test_modifiers_none(self):
        payload = _build_payload(_make_spool(subtype=None))
        mods = payload[0x07:0x0C].decode("utf-8")
        assert mods == "     "

    def test_reserved_is_zero(self):
        payload = _build_payload(_make_spool())
        assert payload[0x0C:0x1B] == b"\x00" * 15

    def test_brand_field(self):
        payload = _build_payload(_make_spool(brand="Polymaker"))
        brand = payload[0x1B:0x2B].decode("utf-8")
        assert brand == "Polymaker       "

    def test_color_name_field(self):
        payload = _build_payload(_make_spool(color_name="Jade White"))
        cn = payload[0x2B:0x4B].decode("utf-8")
        assert cn.startswith("Jade White")
        assert len(cn) == 32

    def test_rgba_field(self):
        payload = _build_payload(_make_spool(rgba="FF0000FF"))
        assert payload[0x4B:0x4F] == bytes([0xFF, 0x00, 0x00, 0xFF])

    def test_rgba_none(self):
        payload = _build_payload(_make_spool(rgba=None))
        assert payload[0x4B:0x4F] == b"\x00\x00\x00\x00"

    def test_target_diameter(self):
        payload = _build_payload(_make_spool())
        diameter = struct.unpack_from(">H", payload, 0x5C)[0]
        assert diameter == 1750

    def test_target_weight(self):
        payload = _build_payload(_make_spool(label_weight=750))
        weight = struct.unpack_from(">H", payload, 0x5E)[0]
        assert weight == 750

    def test_print_temp(self):
        payload = _build_payload(_make_spool(nozzle_temp_min=220))
        assert payload[0x60] == 44  # 220 / 5

    def test_print_temp_none(self):
        payload = _build_payload(_make_spool(nozzle_temp_min=None))
        assert payload[0x60] == 0


class TestEncodeOpentag3d:
    def test_starts_with_the_ndef_tlv(self):
        # #3500: page 4 is data on NTAG21x. A CC copy there hid the message.
        data = encode_opentag3d(_make_spool())
        # TLV type = 0x03
        assert data[0] == 0x03
        # TLV length = 3 (record header) + 21 (mime type) + 102 (payload) = 126
        assert data[1] == 126

    def test_ndef_record_header(self):
        data = encode_opentag3d(_make_spool())
        # Record starts after TLV(2) = offset 2
        assert data[2] == 0xD2  # MB|ME|SR + TNF=MIME
        assert data[3] == len(OPENTAG3D_MIME_TYPE)  # type length = 21
        assert data[4] == PAYLOAD_SIZE  # payload length = 102

    def test_a_type_2_reader_finds_the_record(self):
        data = encode_opentag3d(_make_spool(material="PLA", brand="Polymaker"))

        record = _first_ndef_message(data)

        assert record is not None
        assert record[3 : 3 + 21] == OPENTAG3D_MIME_TYPE
        assert record[3 + 21 :][0x02:0x05] == b"PLA"

    def test_mime_type(self):
        data = encode_opentag3d(_make_spool())
        mime = data[5:26]
        assert mime == b"application/opentag3d"

    def test_ends_with_terminator(self):
        data = encode_opentag3d(_make_spool())
        assert data[-1] == 0xFE

    def test_total_size(self):
        data = encode_opentag3d(_make_spool())
        # TLV(2) + header(3) + type(21) + payload(102) + terminator(1) = 129
        assert len(data) == 129

    def test_fits_ntag213(self):
        """NTAG213 has 36 writable pages (144 bytes). Our data must fit."""
        data = encode_opentag3d(_make_spool())
        ntag213_capacity = 36 * 4  # 144 bytes
        assert len(data) <= ntag213_capacity


# ---------------------------------------------------------------------------
# _build_payload_from_dict / encode_opentag3d_from_mapped
# ---------------------------------------------------------------------------

MINIMAL_MAPPED = {
    "material": "PLA",
    "subtype": "Basic",
    "brand": "Bambu Lab",
    "color_name": None,
    "rgba": "FF0000FF",
    "label_weight": 1000,
    "nozzle_temp_min": None,
}


class TestBuildPayloadFromDict:
    def test_payload_is_102_bytes(self):
        assert len(_build_payload_from_dict(MINIMAL_MAPPED)) == PAYLOAD_SIZE

    def test_material_encoded(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "material": "PETG"})
        assert payload[0x02:0x07].decode("utf-8") == "PETG "

    def test_subtype_encoded(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "subtype": "Silk"})
        assert payload[0x07:0x0C].decode("utf-8") == "Silk "

    def test_brand_encoded(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "brand": "Polymaker"})
        assert payload[0x1B:0x2B].decode("utf-8") == "Polymaker       "

    def test_rgba_encoded(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "rgba": "00FF00FF"})
        assert payload[0x4B:0x4F] == bytes([0x00, 0xFF, 0x00, 0xFF])

    def test_label_weight_encoded(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "label_weight": 750})
        weight = struct.unpack_from(">H", payload, 0x5E)[0]
        assert weight == 750

    def test_none_color_name_zero_filled(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "color_name": None})
        assert payload[0x2B:0x4B] == b"                                "

    def test_missing_keys_produce_safe_defaults(self):
        payload = _build_payload_from_dict({})
        assert len(payload) == PAYLOAD_SIZE
        assert payload[0x02:0x07] == b"     "
        weight = struct.unpack_from(">H", payload, 0x5E)[0]
        assert weight == 0

    def test_label_weight_overflow_clamped_to_65535(self):
        """label_weight > 65535 must not raise struct.error (uint16 overflow)."""
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "label_weight": 70000})
        weight = struct.unpack_from(">H", payload, 0x5E)[0]
        assert weight == 65535

    def test_label_weight_negative_clamped_to_zero(self):
        """Negative label_weight must be clamped to 0, not raise struct.error."""
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "label_weight": -1})
        weight = struct.unpack_from(">H", payload, 0x5E)[0]
        assert weight == 0

    def test_label_weight_at_uint16_max_accepted(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "label_weight": 65535})
        weight = struct.unpack_from(">H", payload, 0x5E)[0]
        assert weight == 65535

    def test_nozzle_temp_float_does_not_crash(self):
        """nozzle_temp_min as float (e.g. 220.5) must not raise TypeError."""
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "nozzle_temp_min": 220.5})
        assert payload[0x60] == 44  # int(220.5 // 5) = 44

    def test_nozzle_temp_overflow_clamped_to_255(self):
        """nozzle_temp_min causing byte > 255 must be clamped, not raise ValueError."""
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "nozzle_temp_min": 1280})
        assert payload[0x60] == 255  # 1280 // 5 = 256 → clamped to 255

    def test_nozzle_temp_negative_clamped_to_zero(self):
        payload = _build_payload_from_dict({**MINIMAL_MAPPED, "nozzle_temp_min": -50})
        assert payload[0x60] == 0

    def test_matches_orm_path_for_same_data(self):
        """_build_payload_from_dict must produce identical bytes to _build_payload."""
        spool = _make_spool(
            material="PLA",
            subtype="Matte",
            brand="Polymaker",
            color_name="Jade White",
            rgba="00AE42FF",
            label_weight=1000,
            nozzle_temp_min=220,
        )
        orm_payload = _build_payload(spool)
        dict_payload = _build_payload_from_dict(
            {
                "material": "PLA",
                "subtype": "Matte",
                "brand": "Polymaker",
                "color_name": "Jade White",
                "rgba": "00AE42FF",
                "label_weight": 1000,
                "nozzle_temp_min": 220,
            }
        )
        assert orm_payload == dict_payload


class TestEncodeOpentag3dFromMapped:
    def test_total_size(self):
        data = encode_opentag3d_from_mapped(MINIMAL_MAPPED)
        assert len(data) == 129

    def test_starts_with_the_ndef_tlv(self):
        data = encode_opentag3d_from_mapped(MINIMAL_MAPPED)
        assert data[:2] == bytes([0x03, 126])

    def test_ends_with_terminator(self):
        data = encode_opentag3d_from_mapped(MINIMAL_MAPPED)
        assert data[-1] == 0xFE

    def test_mime_type_present(self):
        data = encode_opentag3d_from_mapped(MINIMAL_MAPPED)
        assert b"application/opentag3d" in data

    def test_fits_ntag213(self):
        data = encode_opentag3d_from_mapped(MINIMAL_MAPPED)
        assert len(data) <= 36 * 4  # 144 bytes

    def test_identical_output_to_orm_path(self):
        """encode_opentag3d_from_mapped must produce the same bytes as encode_opentag3d."""
        spool = _make_spool(
            material="PLA",
            subtype="Matte",
            brand="Polymaker",
            color_name="Jade White",
            rgba="00AE42FF",
            label_weight=1000,
            nozzle_temp_min=220,
        )
        orm_bytes = encode_opentag3d(spool)
        mapped_bytes = encode_opentag3d_from_mapped(
            {
                "material": "PLA",
                "subtype": "Matte",
                "brand": "Polymaker",
                "color_name": "Jade White",
                "rgba": "00AE42FF",
                "label_weight": 1000,
                "nozzle_temp_min": 220,
            }
        )
        assert orm_bytes == mapped_bytes

    def test_spoolman_mapped_dict_accepted(self):
        """Accepts the exact dict shape produced by _map_spoolman_spool."""
        from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

        raw = {
            "id": 7,
            "filament": {
                "material": "PETG",
                "name": "PETG Basic",
                "color_hex": "00FF00",
                "weight": 1000.0,
                "vendor": {"name": "Bambu Lab"},
            },
            "used_weight": 100.0,
            "archived": False,
            "registered": "2024-01-01T00:00:00Z",
        }
        mapped = _map_spoolman_spool(raw)
        data = encode_opentag3d_from_mapped(mapped)
        assert len(data) == 129
        assert data[:2] == bytes([0x03, 126])

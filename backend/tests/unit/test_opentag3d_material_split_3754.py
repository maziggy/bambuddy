"""OpenTag3D keeps a material longer than its 5-byte field (#3754).

Base Material and Material Modifiers are 5 bytes each. The encoder cut the
whole material to fit, so PETG-HS was written as "PETG-" and read back as a
material nothing knows. It now splits such a name into base and modifier, and
the decoder joins the two where they make a known type.
"""

import pytest

from backend.app.services.opentag3d import (
    _build_payload_from_dict,
    _build_v2_payload_from_dict,
    decode_opentag3d,
)

LAYOUTS = [
    pytest.param(_build_payload_from_dict, id="v1"),
    pytest.param(_build_v2_payload_from_dict, id="v2"),
]


def _fields(payload: bytes) -> tuple[bytes, bytes]:
    return payload[0x02:0x07], payload[0x07:0x0C]


def _round_trip(build, material: str, subtype: str | None) -> tuple[str, str | None]:
    decoded = decode_opentag3d(build({"material": material, "subtype": subtype}))
    return decoded["material"], decoded["subtype"]


@pytest.mark.parametrize("build", LAYOUTS)
class TestEncode:
    def test_a_long_material_is_split_at_its_hyphen_and_its_suffix_takes_the_modifier(self, build):
        assert _fields(build({"material": "PETG-HS", "subtype": "Matte"})) == (b"PETG ", b"HS   ")

    def test_a_space_splits_it_too(self, build):
        assert _fields(build({"material": "TPU 95A", "subtype": None})) == (b"TPU  ", b"95A  ")
        assert _fields(build({"material": "PLA Matte", "subtype": None})) == (b"PLA  ", b"Matte")

    def test_a_name_that_fits_is_written_as_before(self, build):
        assert _fields(build({"material": "PLA-S", "subtype": "Basic"})) == (b"PLA-S", b"Basic")
        assert _fields(build({"material": "PETG", "subtype": "HF"})) == (b"PETG ", b"HF   ")

    def test_without_a_usable_split_the_printer_type_it_names_is_the_base(self, build):
        assert _fields(build({"material": "PolyTerra PLA", "subtype": "Matte"})) == (b"PLA  ", b"Matte")

    def test_a_name_with_no_type_in_it_is_still_cut(self, build):
        assert _fields(build({"material": "SUPERLONG", "subtype": None}))[0] == b"SUPER"

    def test_a_multi_byte_name_is_cut_without_splitting_a_character(self, build):
        base, _ = _fields(build({"material": "PLÄÄÄ", "subtype": None}))
        assert base.decode("utf-8") == "PLÄ "


@pytest.mark.parametrize("build", LAYOUTS)
class TestRoundTrip:
    def test_a_suffix_that_makes_a_known_type_joins_the_material_again(self, build):
        assert _round_trip(build, "PAHT-CF", None) == ("PAHT-CF", None)
        assert _round_trip(build, "PLA-AERO", "Basic") == ("PLA-AERO", None)
        # As the colour catalogue and preset names spell it.
        assert _round_trip(build, "PLA Aero", None) == ("PLA-AERO", None)

    def test_petg_hs_keeps_its_hs(self, build):
        """PETG-HS is no printer type, so it reads back as PETG with HS kept."""
        assert _round_trip(build, "PETG-HS", "Matte") == ("PETG", "HS")

    def test_a_subtype_stays_a_subtype(self, build):
        """HF and Matte are what users pick as a subtype (#1345)."""
        assert _round_trip(build, "PETG", "HF") == ("PETG", "HF")
        assert _round_trip(build, "PLA", "Matte") == ("PLA", "Matte")

    def test_a_short_name_is_unchanged(self, build):
        assert _round_trip(build, "PLA-S", None) == ("PLA-S", None)


def test_a_tag_cut_after_its_hyphen_reads_as_the_base():
    """Tags written before #3754 hold "PETG-" for PETG-HS."""
    payload = bytearray(_build_v2_payload_from_dict({"material": "PETG", "subtype": "Matte"}))
    payload[0x02:0x07] = b"PETG-"
    decoded = decode_opentag3d(bytes(payload))
    assert (decoded["material"], decoded["subtype"]) == ("PETG", "Matte")

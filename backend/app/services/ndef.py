"""NDEF messages on NFC Forum Type 2 tags (NTAG213/215/216).

Finds the NDEF message in a tag's data area and splits it into records, for the
tag formats that live in one (``opentag3d.py``, ``openspool.py``). Reading only;
``opentag3d._encode_ndef`` still builds the one message Bambuddy writes (#3657).
"""

import struct
from dataclasses import dataclass

# Type Name Format of the records a filament format uses
TNF_MIME = 0x02
# NFC Tools (iOS) writes a "custom" record with the MIME type as an NFC Forum
# external type instead of a MIME record. The type string is the same.
TNF_EXTERNAL = 0x04

_TLV_NULL = 0x00
_TLV_NDEF = 0x03
_TLV_TERMINATOR = 0xFE
# Byte 0 of the Capability Container. Tags SpoolBuddy wrote before #3500 carry
# a copy of it at page 4, in front of the NDEF TLV.
_CC_MAGIC = 0xE1

_ME = 0x40
_CF = 0x20
_SR = 0x10
_IL = 0x08


class NdefError(ValueError):
    """The data area is not a well-formed NDEF message."""


@dataclass(frozen=True)
class NdefRecord:
    tnf: int
    type: bytes
    payload: bytes

    @property
    def mime_type(self) -> str | None:
        """The record's MIME type in lower case, for a MIME or external-type record."""
        if self.tnf not in (TNF_MIME, TNF_EXTERNAL):
            return None
        # Parameters such as "; charset=utf-8" don't change the format
        return self.type.decode("ascii", errors="replace").split(";", 1)[0].strip().lower()


def find_message(data: bytes) -> bytes | None:
    """The NDEF message in a Type 2 data area that starts at page 4, or None for a blank tag.

    Blank means a terminator before any NDEF message, an empty NDEF message, or
    nothing but NULL bytes. Raises NdefError for data that is cut off, as by a
    read that stopped early, or that isn't NDEF at all, so a client is never
    told a tag holding someone else's data is blank.
    """
    pos = 0
    seen_tlv = False
    if len(data) > 4 and data[0] == _CC_MAGIC and data[4] == _TLV_NDEF:
        pos = 4
    while pos < len(data):
        tlv_type = data[pos]
        pos += 1
        if tlv_type == _TLV_NULL:
            continue
        if tlv_type == _TLV_TERMINATOR:
            return None
        if pos >= len(data):
            raise NdefError(f"TLV 0x{tlv_type:02X} has no length")
        length = data[pos]
        pos += 1
        if length == 0xFF:
            if pos + 2 > len(data):
                raise NdefError(f"TLV 0x{tlv_type:02X} has a cut-off 3-byte length")
            length = struct.unpack_from(">H", data, pos)[0]
            pos += 2
        if pos + length > len(data):
            if tlv_type == _TLV_NDEF:
                raise NdefError(f"NDEF message of {length} bytes, only {len(data) - pos} read")
            # Not a TLV at all, most likely: bytes that aren't NDEF
            raise NdefError(f"TLV 0x{tlv_type:02X} of {length} bytes runs past the data")
        if tlv_type == _TLV_NDEF:
            return data[pos : pos + length] if length else None
        pos += length  # Lock Control, Memory Control, proprietary: skip
        seen_tlv = True
    if seen_tlv:
        # TLVs that never reach an NDEF message or a terminator: not an NDEF tag
        raise NdefError("no NDEF message or terminator")
    return None


def parse_records(message: bytes) -> tuple[list[NdefRecord], str | None]:
    """The message's unchunked records in tag order, and why parsing stopped early (None if it didn't).

    A malformed record ends the list; the records before it are kept.
    """
    records: list[NdefRecord] = []
    pos = 0
    end = len(message)
    while pos < end:
        header = message[pos]
        pos += 1
        if pos >= end:
            return records, "record header cut off"
        type_len = message[pos]
        pos += 1
        if header & _SR:
            if pos >= end:
                return records, "record length cut off"
            payload_len = message[pos]
            pos += 1
        else:
            if pos + 4 > end:
                return records, "record length cut off"
            payload_len = struct.unpack_from(">I", message, pos)[0]
            pos += 4
        id_len = 0
        if header & _IL:
            if pos >= end:
                return records, "record ID length cut off"
            id_len = message[pos]
            pos += 1
        if type_len + id_len + payload_len > end - pos:
            return records, "record runs past the end of the message"
        rtype = message[pos : pos + type_len]
        pos += type_len + id_len
        payload = message[pos : pos + payload_len]
        pos += payload_len
        if not header & _CF:
            records.append(NdefRecord(header & 0x07, rtype, payload))
        if header & _ME:
            break
    return records, None

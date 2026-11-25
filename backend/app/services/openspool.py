"""OpenSpool JSON tags (https://github.com/spuder/OpenSpool) (#3657).

An OpenSpool tag is one ``application/json`` NDEF record, as its phone apps,
the Snapmaker U1 extended firmware and Spoolman tools write it:

    {"protocol": "openspool", "version": "1.0", "type": "PLA",
     "color_hex": "FFAABB", "brand": "Generic", "min_temp": "220", "max_temp": "240"}

The U1 firmware adds optional ``subtype``, ``additional_color_hexes``,
``weight`` and ``diameter``. FilaMan System writes the same record with
``"protocol": "filaman"`` and its own ``spool_id`` (its database id, not
Spoolman's), so it is read the same way. Reading only.
"""

from backend.app.services import tag_values

# protocol -> tag_format
PROTOCOLS = {"openspool": "openspool", "filaman": "filaman_system"}


def decode_openspool(doc: dict) -> tuple[str, dict]:
    """The tag format and the spool fields of a JSON record with an OpenSpool ``protocol``.

    Raises TagDecodeError for another protocol, or a record without a filament type.
    """
    protocol = doc.get("protocol")
    tag_format = PROTOCOLS.get(protocol.strip().lower()) if isinstance(protocol, str) else None
    if tag_format is None:
        raise tag_values.TagDecodeError(f"JSON record with protocol {tag_values.for_log(protocol)}")
    return tag_format, openspool_fields(doc, tag_format)


def openspool_fields(doc: dict, tag_format: str) -> dict:
    """The spool fields of an OpenSpool-shaped object, None where it leaves one out.

    Fields: material, subtype, brand, rgba, extra_colors, nozzle_temp_min,
    nozzle_temp_max, label_weight, diameter. Also read for FilaMan's spool
    tags, which carry the same keys without a protocol. Raises TagDecodeError
    without a filament type.
    """
    material = tag_values.text(doc.get("type"), tag_values.MAX_MATERIAL)
    if not material:
        raise tag_values.TagDecodeError(f"{tag_format} tag without a type")
    extra = doc.get("additional_color_hexes")
    return {
        "material": material,
        "subtype": tag_values.text(doc.get("subtype"), tag_values.MAX_SUBTYPE),
        "brand": tag_values.text(doc.get("brand"), tag_values.MAX_BRAND),
        "rgba": tag_values.rgba_from_hex(doc.get("color_hex")),
        "extra_colors": tag_values.extra_colors([tag_values.rgba_from_hex(c) for c in extra])
        if isinstance(extra, list)
        else None,
        "nozzle_temp_min": tag_values.temperature(tag_values.number(doc.get("min_temp"))),
        "nozzle_temp_max": tag_values.temperature(tag_values.number(doc.get("max_temp"))),
        "label_weight": tag_values.weight(tag_values.number(doc.get("weight"))),
        "diameter": tag_values.diameter(tag_values.number(doc.get("diameter"))),
    }

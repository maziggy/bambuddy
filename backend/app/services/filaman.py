"""FilaMan JSON tags (https://github.com/ManuelW77/Filaman) (#3657).

FilaMan writes one ``application/json`` record, in one of three shapes, none
with a ``protocol`` (FilaMan System's tags have one and are read in
``openspool.py``):

- Manufacturer tag, short keys (README_ManufacturerTags_EN.md):
  ``{"b": brand, "an": article number, "t": type, "c": colour, "cn": colour
  name, "et": extruder temp, "bt": bed temp, "di": diameter, "de": density,
  "sw": empty spool weight, "mc": extra colours, "u": URL}``. FilaMan stamps
  ``"sm_id": "0"`` on one it hasn't registered yet.
- Spool tag, the OpenSpool keys plus the Spoolman spool id:
  ``{"sm_id": "123", "color_hex": ..., "type": ..., "min_temp": ...,
  "max_temp": ..., "brand": ...}``.
- A manufacturer tag after FilaMan registered its spool in Spoolman, rewritten
  as ``{"sm_id": "123", "b": brand, "cn": article number}``: a link, with no
  filament data (``cn`` holds the article number there, not a colour name).

Location tags (``{"location": ...}``) are not read here.
"""

from backend.app.services import tag_values
from backend.app.services.openspool import openspool_fields

# What FilaMan's web writer puts on a spool tag when Spoolman has no
# temperatures for the filament (html/rfid.js). No filament's range is this.
_PLACEHOLDER_TEMPS = (175, 275)


def decode_filaman(doc: dict) -> tuple[str, dict | None]:
    """The tag format and the spool fields of a FilaMan record without a protocol.

    The fields are None for a registered manufacturer tag, which holds only a
    Spoolman link. Raises TagDecodeError for any other JSON shape.
    """
    if "b" in doc and "t" in doc:
        return "filaman_mfr", _manufacturer_fields(doc)

    sm_id = doc.get("sm_id")
    linked = isinstance(sm_id, str | int) and not isinstance(sm_id, bool) and str(sm_id).strip() not in ("", "0")
    if linked and "type" in doc:
        fields = openspool_fields(doc, "filaman")
        if (fields["nozzle_temp_min"], fields["nozzle_temp_max"]) == _PLACEHOLDER_TEMPS:
            fields["nozzle_temp_min"] = fields["nozzle_temp_max"] = None
        return "filaman", fields
    if linked and "b" in doc:
        return "filaman", None
    raise tag_values.TagDecodeError("JSON record without a protocol, in no FilaMan shape")


def _manufacturer_fields(doc: dict) -> dict:
    material = tag_values.text(doc.get("t"), tag_values.MAX_MATERIAL)
    if not material:
        raise tag_values.TagDecodeError("FilaMan manufacturer tag without a type")
    extra = doc.get("mc")  # "FF0000,00FF00,0000FF"
    return {
        "material": material,
        "brand": tag_values.text(doc.get("b"), tag_values.MAX_BRAND),
        "rgba": tag_values.rgba_from_hex(doc.get("c")),
        "color_name": tag_values.text(doc.get("cn"), tag_values.MAX_COLOR_NAME),
        "extra_colors": tag_values.extra_colors([tag_values.rgba_from_hex(c) for c in extra.split(",")])
        if isinstance(extra, str)
        else None,
        # One extruder temperature: the minimum, with no maximum made up
        "nozzle_temp_min": tag_values.temperature(tag_values.number(doc.get("et"))),
        "diameter": tag_values.diameter(tag_values.number(doc.get("di"))),
        "core_weight": tag_values.weight(tag_values.number(doc.get("sw"))),
    }

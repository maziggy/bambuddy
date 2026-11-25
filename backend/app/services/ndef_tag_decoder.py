"""What an NTAG filament tag says about its spool, for SpoolBuddy's Quick Add (#3657).

SpoolBuddy sends the data area of an NTAG it scanned, from page 4 on, as hex.
This finds its NDEF records (``ndef.py``), decodes the first one in a format it
knows, and turns it into the same Quick Add prefill a Bambu tag gives (#2685),
so an unknown spool starts from what its tag says.

Formats, each decoded in its own module:
- OpenTag3D (``application/opentag3d``), both layouts: ``opentag3d.py``
- OpenSpool, and FilaMan System's variant of it (``application/json`` with a
  ``protocol``): ``openspool.py``
- FilaMan's own spool and manufacturer tags (``application/json`` without
  one): ``filaman.py``

Nothing here maps onto Bambu's colour catalogue or Bambu defaults: these are
other brands' rolls, so a field the tag leaves out stays None. The scan route
then asks the colour catalogue for a name the tag left out, by the tag's own
brand and exact colour (``color_catalog_lookup.resolve_brand_color``).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.app.schemas.spool import effect_type_from_subtype
from backend.app.services import ndef
from backend.app.services.filaman import decode_filaman
from backend.app.services.openspool import decode_openspool
from backend.app.services.opentag3d import decode_opentag3d
from backend.app.services.tag_values import TagDecodeError, for_log, json_object

logger = logging.getLogger(__name__)

# Every key Quick Add reads from a prefill (UnknownTagData in the frontend).
_PREFILL_KEYS = (
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
)


@dataclass(frozen=True)
class NdefTag:
    """A decoded tag: its format, and the Quick Add prefill.

    The prefill is empty for a blank tag, and for a FilaMan tag that only links
    a spool already registered in Spoolman.
    """

    # "opentag3d", "openspool", "filaman_system", "filaman", "filaman_mfr" or "blank"
    tag_format: str
    prefill: dict = field(default_factory=dict)


def decode_ndef_tag(data: bytes, tag_uid: str = "") -> NdefTag | None:
    """What an NTAG's data area says about its spool, or None when it holds nothing usable.

    *data* starts at page 4. A tag with no NDEF message, or an empty one, is
    ``blank``. None means the data is malformed or in a format not known here;
    the reason is logged once, so support logs show which tags people scan.
    """
    try:
        message = ndef.find_message(data)
    except ndef.NdefError as exc:
        logger.info("NDEF on %s: %s", tag_uid, exc)
        return None
    if message is None:
        return NdefTag("blank")

    records, stopped = ndef.parse_records(message)
    seen: list[str] = []
    for record in records:
        mime = record.mime_type
        try:
            if mime == "application/opentag3d":
                return NdefTag("opentag3d", _prefill(decode_opentag3d(record.payload)))
            if mime == "application/json":
                doc = json_object(record.payload)
                if "protocol" in doc:
                    tag_format, fields = decode_openspool(doc)
                    return NdefTag(tag_format, _prefill(fields))
                tag_format, linked_fields = decode_filaman(doc)
                if linked_fields is None:
                    # Registered: a Spoolman link and a brand, nothing to prefill
                    logger.info(
                        "NDEF on %s: FilaMan tag linked to Spoolman spool %s, no filament data",
                        tag_uid,
                        for_log(doc.get("sm_id")),
                    )
                    return NdefTag(tag_format)
                return NdefTag(tag_format, _prefill(linked_fields))
        except TagDecodeError as exc:
            seen.append(str(exc))
            continue
        seen.append(f"{for_log(mime)}, {len(record.payload)} bytes" if mime else f"TNF {record.tnf}")
    if stopped:
        seen.append(stopped)
    logger.info("NDEF on %s: no known filament record (%s)", tag_uid, "; ".join(seen) or "no records")
    return None


def _prefill(fields: dict[str, Any]) -> dict[str, Any]:
    prefill: dict[str, Any] = dict.fromkeys(_PREFILL_KEYS, None)
    prefill.update(fields)
    # The swatch's rendering hint from the subtype ("Silk", "Wood"), as AMS
    # auto-creation sets it
    prefill["effect_type"] = effect_type_from_subtype(prefill["subtype"])
    low, high = prefill["nozzle_temp_min"], prefill["nozzle_temp_max"]
    if low is not None and high is not None and low > high:
        prefill["nozzle_temp_min"], prefill["nozzle_temp_max"] = high, low
    return prefill

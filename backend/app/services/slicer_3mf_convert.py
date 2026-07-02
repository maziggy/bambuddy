"""Per-slice 3MF input normalisation for the slicer pipeline.

This module currently exposes one helper, :func:`substitute_unused_plate_filaments`,
which rewrites the user's filament list so unused-slot entries don't trip
BambuStudio's loaded-filament temperature validator. The original goal of
this module — a two-pass cross-nozzle-class config-splice (#1493) — was
replaced by a simpler approach: forwarding the sidecar's existing
``--arrange`` flag (see ``slicer_api.SlicerApiService.slice_with_profiles``
and ``_run_slicer_with_fallback`` in ``api/routes/library.py``). BambuStudio
itself reconciles the embedded ``project_settings.config`` against the
target printer when ``--arrange`` is on, so Bambuddy never has to reproduce
that schema logic locally.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from io import BytesIO

logger = logging.getLogger(__name__)

_PROJECT_SETTINGS_PATH = "Metadata/project_settings.config"
_MODEL_SETTINGS_PATH = "Metadata/model_settings.config"
_SLICE_INFO_PATH = "Metadata/slice_info.config"


def count_plates_in_3mf(zip_bytes: bytes) -> int:
    """Return the number of plates the source 3MF defines, or ``0`` if the
    file isn't a parseable 3MF / has no plate metadata. Used by the
    cross-class slice-all loop (#1493) to know how many ``--slice N``
    calls to dispatch before merging the per-plate outputs back into one
    multi-plate 3MF.
    """
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
            if _MODEL_SETTINGS_PATH not in zf.namelist():
                return 0
            xml = zf.read(_MODEL_SETTINGS_PATH).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError, KeyError):
        return 0
    # Count ``<metadata key="plater_id" value="..."/>`` entries — each
    # ``<plate>`` element carries exactly one. Cheap and tolerant of the
    # full schema (no need to parse the whole XML, which is large and may
    # contain CDATA quirks).
    return len(re.findall(r'<metadata key="plater_id" value="(\d+)"', xml))


def extract_source_printer_model(zip_bytes: bytes) -> str | None:
    """Return the canonical short model code (e.g. ``"X1C"``, ``"H2D"``) for
    the 3MF's embedded ``printer_model`` field, or ``None`` if the input
    isn't a 3MF, has no embedded settings, the field is missing, or the
    model isn't recognised. Canonicalisation goes through
    :func:`normalize_printer_model`, which strips the ``"Bambu Lab "``
    vendor prefix and maps long display names to the short codes that
    :func:`is_dual_nozzle_model` matches against (the raw field is
    ``"Bambu Lab H2D"``, not ``"H2D"``).
    """
    from backend.app.utils.printer_models import normalize_printer_model

    try:
        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
            if _PROJECT_SETTINGS_PATH not in zf.namelist():
                return None
            cfg = json.loads(zf.read(_PROJECT_SETTINGS_PATH).decode("utf-8"))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError, KeyError):
        return None
    if not isinstance(cfg, dict):
        return None
    raw = cfg.get("printer_model")
    if not raw:
        return None
    canonical = normalize_printer_model(str(raw))
    return canonical or None


_PLATE_BLOCK_RE = re.compile(r"<plate>.*?</plate>", re.DOTALL)


def merge_plate_3mfs(
    plate_outputs: list[tuple[int, bytes]],
    source_3mf_bytes: bytes | None = None,
) -> bytes:
    """Combine N single-plate sliced 3MFs into one multi-plate 3MF.

    Used by the cross-class slice-all loop (#1493) where Bambuddy slices
    each plate independently against the target printer (BS CLI's
    ``--arrange`` is project-wide so a single ``--slice 0`` call would
    consolidate every plate's objects onto one bed — the bug this whole
    path exists to work around). Each input is a single-plate 3MF whose
    ``Metadata/plate_N.gcode`` / ``plate_N.json`` / ``plate_N.png``
    entries already carry the right plate index because the BS CLI
    preserves the requested plate number in the output filenames.

    The merge strategy:
    - The first plate's 3MF is the base — its ``project_settings.config``
      (target printer), ``3D/3dmodel.model``, and Auxiliaries images
      carry forward.
    - Per-plate artifacts from the other inputs (``plate_N.gcode``,
      ``plate_N.gcode.md5``, ``plate_N.json``, ``plate_N.png``,
      ``plate_N_small.png``, ``plate_no_light_N.png``, ``top_N.png``,
      ``pick_N.png``) are overlaid into the base.
    - ``slice_info.config`` is re-assembled from each input's single
      ``<plate>`` block so the resulting file lists all N plates.
    - ``source_3mf_bytes``, when supplied, is used as a fallback source
      of per-plate thumbnails (``plate_N.png`` and ``plate_N_small.png``)
      when the sliced outputs don't carry them — BS CLI with ``--arrange``
      regenerates the plate gcode but rarely writes a fresh per-plate
      preview, so without this fallback the merged 3MF would only have
      a cover image for plate 1 (the base 3MF) and the archive page's
      per-plate previews would be blank.

    Returns the merged 3MF bytes. Single-element input is a passthrough.
    Empty input raises ``ValueError``.
    """
    if not plate_outputs:
        raise ValueError("merge_plate_3mfs: at least one plate output required")
    ordered = sorted(plate_outputs, key=lambda p: p[0])

    if len(ordered) == 1:
        return ordered[0][1]

    # Collect each plate's <plate>...</plate> block out of its
    # slice_info.config. The single-plate slice output puts exactly one
    # such block; if a plate's output is missing the section (shouldn't
    # happen on a successful slice, but stay defensive) skip it — better
    # to ship a partial multi-plate 3MF than to fail the whole merge.
    plate_blocks: list[str] = []
    for plate_num, plate_bytes in ordered:
        try:
            with zipfile.ZipFile(BytesIO(plate_bytes), "r") as zf:
                if _SLICE_INFO_PATH not in zf.namelist():
                    continue
                xml = zf.read(_SLICE_INFO_PATH).decode("utf-8", errors="replace")
        except (zipfile.BadZipFile, OSError, KeyError) as exc:
            logger.warning("merge_plate_3mfs: couldn't read plate %d slice_info (%s)", plate_num, exc)
            continue
        match = _PLATE_BLOCK_RE.search(xml)
        if match:
            plate_blocks.append(match.group(0))

    combined_slice_info = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<config>\n"
        "  <header>\n"
        '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
        '    <header_item key="X-BBL-Client-Version" value="02.06.00.51"/>\n'
        "  </header>\n" + "\n".join(f"  {block}" for block in plate_blocks) + "\n</config>\n"
    ).encode("utf-8")

    # Per-plate artifact filenames we lift from each input into the base.
    def _per_plate_entries(n: int) -> set[str]:
        return {
            f"Metadata/plate_{n}.gcode",
            f"Metadata/plate_{n}.gcode.md5",
            f"Metadata/plate_{n}.json",
            f"Metadata/plate_{n}.png",
            f"Metadata/plate_{n}_small.png",
            f"Metadata/plate_no_light_{n}.png",
            f"Metadata/top_{n}.png",
            f"Metadata/pick_{n}.png",
        }

    # When the per-plate slices skip writing ``plate_N.png`` (BS CLI with
    # ``--arrange`` does this — the gcode is fresh but the preview slot
    # is empty), fall back to the source 3MF's stored render of the same
    # plate. The visual layout will differ from the arranged H2D version
    # but a recognisable preview is much better than a blank card.
    def _source_thumbnail_fallback(plate_num: int) -> dict[str, bytes]:
        if source_3mf_bytes is None:
            return {}
        wanted = {
            f"Metadata/plate_{plate_num}.png",
            f"Metadata/plate_{plate_num}_small.png",
        }
        found: dict[str, bytes] = {}
        try:
            with zipfile.ZipFile(BytesIO(source_3mf_bytes), "r") as src_zf:
                for name in src_zf.namelist():
                    if name in wanted:
                        found[name] = src_zf.read(name)
        except (zipfile.BadZipFile, OSError) as exc:
            logger.warning("merge_plate_3mfs: source thumbnail fallback failed (%s)", exc)
        return found

    base_num, base_bytes = ordered[0]
    out_buf = BytesIO()
    base_zip_names: set[str] = set()
    with (
        zipfile.ZipFile(BytesIO(base_bytes), "r") as base_zf,
        zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out_zf,
    ):
        # Pass 1: emit base entries. Track which per-plate-N thumbnails
        # the base actually had so the fallback pass below can fill in
        # the ones that are missing.
        for item in base_zf.infolist():
            base_zip_names.add(item.filename)
            if item.filename == _SLICE_INFO_PATH:
                out_zf.writestr(item, combined_slice_info)
            else:
                out_zf.writestr(item, base_zf.read(item.filename))

        # Source-thumbnail fallback for the base plate when the slicer
        # didn't write its own preview.
        for name, payload in _source_thumbnail_fallback(base_num).items():
            if name not in base_zip_names:
                out_zf.writestr(name, payload)
                base_zip_names.add(name)

        # Pass 2: overlay per-plate artifacts from the other plates'
        # 3MFs, falling back to the source for any plate-N thumbnails
        # the slicer didn't write.
        for plate_num, plate_bytes in ordered[1:]:
            wanted = _per_plate_entries(plate_num)
            written: set[str] = set()
            try:
                with zipfile.ZipFile(BytesIO(plate_bytes), "r") as plate_zf:
                    for name in plate_zf.namelist():
                        if name in wanted:
                            out_zf.writestr(name, plate_zf.read(name))
                            written.add(name)
            except (zipfile.BadZipFile, OSError) as exc:
                logger.warning(
                    "merge_plate_3mfs: couldn't read plate %d artifacts (%s); skipping",
                    plate_num,
                    exc,
                )
                continue
            for name, payload in _source_thumbnail_fallback(plate_num).items():
                if name not in written and name not in base_zip_names:
                    out_zf.writestr(name, payload)

    return out_buf.getvalue()


def substitute_unused_plate_filaments(source_3mf_bytes: bytes, plate_id: int | None, items: list[str]) -> list[str]:
    """Replace any filament-list entry whose 1-indexed slot isn't used by
    ``plate_id`` with the entry at slot 1 (index 0).

    Why: the slice modal lets the user pick a filament profile per slot,
    but each plate in a multi-plate project only uses a subset of those
    slots. The modal labels the unused rows "not used by this plate" yet
    still submits their dropdown values. BambuStudio then validates every
    loaded filament for material compatibility — PLA in a used slot +
    ABS defaulted into an unused slot trips
    "the temperature difference of the filaments used is too large"
    (exit 194), even though the plate's G-code never touches the ABS
    slot. Substituting unused entries with slot 1's filament keeps the
    per-filament array length intact (so the source 3MF's per-slot
    references stay valid) while making the loaded-filament set
    materially homogeneous, so the validator passes.

    The substitution is a no-op when:
    - ``plate_id`` is None (we can't determine which slots are unused),
    - the source isn't a valid 3MF / zip,
    - the source doesn't carry plate-extruder metadata (parse returns
      empty set — treat as "every slot is used", same fallback the
      SliceModal uses),
    - ``items`` has fewer than 2 entries (nothing to substitute).
    """
    if plate_id is None or len(items) < 2:
        return items
    # Local import keeps the bytes->ZipFile boundary in this module and
    # avoids dragging zipfile into every caller.
    from backend.app.utils.threemf_tools import (
        extract_plate_extruder_set_from_3mf,
        extract_support_filament_slots_from_3mf,
    )

    try:
        with zipfile.ZipFile(BytesIO(source_3mf_bytes), "r") as zf:
            # Geometry-derived slots (per-object metadata + paint_color)
            # plus process-derived support-filament slots. Supports aren't
            # attached to object geometry so the geometry pass alone
            # misses PVA-in-support-slot setups (#1881).
            used = extract_plate_extruder_set_from_3mf(zf, plate_id)
            used |= extract_support_filament_slots_from_3mf(zf)
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("Plate-filament parse failed (%s); leaving filament list unchanged", exc)
        return items
    if not used:
        # Empty result usually means the source 3MF has no per-object
        # extruder metadata (single-filament unsliced project). Treating
        # "no info" as "every slot is used" matches the SliceModal's
        # fail-open default — better to send the user's picks through
        # than to silently rewrite them.
        return items
    out = list(items)
    substituted = []
    for idx in range(len(out)):
        slot = idx + 1
        if slot not in used:
            substituted.append(slot)
            out[idx] = out[0]
    if substituted:
        logger.info(
            "Substituted slot-1 filament for unused slot(s) %s on plate %s "
            "(avoids loaded-filament temp-spread validator)",
            substituted,
            plate_id,
        )
    return out

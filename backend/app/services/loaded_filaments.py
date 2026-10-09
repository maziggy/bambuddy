"""The filaments loaded on the active printers of one model.

Read by the ``/printers/available-filaments`` route and by the slicer's
filament map, so the map does not have to call a route handler and resolve
its dependencies by hand.
"""

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.printer import Printer
from backend.app.services.printer_manager import printer_manager


async def loaded_filaments(
    db: AsyncSession,
    model: str,
    *,
    location: str | None = None,
    allows: Callable[[int], bool] | None = None,
) -> list[dict]:
    """Deduplicated filaments loaded across the active printers of ``model``.

    ``allows`` limits the printers to the ones a caller may see (the route
    passes its printer scope). Without it every active printer of the model
    counts.
    """
    from backend.app.utils.printer_models import normalize_printer_model, normalize_printer_model_id

    # Normalize model name
    normalized_model = normalize_printer_model(model) or normalize_printer_model_id(model) or model

    query = (
        select(Printer).where(func.lower(Printer.model) == normalized_model.lower()).where(Printer.is_active == True)  # noqa: E712
    )
    if location:
        query = query.where(Printer.location == location)

    result = await db.execute(query)
    printers_list = [p for p in result.scalars().all() if allows is None or allows(p.id)]

    if not printers_list:
        return []

    # Collect filaments from all matching printers
    # Dedup key includes extruder_id and tray_sub_brands so "PLA Basic" and "PLA Matte" appear separately
    seen: set[tuple[str, str, str, int | None]] = set()  # (type_upper, color_normalized, sub_brands_upper, extruder_id)
    filaments = []

    for printer in printers_list:
        status = printer_manager.get_status(printer.id)
        if not status:
            continue

        # Get ams_extruder_map for dual-nozzle printers
        ams_extruder_map = status.raw_data.get("ams_extruder_map", {})

        # AMS trays
        for ams_unit in status.raw_data.get("ams", []):
            ams_id = str(ams_unit.get("id", 0))
            extruder_id = ams_extruder_map.get(ams_id)
            for tray in ams_unit.get("tray", []):
                tray_type = tray.get("tray_type")
                if not tray_type:
                    continue
                tray_color = tray.get("tray_color", "") or "808080"
                # Preserve the full RRGGBBAA so transparent filament (alpha=00)
                # reaches the frontend instead of collapsing to #000000 → black
                # (#1545). Opaque colours still round-trip as #RRGGBB. The
                # dedup key uses the 6-char RGB so two slots that share an RGB
                # but differ only in alpha still merge.
                stripped = tray_color.replace("#", "")
                rgb = stripped[:6].lower() or "808080"
                color = f"#{stripped}"
                tray_info_idx = tray.get("tray_info_idx", "")
                tray_sub_brands = tray.get("tray_sub_brands", "") or ""

                key = (tray_type.upper(), rgb, tray_sub_brands.upper(), extruder_id)
                if key not in seen:
                    seen.add(key)
                    filaments.append(
                        {
                            "type": tray_type,
                            "color": color,
                            "tray_info_idx": tray_info_idx,
                            "tray_sub_brands": tray_sub_brands,
                            "extruder_id": extruder_id,
                        }
                    )

        # External spools (vt_tray)
        for vt in status.raw_data.get("vt_tray") or []:
            vt_type = vt.get("tray_type")
            if not vt_type:
                continue
            vt_color = vt.get("tray_color", "") or "808080"
            # Same alpha-preserving handling as the AMS branch — see #1545.
            stripped = vt_color.replace("#", "")
            rgb = stripped[:6].lower() or "808080"
            color = f"#{stripped}"
            tray_info_idx = vt.get("tray_info_idx", "")
            tray_sub_brands = vt.get("tray_sub_brands", "") or ""
            vt_id = int(vt.get("id", 254))
            extruder_id = (255 - vt_id) if ams_extruder_map else None

            key = (vt_type.upper(), rgb, tray_sub_brands.upper(), extruder_id)
            if key not in seen:
                seen.add(key)
                filaments.append(
                    {
                        "type": vt_type,
                        "color": color,
                        "tray_info_idx": tray_info_idx,
                        "tray_sub_brands": tray_sub_brands,
                        "extruder_id": extruder_id,
                    }
                )

    return filaments

"""Built-in inventory stock grouping and lookup (#3590)."""

from datetime import datetime

import pytest

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.services.inventory_stock import StockEntry, inventory_stock, stock_for, stock_in

pytestmark = pytest.mark.asyncio


def _spool(**kw) -> Spool:
    base = {"material": "PLA", "rgba": "FF0000FF", "label_weight": 1000, "weight_used": 0}
    base.update(kw)
    return Spool(**base)


async def test_one_entry_per_type_and_colour_when_idx_does_not_distinguish(db_session):
    db_session.add_all(
        [
            _spool(weight_used=250, subtype="Basic", color_name="Red"),
            _spool(weight_used=0, subtype="Basic", color_name="Red", slicer_filament="GFA00"),
            _spool(rgba="00FF00FF", slicer_filament="GFA01"),
        ]
    )
    await db_session.commit()
    entries = await inventory_stock(db_session)
    by_key = {(e.color, e.tray_info_idx): e for e in entries}
    # One distinct idx for red (GFA00 + none) is no variant split: one idx-less entry.
    assert set(by_key) == {("#FF0000", ""), ("#00FF00", "")}
    red = by_key[("#FF0000", "")]
    assert (red.spool_count, red.remaining_grams, red.subtype, red.color_name) == (2, 1750.0, "Basic", "Red")


async def test_idx_kept_only_when_variants_differ(db_session):
    db_session.add_all(
        [_spool(slicer_filament="GFA00"), _spool(slicer_filament="GFA01"), _spool(slicer_filament="GFA01")]
    )
    await db_session.commit()
    entries = await inventory_stock(db_session)
    assert {(e.tray_info_idx, e.spool_count) for e in entries} == {("GFA00", 1), ("GFA01", 2)}


async def test_archived_excluded_and_unknown_weight_counts_as_present(db_session):
    db_session.add_all(
        [
            _spool(archived_at=datetime(2026, 1, 1)),
            _spool(label_weight=0),
            _spool(rgba="0000FFFF", weight_used=400),
        ]
    )
    await db_session.commit()
    entries = await inventory_stock(db_session)
    assert entries is not None
    red = [e for e in entries if e.color == "#FF0000"]
    # Unknown weight: present (count 1), 0 g, never "none left".
    assert [(e.spool_count, e.remaining_grams) for e in red] == [(1, 0.0)]
    assert await stock_for(db_session, "PLA", "#FF0000", "") == (1, 0.0)
    blue = next(e for e in entries if e.color == "#0000FF")
    assert blue.remaining_grams == 600.0
    assert all(not e.archived_only for e in entries)


async def test_empty_spools_are_none_left_and_sorted_last(db_session):
    db_session.add_all([_spool(weight_used=1000), _spool(rgba="0000FFFF", weight_used=999)])
    await db_session.commit()
    entries = await inventory_stock(db_session)
    assert [e.color for e in entries] == ["#0000FF", "#FF0000"]
    assert (entries[1].spool_count, entries[1].empty_spool_count) == (0, 1)
    assert await stock_for(db_session, "PLA", "#FF0000", "") == (0, 0.0)


async def test_never_known_colour_is_unknown(db_session):
    db_session.add(_spool())
    await db_session.commit()
    assert await stock_for(db_session, "PLA", "#123456", "") is None
    assert await stock_for(db_session, "PETG", "#FF0000", "") is None


async def test_archived_only_is_known_none_left(db_session):
    db_session.add_all([_spool(archived_at=datetime(2026, 1, 1)), _spool(rgba="0000FFFF")])
    await db_session.commit()
    assert await stock_for(db_session, "PLA", "#FF0000", "") == (0, 0.0)
    assert all(e.color != "#FF0000" for e in await inventory_stock(db_session))


async def test_empty_inventory_is_unknown(db_session):
    assert await stock_for(db_session, "PLA", "#FF0000", "") is None


async def test_stock_in_is_pure():
    e = StockEntry("PLA", "#FF0000", "", "", "", None, None, None, 2, 500.0, 0)
    assert stock_in([e], "pla", "#ff0000", "GFA00") == (2, 500.0)
    assert stock_in([e], "PLA", "#00FF00", "") is None
    assert stock_in([], "PLA", "#FF0000", "") is None


async def test_spoolman_mode_is_unknown(db_session):
    db_session.add_all([Settings(key="spoolman_enabled", value="true"), _spool()])
    await db_session.commit()
    assert await inventory_stock(db_session) is None
    assert await stock_for(db_session, "PLA", "#FF0000", "") is None


async def test_stock_for_matching_rule(db_session):
    db_session.add_all(
        [_spool(slicer_filament="GFA00"), _spool(slicer_filament="GFA01", weight_used=500), _spool(material="PETG")]
    )
    await db_session.commit()
    # No idx on the override matches every variant of type + colour.
    assert await stock_for(db_session, "PLA", "#ff0000", "") == (2, 1500.0)
    assert await stock_for(db_session, "PLA", "#FF0000", "GFA00") == (1, 1000.0)
    assert await stock_for(db_session, "PLA", "#FF0000", "GFA02") is None
    assert await stock_for(db_session, "ABS", "#FF0000", "") is None


async def test_archived_variant_is_not_covered_by_another_active_variant(db_session):
    # X archived, Y active: an override naming X must read "none left" (#3590).
    db_session.add_all(
        [_spool(slicer_filament="GFA00", archived_at=datetime(2026, 1, 1)), _spool(slicer_filament="GFA01")]
    )
    await db_session.commit()
    assert await stock_for(db_session, "PLA", "#FF0000", "GFA00") == (0, 0.0)
    assert await stock_for(db_session, "PLA", "#FF0000", "GFA01") == (1, 1000.0)
    assert await stock_for(db_session, "PLA", "#FF0000", "") == (1, 1000.0)
    # The API list keeps the collapsed, idx-less entry.
    entries = await inventory_stock(db_session)
    assert [(e.tray_info_idx, e.spool_count) for e in entries] == [("", 1)]


async def test_variant_lookup_counts_spools_without_idx(db_session):
    db_session.add_all([_spool(slicer_filament="GFA00", archived_at=datetime(2026, 1, 1)), _spool()])
    await db_session.commit()
    assert await stock_for(db_session, "PLA", "#FF0000", "GFA00") == (1, 1000.0)


async def test_type_aliases_share_one_entry(db_session):
    db_session.add_all([_spool(material="PA-CF"), _spool(material="PAHT-CF"), _spool(material="PAHT-CF")])
    await db_session.commit()
    entries = await inventory_stock(db_session)
    assert len(entries) == 1
    assert entries[0].spool_count == 3
    assert entries[0].type == "PAHT-CF"  # most common spelling

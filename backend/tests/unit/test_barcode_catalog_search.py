"""Unit tests for GET /inventory/barcode/catalog-search.

The kiosk "Find This Filament" picker searches the user's own inventory plus
the cached community databases (OFD, SpoolmanDB-Community). These tests pin:
  - local inventory matches (token AND-match over brand/material/subtype/color)
  - source ranking: inventory before OFD before SpoolmanDB-Community
  - external sources gated off when barcode_lookup_enabled is false
  - OFD rows are per purchasable package (one row per size entry)
  - each row carries its sibling `codes` for linking
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.inventory import barcode_catalog_search


def _spool(**over):
    s = MagicMock()
    defaults = {
        "id": 1,
        "material": "PLA",
        "brand": "Polymaker",
        "subtype": "PolyTerra Matte",
        "color_name": "Charcoal Black",
        "rgba": "3B3B3FFF",
        "label_weight": 1000,
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
        "archived_at": None,
        "gtin_code": None,
        "asin_code": None,
        "sku_code": None,
        "other_code": None,
        "bought_as_refill": False,
    }
    defaults.update(over)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _settings_row(key, value):
    row = MagicMock()
    row.key = key
    row.value = value
    return row


def _db(local_spools=(), settings_rows=()):
    """db.execute: 1st call → settings map, 2nd call → local spool query."""
    db = AsyncMock()
    settings_result = MagicMock()
    settings_result.scalars.return_value.all.return_value = list(settings_rows)
    spool_result = MagicMock()
    spool_result.scalars.return_value.all.return_value = list(local_spools)
    calls = {"n": 0}

    async def _execute(*_a, **_k):
        calls["n"] += 1
        return settings_result if calls["n"] == 1 else spool_result

    db.execute = _execute
    return db


def _patch_external(gtin_index=None, filaments=None, article_index=None):
    return patch.multiple(
        "backend.app.services.ofd_client",
        get_gtin_index=AsyncMock(return_value=gtin_index or {}),
        get_article_index=AsyncMock(return_value=article_index or {}),
        codes_for_variant=AsyncMock(return_value=[]),
    ), patch.multiple(
        "backend.app.services.spoolmandb_community_client",
        get_filaments=AsyncMock(return_value=filaments or []),
        codes_for_variant=MagicMock(return_value=[]),
    )


class TestCatalogSearch:
    @pytest.mark.asyncio
    async def test_local_inventory_match(self):
        db = _db(local_spools=[_spool()])
        p1, p2 = _patch_external()
        with p1, p2:
            rows = await barcode_catalog_search(q="polymaker charcoal", limit=25, db=db, _=None)

        assert len(rows) == 1
        assert rows[0].source == "inventory"
        assert rows[0].spool_id == 1
        assert rows[0].color_name == "Charcoal Black"

    @pytest.mark.asyncio
    async def test_token_and_match_excludes_partial(self):
        # "charcoal blue" — "blue" is not in the row, so no match.
        db = _db(local_spools=[_spool()])
        p1, p2 = _patch_external()
        with p1, p2:
            rows = await barcode_catalog_search(q="charcoal blue", limit=25, db=db, _=None)
        assert rows == []

    @pytest.mark.asyncio
    async def test_ranks_inventory_before_external(self):
        db = _db(local_spools=[_spool(id=7)])
        gtin_index = {
            "6975337031234": {
                "fields": {"material": "PLA", "brand": "Polymaker", "color_name": "Charcoal"},
                "variant_id": "v1",
            }
        }
        filaments = [{"material": "PLA", "brand": "Polymaker", "subtype": None, "color_name": "Charcoal Grey"}]
        p1, p2 = _patch_external(gtin_index=gtin_index, filaments=filaments)
        with p1, p2:
            rows = await barcode_catalog_search(q="polymaker charcoal", limit=25, db=db, _=None)

        sources = [r.source for r in rows]
        assert sources[0] == "inventory"
        assert "ofd" in sources
        assert "spoolmandb-community" in sources
        assert sources.index("ofd") < sources.index("spoolmandb-community")

    @pytest.mark.asyncio
    async def test_disabled_setting_gates_external(self):
        db = _db(
            local_spools=[],
            settings_rows=[_settings_row("barcode_lookup_enabled", "false")],
        )
        gtin_index = {
            "6975337031234": {
                "fields": {"material": "PLA", "brand": "Polymaker", "color_name": "Charcoal"},
                "variant_id": "v1",
            }
        }
        p1, p2 = _patch_external(
            gtin_index=gtin_index, filaments=[{"brand": "Polymaker", "material": "PLA", "color_name": "Charcoal"}]
        )
        with p1, p2:
            rows = await barcode_catalog_search(q="polymaker charcoal", limit=25, db=db, _=None)

        assert rows == []

    @pytest.mark.asyncio
    async def test_ofd_rows_are_per_package(self):
        """Two GTINs sharing one variant are two PACKAGES (e.g. 1 kg and
        3 kg of the same color) — each gets its own row with its own
        label_weight and only its own same-size code pair. The old
        per-variant dedup merged them into one size-ambiguous row."""
        db = _db(local_spools=[])
        gtin_index = {
            "111": {
                "fields": {"material": "PLA", "brand": "Polymaker", "color_name": "Charcoal", "label_weight": 1000},
                "variant_id": "v1",
                "paired": "SKU-1KG",
                "is_refill": False,
            },
            "222": {
                "fields": {"material": "PLA", "brand": "Polymaker", "color_name": "Charcoal", "label_weight": 3000},
                "variant_id": "v1",
                "paired": None,
                "is_refill": True,
            },
        }
        p1, p2 = _patch_external(gtin_index=gtin_index)
        with p1, p2:
            rows = await barcode_catalog_search(q="polymaker charcoal", limit=25, db=db, _=None)

        ofd_rows = [r for r in rows if r.source == "ofd"]
        assert len(ofd_rows) == 2
        by_weight = {r.label_weight: r for r in ofd_rows}
        assert {c.code for c in by_weight[1000].codes} == {"111", "SKU-1KG"}
        assert {c.code for c in by_weight[3000].codes} == {"222"}
        assert by_weight[3000].codes[0].is_refill is True

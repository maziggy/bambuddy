"""Profile defaults per product (#1326).

A default holds the slicer preset, the per-model preset overrides and the K
profiles for one Brand/Material/Subtype, for all colours or for one, and every
way a spool is added copies it onto the new spool. Both inventory modes; the
default itself is shared.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

DEFAULTS = "/api/v1/inventory/profile-defaults"
INTERNAL = "/api/v1/inventory/spools"
SPOOLMAN = "/api/v1/spoolman/inventory"


def _k(printer_id, *, extruder=0, diameter="0.4", cali_idx=5, k_value=0.02, name="PLA HF"):
    return {
        "printer_id": printer_id,
        "extruder": extruder,
        "nozzle_diameter": diameter,
        "nozzle_type": None,
        "k_value": k_value,
        "name": name,
        "cali_idx": cali_idx,
        "setting_id": None,
    }


def _preset(model="H2D", diameter="0.4", code="PFUS01", name="My PLA @BBL H2D"):
    return {"printer_model": model, "nozzle_diameter": diameter, "slicer_filament": code, "slicer_filament_name": name}


def _default(color_name="", **overrides):
    body = {
        "brand": "Bambu Lab",
        "material": "PLA",
        "subtype": "Basic",
        "color_name": color_name,
        "slicer_filament": "PFUS00",
        "slicer_filament_name": "My PLA",
        "presets": [],
        "k_profiles": [],
    }
    body.update(overrides)
    return body


def _spool_body(**overrides):
    body = {"material": "PLA", "subtype": "Basic", "brand": "Bambu Lab", "color_name": "Jade White"}
    body.update(overrides)
    return body


@pytest.fixture
async def printers(printer_factory):
    return [await printer_factory(model="H2D"), await printer_factory(model="X1C")]


@pytest.mark.integration
class TestDefaultsCrud:
    @pytest.mark.asyncio
    async def test_put_creates_then_replaces(self, async_client: AsyncClient, printers):
        a, b = printers
        response = await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)], presets=[_preset()]))
        assert response.status_code == 200, response.text
        first = response.json()

        response = await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(b.id)]))
        assert response.status_code == 200, response.text
        second = response.json()

        assert second["id"] == first["id"]
        assert [k["printer_id"] for k in second["k_profiles"]] == [b.id]
        assert second["presets"] == []
        listed = (await async_client.get(DEFAULTS)).json()
        assert len(listed) == 1

    @pytest.mark.asyncio
    async def test_product_matches_case_insensitively(self, async_client: AsyncClient):
        await async_client.put(DEFAULTS, json=_default())
        await async_client.put(DEFAULTS, json=_default(brand="bambu lab", material="pla", subtype="basic"))
        assert len((await async_client.get(DEFAULTS)).json()) == 1

    @pytest.mark.asyncio
    async def test_duplicate_hotend_is_refused_before_anything_changes(self, async_client: AsyncClient, printers):
        a, _ = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))
        response = await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id), _k(a.id, cali_idx=7)]))
        assert response.status_code == 422
        kept = (await async_client.get(DEFAULTS)).json()[0]
        assert [k["cali_idx"] for k in kept["k_profiles"]] == [5]

    @pytest.mark.asyncio
    async def test_unknown_printer_is_refused(self, async_client: AsyncClient):
        response = await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(999)]))
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete(self, async_client: AsyncClient):
        row = (await async_client.put(DEFAULTS, json=_default())).json()
        assert (await async_client.delete(f"{DEFAULTS}/{row['id']}")).status_code == 200
        assert (await async_client.get(DEFAULTS)).json() == []
        assert (await async_client.delete(f"{DEFAULTS}/{row['id']}")).status_code == 404

    @pytest.mark.asyncio
    async def test_deleting_a_printer_takes_its_k_profiles_with_it(self, async_client: AsyncClient, printers):
        a, b = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id), _k(b.id)]))
        assert (await async_client.delete(f"/api/v1/printers/{a.id}")).status_code == 200
        kept = (await async_client.get(DEFAULTS)).json()[0]
        assert [k["printer_id"] for k in kept["k_profiles"]] == [b.id]


@pytest.mark.integration
class TestResolve:
    @pytest.mark.asyncio
    async def test_no_default(self, async_client: AsyncClient):
        response = await async_client.get(f"{DEFAULTS}/resolve", params={"material": "PLA"})
        assert response.status_code == 200
        assert response.json() is None

    @pytest.mark.asyncio
    async def test_all_colours_default_covers_every_colour(self, async_client: AsyncClient, printers):
        a, _ = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))
        resolved = (await async_client.get(f"{DEFAULTS}/resolve", params=_spool_body(color_name="Never Seen"))).json()
        assert resolved["slicer_filament"] == "PFUS00"
        assert [k["cali_idx"] for k in resolved["k_profiles"]] == [5]

    @pytest.mark.asyncio
    async def test_colour_wins_per_hotend_and_keeps_the_rest(self, async_client: AsyncClient, printers):
        a, b = printers
        await async_client.put(
            DEFAULTS,
            json=_default(
                k_profiles=[_k(a.id, extruder=0), _k(a.id, extruder=1), _k(b.id)],
                presets=[_preset("H2D"), _preset("X1C", code="PFUS02")],
            ),
        )
        await async_client.put(
            DEFAULTS,
            json=_default(
                "Jade White",
                slicer_filament=None,
                k_profiles=[_k(a.id, extruder=1, cali_idx=9, k_value=0.031)],
                presets=[_preset("X1C", code="PFUS09")],
            ),
        )
        resolved = (await async_client.get(f"{DEFAULTS}/resolve", params=_spool_body())).json()
        by_hotend = {(k["printer_id"], k["extruder"]): k["cali_idx"] for k in resolved["k_profiles"]}
        assert by_hotend == {(a.id, 0): 5, (a.id, 1): 9, (b.id, 0): 5}
        by_model = {p["printer_model"]: p["slicer_filament"] for p in resolved["presets"]}
        assert by_model == {"H2D": "PFUS01", "X1C": "PFUS09"}
        # The colour row sets no preset of its own, so the product's stays.
        assert resolved["slicer_filament"] == "PFUS00"

    @pytest.mark.asyncio
    async def test_other_products_are_not_matched(self, async_client: AsyncClient):
        await async_client.put(DEFAULTS, json=_default())
        for params in (_spool_body(subtype="Matte"), _spool_body(brand="Polymaker"), _spool_body(material="PETG")):
            assert (await async_client.get(f"{DEFAULTS}/resolve", params=params)).json() is None


@pytest.mark.integration
class TestInternalCreate:
    @pytest.mark.asyncio
    async def test_new_spool_gets_the_default(self, async_client: AsyncClient, printers):
        a, _ = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)], presets=[_preset()]))

        spool = (await async_client.post(INTERNAL, json=_spool_body())).json()
        assert spool["slicer_filament"] == "PFUS00"
        assert spool["slicer_filament_name"] == "My PLA"
        assert [(k["printer_id"], k["cali_idx"]) for k in spool["k_profiles"]] == [(a.id, 5)]
        presets = (await async_client.get(f"{INTERNAL}/{spool['id']}/filament-presets")).json()
        assert [p["slicer_filament"] for p in presets] == ["PFUS01"]

    @pytest.mark.asyncio
    async def test_a_preset_in_the_request_is_kept(self, async_client: AsyncClient):
        await async_client.put(DEFAULTS, json=_default())
        spool = (
            await async_client.post(
                INTERNAL, json=_spool_body(slicer_filament="GFA00", slicer_filament_name="Bambu PLA Basic")
            )
        ).json()
        assert spool["slicer_filament"] == "GFA00"

    @pytest.mark.asyncio
    async def test_bulk_create_gives_every_copy_the_default(self, async_client: AsyncClient, printers):
        a, _ = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))
        response = await async_client.post(f"{INTERNAL}/bulk", json={"spool": _spool_body(), "quantity": 3})
        assert response.status_code == 200, response.text
        spools = response.json()
        assert len(spools) == 3
        assert all([k["cali_idx"] for k in s["k_profiles"]] == [5] for s in spools)
        assert all(s["slicer_filament"] == "PFUS00" for s in spools)

    @pytest.mark.asyncio
    async def test_without_a_default_nothing_is_added(self, async_client: AsyncClient):
        spool = (await async_client.post(INTERNAL, json=_spool_body())).json()
        assert spool["slicer_filament"] is None
        assert spool["k_profiles"] == []

    @pytest.mark.asyncio
    async def test_rfid_auto_create_keeps_the_tag_preset_and_assigns_the_default_k(self, db_session, printers):
        from backend.app.models.filament_profile_default import (
            FilamentProfileDefault,
            FilamentProfileDefaultKProfile,
        )
        from backend.app.services.spool_tag_matcher import create_spool_from_tray

        a, _ = printers
        row = FilamentProfileDefault(
            match_key="bambu lab\x1fpla\x1fbasic\x1f",
            brand="Bambu Lab",
            material="PLA",
            subtype="Basic",
            slicer_filament="PFUS00",
        )
        db_session.add(row)
        await db_session.flush()
        db_session.add(FilamentProfileDefaultKProfile(profile_default_id=row.id, **_k(a.id)))
        await db_session.commit()

        spool = await create_spool_from_tray(
            db_session,
            {
                "tray_type": "PLA",
                "tray_sub_brands": "PLA Basic",
                "tray_color": "FFFFFFFF",
                "tray_info_idx": "GFA00",
                "tray_weight": "1000",
                "remain": 100,
                "tag_uid": "A1B2C3D4E5F60708",
                "tray_uuid": "0123456789ABCDEF0123456789ABCDEF",
            },
        )
        assert spool.slicer_filament == "GFA00"
        # In the in-memory list the RFID assign right after reads.
        assert [k.cali_idx for k in spool.k_profiles] == [5]


@pytest.mark.integration
class TestInternalApply:
    @pytest.mark.asyncio
    async def test_fills_only_what_is_missing(self, async_client: AsyncClient, printers):
        a, b = printers
        # Spools first, so the create path has no default to apply.
        bare = (await async_client.post(INTERNAL, json=_spool_body())).json()
        own = (await async_client.post(INTERNAL, json=_spool_body(slicer_filament="GFA00"))).json()
        await async_client.put(f"{INTERNAL}/{own['id']}/k-profiles", json=[_k(a.id, cali_idx=11, k_value=0.05)])
        other = (await async_client.post(INTERNAL, json=_spool_body(subtype="Matte"))).json()
        archived = (await async_client.post(INTERNAL, json=_spool_body())).json()
        await async_client.post(f"{INTERNAL}/{archived['id']}/archive")

        row = (await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id), _k(b.id)]))).json()
        response = await async_client.post(f"{DEFAULTS}/{row['id']}/apply")
        assert response.status_code == 200, response.text
        assert response.json() == {"updated": 2}

        def k_of(spool):
            return {k["printer_id"]: k["cali_idx"] for k in spool["k_profiles"]}

        bare = (await async_client.get(f"{INTERNAL}/{bare['id']}")).json()
        assert bare["slicer_filament"] == "PFUS00"
        assert k_of(bare) == {a.id: 5, b.id: 5}
        own = (await async_client.get(f"{INTERNAL}/{own['id']}")).json()
        assert own["slicer_filament"] == "GFA00"
        assert k_of(own) == {a.id: 11, b.id: 5}
        assert (await async_client.get(f"{INTERNAL}/{other['id']}")).json()["k_profiles"] == []
        assert (await async_client.get(f"{INTERNAL}/{archived['id']}")).json()["k_profiles"] == []

        # Nothing left to fill.
        assert (await async_client.post(f"{DEFAULTS}/{row['id']}/apply")).json() == {"updated": 0}

    @pytest.mark.asyncio
    async def test_all_colours_apply_gives_a_colour_its_own_values(self, async_client: AsyncClient, printers):
        a, _ = printers
        white = (await async_client.post(INTERNAL, json=_spool_body())).json()
        black = (await async_client.post(INTERNAL, json=_spool_body(color_name="Black"))).json()
        row = (await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))).json()
        await async_client.put(DEFAULTS, json=_default("Jade White", k_profiles=[_k(a.id, cali_idx=9)]))

        assert (await async_client.post(f"{DEFAULTS}/{row['id']}/apply")).json() == {"updated": 2}
        white = (await async_client.get(f"{INTERNAL}/{white['id']}")).json()
        black = (await async_client.get(f"{INTERNAL}/{black['id']}")).json()
        assert [k["cali_idx"] for k in white["k_profiles"]] == [9]
        assert [k["cali_idx"] for k in black["k_profiles"]] == [5]


# ── Spoolman mode ────────────────────────────────────────────────────────────


def _spoolman_spool(spool_id, *, extra=None, name="PLA Basic", archived=False):
    return {
        "id": spool_id,
        "filament": {
            "id": 7,
            "name": name,
            "material": "PLA",
            "color_hex": "FFFFFF",
            "weight": 1000,
            "vendor": {"id": 3, "name": "Bambu Lab"},
        },
        "remaining_weight": 1000.0,
        "used_weight": 0.0,
        "location": None,
        "comment": None,
        "first_used": None,
        "last_used": None,
        "registered": "2024-01-01T00:00:00+00:00",
        "archived": archived,
        "price": None,
        "extra": extra or {},
    }


@pytest.fixture
def spoolman_client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    ids = iter(range(100, 200))
    client.create_spool = AsyncMock(side_effect=lambda **kwargs: _spoolman_spool(next(ids)))
    client.find_or_create_filament = AsyncMock(return_value=7)
    client.find_or_create_vendor = AsyncMock(return_value=3)
    client.ensure_extra_field = AsyncMock(return_value=True)
    client.merge_spool_extra = AsyncMock(side_effect=lambda spool_id, extra: _spoolman_spool(spool_id, extra=extra))
    with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)):
        yield client


async def _spoolman_rows(db_session, spool_id):
    from backend.app.models.spool_filament_preset import SpoolmanFilamentPreset
    from backend.app.models.spoolman_k_profile import SpoolmanKProfile

    k = (
        (await db_session.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == spool_id)))
        .scalars()
        .all()
    )
    presets = (
        (
            await db_session.execute(
                select(SpoolmanFilamentPreset).where(SpoolmanFilamentPreset.spoolman_spool_id == spool_id)
            )
        )
        .scalars()
        .all()
    )
    return [r.cali_idx for r in k], [p.slicer_filament for p in presets]


@pytest.mark.integration
class TestSpoolman:
    @pytest.mark.asyncio
    async def test_create_gets_the_default(self, async_client: AsyncClient, db_session, printers, spoolman_client):
        a, _ = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)], presets=[_preset()]))

        response = await async_client.post(f"{SPOOLMAN}/spools", json=_spool_body())
        assert response.status_code == 200, response.text
        assert response.json()["slicer_filament"] == "PFUS00"
        extra = spoolman_client.merge_spool_extra.call_args.args[1]
        assert extra["bambu_slicer_filament"] == '"PFUS00"'
        assert extra["bambu_color_name"] == '"Jade White"'
        assert await _spoolman_rows(db_session, 100) == ([5], ["PFUS01"])

    @pytest.mark.asyncio
    async def test_bulk_create_writes_preset_colour_and_default_on_every_copy(
        self, async_client: AsyncClient, db_session, printers, spoolman_client
    ):
        a, _ = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))
        response = await async_client.post(f"{SPOOLMAN}/spools/bulk", json={"spool": _spool_body(), "quantity": 2})
        assert response.status_code == 200, response.text
        # Bulk wrote neither the preset nor the colour name before.
        written = {c.args[0]: c.args[1] for c in spoolman_client.merge_spool_extra.call_args_list}
        assert set(written) == {100, 101}
        assert all(e["bambu_color_name"] == '"Jade White"' for e in written.values())
        assert all(e["bambu_slicer_filament"] == '"PFUS00"' for e in written.values())
        for spool_id in (100, 101):
            assert (await _spoolman_rows(db_session, spool_id))[0] == [5]

    @pytest.mark.asyncio
    async def test_apply_fills_spoolman_spools(self, async_client: AsyncClient, db_session, printers, spoolman_client):
        a, _ = printers
        spoolman_client.get_all_spools = AsyncMock(
            return_value=[
                _spoolman_spool(1, extra={"bambu_color_name": '"Jade White"'}),
                _spoolman_spool(2, extra={"bambu_slicer_filament": '"GFA00"'}),
                _spoolman_spool(3, name="PLA Matte"),
                _spoolman_spool(4, archived=True),
            ]
        )
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        # Spool 2 has its own K profile for the hotend the default covers.
        db_session.add(SpoolmanKProfile(spoolman_spool_id=2, **_k(a.id, cali_idx=11)))
        await db_session.commit()
        row = (await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))).json()

        response = await async_client.post(f"{SPOOLMAN}/profile-defaults/{row['id']}/apply")
        assert response.status_code == 200, response.text
        # Spool 2 has both its preset and its K profile: nothing to fill.
        assert response.json() == {"updated": 1}
        assert (await _spoolman_rows(db_session, 1))[0] == [5]
        assert (await _spoolman_rows(db_session, 2))[0] == [11]
        assert (await _spoolman_rows(db_session, 3))[0] == []
        assert (await _spoolman_rows(db_session, 4))[0] == []
        # Only the spool without a preset of its own gets the default's.
        assert [c.args[0] for c in spoolman_client.merge_spool_extra.call_args_list] == [1]

    @pytest.mark.asyncio
    async def test_ams_auto_add_gets_k_profiles(self, async_client: AsyncClient, db_session, printers):
        from backend.app.services.filament_profile_default import fill_auto_added_spoolman_spool

        a, _ = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)], presets=[_preset()]))
        await fill_auto_added_spoolman_spool(_spoolman_spool(55))
        assert await _spoolman_rows(db_session, 55) == ([5], ["PFUS01"])


@pytest.fixture
def limited_to(request):
    """Run the request as a caller limited to some printers (#1727)."""
    from backend.app.core.auth import get_printer_scope_if_auth_enabled
    from backend.app.core.printer_scope import PrinterScope
    from backend.app.main import app

    def _limit(*printer_ids):
        app.dependency_overrides[get_printer_scope_if_auth_enabled] = lambda: PrinterScope(frozenset(printer_ids))

    yield _limit
    app.dependency_overrides.pop(get_printer_scope_if_auth_enabled, None)


@pytest.mark.integration
class TestPrinterScope:
    @pytest.mark.asyncio
    async def test_a_limited_caller_sees_only_its_printers(self, async_client: AsyncClient, printers, limited_to):
        a, b = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id), _k(b.id)]))
        limited_to(b.id)
        listed = (await async_client.get(DEFAULTS)).json()[0]
        assert [k["printer_id"] for k in listed["k_profiles"]] == [b.id]
        resolved = (await async_client.get(f"{DEFAULTS}/resolve", params=_spool_body())).json()
        assert [k["printer_id"] for k in resolved["k_profiles"]] == [b.id]

    @pytest.mark.asyncio
    async def test_a_limited_save_keeps_the_printers_it_cannot_see(
        self, async_client: AsyncClient, printers, limited_to
    ):
        a, b = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id), _k(b.id)]))
        limited_to(b.id)
        response = await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(b.id, cali_idx=8)]))
        assert response.status_code == 200, response.text
        limited_to(a.id, b.id)
        kept = (await async_client.get(DEFAULTS)).json()[0]
        assert {k["printer_id"]: k["cali_idx"] for k in kept["k_profiles"]} == {a.id: 5, b.id: 8}

    @pytest.mark.asyncio
    async def test_a_limited_caller_cannot_write_another_printer(self, async_client: AsyncClient, printers, limited_to):
        # The row is ignored, not refused: a form opened on a spool carries
        # every printer's profile, and saving it as the default must work.
        a, b = printers
        limited_to(b.id)
        response = await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id), _k(b.id)]))
        assert response.status_code == 200, response.text
        limited_to(a.id, b.id)
        kept = (await async_client.get(DEFAULTS)).json()[0]
        assert [k["printer_id"] for k in kept["k_profiles"]] == [b.id]

    @pytest.mark.asyncio
    async def test_a_limited_caller_cannot_delete_another_printers_profiles(
        self, async_client: AsyncClient, printers, limited_to
    ):
        a, b = printers
        row = (await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))).json()
        limited_to(b.id)
        assert (await async_client.delete(f"{DEFAULTS}/{row['id']}")).status_code == 403


@pytest.mark.integration
class TestInternalPresetFill:
    @pytest.mark.asyncio
    async def test_a_spool_keeps_its_own_preset_for_a_model(self, async_client: AsyncClient):
        spool = (await async_client.post(INTERNAL, json=_spool_body())).json()
        await async_client.put(f"{INTERNAL}/{spool['id']}/filament-presets", json=[_preset("H2D", code="OWN01")])
        row = (
            await async_client.put(DEFAULTS, json=_default(presets=[_preset("H2D"), _preset("X1C", code="PFUS02")]))
        ).json()
        assert (await async_client.post(f"{DEFAULTS}/{row['id']}/apply")).json() == {"updated": 1}
        presets = (await async_client.get(f"{INTERNAL}/{spool['id']}/filament-presets")).json()
        assert {p["printer_model"]: p["slicer_filament"] for p in presets} == {"H2D": "OWN01", "X1C": "PFUS02"}


@pytest.mark.integration
class TestNullKeyFields:
    @pytest.mark.asyncio
    async def test_null_brand_subtype_and_colour_read_as_empty(self, async_client: AsyncClient):
        response = await async_client.put(DEFAULTS, json=_default(brand=None, subtype=None, color_name=None))
        assert response.status_code == 200, response.text
        assert {k: response.json()[k] for k in ("brand", "subtype", "color_name")} == {
            "brand": "",
            "subtype": "",
            "color_name": "",
        }

    @pytest.mark.asyncio
    async def test_null_material_is_refused_not_a_500(self, async_client: AsyncClient):
        assert (await async_client.put(DEFAULTS, json=_default(material=None))).status_code == 422


@pytest.mark.integration
class TestReviewFixes:
    @pytest.mark.asyncio
    async def test_a_non_ascii_colour_matches_its_own_row(self, async_client: AsyncClient, printers):
        # SQLite's lower() folds ASCII only.
        a, _ = printers
        await async_client.put(DEFAULTS, json=_default("Красный", k_profiles=[_k(a.id, cali_idx=9)]))
        response = await async_client.put(DEFAULTS, json=_default("Красный", k_profiles=[_k(a.id, cali_idx=8)]))
        assert response.status_code == 200, response.text
        assert len((await async_client.get(DEFAULTS)).json()) == 1
        resolved = (await async_client.get(f"{DEFAULTS}/resolve", params=_spool_body(color_name="Красный"))).json()
        assert [k["cali_idx"] for k in resolved["k_profiles"]] == [8]

    @pytest.mark.asyncio
    async def test_a_limited_k_profile_save_keeps_the_other_printers(
        self, async_client: AsyncClient, printers, limited_to
    ):
        a, b = printers
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id), _k(b.id)]))
        spool = (await async_client.post(INTERNAL, json=_spool_body())).json()
        limited_to(b.id)
        # The limited form only ever saw printer b.
        response = await async_client.put(f"{INTERNAL}/{spool['id']}/k-profiles", json=[_k(b.id, cali_idx=7)])
        assert response.status_code == 200, response.text
        limited_to(a.id, b.id)
        spool = (await async_client.get(f"{INTERNAL}/{spool['id']}")).json()
        assert {k["printer_id"]: k["cali_idx"] for k in spool["k_profiles"]} == {a.id: 5, b.id: 7}

    @pytest.mark.asyncio
    async def test_a_limited_spoolman_k_profile_save_keeps_the_other_printers(
        self, async_client: AsyncClient, db_session, printers, limited_to, spoolman_client
    ):
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        a, b = printers
        spoolman_client.get_spool = AsyncMock(return_value=_spoolman_spool(9))
        db_session.add(SpoolmanKProfile(spoolman_spool_id=9, **_k(a.id)))
        db_session.add(SpoolmanKProfile(spoolman_spool_id=9, **_k(b.id)))
        await db_session.commit()
        limited_to(b.id)
        response = await async_client.put(f"{SPOOLMAN}/spools/9/k-profiles", json=[_k(b.id, cali_idx=7)])
        assert response.status_code == 200, response.text
        rows = (
            (await db_session.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == 9)))
            .scalars()
            .all()
        )
        assert {r.printer_id: r.cali_idx for r in rows} == {a.id: 5, b.id: 7}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("include_slicer", [True, False])
    async def test_an_auto_added_spoolman_spool_gets_the_preset_only_without_one_of_its_own(
        self, async_client: AsyncClient, include_slicer
    ):
        from backend.app.services.filament_profile_default import fill_auto_added_spoolman_spool

        await async_client.put(DEFAULTS, json=_default())
        client = MagicMock()
        client.ensure_extra_field = AsyncMock(return_value=True)
        client.merge_spool_extra = AsyncMock(return_value={})
        await fill_auto_added_spoolman_spool(_spoolman_spool(56), client, include_slicer=include_slicer)
        if include_slicer:
            client.merge_spool_extra.assert_awaited_once()
            assert client.merge_spool_extra.call_args.args[1]["bambu_slicer_filament"] == '"PFUS00"'
        else:
            client.merge_spool_extra.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_folds_non_ascii_case_the_same_way_as_the_lookup(self, async_client: AsyncClient, printers):
        a, _ = printers
        spool = (await async_client.post(INTERNAL, json=_spool_body(color_name="красный"))).json()
        row = (await async_client.put(DEFAULTS, json=_default("Красный", k_profiles=[_k(a.id, cali_idx=9)]))).json()
        await async_client.put(DEFAULTS, json=_default(k_profiles=[_k(a.id)]))
        assert (await async_client.post(f"{DEFAULTS}/{row['id']}/apply")).json() == {"updated": 1}
        spool = (await async_client.get(f"{INTERNAL}/{spool['id']}")).json()
        assert [k["cali_idx"] for k in spool["k_profiles"]] == [9]

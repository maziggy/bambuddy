"""Print cost comes from the linked Spoolman spool's price (#2591).

Spoolman exists to hold per-spool pricing, and #261 gave that as the reason for
integrating with it. Bambuddy never read it. ``archive.py`` prices a print once,
at archive time, from the built-in Filament catalogue matched on the primary
type and falling back to the global default rate -- and in Spoolman mode nothing
revisited that figure, because the per-spool recompute in
``usage_tracker.on_print_complete`` runs only over rows the built-in inventory
writes, and ``spoolman_owns_usage`` stops it writing any.

The reporter's install had an empty catalogue (``filaments_total: 0``), so every
print was priced at the global default no matter what the spool cost.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.spoolman_tracking import _PrintCost, _spool_cost_per_gram


class _AsyncCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _spool(spool_id, *, price=None, filament_price=None, weight=1000, color="888888", material="PLA"):
    """A Spoolman spool row, shaped as its API returns one."""
    return {
        "id": spool_id,
        "price": price,
        "filament": {
            "price": filament_price,
            "weight": weight,
            "color_hex": color,
            "material": material,
        },
    }


class TestSpoolCostPerGram:
    def test_uses_the_filament_catalogue_price(self):
        """25.00 for a 1 kg spool is 2.5 cents a gram."""
        assert _spool_cost_per_gram(_spool(1, filament_price=25.0, weight=1000)) == pytest.approx(0.025)

    def test_the_spools_own_price_overrides_the_filaments(self):
        """Spoolman carries a price on the spool for the purchase that cost
        something other than the catalogue figure. That is the one the Spoolman
        UI shows, so it is the one a print should be charged at."""
        rate = _spool_cost_per_gram(_spool(1, price=40.0, filament_price=25.0, weight=1000))
        assert rate == pytest.approx(0.04)

    def test_weight_is_net_filament_grams_not_a_fixed_kilo(self):
        """A 750 g spool is not a kilo. Dividing by a constant would under-price
        every non-standard roll."""
        assert _spool_cost_per_gram(_spool(1, filament_price=30.0, weight=750)) == pytest.approx(0.04)

    def test_a_zero_spool_override_falls_through_to_the_catalogue(self):
        """Spoolman leaves the spool override null when unset, but importers and
        API clients write 0 often enough that reading it as "this roll was free"
        would price a whole print at the default rate with a perfectly good
        catalogue price one level down."""
        rate = _spool_cost_per_gram(_spool(1, price=0, filament_price=25.0, weight=1000))
        assert rate == pytest.approx(0.025)

    @pytest.mark.parametrize(
        "spool",
        [
            _spool(1, weight=1000),  # no price anywhere
            _spool(1, filament_price=25.0, weight=None),  # no reference weight
            _spool(1, filament_price=0, weight=1000),  # zero is unpriced, not free
            _spool(1, filament_price=-5, weight=1000),
            _spool(1, filament_price="abc", weight=1000),
            {"id": 1, "price": 25, "filament": "not a dict"},
            {"id": 1, "price": 25, "filament": {"weight": True}},  # bool is an int in Python
            {"id": 1, "price": float("nan"), "filament": {"weight": 1000}},
            {"id": 1, "price": 1e308, "filament": {"weight": 1e-308}},  # quotient overflows
            None,
        ],
    )
    def test_says_nothing_rather_than_guessing(self, spool):
        """None means "fall back to the default rate", not "this was free" --
        and never a NaN or an infinity, which every later comparison would
        silently pass through into the archive."""
        assert _spool_cost_per_gram(spool) is None


class TestPrintCostAccumulator:
    def test_sums_each_slot_at_its_own_rate(self):
        """The defect archive.py has: it takes the primary type's rate and
        applies it to the whole print's grams, so a slot of expensive filament
        is billed at the price of the cheap one beside it."""
        cost = _PrintCost()
        cost.add(100.0, _spool(1, filament_price=20.0, weight=1000), "slot 1")  # 0.02/g
        cost.add(50.0, _spool(2, filament_price=60.0, weight=1000), "slot 2")  # 0.06/g

        assert cost.cost == pytest.approx(2.0 + 3.0)
        assert cost.priced_grams == pytest.approx(150.0)
        assert cost.priced == 2

    def test_an_unpriced_spool_is_counted_but_not_charged(self):
        """Its grams stay out of priced_grams so the caller covers them at the
        default rate rather than recording them as free."""
        cost = _PrintCost()
        cost.add(100.0, _spool(1, filament_price=20.0, weight=1000), "slot 1")
        cost.add(50.0, _spool(2, weight=1000), "slot 2")

        assert cost.cost == pytest.approx(2.0)
        assert cost.priced_grams == pytest.approx(100.0)
        assert (cost.priced, cost.unpriced) == (1, 1)

    def test_zero_grams_is_not_a_slot(self):
        cost = _PrintCost()
        cost.add(0.0, _spool(1, filament_price=20.0, weight=1000), "slot 1")
        assert (cost.priced, cost.unpriced, cost.cost) == (0, 0, 0.0)


class TestReportUsagePricesTheArchive:
    """End to end: the price has to reach PrintArchive.cost."""

    @staticmethod
    def _run(tracking, state, spools_by_tag, archive, *, existing_runs=0, default_cost="25"):
        rows = iter([tracking])

        def _next_row(*_args, **_kwargs):
            result = MagicMock()
            result.scalar_one_or_none.return_value = next(rows, archive)
            # The first-run guard counts PrintLogEntry rows.
            result.scalar.return_value = existing_runs
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_next_row)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        client = AsyncMock()
        client.find_spool_by_tag = AsyncMock(side_effect=lambda tag: spools_by_tag.get(tag))
        client.get_spool = AsyncMock(
            side_effect=lambda sid: next((s for s in spools_by_tag.values() if s["id"] == sid), None)
        )
        client.use_spool = AsyncMock()

        pm = MagicMock()
        pm.get_status.return_value = state

        async def _get_setting(_db, key):
            return {"spoolman_enabled": "true", "default_filament_cost": default_cost}.get(key)

        async def _go():
            from backend.app.services.spoolman_tracking import report_usage

            with (
                patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
                patch("backend.app.api.routes.settings.get_setting", AsyncMock(side_effect=_get_setting)),
                patch(
                    "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                    AsyncMock(return_value=client),
                ),
                patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SER")),
                patch(
                    "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                    AsyncMock(return_value=None),
                ),
                patch("backend.app.services.printer_manager.printer_manager", pm),
            ):
                await report_usage(printer_id=1, archive_id=7)

        return _go, client

    @staticmethod
    def _state():
        return SimpleNamespace(
            raw_data={},
            tray_change_log=[],
            total_layers=0,
            layer_num=0,
            tray_now=255,
            last_loaded_tray=-1,
        )

    @pytest.mark.asyncio
    async def test_the_linked_spools_price_replaces_the_default(self):
        """The reported bug. 100 g off a spool that cost 40.00 for 1 kg is 4.00,
        not the 2.50 the global 25/kg default produced."""
        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 100.0, "type": "PLA", "color": "#888888"}],
            ams_trays={"0": {"tray_uuid": "TRAY0", "tag_uid": "", "tray_type": "PLA"}},
            slot_to_tray=[0],
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
            tray_now_at_start=0,
        )
        archive = SimpleNamespace(filament_color="#888888", filament_type="PLA", filament_used_grams=100.0, cost=2.5)

        run, client = self._run(tracking, self._state(), {"TRAY0": _spool(41, price=40.0, weight=1000)}, archive)
        await run()

        client.use_spool.assert_awaited_once_with(41, 100.0)
        assert archive.cost == pytest.approx(4.0)

    @pytest.mark.asyncio
    async def test_multi_material_bills_each_slot_at_its_own_price(self):
        """archive.py charged the whole print at the primary type's rate. Two
        slots, 100 g at 0.02/g and 50 g at 0.06/g, is 5.00 -- not 150 g at
        either one of them (3.00 or 9.00)."""
        tracking = SimpleNamespace(
            filament_usage=[
                {"slot_id": 1, "used_g": 100.0, "type": "PLA", "color": "#888888"},
                {"slot_id": 2, "used_g": 50.0, "type": "PA", "color": "#111111"},
            ],
            ams_trays={
                "0": {"tray_uuid": "TRAY0", "tag_uid": "", "tray_type": "PLA"},
                "1": {"tray_uuid": "TRAY1", "tag_uid": "", "tray_type": "PA"},
            },
            slot_to_tray=[0, 1],
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
            tray_now_at_start=0,
        )
        archive = SimpleNamespace(
            filament_color="#888888", filament_type="PLA,PA", filament_used_grams=150.0, cost=3.75
        )

        run, _client = self._run(
            tracking,
            self._state(),
            {
                "TRAY0": _spool(41, filament_price=20.0, weight=1000),
                "TRAY1": _spool(42, filament_price=60.0, weight=1000, material="PA", color="111111"),
            },
            archive,
        )
        await run()

        assert archive.cost == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_grams_no_spool_could_price_fall_back_to_the_default_rate(self):
        """One priced slot out of a heavier print must not report only its own
        share -- that is #1344 in the other inventory mode. 100 g priced at
        0.04/g plus 50 g the archive knows about but nothing priced, at the
        25/kg default, is 4.00 + 1.25."""
        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 100.0, "type": "PLA", "color": "#888888"}],
            ams_trays={"0": {"tray_uuid": "TRAY0", "tag_uid": "", "tray_type": "PLA"}},
            slot_to_tray=[0],
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
            tray_now_at_start=0,
        )
        archive = SimpleNamespace(filament_color="#888888", filament_type="PLA", filament_used_grams=150.0, cost=3.75)

        run, _client = self._run(tracking, self._state(), {"TRAY0": _spool(41, price=40.0, weight=1000)}, archive)
        await run()

        assert archive.cost == pytest.approx(5.25)

    @pytest.mark.asyncio
    async def test_a_spool_with_no_price_leaves_the_archive_alone(self):
        """Nothing better is known than what archive.py already recorded, so
        an install with prices in neither place stays exactly where it was."""
        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 100.0, "type": "PLA", "color": "#888888"}],
            ams_trays={"0": {"tray_uuid": "TRAY0", "tag_uid": "", "tray_type": "PLA"}},
            slot_to_tray=[0],
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
            tray_now_at_start=0,
        )
        archive = SimpleNamespace(filament_color="#888888", filament_type="PLA", filament_used_grams=100.0, cost=2.5)

        run, _client = self._run(tracking, self._state(), {"TRAY0": _spool(41, weight=1000)}, archive)
        await run()

        assert archive.cost == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_a_reprint_does_not_overwrite_the_first_runs_cost(self):
        """Same guard the built-in writer carries (#1378): reprint actuals live
        in PrintLogEntry, and the archive card keeps the first run's figure."""
        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#888888"}],
            ams_trays={"0": {"tray_uuid": "TRAY0", "tag_uid": "", "tray_type": "PLA"}},
            slot_to_tray=[0],
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
            tray_now_at_start=0,
        )
        archive = SimpleNamespace(filament_color="#888888", filament_type="PLA", filament_used_grams=100.0, cost=4.0)

        run, client = self._run(
            tracking, self._state(), {"TRAY0": _spool(41, price=40.0, weight=1000)}, archive, existing_runs=1
        )
        await run()

        # The spool is still charged for the reprint's grams...
        client.use_spool.assert_awaited_once_with(41, 10.0)
        # ...but the archive keeps the first run's number.
        assert archive.cost == pytest.approx(4.0)

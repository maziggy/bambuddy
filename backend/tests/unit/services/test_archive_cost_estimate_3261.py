"""A running print is priced from its spools at start, not only at completion (#3261).

Before this, the archive card showed ``grams x default_filament_cost`` (or the
catalogue rate) for the whole print and swapped in the spool-based figure only
when the print finished. The estimate uses the same rules as the completion
writers: each slot at the spool in its mapped tray, everything else at the
default rate, and a reprint keeps its first run's cost.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services import archive_cost_estimate
from backend.app.services.archive_cost_estimate import estimate_archive_cost_at_start

# Reporter's archive 295: 4.17 g of PLA; their default rate is 3,400/kg and the
# linked spool cost 1,870 for 1,000 g.
DEFAULT_PER_KG = "3400"


def _printer_manager(trays: list[dict] | None = None, vt_tray: list[dict] | None = None):
    raw = {
        "ams": [
            {"id": 0, "tray": trays if trays is not None else [{"id": 0, "tray_type": "PLA", "tray_color": "FFFFFFFF"}]}
        ],
        "vt_tray": vt_tray or [],
    }
    state = SimpleNamespace(raw_data=raw)
    return SimpleNamespace(get_status=lambda _pid: state)


@pytest.fixture
def three_mf(tmp_path, monkeypatch):
    """Point base_dir at tmp_path, drop a 3MF there, and stub its per-slot usage."""
    monkeypatch.setattr(archive_cost_estimate.app_settings, "base_dir", tmp_path)
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "print.3mf").write_bytes(b"3mf")
    usage: list[dict] = []
    monkeypatch.setattr(
        "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
        lambda _path, _plate: usage,
    )
    return usage


@pytest.fixture
def ws():
    with patch("backend.app.core.websocket.ws_manager.send_archive_updated", new=AsyncMock()) as sent:
        yield sent


async def _setup(db_session, printer_factory, archive_factory, *, spoolman=False, with_run=False, grams=4.17):
    db_session.add(Settings(key="default_filament_cost", value=DEFAULT_PER_KG))
    if spoolman:
        db_session.add(Settings(key="spoolman_enabled", value="true"))
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id,
        print_name="Archive295",
        status="printing",
        cost=14.18,
        with_run=with_run,
        file_path="archive/print.3mf",
        filament_used_grams=grams,
        filament_type="PLA",
    )
    await db_session.commit()
    return printer, archive


async def _assign_spool(db_session, printer_id: int, ams_id: int, tray_id: int, cost_per_kg: float | None):
    spool = Spool(material="PLA", cost_per_kg=cost_per_kg)
    db_session.add(spool)
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer_id, ams_id=ams_id, tray_id=tray_id))
    await db_session.commit()


class TestInternalInventory:
    @pytest.mark.asyncio
    async def test_priced_from_the_assigned_spool(self, db_session, printer_factory, archive_factory, three_mf, ws):
        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
        three_mf.append({"slot_id": 1, "used_g": 4.17, "color": "#FFFFFF"})

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

        await db_session.refresh(archive)
        assert archive.cost == 7.80
        ws.assert_awaited_once_with({"id": archive.id, "cost": 7.80})

    @pytest.mark.asyncio
    async def test_unpriced_grams_at_the_default_rate(self, db_session, printer_factory, archive_factory, three_mf, ws):
        """Slot 2 maps to a tray with no spool; its grams, and grams the 3MF
        didn't attribute, are covered at the default rate like completion does."""
        printer, archive = await _setup(db_session, printer_factory, archive_factory, grams=12.0)
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1000)
        three_mf.extend([{"slot_id": 1, "used_g": 5.0}, {"slot_id": 2, "used_g": 5.0}])

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0, 1])

        await db_session.refresh(archive)
        # 5 g at 1.0/g + 7 g at 3.4/g
        assert archive.cost == 28.80

    @pytest.mark.asyncio
    async def test_spool_without_price_uses_the_default_rate(
        self, db_session, printer_factory, archive_factory, three_mf, ws
    ):
        printer, archive = await _setup(db_session, printer_factory, archive_factory, grams=10.0)
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=None)
        three_mf.append({"slot_id": 1, "used_g": 10.0})

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

        await db_session.refresh(archive)
        assert archive.cost == 34.00

    @pytest.mark.asyncio
    async def test_queue_item_mapping_is_used(self, db_session, printer_factory, archive_factory, three_mf, ws):
        from backend.app.models.print_queue import PrintQueueItem

        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        await _assign_spool(db_session, printer.id, 0, 2, cost_per_kg=1870)
        db_session.add(
            PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="printing", ams_mapping="[2]")
        )
        await db_session.commit()
        three_mf.append({"slot_id": 1, "used_g": 4.17})
        trays = [{"id": i, "tray_type": "PLA", "tray_color": "FFFFFFFF"} for i in range(4)]

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(trays))

        await db_session.refresh(archive)
        assert archive.cost == 7.80

    @pytest.mark.asyncio
    async def test_external_spool_mapped_as_minus_one(self, db_session, printer_factory, archive_factory, three_mf, ws):
        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        await _assign_spool(db_session, printer.id, 255, 0, cost_per_kg=1870)
        three_mf.append({"slot_id": 1, "used_g": 4.17})
        pm = _printer_manager(vt_tray=[{"id": 254, "tray_type": "PLA"}])

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, pm, ams_mapping=[-1])

        await db_session.refresh(archive)
        assert archive.cost == 7.80


class TestLeavesThePlaceholder:
    @pytest.mark.asyncio
    async def test_reprint_keeps_the_first_runs_cost(self, db_session, printer_factory, archive_factory, three_mf, ws):
        printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True)
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
        three_mf.append({"slot_id": 1, "used_g": 4.17})

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

        await db_session.refresh(archive)
        assert archive.cost == 14.18
        ws.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_trustworthy_mapping(self, db_session, printer_factory, archive_factory, three_mf, ws):
        """No print-command or queue mapping, and the colours don't match a tray:
        no positional guess, the placeholder stays."""
        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
        three_mf.append({"slot_id": 1, "used_g": 4.17, "color": "#123456"})

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager())

        await db_session.refresh(archive)
        assert archive.cost == 14.18

    @pytest.mark.asyncio
    async def test_colour_match_when_no_mapping(self, db_session, printer_factory, archive_factory, three_mf, ws):
        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
        three_mf.append({"slot_id": 1, "used_g": 4.17, "color": "#FFFFFF", "type": "PLA"})

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager())

        await db_session.refresh(archive)
        assert archive.cost == 7.80

    @pytest.mark.asyncio
    async def test_no_spool_in_any_mapped_tray(self, db_session, printer_factory, archive_factory, three_mf, ws):
        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        three_mf.append({"slot_id": 1, "used_g": 4.17})

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

        await db_session.refresh(archive)
        assert archive.cost == 14.18

    @pytest.mark.asyncio
    async def test_missing_3mf(self, db_session, printer_factory, archive_factory, three_mf, ws, tmp_path):
        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
        (tmp_path / "archive" / "print.3mf").unlink()

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

        await db_session.refresh(archive)
        assert archive.cost == 14.18

    @pytest.mark.asyncio
    async def test_an_error_never_escapes(
        self, db_session, printer_factory, archive_factory, three_mf, ws, monkeypatch
    ):
        printer, archive = await _setup(db_session, printer_factory, archive_factory)
        three_mf.append({"slot_id": 1, "used_g": 4.17})

        def boom(_pid):
            raise RuntimeError("printer state unavailable")

        pm = SimpleNamespace(get_status=boom)

        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, pm, ams_mapping=[0])

        await db_session.refresh(archive)
        assert archive.cost == 14.18


class TestSpoolman:
    @staticmethod
    def _client(spools_by_tag: dict | None = None, spools_by_id: dict | None = None):
        client = SimpleNamespace()
        client.find_spool_by_tag = AsyncMock(side_effect=lambda tag: (spools_by_tag or {}).get(tag))
        client.get_spool = AsyncMock(side_effect=lambda sid: (spools_by_id or {})[sid])
        return client

    @pytest.mark.asyncio
    async def test_priced_from_the_slot_assigned_spool(
        self, db_session, printer_factory, archive_factory, three_mf, ws
    ):
        printer, archive = await _setup(db_session, printer_factory, archive_factory, spoolman=True)
        three_mf.append({"slot_id": 1, "used_g": 4.17})
        spool = {"id": 7, "price": 1870, "initial_weight": 1000, "filament": {"weight": 1000}}
        client = self._client(spools_by_id={7: spool})

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                new=AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", new=AsyncMock(return_value="")),
            patch(
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                new=AsyncMock(return_value=7),
            ),
        ):
            await estimate_archive_cost_at_start(
                db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0]
            )

        await db_session.refresh(archive)
        assert archive.cost == 7.80

    @pytest.mark.asyncio
    async def test_spoolman_unreachable_keeps_the_placeholder(
        self, db_session, printer_factory, archive_factory, three_mf, ws
    ):
        printer, archive = await _setup(db_session, printer_factory, archive_factory, spoolman=True)
        three_mf.append({"slot_id": 1, "used_g": 4.17})

        with patch(
            "backend.app.services.spoolman.get_spoolman_client",
            new=AsyncMock(return_value=None),
        ):
            await estimate_archive_cost_at_start(
                db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0]
            )

        await db_session.refresh(archive)
        assert archive.cost == 14.18

    @pytest.mark.asyncio
    async def test_a_failing_spool_fetch_leaves_only_that_slot_unpriced(
        self, db_session, printer_factory, archive_factory, three_mf, ws
    ):
        printer, archive = await _setup(db_session, printer_factory, archive_factory, spoolman=True, grams=10.0)
        three_mf.extend([{"slot_id": 1, "used_g": 5.0}, {"slot_id": 2, "used_g": 5.0}])
        client = self._client(spools_by_id={7: {"id": 7, "price": 1000, "initial_weight": 1000}})
        trays = [{"id": i, "tray_type": "PLA"} for i in range(2)]

        async def slot_assignment(_pid, _ams, tray_id):
            return 7 if tray_id == 0 else 8  # spool 8 isn't in Spoolman: get_spool raises

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                new=AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", new=AsyncMock(return_value="")),
            patch(
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                new=slot_assignment,
            ),
        ):
            await estimate_archive_cost_at_start(
                db_session, printer.id, archive.id, _printer_manager(trays), ams_mapping=[0, 1]
            )

        await db_session.refresh(archive)
        # 5 g at 1.0/g + 5 g at the 3.4/g default
        assert archive.cost == 22.00


class TestScheduling:
    @pytest.mark.asyncio
    async def test_runs_in_the_background_on_its_own_session(self):
        """Print start doesn't wait for the estimate; it gets its own session and
        a copy of the mapping, so later changes to the caller's list don't leak in."""
        from contextlib import asynccontextmanager

        sentinel_db = object()

        @asynccontextmanager
        async def fake_session():
            yield sentinel_db

        mapping = [0, 1]
        with (
            patch("backend.app.core.database.async_session", new=fake_session),
            patch.object(archive_cost_estimate, "estimate_archive_cost_at_start", new=AsyncMock()) as estimate,
        ):
            task = archive_cost_estimate.schedule_archive_cost_estimate(3, 42, "pm", ams_mapping=mapping, plate_id=2)
            mapping.append(9)
            await task

        estimate.assert_awaited_once_with(sentinel_db, 3, 42, "pm", [0, 1], 2)


class TestRaceWithCompletion:
    @pytest.mark.asyncio
    async def test_print_that_ended_during_the_lookup_keeps_the_final_cost(
        self, db_session, printer_factory, archive_factory, three_mf, ws
    ):
        """A print that fails seconds in can complete while Spoolman is still
        answering; the figure completion wrote must survive."""
        printer, archive = await _setup(db_session, printer_factory, archive_factory, spoolman=True)
        three_mf.append({"slot_id": 1, "used_g": 4.17})
        archive_id = archive.id

        async def slow_spool(_sid):
            # Completion lands while the estimate waits on Spoolman.
            from sqlalchemy import update

            from backend.app.models.archive import PrintArchive

            await db_session.execute(
                update(PrintArchive).where(PrintArchive.id == archive_id).values(status="failed", cost=1.23)
            )
            await db_session.commit()
            return {"id": 7, "price": 1870, "initial_weight": 1000}

        client = SimpleNamespace(find_spool_by_tag=AsyncMock(return_value=None), get_spool=slow_spool)
        with (
            patch("backend.app.services.spoolman.get_spoolman_client", new=AsyncMock(return_value=client)),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", new=AsyncMock(return_value="")),
            patch(
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                new=AsyncMock(return_value=7),
            ),
        ):
            await estimate_archive_cost_at_start(
                db_session, printer.id, archive_id, _printer_manager(), ams_mapping=[0]
            )

        await db_session.refresh(archive)
        assert archive.cost == 1.23
        ws.assert_not_awaited()


class TestCompletionStopsTheEstimate:
    @pytest.mark.asyncio
    async def test_settling_cancels_a_running_estimate_before_it_writes(self):
        """COST-4: completion commits the real cost before it marks the archive
        ended, so an estimate still waiting on Spoolman saw "printing" and wrote
        over it. Completion now stops the estimate first."""
        import asyncio
        from contextlib import asynccontextmanager

        wrote: list[int] = []
        blocked = asyncio.Event()

        async def slow_estimate(*_args):
            await blocked.wait()
            wrote.append(1)

        @asynccontextmanager
        async def fake_session():
            yield object()

        with (
            patch("backend.app.core.database.async_session", new=fake_session),
            patch.object(archive_cost_estimate, "estimate_archive_cost_at_start", new=slow_estimate),
        ):
            task = archive_cost_estimate.schedule_archive_cost_estimate(3, 42, "pm")
            await asyncio.sleep(0)
            await archive_cost_estimate.settle_archive_cost_estimate(42)
            blocked.set()
            await asyncio.sleep(0)

        assert task.cancelled()
        assert wrote == []

    @pytest.mark.asyncio
    async def test_settling_with_nothing_running_is_harmless(self):
        await archive_cost_estimate.settle_archive_cost_estimate(9999)

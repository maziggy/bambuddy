"""The remain%-delta fallback must say when it charges nothing (#1820).

The fallback exists so a print with no 3MF still moves the spool weight. On an
H2S the AMS ``remain%`` it reads is too coarse and too noisy to carry that: the
reporter measured it rising mid-print, swinging +/-5 points over one job,
saturating at 100 on a fresh spool, and going negative near the end of one.

Two of their prints wrote nothing, each for a different one of those reasons,
and both looked identical from the outside -- ``no spools updated``, which is
also what a print with genuinely nothing to charge prints. The arithmetic is a
separate question; this is about not failing silently, so an operator can tell
which prints need correcting by hand.
"""

import logging
import types

import pytest

from backend.app.services.spoolman_tracking import (
    _print_used_tray_keys,
    _report_remain_delta_for_slots,
    _snapshot_tray_remain,
)

pytestmark = pytest.mark.unit


def _raw(remain, tray_uuid="uuid-a"):
    return {"ams": [{"id": 0, "tray": [{"id": 0, "remain": remain, "tray_uuid": tray_uuid}]}]}


def _slot(remain, tray_uuid="uuid-a"):
    return {"0-0": {"remain": remain, "tray_uuid": tray_uuid}}


class _Client:
    """Records anything the fallback tries to write."""

    def __init__(self):
        self.used = []

    async def get_spool(self, spool_id):
        return {"filament": {"weight": 1000}}

    async def use_spool(self, spool_id, grams):
        self.used.append((spool_id, grams))


async def _run(caplog, **kwargs):
    client = _Client()
    with caplog.at_level(logging.INFO, logger="backend.app.services.spoolman_tracking"):
        written = await _report_remain_delta_for_slots(
            client,
            printer_id=1,
            handled_global_tray_ids=set(),
            archive_id=7,
            **kwargs,
        )
    return client, written, caplog.text


class TestTheSnapshotGate:
    """A negative remain% -- what the AMS reports on a nearly empty spool --
    keeps the slot out of the snapshot entirely. That is how the reporter's
    second print lost the only slot that was printing."""

    def test_a_negative_remain_is_reported_as_skipped(self):
        skipped = []

        snapshot = _snapshot_tray_remain(_raw(-3), skipped)

        assert snapshot == {}
        assert skipped == ["AMS0-T0(remain=-3)"]

    def test_a_valid_remain_is_not_reported(self):
        skipped = []

        snapshot = _snapshot_tray_remain(_raw(42), skipped)

        assert snapshot == {"0-0": {"remain": 42, "tray_uuid": "uuid-a"}}
        assert skipped == []

    def test_the_external_spool_holder_is_reported_too(self):
        skipped = []

        _snapshot_tray_remain({"vt_tray": {"id": 254, "remain": -1}}, skipped)

        assert skipped == ["VT254(remain=-1)"]

    def test_the_collector_is_optional(self):
        """Two of the three call sites pass nothing; they must still work."""
        assert _snapshot_tray_remain(_raw(-3)) == {}


@pytest.mark.asyncio
class TestNothingCharged:
    async def test_a_spool_still_reading_full_is_reported(self, caplog):
        """The reporter's first print: 36 minutes on a fresh spool, 100% at
        both ends, so the delta was zero and the slot was skipped in silence."""
        client, written, text = await _run(caplog, tray_remain_start=_slot(100), current_lookup=_slot(100))

        assert written == 0
        assert client.used == []
        assert "did not fall" in text
        assert "100% -> 100%" in text

    async def test_a_reading_that_rose_is_reported(self, caplog):
        """remain% moving upward mid-print is noise, not a refill, but either
        way nothing is charged and the operator should hear about it."""
        _, written, text = await _run(caplog, tray_remain_start=_slot(12), current_lookup=_slot(17))

        assert written == 0
        assert "12% -> 17%" in text

    async def test_a_slot_missing_at_completion_is_reported(self, caplog):
        """The completion-side twin of the snapshot gate."""
        _, written, text = await _run(caplog, tray_remain_start=_slot(50), current_lookup={})

        assert written == 0
        assert "no valid remain" in text

    async def test_an_unassigned_slot_names_what_was_lost(self, caplog, monkeypatch):
        """It consumed something real and there is nowhere to put it, which is
        worth more than the debug line it used to get."""
        monkeypatch.setattr(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
            _fake_resolver(None),
        )

        _, written, text = await _run(caplog, tray_remain_start=_slot(60), current_lookup=_slot(50))

        assert written == 0
        assert "no Spoolman slot assignment" in text
        assert "consumed 10%" in text


@pytest.mark.asyncio
class TestItStillWritesWhenItCan:
    async def test_a_real_drop_is_charged(self, caplog, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
            _fake_resolver(42),
        )

        client, written, text = await _run(caplog, tray_remain_start=_slot(60), current_lookup=_slot(50))

        assert written == 1
        assert client.used == [(42, 100.0)]  # 10% of a 1000 g reference weight
        assert "did not fall" not in text

    async def test_a_spool_swap_is_still_refused(self, caplog, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
            _fake_resolver(42),
        )

        client, written, text = await _run(
            caplog,
            tray_remain_start=_slot(60, "uuid-a"),
            current_lookup=_slot(10, "uuid-b"),
        )

        assert written == 0
        assert client.used == []
        assert "swapped mid-print" in text


def _fake_resolver(spool_id):
    async def _resolve(*_args, **_kwargs):
        return spool_id

    return _resolve


class TestWhichSlotsThePrintUsed:
    """The guard the internal tracker has had since #1269, now on this path
    too. Without it a spool swapped into an idle slot mid-print reads as
    consumption and is charged to whatever that slot is assigned to."""

    def test_the_mapping_names_the_slots(self):
        """Global tray ids: 0-3 are AMS 0, 4-7 are AMS 1."""
        assert _print_used_tray_keys([0, 5], None, None) == {(0, 0), (1, 1)}

    def test_a_slicer_slot_routed_to_the_external_spool_is_ignored(self):
        """-1 means "external spool" in the flat mapping and names no AMS slot;
        the external holder arrives as 254/255 when it is really used."""
        assert _print_used_tray_keys([-1], None, None) == set()
        assert _print_used_tray_keys([254], None, None) == {(255, 0)}

    def test_an_ams_ht_keeps_its_own_id(self):
        assert _print_used_tray_keys([128], None, None) == {(128, 0)}

    def test_a_mid_print_tray_change_counts(self):
        """Filament backup switches trays mid-print; the substitute fed part of
        the job and has to be chargeable."""
        state = types.SimpleNamespace(tray_change_log=[[0, 0], [5, 120]])

        assert _print_used_tray_keys(None, None, state) == {(0, 0), (1, 1)}

    def test_the_tray_in_use_at_the_start_counts(self):
        """Often the only evidence: a print started from the printer's screen
        carries no mapping and may never change tray."""
        assert _print_used_tray_keys(None, 2, None) == {(0, 2)}

    def test_an_unloaded_printer_is_not_read_as_a_slot(self):
        """255 is what tray_now reads at rest -- its initial value, the
        unparseable-reading fallback, and "nothing loaded". Mapped as a tray id
        it becomes (255, 1), and as the only evidence it would exclude every
        real slot and charge nothing at all, which is this issue's own bug."""
        assert _print_used_tray_keys(None, 255, None) == set()

    def test_the_external_spool_in_use_is_a_slot(self):
        """It reports 254 when actually in use, which is a real slot."""
        assert _print_used_tray_keys(None, 254, None) == {(255, 0)}

    def test_no_evidence_at_all_yields_nothing(self):
        """Which callers must read as "consider every slot", not "no slots" --
        otherwise a printer reporting none of the three stops being tracked."""
        assert _print_used_tray_keys(None, None, None) == set()
        assert _print_used_tray_keys([], -1, types.SimpleNamespace(tray_change_log=[])) == set()

    def test_a_row_written_before_the_column_existed(self):
        """tray_now_at_start is nullable for exactly this reason."""
        assert _print_used_tray_keys([4], None, None) == {(1, 0)}


@pytest.mark.asyncio
class TestSlotsThePrintNeverTouched:
    async def test_an_untouched_slot_is_not_charged(self, caplog, monkeypatch):
        """A spool swapped into an idle slot drops that slot's remain%. Reading
        that as consumption is a phantom write to an uninvolved spool."""
        monkeypatch.setattr(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
            _fake_resolver(42),
        )

        client, written, text = await _run(
            caplog,
            tray_remain_start=_slot(60),
            current_lookup=_slot(10),
            print_used_keys={(1, 3)},  # this print used AMS1-T3, not AMS0-T0
        )

        assert written == 0
        assert client.used == []
        assert "slots not part of this print" in text
        assert "AMS0-T0" in text

    async def test_the_slot_the_print_used_is_still_charged(self, caplog, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
            _fake_resolver(42),
        )

        client, written, _ = await _run(
            caplog,
            tray_remain_start=_slot(60),
            current_lookup=_slot(50),
            print_used_keys={(0, 0)},
        )

        assert written == 1
        assert client.used == [(42, 100.0)]

    async def test_without_evidence_every_slot_is_still_considered(self, caplog, monkeypatch):
        """The reporter's own prints have no mapping and no tray changes. The
        guard must not turn "we don't know" into "charge nothing"."""
        monkeypatch.setattr(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
            _fake_resolver(42),
        )

        client, written, _ = await _run(
            caplog,
            tray_remain_start=_slot(60),
            current_lookup=_slot(50),
            print_used_keys=set(),
        )

        assert written == 1
        assert client.used == [(42, 100.0)]

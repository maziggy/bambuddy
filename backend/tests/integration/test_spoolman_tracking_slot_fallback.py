"""Integration tests for #1459 — per-print weight tracker falls back to the
local spoolman_slot_assignments table when Spoolman's extra.tag is empty.

Without this, tag-less spools assigned via the Bambuddy UI never get their
weight decremented because the Assign route intentionally leaves extra.tag
unset (per #1457 — fallback tags must not pollute Spoolman).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.spoolman_tracking import _report_spool_usage_for_slots


@pytest.fixture
def mock_spoolman_client():
    client = MagicMock()
    # Default: every tag-lookup returns None (the bug case — no extra.tag on Spoolman side).
    client.find_spool_by_tag = AsyncMock(return_value=None)
    client.use_spool = AsyncMock(return_value={"id": 0})
    return client


@pytest.fixture
def patch_async_session(test_engine):
    """Route the tracker's async_session() to the test engine so the slot-assignment
    fallback lookup sees rows committed via db_session in the same test."""
    test_async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("backend.app.services.spoolman_tracking.async_session", test_async_session):
        yield


@pytest.fixture
async def test_printer(db_session):
    from backend.app.models.printer import Printer

    printer = Printer(
        name="Tracking Test",
        serial_number="TRACKTEST123456",
        ip_address="192.168.0.99",
        access_code="12345678",
        model="P1S",
        is_active=True,
        auto_archive=True,
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    return printer


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("patch_async_session")
class TestSlotAssignmentFallback:
    async def test_falls_back_to_slot_assignment_when_tag_missing(self, test_printer, mock_spoolman_client, db_session):
        """Tag-less spool assigned via Bambuddy UI: extra.tag is empty (find_spool_by_tag
        returns None) but the local spoolman_slot_assignments row says spool 42 lives in
        AMS 0 tray 2 — the tracker must still report usage to spool 42.

        slot_id is 1-based; ams_trays is keyed by global_tray_id. For AMS 0 tray 2,
        global_tray_id = 2, so we hand the tracker slot_id=3 (since slot_id-1=global=2).
        """
        db_session.add(SpoolmanSlotAssignment(printer_id=test_printer.id, ams_id=0, tray_id=2, spoolman_spool_id=42))
        await db_session.commit()

        ams_trays = {2: {"tray_uuid": "", "tag_uid": "", "tray_type": "PLA"}}
        usage_items = [(3, 15.5)]

        spools_updated = await _report_spool_usage_for_slots(
            mock_spoolman_client,
            usage_items,
            ams_trays,
            slot_to_tray=None,
            method_label="Test",
            printer_serial=test_printer.serial_number,
            printer_id=test_printer.id,
        )

        assert spools_updated == 1
        mock_spoolman_client.use_spool.assert_awaited_once_with(42, 15.5)

    async def test_tag_match_wins_over_slot_assignment(self, test_printer, mock_spoolman_client, db_session):
        """When both paths could resolve a spool, the tag-match wins — RFID is the
        authoritative binding when present. Order matters so RFID auto-sync continues
        to bind to the spool whose extra.tag literally holds that RFID, even if the
        slot-assignment table happens to point at a different spool."""
        db_session.add(SpoolmanSlotAssignment(printer_id=test_printer.id, ams_id=0, tray_id=0, spoolman_spool_id=999))
        await db_session.commit()

        mock_spoolman_client.find_spool_by_tag = AsyncMock(return_value={"id": 7})

        ams_trays = {0: {"tray_uuid": "A" * 32, "tag_uid": "", "tray_type": "PLA"}}
        # slot_id=1 → global_tray_id=0 (AMS 0 tray 0).
        usage_items = [(1, 10.0)]

        spools_updated = await _report_spool_usage_for_slots(
            mock_spoolman_client,
            usage_items,
            ams_trays,
            slot_to_tray=None,
            method_label="Test",
            printer_serial=test_printer.serial_number,
            printer_id=test_printer.id,
        )

        assert spools_updated == 1
        mock_spoolman_client.use_spool.assert_awaited_once_with(7, 10.0)

    async def test_skips_when_neither_path_resolves(self, test_printer, mock_spoolman_client, db_session):
        """No tag in Spoolman AND no slot-assignment row → tracker skips the slot
        rather than crashing or reporting against the wrong spool."""
        ams_trays = {0: {"tray_uuid": "", "tag_uid": "", "tray_type": "PLA"}}
        # slot_id=1 → global_tray_id=0 (AMS 0 tray 0); no assignment row exists.
        usage_items = [(1, 5.0)]

        spools_updated = await _report_spool_usage_for_slots(
            mock_spoolman_client,
            usage_items,
            ams_trays,
            slot_to_tray=None,
            method_label="Test",
            printer_serial=test_printer.serial_number,
            printer_id=test_printer.id,
        )

        assert spools_updated == 0
        mock_spoolman_client.use_spool.assert_not_called()

    async def test_skips_when_printer_id_not_supplied(self, test_printer, mock_spoolman_client, db_session):
        """Slot-assignment fallback requires printer_id to look up the binding —
        when callers don't supply it (legacy call shape) the lookup is skipped
        and the slot is reported as unresolved, matching pre-#1459 behaviour for
        those callers."""
        db_session.add(SpoolmanSlotAssignment(printer_id=test_printer.id, ams_id=0, tray_id=0, spoolman_spool_id=42))
        await db_session.commit()

        ams_trays = {0: {"tray_uuid": "", "tag_uid": "", "tray_type": "PLA"}}
        usage_items = [(1, 5.0)]

        spools_updated = await _report_spool_usage_for_slots(
            mock_spoolman_client,
            usage_items,
            ams_trays,
            slot_to_tray=None,
            method_label="Test",
            printer_serial=test_printer.serial_number,
            # printer_id omitted on purpose
        )

        assert spools_updated == 0
        mock_spoolman_client.use_spool.assert_not_called()

    async def test_external_slot_falls_back_via_correct_ams_tray_pair(
        self, test_printer, mock_spoolman_client, db_session
    ):
        """External spool slots use global_tray_id 254/255 which map to ams_id=255,
        tray_id=0/1. The slot-assignment lookup must use that translated pair, not the
        raw global id, otherwise the row is never found."""
        db_session.add(SpoolmanSlotAssignment(printer_id=test_printer.id, ams_id=255, tray_id=0, spoolman_spool_id=88))
        await db_session.commit()

        # Position-based default with ams_trays={254: ...}: sorted_tray_ids=[254],
        # slot_id=1 → sorted_tray_ids[0] = 254 (global) → ams_id=255 tray_id=0.
        ams_trays = {254: {"tray_uuid": "", "tag_uid": "", "tray_type": "PLA"}}
        usage_items = [(1, 25.0)]

        spools_updated = await _report_spool_usage_for_slots(
            mock_spoolman_client,
            usage_items,
            ams_trays,
            slot_to_tray=None,
            method_label="Test",
            printer_serial=test_printer.serial_number,
            printer_id=test_printer.id,
        )

        assert spools_updated == 1
        mock_spoolman_client.use_spool.assert_awaited_once_with(88, 25.0)

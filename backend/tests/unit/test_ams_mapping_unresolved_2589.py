"""Regression tests for the unresolved-mapping -> silent-external bug (#2589).

A P1S queue item persisted with ``ams_mapping=[-1]`` (an unresolved mapping from
a frontend status-load race) was silently dispatched with ``use_ams=False`` — the
print then started against the empty external feed and paused with a filament
runout. Two behaviours combined:

1. ``start_print`` treated an all-``-1`` mapping as "all external spool" and
   forced ``use_ams=False``. Only an explicit external selection (``>=254``) may
   do that; unresolved ``-1`` must not.
2. The scheduler trusted a stored ``[-1]`` (non-empty, so "already resolved") and
   skipped the recompute that would have matched the live AMS trays.

These tests lock in the fix at both layers.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.print_scheduler import PrintScheduler, _mapping_is_all_unresolved


class TestMappingIsAllUnresolved:
    """Unit tests for the ``_mapping_is_all_unresolved`` predicate."""

    def test_all_minus_one_is_unresolved(self):
        assert _mapping_is_all_unresolved([-1]) is True
        assert _mapping_is_all_unresolved([-1, -1]) is True

    def test_none_entries_are_unresolved(self):
        assert _mapping_is_all_unresolved([None]) is True
        assert _mapping_is_all_unresolved([-1, None]) is True

    def test_partially_resolved_is_not_unresolved(self):
        # A plate that only prints slot 3 pads earlier slots with -1.
        assert _mapping_is_all_unresolved([-1, -1, 5]) is False
        assert _mapping_is_all_unresolved([5, -1]) is False

    def test_explicit_external_is_not_unresolved(self):
        # 254/255 are explicit external/virtual spool selections, not unresolved.
        assert _mapping_is_all_unresolved([254]) is False
        assert _mapping_is_all_unresolved([255, 254]) is False

    def test_resolved_ams_is_not_unresolved(self):
        assert _mapping_is_all_unresolved([0]) is False
        assert _mapping_is_all_unresolved([4, 8]) is False

    def test_empty_or_none_is_not_unresolved(self):
        # Absent/empty is "needs computing", handled by the missing-mapping path,
        # not this predicate — which is specifically about a *bogus* stored value.
        assert _mapping_is_all_unresolved([]) is False
        assert _mapping_is_all_unresolved(None) is False


class TestStartPrintExternalDowngrade:
    """``start_print`` must only force use_ams=False for *explicit* external."""

    @pytest.fixture
    def mqtt_client(self):
        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="01P00A452600691",
            access_code="12345678",
        )
        # Single-nozzle P1S so the dual-nozzle bypass does not apply.
        client.model = "P1S"
        client._client = MagicMock()
        client.state.connected = True
        return client

    def _sent_command(self, mqtt_client) -> dict:
        """Parse the JSON payload the client published."""
        assert mqtt_client._client.publish.called, "start_print did not publish"
        payload = mqtt_client._client.publish.call_args.args[1]
        return json.loads(payload)["print"]

    def test_unresolved_mapping_keeps_use_ams_true(self, mqtt_client):
        """[-1] is unresolved, NOT external — must not silently go external."""
        assert mqtt_client.start_print("Turm.3mf", ams_mapping=[-1], use_ams=True) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_explicit_external_forces_use_ams_false(self, mqtt_client):
        """An explicit external selection (254) still downgrades to use_ams=False."""
        assert mqtt_client.start_print("Turm.3mf", ams_mapping=[254], use_ams=True) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is False

    def test_resolved_ams_keeps_use_ams_true(self, mqtt_client):
        """A real AMS tray keeps use_ams=True."""
        assert mqtt_client.start_print("Turm.3mf", ams_mapping=[5], use_ams=True) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_padded_partial_mapping_keeps_use_ams_true(self, mqtt_client):
        """A padded mapping ([-1, -1, tray]) is not all-external."""
        assert mqtt_client.start_print("Turm.3mf", ams_mapping=[-1, -1, 5], use_ams=True) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is True


class TestEnsureAmsMapping:
    """The scheduler must recompute a stored unresolved [-1], not trust it."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def _item(self, ams_mapping):
        item = MagicMock()
        item.id = 92
        item.printer_id = 82
        item.ams_mapping = ams_mapping
        return item

    @pytest.mark.asyncio
    async def test_stored_unresolved_is_recomputed(self, scheduler):
        """A stored [-1] triggers recompute; the live-resolved mapping wins."""
        db = AsyncMock()
        item = self._item(json.dumps([-1]))
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[5])

        await scheduler._ensure_ams_mapping(db, 82, item)

        scheduler._compute_ams_mapping_for_printer.assert_awaited_once()
        assert json.loads(item.ams_mapping) == [5]
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_missing_mapping_is_computed(self, scheduler):
        """No stored mapping still computes one (existing behaviour preserved)."""
        db = AsyncMock()
        item = self._item(None)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[4, 8])

        await scheduler._ensure_ams_mapping(db, 82, item)

        assert json.loads(item.ams_mapping) == [4, 8]

    @pytest.mark.asyncio
    async def test_resolved_mapping_is_left_untouched(self, scheduler):
        """A resolved stored mapping (e.g. a manual override) is never recomputed."""
        db = AsyncMock()
        item = self._item(json.dumps([5, 9]))
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[0, 0])

        await scheduler._ensure_ams_mapping(db, 82, item)

        scheduler._compute_ams_mapping_for_printer.assert_not_awaited()
        assert json.loads(item.ams_mapping) == [5, 9]

    @pytest.mark.asyncio
    async def test_unresolvable_stored_mapping_is_cleared(self, scheduler):
        """If recompute also can't resolve it, the bogus [-1] is cleared to None
        so dispatch never mistakes it for an explicit external selection."""
        db = AsyncMock()
        item = self._item(json.dumps([-1]))
        # Live status has no compatible tray -> matcher returns another all-[-1].
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[-1])

        await scheduler._ensure_ams_mapping(db, 82, item)

        assert item.ams_mapping is None
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_recompute_none_leaves_missing_untouched(self, scheduler):
        """Missing mapping + recompute returns None (no status) -> stays None,
        no spurious clear-warning path, no crash."""
        db = AsyncMock()
        item = self._item(None)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=None)

        await scheduler._ensure_ams_mapping(db, 82, item)

        assert item.ams_mapping is None

"""use_ams must be reconciled against the resolved ams_mapping at dispatch (#2595).

A print sent to a Virtual Printer is sliced against the VP, which advertises no
AMS, so the slicer sends ``use_ams=false`` and that flag is stamped onto the
queue item. But an "Any [model]" queue item is colour-matched to a real printer
at dispatch, resolving a *real* AMS slot in ``ams_mapping``. The stale
``use_ams=False`` used to survive to the print command, so the printer ignored
the mapped slot and aborted at layer 0 on the empty external spool ("not enough
filament"). Diagnosed by @Sawtaytoes.

The command builder now treats the mapping as authoritative for single-nozzle
printers: a real tray forces ``use_ams=True``; an explicit external selection
forces it False; an unresolved ``-1`` mapping (#2589) does neither. Dual-nozzle
is untouched (``use_ams`` is nozzle routing there).
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


class TestUseAmsReconcile:
    @pytest.fixture
    def mqtt_client(self):
        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="01P00A452600691",
            access_code="12345678",
        )
        # Single-nozzle X1C so the dual-nozzle bypass does not apply.
        client.model = "X1C"
        client._client = MagicMock()
        client.state.connected = True
        return client

    def _sent_command(self, mqtt_client) -> dict:
        assert mqtt_client._client.publish.called, "start_print did not publish"
        payload = mqtt_client._client.publish.call_args.args[1]
        return json.loads(payload)["print"]

    def test_vp_false_with_real_tray_forces_use_ams_true(self, mqtt_client):
        """The reported bug: use_ams=False (VP-stamped) + a real AMS slot -> True."""
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[4], use_ams=False) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_false_with_padded_real_tray_forces_true(self, mqtt_client):
        """A padded mapping ([-1, -1, tray]) still has a real slot -> True."""
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[-1, -1, 5], use_ams=False) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_false_all_external_stays_false(self, mqtt_client):
        """An explicit external selection must NOT be force-enabled."""
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[254], use_ams=False) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is False

    def test_false_mixed_external_stays_false(self, mqtt_client):
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[255, 254], use_ams=False) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is False

    def test_false_unresolved_stays_false(self, mqtt_client):
        """An unresolved [-1] is neither external nor a real tray — leave it alone
        (preserves the #2589 contract; it should have been recomputed upstream)."""
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[-1], use_ams=False) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is False

    def test_true_all_external_still_downgrades(self, mqtt_client):
        """The original #2589 all-external downgrade still fires."""
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[254], use_ams=True) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is False

    def test_true_real_tray_stays_true(self, mqtt_client):
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[5], use_ams=True) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_dual_nozzle_use_ams_untouched(self, mqtt_client):
        """Dual-nozzle: use_ams is nozzle routing, not an AMS flag — never coerced."""
        mqtt_client._is_dual_nozzle = True
        assert mqtt_client.start_print("shell.3mf", ams_mapping=[4], use_ams=False) is True
        cmd = self._sent_command(mqtt_client)
        assert cmd["use_ams"] is False

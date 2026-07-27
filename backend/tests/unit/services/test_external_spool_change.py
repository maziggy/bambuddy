"""External-spool (vt_tray) change detection (#2575).

The AMS change-hash in ``_handle_ams_data`` is built only from AMS units, so a
filament swap on the external spool alone (e.g. generic TPU -> generic ABS on
the printer) used to never re-trigger ``on_ams_change``. That left a stale
inventory assignment on the ``ams_id=255`` slot: Bambuddy kept showing the old
filament after the physical type had changed.

These tests drive full MQTT messages through ``_process_message`` and assert the
callback fires exactly when the external spool's *identity* changes — and not on
every push (e.g. a steadily-dropping ``remain`` percentage during a print).
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _ext_spool_msg(tray_type: str, remain: int = 100, color: str = "000000FF"):
    """A realistic print message carrying only external-spool (vt_tray) data."""
    return {
        "print": {
            "vt_tray": {
                "id": "254",
                "tray_type": tray_type,
                "tray_color": color,
                "tray_info_idx": "",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": remain,
            }
        }
    }


class TestExternalSpoolChangeDetection:
    @pytest.fixture
    def mqtt_client(self):
        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

    def test_type_swap_fires_callback(self, mqtt_client):
        """Swapping the external filament type re-triggers the sync callback."""
        calls: list = []
        mqtt_client.on_ams_change = lambda ams_data: calls.append(ams_data)

        # First observation of the external spool (TPU) — fires once.
        mqtt_client._process_message(_ext_spool_msg("TPU"))
        assert len(calls) == 1

        # Physical filament changed to ABS — must fire again so the stale
        # ams_id=255 assignment gets reconciled.
        mqtt_client._process_message(_ext_spool_msg("ABS"))
        assert len(calls) == 2

        # The callback receives the merged AMS list (never None).
        assert all(isinstance(c, list) for c in calls)

    def test_identical_push_does_not_refire(self, mqtt_client):
        """Repeated identical vt_tray pushes fire the callback only once."""
        calls: list = []
        mqtt_client.on_ams_change = lambda ams_data: calls.append(ams_data)

        mqtt_client._process_message(_ext_spool_msg("ABS"))
        mqtt_client._process_message(_ext_spool_msg("ABS"))
        mqtt_client._process_message(_ext_spool_msg("ABS"))
        assert len(calls) == 1

    def test_remain_only_change_does_not_refire(self, mqtt_client):
        """A dropping fill percentage must not spam the reconciliation callback."""
        calls: list = []
        mqtt_client.on_ams_change = lambda ams_data: calls.append(ams_data)

        mqtt_client._process_message(_ext_spool_msg("PLA", remain=100))
        assert len(calls) == 1
        # remain drops during a print — identity unchanged, no refire.
        mqtt_client._process_message(_ext_spool_msg("PLA", remain=87))
        mqtt_client._process_message(_ext_spool_msg("PLA", remain=42))
        assert len(calls) == 1

    def test_reset_to_empty_fires_callback(self, mqtt_client):
        """Resetting the external spool (empty tray_type) is an identity change."""
        calls: list = []
        mqtt_client.on_ams_change = lambda ams_data: calls.append(ams_data)

        mqtt_client._process_message(_ext_spool_msg("TPU"))
        assert len(calls) == 1
        mqtt_client._process_message(_ext_spool_msg(""))  # reset / unloaded
        assert len(calls) == 2

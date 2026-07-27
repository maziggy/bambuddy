"""Read-back verification of AMS spool assignments (#2582).

After Bambuddy pushes an assignment (``ams_filament_setting`` +
``extrusion_cali_sel``) it registers the desired end-state and watches the
periodic AMS telemetry to confirm the tray actually accepted it. Historically
this was fire-and-forget, so a silently-dropped assignment (the reporter's
"assigned in Bambuddy but Studio never saw it") produced no feedback at all.

These tests lock in the matcher: a tray_info_idx echo confirms the push landed,
cali_idx is a secondary "K-profile applied" signal, and a timeout without a
matching echo reports a non-confirmation instead of inventing success.
"""

import time
from unittest.mock import MagicMock

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client(on_verified=None) -> BambuMQTTClient:
    return BambuMQTTClient(
        ip_address="10.0.0.1",
        serial_number="SERIAL",
        access_code="code",
        model="P1S",
        on_assignment_verified=on_verified,
    )


def _ams_frame(tray_id=0, ams_id=0, **tray_fields):
    """One AMS unit with the given tray carrying content fields."""
    tray = {"id": tray_id}
    tray.update(tray_fields)
    return {"ams": [{"id": ams_id, "tray": [tray]}]}


class TestAssignmentMatch:
    def test_matching_tray_info_idx_fires_verified(self):
        cb = MagicMock()
        client = _client(cb)
        client.register_assignment_verification(
            ams_id=0, tray_id=0, tray_info_idx="GFL05", tray_color="FF0000FF", cali_idx=-1
        )
        client._handle_ams_data(_ams_frame(tray_info_idx="GFL05", tray_type="PLA"))

        cb.assert_called_once()
        ams_id, tray_id, verified, detail = cb.call_args.args
        assert (ams_id, tray_id, verified) == (0, 0, True)
        assert detail["kprofile_applied"] is True
        # Pending entry is cleared once resolved.
        assert (0, 0) not in client._pending_assignments

    def test_match_is_case_insensitive(self):
        cb = MagicMock()
        client = _client(cb)
        client.register_assignment_verification(
            ams_id=0, tray_id=0, tray_info_idx="gfl05", tray_color="", cali_idx=None
        )
        client._handle_ams_data(_ams_frame(tray_info_idx="GFL05"))
        assert cb.call_args.args[2] is True

    def test_kprofile_mismatch_flags_not_applied(self):
        cb = MagicMock()
        client = _client(cb)
        client.register_assignment_verification(ams_id=0, tray_id=0, tray_info_idx="GFL05", tray_color="", cali_idx=3)
        # Filament id landed but the printer kept a different cali_idx.
        client._handle_ams_data(_ams_frame(tray_info_idx="GFL05", cali_idx=1))

        verified, detail = cb.call_args.args[2], cb.call_args.args[3]
        assert verified is True
        assert detail["kprofile_applied"] is False

    def test_kprofile_match_flags_applied(self):
        cb = MagicMock()
        client = _client(cb)
        client.register_assignment_verification(ams_id=0, tray_id=0, tray_info_idx="GFL05", tray_color="", cali_idx=3)
        client._handle_ams_data(_ams_frame(tray_info_idx="GFL05", cali_idx=3))
        assert cb.call_args.args[3]["kprofile_applied"] is True


class TestAssignmentPendingAndTimeout:
    def test_divergent_idx_within_window_keeps_waiting(self):
        cb = MagicMock()
        client = _client(cb)
        client.register_assignment_verification(ams_id=0, tray_id=0, tray_info_idx="GFL05", tray_color="", cali_idx=-1)
        # Tray still shows the previous filament — no callback, stay pending.
        client._handle_ams_data(_ams_frame(tray_info_idx="GFU00"))
        cb.assert_not_called()
        assert (0, 0) in client._pending_assignments

    def test_timeout_after_seeing_divergent_tray_reports_failure(self):
        cb = MagicMock()
        client = _client(cb)
        client.register_assignment_verification(ams_id=0, tray_id=0, tray_info_idx="GFL05", tray_color="", cali_idx=-1)
        # First push observes a divergent id (records last_seen_idx).
        client._handle_ams_data(_ams_frame(tray_info_idx="GFU00"))
        # Force the deadline into the past, then another push evaluates it.
        client._pending_assignments[(0, 0)]["deadline"] = time.monotonic() - 1
        client._handle_ams_data(_ams_frame(tray_info_idx="GFU00"))

        verified, detail = cb.call_args.args[2], cb.call_args.args[3]
        assert verified is False
        assert detail["saw_tray"] is True
        assert detail["actual_tray_info_idx"] == "GFU00"
        assert (0, 0) not in client._pending_assignments

    def test_timeout_without_ever_seeing_tray_reports_no_tray(self):
        cb = MagicMock()
        client = _client(cb)
        client.register_assignment_verification(ams_id=1, tray_id=2, tray_info_idx="GFL05", tray_color="", cali_idx=-1)
        client._pending_assignments[(1, 2)]["deadline"] = time.monotonic() - 1
        # A push for an unrelated AMS unit still triggers deadline evaluation.
        client._handle_ams_data(_ams_frame(ams_id=0, tray_id=0, tray_info_idx="GFL05"))

        verified, detail = cb.call_args.args[2], cb.call_args.args[3]
        assert verified is False
        assert detail["saw_tray"] is False
        assert detail["actual_tray_info_idx"] is None


class TestRegistrationGuards:
    def test_blank_tray_info_idx_is_not_registered(self):
        client = _client()
        client.register_assignment_verification(
            ams_id=0, tray_id=0, tray_info_idx="", tray_color="FF0000FF", cali_idx=-1
        )
        assert not client._pending_assignments

    def test_reconnect_clears_pending(self):
        client = _client()
        client.register_assignment_verification(ams_id=0, tray_id=0, tray_info_idx="GFL05", tray_color="", cali_idx=-1)
        assert client._pending_assignments
        # Mirror the on_connect reset path.
        client._pending_assignments.clear()
        assert not client._pending_assignments


class TestExternalSpool:
    def test_external_tray_matches_via_vt_tray(self):
        cb = MagicMock()
        client = _client(cb)
        # External-left spool: logical ams_id 255 / tray 0 lives at vt_tray id 254.
        client.state.raw_data["vt_tray"] = [{"id": 254, "tray_info_idx": "GFL05"}]
        client.register_assignment_verification(
            ams_id=255, tray_id=0, tray_info_idx="GFL05", tray_color="", cali_idx=-1
        )
        # Any AMS push drives the check; the tray is resolved from vt_tray.
        client._handle_ams_data(_ams_frame(tray_info_idx="GFU00"))

        cb.assert_called_once()
        assert cb.call_args.args[2] is True

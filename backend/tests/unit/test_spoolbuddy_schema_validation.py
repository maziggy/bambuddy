"""Unit tests for SpoolBuddy schema validation (security fixes H2, M2, M4).

Tests Pydantic model validation without requiring a running server or DB.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.spoolbuddy import (
    DeviceRegisterRequest,
    HeartbeatRequest,
    ScaleReadingRequest,
    UpdateStatusRequest,
    WriteTagResultRequest,
)

# ---------------------------------------------------------------------------
# H2 — UpdateStatusRequest: only valid Literal values accepted
# ---------------------------------------------------------------------------


class TestUpdateStatusRequestValidation:
    def test_valid_status_updating(self):
        req = UpdateStatusRequest(status="updating")
        assert req.status == "updating"

    def test_valid_status_complete(self):
        req = UpdateStatusRequest(status="complete")
        assert req.status == "complete"

    def test_valid_status_error(self):
        req = UpdateStatusRequest(status="error")
        assert req.status == "error"

    def test_invalid_status_rejected(self):
        """Arbitrary status strings must be rejected (H2: prevents unbounded WS injection)."""
        with pytest.raises(ValidationError):
            UpdateStatusRequest(status="hacked")

    def test_empty_status_rejected(self):
        with pytest.raises(ValidationError):
            UpdateStatusRequest(status="")

    def test_message_max_length_enforced(self):
        """message field must not exceed 255 chars."""
        with pytest.raises(ValidationError):
            UpdateStatusRequest(status="updating", message="x" * 256)

    def test_message_at_max_length_accepted(self):
        req = UpdateStatusRequest(status="complete", message="x" * 255)
        assert len(req.message) == 255


# ---------------------------------------------------------------------------
# M2 — HeartbeatRequest: system_stats size limit (4096 bytes)
# ---------------------------------------------------------------------------


class TestHeartbeatSystemStatsValidation:
    def test_none_accepted(self):
        req = HeartbeatRequest(system_stats=None)
        assert req.system_stats is None

    def test_small_dict_accepted(self):
        req = HeartbeatRequest(system_stats={"cpu": 12.5, "mem": 60.0})
        assert req.system_stats["cpu"] == 12.5

    def test_oversized_dict_rejected(self):
        """system_stats exceeding 4096 bytes JSON-encoded must be rejected (M2)."""
        huge = {"data": "x" * 5000}
        with pytest.raises(ValidationError, match="4096"):
            HeartbeatRequest(system_stats=huge)

    def test_exactly_4096_bytes_accepted(self):
        """A dict whose JSON is exactly 4096 bytes must pass."""
        import json

        # Build a dict whose JSON is exactly 4096 bytes
        filler = "x" * (4096 - len('{"k": ""}'))
        d = {"k": filler}
        assert len(json.dumps(d)) == 4096
        req = HeartbeatRequest(system_stats=d)
        assert req.system_stats is not None

    def test_one_byte_over_limit_rejected(self):
        import json

        filler = "x" * (4097 - len('{"k": ""}'))
        d = {"k": filler}
        assert len(json.dumps(d)) == 4097
        with pytest.raises(ValidationError):
            HeartbeatRequest(system_stats=d)


# ---------------------------------------------------------------------------
# M4 — DeviceRegisterRequest: max_length on device-sourced string fields
# ---------------------------------------------------------------------------


class TestDeviceRegisterRequestValidation:
    VALID_BASE = {"device_id": "dev1", "hostname": "spoolbuddy.local", "ip_address": "192.168.1.50"}

    def test_valid_minimal_accepted(self):
        req = DeviceRegisterRequest(**self.VALID_BASE)
        assert req.device_id == "dev1"

    def test_firmware_version_too_long_rejected(self):
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(**self.VALID_BASE, firmware_version="x" * 21)

    def test_firmware_version_at_max_accepted(self):
        req = DeviceRegisterRequest(**self.VALID_BASE, firmware_version="x" * 20)
        assert req.firmware_version == "x" * 20

    def test_nfc_reader_type_too_long_rejected(self):
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(**self.VALID_BASE, nfc_reader_type="x" * 21)

    def test_nfc_connection_too_long_rejected(self):
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(**self.VALID_BASE, nfc_connection="x" * 21)

    def test_backend_url_too_long_rejected(self):
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(**self.VALID_BASE, backend_url="http://" + "x" * 249)

    def test_backend_url_at_max_accepted(self):
        url = "http://" + "x" * (255 - len("http://"))
        req = DeviceRegisterRequest(**self.VALID_BASE, backend_url=url)
        assert req.backend_url == url

    def test_device_id_too_long_rejected(self):
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(device_id="x" * 51, hostname="h", ip_address="1.2.3.4")


# ---------------------------------------------------------------------------
# M4 — WriteTagResultRequest: device_id max_length
# ---------------------------------------------------------------------------


class TestWriteTagResultRequestValidation:
    def test_device_id_too_long_rejected(self):
        with pytest.raises(ValidationError):
            WriteTagResultRequest(device_id="x" * 51, spool_id=1, tag_uid="AABBCCDD", success=True)

    def test_device_id_at_max_accepted(self):
        req = WriteTagResultRequest(device_id="x" * 50, spool_id=1, tag_uid="AABBCCDD", success=True)
        assert len(req.device_id) == 50

    def test_tag_uid_hex_pattern_accepted(self):
        req = WriteTagResultRequest(device_id="dev1", spool_id=1, tag_uid="AABBCCDD", success=True)
        assert req.tag_uid == "AABBCCDD"

    def test_tag_uid_non_hex_rejected(self):
        """Non-hex characters in tag_uid must be rejected (prevents injection via NFC write-back)."""
        with pytest.raises(ValidationError):
            WriteTagResultRequest(device_id="dev1", spool_id=1, tag_uid="AABB; DROP", success=True)

    def test_tag_uid_too_short_rejected(self):
        with pytest.raises(ValidationError):
            WriteTagResultRequest(device_id="dev1", spool_id=1, tag_uid="AABB", success=True)

    def test_tag_uid_max_length_accepted(self):
        req = WriteTagResultRequest(device_id="dev1", spool_id=1, tag_uid="A" * 30, success=True)
        assert len(req.tag_uid) == 30

    def test_tag_uid_over_max_length_rejected(self):
        with pytest.raises(ValidationError):
            WriteTagResultRequest(device_id="dev1", spool_id=1, tag_uid="A" * 31, success=True)


# ---------------------------------------------------------------------------
# M4 — ScaleReadingRequest: weight_grams bounds
# ---------------------------------------------------------------------------


class TestScaleReadingRequestValidation:
    def test_valid_weight_accepted(self):
        req = ScaleReadingRequest(device_id="sb1", weight_grams=250.0)
        assert req.weight_grams == 250.0

    def test_zero_weight_accepted(self):
        req = ScaleReadingRequest(device_id="sb1", weight_grams=0.0)
        assert req.weight_grams == 0.0

    def test_max_weight_accepted(self):
        req = ScaleReadingRequest(device_id="sb1", weight_grams=100_000.0)
        assert req.weight_grams == 100_000.0

    def test_negative_weight_rejected(self):
        with pytest.raises(ValidationError):
            ScaleReadingRequest(device_id="sb1", weight_grams=-1.0)

    def test_over_max_weight_rejected(self):
        with pytest.raises(ValidationError):
            ScaleReadingRequest(device_id="sb1", weight_grams=100_001.0)

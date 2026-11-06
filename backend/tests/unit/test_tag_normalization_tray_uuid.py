"""Tests for is_bambu_tray_uuid -- telling a real tray UUID from the pre-#984 filament type."""

import pytest

from backend.app.utils.tag_normalization import is_bambu_tray_uuid


@pytest.mark.parametrize(
    "value",
    [
        # Measured: AMS MQTT tray_uuid == tag block 9 (#984)
        "9E0B0717BEE94D7887EB1D8DFD1A14F3",
        "5E5498918CBF4B94A25EF669C24DECC3",
        "9e0b0717bee94d7887eb1d8dfd1a14f3",
        "9E0B0717-BEE9-4D78-87EB-1D8DFD1A14F3",
    ],
)
def test_real_tray_uuids_pass(value):
    assert is_bambu_tray_uuid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "504C41204D6174746500000000000000",  # "PLA Matte", blocks 4-5 read as tray_uuid
        "00000000000000000000000000000000",
        "9E0B0717BEE91D7887EB1D8DFD1A14F3",  # version 1
        "9E0B0717BEE94D78C7EB1D8DFD1A14F3",  # variant not RFC 4122
        "9E0B0717BEE94D7887EB1D8DFD1A14",  # too short
        "",
        None,
    ],
)
def test_other_values_fail(value):
    assert is_bambu_tray_uuid(value) is False

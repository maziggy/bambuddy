"""Unit tests for slot-preset key derivation.

Regression coverage for #1053: the backend's get_slot_presets response
must use the same keying scheme as the frontend's getGlobalTrayId
(amsHelpers.ts) so that AMS-HT mappings round-trip correctly.
"""

from backend.app.api.routes.printers import _slot_preset_key


def test_regular_ams_uses_global_tray_id():
    assert _slot_preset_key(0, 0) == 0
    assert _slot_preset_key(0, 3) == 3
    assert _slot_preset_key(1, 1) == 5
    assert _slot_preset_key(2, 2) == 10
    assert _slot_preset_key(3, 3) == 15


def test_ams_ht_keyed_by_ams_id():
    # AMS-HT is single-slot and shares its global tray id with the unit id;
    # frontend getGlobalTrayId(amsId, 0, false) returns amsId for 128-135.
    assert _slot_preset_key(128, 0) == 128
    assert _slot_preset_key(129, 0) == 129
    assert _slot_preset_key(135, 0) == 135


def test_external_spool_uses_multiplied_id():
    # External (ams_id=255) matches PrintersPage lookup: 255 * 4 + tray_id.
    assert _slot_preset_key(255, 0) == 1020
    assert _slot_preset_key(255, 1) == 1021

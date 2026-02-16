"""Unit tests for the AMS weight sync calculation logic.

Tests the weight_used calculation and remain% validation extracted from
the POST /inventory/sync-ams-weights endpoint, without requiring a database.
"""

import pytest

from backend.app.api.routes.inventory import _find_tray_in_ams_data


def _calc_weight_used(label_weight: int | None, remain: int) -> float:
    """Reproduce the weight calculation from sync_weights_from_ams."""
    lw = label_weight or 1000
    return round(lw * (100 - remain) / 100.0, 1)


def _is_valid_remain(remain_raw) -> tuple[bool, int]:
    """Reproduce the remain% validation from sync_weights_from_ams.

    Returns (is_valid, parsed_value).  parsed_value is only meaningful
    when is_valid is True.
    """
    if remain_raw is None:
        return False, 0
    try:
        val = int(remain_raw)
    except (TypeError, ValueError):
        return False, 0
    if val < 0 or val > 100:
        return False, val
    return True, val


class TestWeightCalculation:
    """Test the weight_used = label_weight * (100 - remain) / 100 formula."""

    def test_remain_100_means_no_usage(self):
        """A full spool (remain=100) should have weight_used=0."""
        assert _calc_weight_used(1000, 100) == 0.0

    def test_remain_50_with_1000g_spool(self):
        """Half-used 1000g spool should have weight_used=500."""
        assert _calc_weight_used(1000, 50) == 500.0

    def test_remain_0_means_fully_used(self):
        """An empty spool (remain=0) should have weight_used equal to label_weight.

        Unlike the on_ams_change guard, the sync endpoint processes remain=0
        since it is a manual recovery tool.
        """
        assert _calc_weight_used(1000, 0) == 1000.0

    def test_respects_label_weight_500g(self):
        """500g spool at remain=50 should have weight_used=250."""
        assert _calc_weight_used(500, 50) == 250.0

    def test_respects_label_weight_250g(self):
        """250g spool at remain=75 should have weight_used=62.5."""
        assert _calc_weight_used(250, 75) == 62.5

    def test_none_label_weight_defaults_to_1000(self):
        """When label_weight is None, it defaults to 1000g."""
        assert _calc_weight_used(None, 50) == 500.0

    def test_result_is_rounded_to_one_decimal(self):
        """Weight used should be rounded to 1 decimal place.

        For a 1000g spool at remain=33, weight_used = 1000 * 67 / 100 = 670.0
        """
        assert _calc_weight_used(1000, 33) == 670.0

    def test_odd_fraction_rounds_correctly(self):
        """750g spool at remain=33 → 750 * 67/100 = 502.5."""
        assert _calc_weight_used(750, 33) == 502.5

    def test_small_spool_small_remain(self):
        """200g spool at remain=1 → 200 * 99/100 = 198.0."""
        assert _calc_weight_used(200, 1) == 198.0


class TestRemainValidation:
    """Test the remain% bounds and type validation."""

    def test_remain_minus_1_is_invalid(self):
        """remain=-1 (firmware 'unknown') should be skipped."""
        valid, _ = _is_valid_remain(-1)
        assert valid is False

    def test_remain_101_is_invalid(self):
        """remain=101 (out of range) should be skipped."""
        valid, _ = _is_valid_remain(101)
        assert valid is False

    def test_remain_negative_large_is_invalid(self):
        """Large negative remain values should be skipped."""
        valid, _ = _is_valid_remain(-50)
        assert valid is False

    def test_remain_200_is_invalid(self):
        """remain=200 should be skipped."""
        valid, _ = _is_valid_remain(200)
        assert valid is False

    def test_remain_none_is_invalid(self):
        """remain=None (missing from tray data) should be skipped."""
        valid, _ = _is_valid_remain(None)
        assert valid is False

    def test_remain_non_numeric_string_is_invalid(self):
        """Non-numeric string remain should be skipped."""
        valid, _ = _is_valid_remain("abc")
        assert valid is False

    def test_remain_0_is_valid(self):
        """remain=0 should be valid (manual recovery handles empty spools)."""
        valid, val = _is_valid_remain(0)
        assert valid is True
        assert val == 0

    def test_remain_100_is_valid(self):
        """remain=100 should be valid."""
        valid, val = _is_valid_remain(100)
        assert valid is True
        assert val == 100

    def test_remain_50_is_valid(self):
        """remain=50 should be valid."""
        valid, val = _is_valid_remain(50)
        assert valid is True
        assert val == 50

    def test_remain_string_number_is_valid(self):
        """Numeric string remain (e.g. '75') should be parsed as int."""
        valid, val = _is_valid_remain("75")
        assert valid is True
        assert val == 75


class TestFindTrayInAmsData:
    """Test the _find_tray_in_ams_data helper used by the sync endpoint."""

    def test_finds_matching_tray(self):
        """Should return the matching tray dict."""
        ams_data = [
            {
                "id": 0,
                "tray": [
                    {"id": 0, "remain": 80},
                    {"id": 1, "remain": 50},
                ],
            },
        ]
        tray = _find_tray_in_ams_data(ams_data, ams_id=0, tray_id=1)
        assert tray is not None
        assert tray["remain"] == 50

    def test_returns_none_for_missing_ams_unit(self):
        """Should return None when the AMS unit ID is not found."""
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]
        assert _find_tray_in_ams_data(ams_data, ams_id=1, tray_id=0) is None

    def test_returns_none_for_missing_tray(self):
        """Should return None when the tray ID is not found."""
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]
        assert _find_tray_in_ams_data(ams_data, ams_id=0, tray_id=3) is None

    def test_returns_none_for_empty_data(self):
        """Should return None for empty AMS data."""
        assert _find_tray_in_ams_data([], ams_id=0, tray_id=0) is None

    def test_returns_none_for_none_data(self):
        """Should return None for None AMS data."""
        assert _find_tray_in_ams_data(None, ams_id=0, tray_id=0) is None

    def test_multi_ams_unit_lookup(self):
        """Should find trays across multiple AMS units."""
        ams_data = [
            {"id": 0, "tray": [{"id": 0, "remain": 80}]},
            {"id": 1, "tray": [{"id": 2, "remain": 30}]},
        ]
        tray = _find_tray_in_ams_data(ams_data, ams_id=1, tray_id=2)
        assert tray is not None
        assert tray["remain"] == 30

    def test_ams_ht_high_id(self):
        """Should find trays in AMS-HT units (id >= 128)."""
        ams_data = [{"id": 128, "tray": [{"id": 0, "remain": 65}]}]
        tray = _find_tray_in_ams_data(ams_data, ams_id=128, tray_id=0)
        assert tray is not None
        assert tray["remain"] == 65


class TestSyncSkipLogic:
    """Test combinations that exercise the sync/skip decision path."""

    def test_same_value_is_skipped(self):
        """When old weight_used matches new, the spool is skipped (no DB write)."""
        # Simulating the endpoint logic: if round(old_used, 1) == new_used → skip
        label_weight = 1000
        remain = 50
        new_used = _calc_weight_used(label_weight, remain)
        old_used = 500.0  # Already matches
        assert round(old_used, 1) == new_used  # → would be skipped

    def test_different_value_is_synced(self):
        """When old weight_used differs from new, the spool is synced."""
        label_weight = 1000
        remain = 50
        new_used = _calc_weight_used(label_weight, remain)
        old_used = 300.0  # Different
        assert round(old_used, 1) != new_used  # → would be synced

    def test_none_old_used_treated_as_zero(self):
        """When old weight_used is None (new spool), it defaults to 0."""
        old_used = None
        effective_old = old_used or 0
        new_used = _calc_weight_used(1000, 80)  # 200.0
        assert effective_old == 0
        assert round(effective_old, 1) != new_used  # → would be synced

    def test_remain_0_synced_not_skipped(self):
        """remain=0 is valid and produces weight_used=label_weight.

        This is distinct from on_ams_change behavior where remain=0 is
        ignored.  The sync endpoint processes it as a manual recovery tool.
        """
        valid, val = _is_valid_remain(0)
        assert valid is True
        new_used = _calc_weight_used(1000, val)
        assert new_used == 1000.0

    def test_remain_minus_1_never_reaches_calc(self):
        """remain=-1 fails validation before weight calculation."""
        valid, _ = _is_valid_remain(-1)
        assert valid is False
        # The endpoint would skip += 1 and continue

    def test_remain_101_never_reaches_calc(self):
        """remain=101 fails validation before weight calculation."""
        valid, _ = _is_valid_remain(101)
        assert valid is False

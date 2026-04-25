"""Schema validation tests for the spool rgba field (#1055).

Three guarantees to lock in:
1. SpoolCreate and SpoolUpdate must reject malformed rgba (short, long, non-hex)
   on the write path — this is the "add a check" the reporter asked for.
2. SpoolResponse must NOT validate rgba on the read path: a single legacy row
   with a 7-char rgba (as in #1055) must not 500 the entire inventory list.
3. Valid 8-char hex must continue to round-trip through all three schemas.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.spool import SpoolCreate, SpoolUpdate


class TestSpoolCreateRgbaValidation:
    """Write-path validation on the create schema."""

    def test_accepts_valid_8char_hex(self):
        spool = SpoolCreate(material="PLA", rgba="FF00AAFF")
        assert spool.rgba == "FF00AAFF"

    def test_accepts_lowercase_hex(self):
        spool = SpoolCreate(material="PLA", rgba="ff00aaff")
        assert spool.rgba == "ff00aaff"

    def test_accepts_null_rgba(self):
        spool = SpoolCreate(material="PLA", rgba=None)
        assert spool.rgba is None

    def test_rejects_7char_rgba(self):
        """#1055 repro: a 7-char 'FFFFFFF' must not be acceptable on create."""
        with pytest.raises(ValidationError, match="rgba"):
            SpoolCreate(material="PLA", rgba="FFFFFFF")

    def test_rejects_6char_rgba(self):
        """Plain RRGGBB without alpha must be rejected — frontend appends FF."""
        with pytest.raises(ValidationError, match="rgba"):
            SpoolCreate(material="PLA", rgba="FF0000")

    def test_rejects_non_hex_char(self):
        with pytest.raises(ValidationError, match="rgba"):
            SpoolCreate(material="PLA", rgba="FFZZ00FF")


class TestSpoolUpdateRgbaValidation:
    """Write-path validation on the update schema — the gap that let #1055 happen.

    Before the fix, SpoolUpdate.rgba was a bare `str | None` so a PATCH could
    plant a 7-char value straight into the DB. That row then caused a 500 on
    the next GET because SpoolResponse enforced the pattern at serialize time.
    """

    def test_accepts_valid_8char_hex(self):
        update = SpoolUpdate(rgba="00FF00FF")
        assert update.rgba == "00FF00FF"

    def test_accepts_null_rgba(self):
        update = SpoolUpdate(rgba=None)
        assert update.rgba is None

    def test_accepts_missing_rgba(self):
        """Partial updates — rgba not present in payload — must still be valid."""
        update = SpoolUpdate(material="PETG")
        assert update.rgba is None

    def test_rejects_7char_rgba(self):
        """#1055 repro: PATCH must reject the exact pattern that bricked the reporter."""
        with pytest.raises(ValidationError, match="rgba"):
            SpoolUpdate(rgba="FFFFFFF")

    def test_rejects_9char_rgba(self):
        with pytest.raises(ValidationError, match="rgba"):
            SpoolUpdate(rgba="FFFFFFFFF")

    def test_rejects_non_hex_char(self):
        with pytest.raises(ValidationError, match="rgba"):
            SpoolUpdate(rgba="FFGG00FF")


class TestSpoolResponseRgbaLeniency:
    """Read-path leniency — a legacy bad row must never 500 the list endpoint.

    Before the fix, SpoolResponse inherited the pattern from SpoolBase so a
    single 7-char rgba in the DB blew up the whole inventory listing. The
    response schema now treats rgba as an unconstrained Optional[str] — write
    validation is where the pattern belongs; responses must tolerate whatever
    is already persisted.
    """

    # SpoolResponse requires id + timestamps so it's easier to test via a
    # minimal dict payload than by constructing a full instance.
    @staticmethod
    def _make_response_kwargs(**overrides):
        from datetime import datetime

        base = {
            "id": 1,
            "material": "PLA",
            "created_at": datetime.fromisoformat("2026-01-01T00:00:00"),
            "updated_at": datetime.fromisoformat("2026-01-01T00:00:00"),
        }
        base.update(overrides)
        return base

    def test_tolerates_7char_rgba_on_serialize(self):
        """This is the #1055 bug fixed: malformed legacy rgba must serialize cleanly."""
        from backend.app.schemas.spool import SpoolResponse

        response = SpoolResponse(**self._make_response_kwargs(rgba="FFFFFFF"))
        assert response.rgba == "FFFFFFF"

    def test_tolerates_null_rgba(self):
        from backend.app.schemas.spool import SpoolResponse

        response = SpoolResponse(**self._make_response_kwargs(rgba=None))
        assert response.rgba is None

    def test_tolerates_non_hex_rgba(self):
        """Even completely garbage rgba shouldn't crash the endpoint."""
        from backend.app.schemas.spool import SpoolResponse

        response = SpoolResponse(**self._make_response_kwargs(rgba="not-hex-at-all"))
        assert response.rgba == "not-hex-at-all"

    def test_passes_valid_rgba_through(self):
        from backend.app.schemas.spool import SpoolResponse

        response = SpoolResponse(**self._make_response_kwargs(rgba="FF00AAFF"))
        assert response.rgba == "FF00AAFF"


class TestSpoolCategoryAndThreshold:
    """#729: per-spool category + low-stock threshold override schema validation."""

    def test_create_accepts_category_and_threshold(self):
        spool = SpoolCreate(material="PLA", category="Production", low_stock_threshold_pct=50)
        assert spool.category == "Production"
        assert spool.low_stock_threshold_pct == 50

    def test_create_defaults_to_null(self):
        """Both new fields are optional and default to None — backward compat."""
        spool = SpoolCreate(material="PLA")
        assert spool.category is None
        assert spool.low_stock_threshold_pct is None

    def test_update_accepts_partial_changes(self):
        spool = SpoolUpdate(category="Prototype")
        assert spool.category == "Prototype"
        assert spool.low_stock_threshold_pct is None

    def test_update_clears_via_explicit_null(self):
        """Sending null on PATCH explicitly resets the override."""
        spool = SpoolUpdate(category=None, low_stock_threshold_pct=None)
        assert spool.category is None
        assert spool.low_stock_threshold_pct is None

    def test_threshold_rejects_zero(self):
        """0% would mean the spool is never low-stock — disallow as a footgun."""
        with pytest.raises(ValidationError, match="low_stock_threshold_pct"):
            SpoolCreate(material="PLA", low_stock_threshold_pct=0)

    def test_threshold_rejects_100(self):
        """100% would mean the spool is always low-stock — disallow."""
        with pytest.raises(ValidationError, match="low_stock_threshold_pct"):
            SpoolCreate(material="PLA", low_stock_threshold_pct=100)

    def test_threshold_rejects_negative(self):
        with pytest.raises(ValidationError, match="low_stock_threshold_pct"):
            SpoolCreate(material="PLA", low_stock_threshold_pct=-5)

    def test_category_rejects_too_long(self):
        """50-char cap matches the DB column to prevent silent truncation."""
        with pytest.raises(ValidationError, match="category"):
            SpoolCreate(material="PLA", category="X" * 51)

    def test_category_accepts_max_length(self):
        spool = SpoolCreate(material="PLA", category="X" * 50)
        assert spool.category == "X" * 50

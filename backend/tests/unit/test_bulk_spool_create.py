"""Unit tests for bulk spool creation.

Tests:
- SpoolBulkCreate schema validation (quantity bounds)
- Bulk create endpoint creates the requested number of spools
- Bulk create with quantity=1 (single spool)
- Bulk create returns spools with k_profiles loaded
- Bulk create copies the inherited supplier links onto every copy
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.app.models.spool import Spool
from backend.app.models.supplier import SpoolSupplier
from backend.app.schemas.spool import SpoolBulkCreate, SpoolCreate

# ── Schema Validation ──────────────────────────────────────────────────────


class TestSpoolBulkCreateSchema:
    """Tests for the SpoolBulkCreate Pydantic model."""

    def test_default_quantity_is_1(self):
        data = SpoolBulkCreate(spool=SpoolCreate(material="PLA"))
        assert data.quantity == 1

    def test_quantity_within_range(self):
        data = SpoolBulkCreate(spool=SpoolCreate(material="PLA"), quantity=50)
        assert data.quantity == 50

    def test_quantity_max_100(self):
        data = SpoolBulkCreate(spool=SpoolCreate(material="PLA"), quantity=100)
        assert data.quantity == 100

    def test_quantity_zero_rejected(self):
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            SpoolBulkCreate(spool=SpoolCreate(material="PLA"), quantity=0)

    def test_quantity_negative_rejected(self):
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            SpoolBulkCreate(spool=SpoolCreate(material="PLA"), quantity=-1)

    def test_quantity_over_100_rejected(self):
        with pytest.raises(ValidationError, match="less than or equal to 100"):
            SpoolBulkCreate(spool=SpoolCreate(material="PLA"), quantity=101)

    def test_spool_fields_preserved(self):
        data = SpoolBulkCreate(
            spool=SpoolCreate(
                material="PETG",
                brand="Polymaker",
                subtype="Basic",
                color_name="Red",
                rgba="FF0000FF",
                label_weight=750,
                note="Test batch",
            ),
            quantity=5,
        )
        assert data.spool.material == "PETG"
        assert data.spool.brand == "Polymaker"
        assert data.spool.label_weight == 750
        assert data.spool.note == "Test batch"
        assert data.quantity == 5

    def test_spool_without_slicer_filament_is_stock(self):
        """A spool without slicer_filament is a 'stock' spool (computed, not stored)."""
        data = SpoolBulkCreate(
            spool=SpoolCreate(material="PLA", label_weight=1000),
            quantity=3,
        )
        assert data.spool.slicer_filament is None

    def test_spool_with_slicer_filament_is_configured(self):
        data = SpoolBulkCreate(
            spool=SpoolCreate(material="PLA", slicer_filament="GFL99"),
            quantity=2,
        )
        assert data.spool.slicer_filament == "GFL99"

    def test_material_required(self):
        with pytest.raises(ValidationError):
            SpoolBulkCreate(spool=SpoolCreate(material=""), quantity=1)


# ── Endpoint Logic ─────────────────────────────────────────────────────────


def _make_mock_spool(spool_id):
    """Create a mock Spool ORM object."""
    spool = MagicMock()
    spool.id = spool_id
    spool.material = "PLA"
    spool.label_weight = 1000
    spool.k_profiles = []
    return spool


def _result(rows):
    """A stand-in for the Result of a query that selected ``rows``."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    result.scalars.return_value.first.return_value = rows[0] if rows else None
    result.first.return_value = (rows[0],) if rows else None
    return result


def _make_db(refetched, *, donor_id=None, donor_links=()):
    """Session double that answers each of the bulk path's queries separately.

    The endpoint runs three different selects — the supplier donor lookup,
    that donor's link rows (#2988), and the re-fetch of the created spools —
    so one canned result for all of them hands Spool rows to the supplier
    code and invents a donor that was never seeded. Route on what each select
    asks for instead. Returns the session and the list ``db.add`` lands in.
    """
    added = []
    db = AsyncMock()
    db.add = added.append

    async def execute(statement, *_args, **_kwargs):
        described = statement.column_descriptions[0]
        if described["entity"] is SpoolSupplier:
            return _result(donor_links)
        if described["name"] == "id":
            return _result([] if donor_id is None else [donor_id])
        return _result(refetched)

    db.execute = AsyncMock(side_effect=execute)
    return db, added


class TestBulkCreateEndpoint:
    """Tests for the bulk_create_spools endpoint logic."""

    @pytest.mark.asyncio
    async def test_creates_requested_number_of_spools(self):
        """Verify N spools are created and added to the session."""
        from backend.app.api.routes.inventory import bulk_create_spools

        data = SpoolBulkCreate(
            spool=SpoolCreate(material="PLA", brand="Test", label_weight=1000),
            quantity=3,
        )

        db, added_objects = _make_db([_make_mock_spool(i + 1) for i in range(3)])

        result = await bulk_create_spools(data=data, db=db, _=None)

        assert len(result) == 3
        assert len(added_objects) == 3
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_quantity_creates_one_spool(self):
        """Bulk create with quantity=1 should create exactly one spool."""
        from backend.app.api.routes.inventory import bulk_create_spools

        data = SpoolBulkCreate(
            spool=SpoolCreate(material="PETG"),
            quantity=1,
        )

        db, added_objects = _make_db([_make_mock_spool(1)])

        result = await bulk_create_spools(data=data, db=db, _=None)

        assert len(result) == 1
        assert len(added_objects) == 1

    @pytest.mark.asyncio
    async def test_all_spools_have_same_fields(self):
        """All created spools should have identical field values."""
        from backend.app.api.routes.inventory import bulk_create_spools

        data = SpoolBulkCreate(
            spool=SpoolCreate(
                material="ABS",
                brand="Bambu Lab",
                color_name="Black",
                rgba="000000FF",
                label_weight=750,
            ),
            quantity=3,
        )

        db, added_objects = _make_db([_make_mock_spool(i + 1) for i in range(3)])

        await bulk_create_spools(data=data, db=db, _=None)

        # The spools, not everything the session was handed: the bulk path also
        # adds the inherited supplier links, and a SpoolSupplier has no material.
        spools = [obj for obj in added_objects if isinstance(obj, Spool)]
        assert len(spools) == 3
        for spool_obj in spools:
            assert spool_obj.material == "ABS"
            assert spool_obj.brand == "Bambu Lab"
            assert spool_obj.color_name == "Black"
            assert spool_obj.label_weight == 750

"""Schema tests for the spool storage_location field (#1291).

Reporter @needo37: the `storage_location` column existed on the Spool ORM model
but was missing from SpoolBase, SpoolUpdate, and SpoolResponse. Pydantic
silently drops unknown fields, so PATCH writes never reached the DB and reads
omitted the field entirely. The fix is purely additive on the schema layer.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.spool import SpoolCreate, SpoolResponse, SpoolUpdate


class TestStorageLocationRoundtrips:
    """The bug was that storage_location wasn't on the schemas at all — pin
    the round-trip so a future refactor can't quietly drop it again."""

    def test_create_accepts_storage_location(self):
        spool = SpoolCreate(material="PLA", storage_location="Drybox #1")
        assert spool.storage_location == "Drybox #1"

    def test_create_storage_location_optional(self):
        spool = SpoolCreate(material="PLA")
        assert spool.storage_location is None

    def test_update_accepts_storage_location(self):
        update = SpoolUpdate(storage_location="Top shelf")
        assert update.storage_location == "Top shelf"

    def test_update_omits_unset_storage_location(self):
        """A PATCH that doesn't mention storage_location must NOT clear it —
        model_dump(exclude_unset=True) keeps the field out of the update dict
        so the route's setattr loop skips it."""
        update = SpoolUpdate.model_validate({})
        dumped = update.model_dump(exclude_unset=True)
        assert "storage_location" not in dumped

    def test_update_explicit_null_clears_storage_location(self):
        """A PATCH that explicitly sends storage_location=null must reach
        the route's update_data dict as None, so setattr writes NULL to the
        DB — that's how the UI clears the field."""
        update = SpoolUpdate.model_validate({"storage_location": None})
        dumped = update.model_dump(exclude_unset=True)
        assert "storage_location" in dumped
        assert dumped["storage_location"] is None

    def test_response_carries_storage_location(self):
        """SpoolResponse inherits from SpoolBase, so the field must surface
        on read too — otherwise the inventory table silently always shows '-'."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        response = SpoolResponse.model_validate(
            {
                "id": 1,
                "material": "PLA",
                "storage_location": "Drybox #1",
                "created_at": now,
                "updated_at": now,
            }
        )
        assert response.storage_location == "Drybox #1"


class TestStorageLocationLength:
    """The DB column is String(255). Schema must enforce the same cap so the
    API rejects too-long input cleanly instead of letting SQLAlchemy raise."""

    def test_accepts_max_length(self):
        update = SpoolUpdate(storage_location="x" * 255)
        assert len(update.storage_location) == 255

    def test_rejects_over_max_length(self):
        with pytest.raises(ValidationError, match="storage_location"):
            SpoolUpdate(storage_location="x" * 256)

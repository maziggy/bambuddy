"""Unit tests for catalog bulk delete endpoints."""

import pytest
from pydantic import ValidationError

from backend.app.api.routes.inventory import BulkDeleteIdsRequest


class TestBulkDeleteIdsRequest:
    """Tests for BulkDeleteIdsRequest schema."""

    def test_accepts_list_of_ids(self):
        req = BulkDeleteIdsRequest(ids=[1, 2, 3])
        assert req.ids == [1, 2, 3]

    def test_accepts_empty_list(self):
        req = BulkDeleteIdsRequest(ids=[])
        assert req.ids == []

    def test_rejects_missing_ids(self):
        with pytest.raises(ValidationError):
            BulkDeleteIdsRequest()

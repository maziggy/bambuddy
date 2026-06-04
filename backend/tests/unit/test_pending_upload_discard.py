"""Tests for pending-upload discard guard."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.app.api.routes.pending_uploads import discard_pending_upload


@pytest.mark.asyncio
async def test_discard_rejects_already_archived_upload():
    pending = MagicMock()
    pending.status = "archived"
    pending.file_path = "/tmp/foo.3mf"

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=pending)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc:
        await discard_pending_upload(upload_id=1, db=db, _=None)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Upload already processed"

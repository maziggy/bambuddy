"""The filament map of a library slice reads only the caller's printers (#3224).

The map is planned from the spools loaded on the printers of the target
model. An API key limited to some printers slices as no user when it holds
no cloud scope, so the scope has to come from the request's auth, not from
a user id, or such a key plans from every printer (#1727).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.auth import generate_api_key
from backend.app.core.config import settings as app_settings
from backend.app.models.api_key import APIKey
from backend.app.models.library import LibraryFile
from backend.app.models.printer import Printer
from backend.tests.integration import test_api_key_queue_acts_as_owner as owner_tests

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

world = owner_tests.world

SLICE_BODY = {
    "printer_preset": {"source": "local", "id": "1"},
    "process_preset": {"source": "local", "id": "2"},
    "filament_presets": [{"source": "local", "id": "3"}],
}


async def test_a_key_limited_to_one_printer_plans_from_that_printer_only(
    async_client, db_session, world, tmp_path, monkeypatch
):
    seen = world["printer"]
    unseen = Printer(
        name="Q", ip_address="192.168.9.10", serial_number="00M00A3224000002", access_code="12345678", model="X1C"
    )
    db_session.add(unseen)
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "cube.stl").write_bytes(b"solid cube\nendsolid cube\n")
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    source = LibraryFile(
        filename="cube.stl",
        file_path="library/cube.stl",
        file_type="stl",
        file_size=1,
        created_by_id=world["admin"].id,
    )
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add_all(
        [
            source,
            APIKey(
                name="one printer",
                key_hash=key_hash,
                key_prefix=key_prefix,
                user_id=world["admin"].id,
                can_manage_library=True,
                printer_ids=[seen.id],
            ),
        ]
    )
    await db_session.commit()

    runs = []

    async def _enqueue(**kwargs):
        runs.append(kwargs["run"])
        return MagicMock(id=1, status="pending")

    persist = AsyncMock(return_value=MagicMock(model_dump=MagicMock(return_value={})))
    with (
        patch("backend.app.services.slice_dispatch.slice_dispatch.enqueue", new=_enqueue),
        patch("backend.app.api.routes.library.slice_and_persist", new=persist),
    ):
        response = await async_client.post(
            f"/api/v1/library/files/{source.id}/slice", json=SLICE_BODY, headers={"X-API-Key": full_key}
        )
        assert response.status_code == 202, response.text
        await runs[0](1)

    scope = persist.await_args.kwargs["printer_scope"]
    assert (scope.allows(seen.id), scope.allows(unseen.id)) == (True, False)

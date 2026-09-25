"""POST /inventory/spools must not create a second active spool for a tag that
is already linked — every later tag lookup (SpoolBuddy NFC match, AMS sync)
would be ambiguous about which spool it hits. Reachable from the SpoolBuddy
barcode add flow when stale tag state leaks into the create payload (a missed
tag-removed event kept the previous roll's tag alive on the kiosk).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool

pytestmark = pytest.mark.integration

SPOOLS_URL = "/api/v1/inventory/spools"


async def _create(async_client: AsyncClient, **extra):
    payload = {"material": "PLA", "label_weight": 1000, **extra}
    return await async_client.post(SPOOLS_URL, json=payload)


async def _count_with_tag(db_session: AsyncSession, tag_uid: str) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(Spool).where(func.upper(Spool.tag_uid) == tag_uid.upper())
    )
    return result.scalar_one()


class TestCreateSpoolTagConflict:
    async def test_duplicate_active_tag_is_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        first = await _create(async_client, tag_uid="72DB77EB")
        assert first.status_code == 200
        first_id = first.json()["id"]

        second = await _create(async_client, tag_uid="72DB77EB")
        assert second.status_code == 409
        assert f"#{first_id}" in second.json()["detail"]
        assert await _count_with_tag(db_session, "72DB77EB") == 1

    async def test_duplicate_tag_check_is_case_insensitive(self, async_client: AsyncClient):
        first = await _create(async_client, tag_uid="AABBCCDD")
        assert first.status_code == 200

        second = await _create(async_client, tag_uid="aabbccdd")
        assert second.status_code == 409

    async def test_tag_of_archived_spool_is_reusable(self, async_client: AsyncClient, db_session: AsyncSession):
        first = await _create(async_client, tag_uid="662AC3E6")
        assert first.status_code == 200
        first_id = first.json()["id"]

        archived = await async_client.post(f"{SPOOLS_URL}/{first_id}/archive")
        assert archived.status_code == 200

        second = await _create(async_client, tag_uid="662AC3E6")
        assert second.status_code == 200
        assert second.json()["id"] != first_id

    async def test_create_without_tag_is_unaffected(self, async_client: AsyncClient):
        first = await _create(async_client)
        second = await _create(async_client)
        assert first.status_code == 200
        assert second.status_code == 200

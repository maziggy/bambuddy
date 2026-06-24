"""By-tag spool lookup endpoint (#1663).

``GET /api/v1/inventory/spools/by-tag`` lets NFC inventory integrations
dedupe a scan by ``tray_uuid``/``tag_uid`` without listing the whole
inventory, and is readable with either a read-status or a manage-inventory
API-key scope.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool

TRAY_UUID = "AABBCCDDEEFF0011AABBCCDDEEFF0011"
TAG_UID = "04A1B2C3"


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    """Create a Spool with sensible defaults."""

    async def _create(**kwargs):
        defaults = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Bambu",
            "color_name": "Red",
            "rgba": "FF0000FF",
            "label_weight": 1000,
            "weight_used": 0,
            "weight_used_baseline": 0,
            "weight_locked": False,
        }
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create


class TestSpoolByTagLookup:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_lookup_by_tray_uuid(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory(tray_uuid=TRAY_UUID)
        resp = await async_client.get(f"/api/v1/inventory/spools/by-tag?tray_uuid={TRAY_UUID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == spool.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_lookup_by_tag_uid(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory(tag_uid=TAG_UID)
        resp = await async_client.get(f"/api/v1/inventory/spools/by-tag?tag_uid={TAG_UID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == spool.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_lookup_normalizes_input(self, async_client: AsyncClient, spool_factory):
        """Lowercase / separator-laden input still matches the stored hex."""
        spool = await spool_factory(tray_uuid=TRAY_UUID)
        messy = "aa:bb:cc:dd:ee:ff:00:11:aa:bb:cc:dd:ee:ff:00:11"
        resp = await async_client.get(f"/api/v1/inventory/spools/by-tag?tray_uuid={messy}")
        assert resp.status_code == 200
        assert resp.json()["id"] == spool.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tray_uuid_preferred_over_tag_uid(self, async_client: AsyncClient, spool_factory):
        """When both identifiers are given, tray_uuid wins (it's the AMS key)."""
        by_uuid = await spool_factory(tray_uuid=TRAY_UUID)
        await spool_factory(tag_uid=TAG_UID)
        resp = await async_client.get(f"/api/v1/inventory/spools/by-tag?tray_uuid={TRAY_UUID}&tag_uid={TAG_UID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == by_uuid.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tray_uuid_miss_falls_through_to_tag_uid(self, async_client: AsyncClient, spool_factory):
        """tray_uuid is tried first, but a miss must fall through to tag_uid, not 404."""
        by_tag = await spool_factory(tag_uid=TAG_UID)
        unknown_tray = "FF" * 16
        resp = await async_client.get(f"/api/v1/inventory/spools/by-tag?tray_uuid={unknown_tray}&tag_uid={TAG_UID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == by_tag.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_identifier_is_400(self, async_client: AsyncClient):
        resp = await async_client.get("/api/v1/inventory/spools/by-tag")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_non_hex_identifier_is_400(self, async_client: AsyncClient):
        """A value with no hex characters normalizes to empty → treated as absent."""
        resp = await async_client.get("/api/v1/inventory/spools/by-tag?tray_uuid=zzz")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_match_is_404(self, async_client: AsyncClient, spool_factory):
        await spool_factory(tray_uuid=TRAY_UUID)
        resp = await async_client.get("/api/v1/inventory/spools/by-tag?tray_uuid=" + "FF" * 16)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archived_excluded_by_default(self, async_client: AsyncClient, spool_factory):
        from datetime import datetime, timezone

        await spool_factory(tray_uuid=TRAY_UUID, archived_at=datetime.now(timezone.utc))
        resp = await async_client.get(f"/api/v1/inventory/spools/by-tag?tray_uuid={TRAY_UUID}")
        assert resp.status_code == 404

        resp = await async_client.get(f"/api/v1/inventory/spools/by-tag?tray_uuid={TRAY_UUID}&include_archived=true")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_does_not_collide_with_spool_id_route(self, async_client: AsyncClient, spool_factory):
        """'by-tag' must route to the lookup handler, not /spools/{spool_id}."""
        await spool_factory(tray_uuid=TRAY_UUID)
        resp = await async_client.get("/api/v1/inventory/spools/by-tag?tray_uuid=" + TRAY_UUID)
        assert resp.status_code == 200


async def _make_api_key(db_session: AsyncSession, **scopes) -> str:
    full_key, key_hash, key_prefix = generate_api_key()
    flags = {
        "can_queue": False,
        "can_control_printer": False,
        "can_read_status": False,
        "can_manage_inventory": False,
    }
    flags.update(scopes)
    db_session.add(APIKey(name="test-key", key_hash=key_hash, key_prefix=key_prefix, enabled=True, **flags))
    db_session.add(Settings(key="auth_enabled", value="true"))
    await db_session.commit()
    return full_key


class TestSpoolByTagApiKeyScope:
    """The endpoint is the core ask of #1663: a Manage-Inventory key (which can
    already create/update spools) must be able to read them back."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_manage_inventory_key_can_read(self, async_client: AsyncClient, db_session, spool_factory):
        spool = await spool_factory(tray_uuid=TRAY_UUID)
        key = await _make_api_key(db_session, can_manage_inventory=True)
        resp = await async_client.get(
            f"/api/v1/inventory/spools/by-tag?tray_uuid={TRAY_UUID}",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == spool.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_read_status_key_can_read(self, async_client: AsyncClient, db_session, spool_factory):
        spool = await spool_factory(tray_uuid=TRAY_UUID)
        key = await _make_api_key(db_session, can_read_status=True)
        resp = await async_client.get(
            f"/api/v1/inventory/spools/by-tag?tray_uuid={TRAY_UUID}",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == spool.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_key_without_inventory_scope_is_denied(self, async_client: AsyncClient, db_session, spool_factory):
        await spool_factory(tray_uuid=TRAY_UUID)
        key = await _make_api_key(db_session, can_control_printer=True)
        resp = await async_client.get(
            f"/api/v1/inventory/spools/by-tag?tray_uuid={TRAY_UUID}",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 403

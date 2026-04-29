"""Integration tests for SpoolBuddy + Spoolman NFC fixes.

Group 1 – tag-scanned broadcasts include tray_uuid in all WebSocket messages.
Group 2 – PATCH /api/v1/spoolman/inventory/spools/{id}/tag endpoint.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.services.spoolman import SpoolmanNotFoundError, SpoolmanUnavailableError

SPOOLBUDDY_API = "/api/v1/spoolbuddy"
INVENTORY_API = "/api/v1/spoolman/inventory"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def spoolman_settings_local(db_session: AsyncSession):
    """Spoolman enabled, URL = spoolman.local (matches SpoolBuddy service patches)."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://spoolman.local:7912"))
    await db_session.commit()


@pytest.fixture
async def spoolman_settings_inventory(db_session: AsyncSession):
    """Spoolman enabled, URL = localhost (matches inventory proxy patches)."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


def _spoolman_spool(spool_id: int) -> dict:
    """Minimal Spoolman raw spool dict suitable for _map_spoolman_spool()."""
    return {
        "id": spool_id,
        "filament": {
            "material": "PLA",
            "name": "PLA Basic",
            "color_hex": "FF0000",
            "weight": 1000.0,
            "spool_weight": 196.0,
            "vendor": {"name": "Bambu Lab"},
        },
        "used_weight": 0.0,
        "archived": False,
        "registered": "2024-01-01T00:00:00Z",
    }


def _mock_spoolman_client_local() -> MagicMock:
    client = MagicMock()
    client.base_url = "http://spoolman.local:7912"
    client.get_spools = AsyncMock(return_value=[])
    client.find_spool_by_tag = AsyncMock(return_value=None)
    client.merge_spool_extra = AsyncMock(return_value={})
    return client


# ---------------------------------------------------------------------------
# Group 1: broadcast tests — tray_uuid forwarded in all WS broadcasts
# ---------------------------------------------------------------------------


class TestTagScannedBroadcastsTrayUuid:
    """nfc/tag-scanned broadcasts include tray_uuid from the request payload."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_local_match_broadcast_includes_tray_uuid(self, async_client: AsyncClient, spoolman_settings_local):
        """Local DB match broadcasts tray_uuid alongside tag_uid."""
        mock_local_spool = MagicMock()
        mock_local_spool.id = 1
        mock_local_spool.material = "PLA"
        mock_local_spool.subtype = None
        mock_local_spool.color_name = "Red"
        mock_local_spool.rgba = "FF0000FF"
        mock_local_spool.brand = "Bambu Lab"
        mock_local_spool.label_weight = 1000
        mock_local_spool.core_weight = 250
        mock_local_spool.weight_used = 0

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
                new_callable=AsyncMock,
                return_value=mock_local_spool,
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{SPOOLBUDDY_API}/nfc/tag-scanned",
                json={
                    "device_id": "sb-test",
                    "tag_uid": "AABB1122334455FF",
                    "tray_uuid": "DEADBEEFDEADBEEFDEADBEEFDEADBEEF",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["matched"] is True
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_matched"
        assert msg["tag_uid"] == "AABB1122334455FF"
        assert msg["tray_uuid"] == "DEADBEEFDEADBEEFDEADBEEFDEADBEEF"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_match_broadcast_includes_tray_uuid(
        self, async_client: AsyncClient, spoolman_settings_local
    ):
        """Spoolman fallback match broadcasts tray_uuid alongside tag_uid."""
        sm_spool = _spoolman_spool(5)
        sm_spool["extra"] = {"tag": '"DEADBEEFDEADBEEFDEADBEEFDEADBEEF"'}
        mock_client = _mock_spoolman_client_local()
        mock_client.find_spool_by_tag = AsyncMock(return_value=sm_spool)

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{SPOOLBUDDY_API}/nfc/tag-scanned",
                json={
                    "device_id": "sb-test",
                    "tag_uid": "AABB1122334455FF",
                    "tray_uuid": "DEADBEEFDEADBEEFDEADBEEFDEADBEEF",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["matched"] is True
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_matched"
        assert msg["tray_uuid"] == "DEADBEEFDEADBEEFDEADBEEFDEADBEEF"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_tag_broadcast_includes_tray_uuid(self, async_client: AsyncClient, spoolman_settings_local):
        """Unknown tag broadcast includes tray_uuid when Bambu spool is not yet linked."""
        mock_client = _mock_spoolman_client_local()
        mock_client.find_spool_by_tag = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{SPOOLBUDDY_API}/nfc/tag-scanned",
                json={
                    "device_id": "sb-test",
                    "tag_uid": "AABB1122334455FF",
                    "tray_uuid": "CAFEBABECAFEBABECAFEBABECAFEBABE",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["matched"] is False
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_unknown_tag"
        assert msg["tray_uuid"] == "CAFEBABECAFEBABECAFEBABECAFEBABE"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_tag_broadcast_tray_uuid_null_when_absent(
        self, async_client: AsyncClient, spoolman_settings_local
    ):
        """tray_uuid is None in the broadcast when the request omits it."""
        mock_client = _mock_spoolman_client_local()
        mock_client.find_spool_by_tag = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{SPOOLBUDDY_API}/nfc/tag-scanned",
                json={"device_id": "sb-test", "tag_uid": "AABB1122334455FF"},
            )

        assert resp.status_code == 200
        assert resp.json()["matched"] is False
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_unknown_tag"
        assert msg["tray_uuid"] is None


# ---------------------------------------------------------------------------
# Group 2: PATCH /spoolman/inventory/spools/{id}/tag endpoint
# ---------------------------------------------------------------------------


class TestLinkTagToSpoolmanSpool:
    """PATCH /spoolman/inventory/spools/{id}/tag writes an NFC tag into Spoolman extra.tag."""

    def _mock_client(self, spool_id: int) -> MagicMock:
        client = MagicMock()
        client.base_url = "http://localhost:7912"
        # get_all_spools returns empty list — no duplicate tags in Spoolman.
        client.get_all_spools = AsyncMock(return_value=[])
        client.get_spool = AsyncMock(return_value=_spoolman_spool(spool_id))
        client.update_spool_full = AsyncMock(return_value=_spoolman_spool(spool_id))
        return client

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_tag_uid_writes_to_extra_tag(self, async_client: AsyncClient):
        """PATCH with tag_uid writes uppercased tag_uid to Spoolman extra.tag."""
        import json as _json

        mock_client = self._mock_client(42)

        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.patch(
                f"{INVENTORY_API}/spools/42/tag",
                json={"tag_uid": "aabb1122334455ff"},
            )

        assert resp.status_code == 200
        mock_client.update_spool_full.assert_called_once()
        _, kwargs = mock_client.update_spool_full.call_args
        assert kwargs.get("extra", {}).get("tag") == _json.dumps("AABB1122334455FF")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tray_uuid_takes_precedence_over_tag_uid(self, async_client: AsyncClient):
        """tray_uuid takes precedence when both tag_uid and tray_uuid are provided."""
        import json as _json

        mock_client = self._mock_client(7)

        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.patch(
                f"{INVENTORY_API}/spools/7/tag",
                json={
                    "tag_uid": "AABB1122334455FF",
                    "tray_uuid": "deadbeefdeadbeefdeadbeefdeadbeef",
                },
            )

        assert resp.status_code == 200
        mock_client.update_spool_full.assert_called_once()
        _, kwargs = mock_client.update_spool_full.call_args
        assert kwargs.get("extra", {}).get("tag") == _json.dumps("DEADBEEFDEADBEEFDEADBEEFDEADBEEF")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_neither_tag_uid_nor_tray_uuid_returns_422(self, async_client: AsyncClient):
        """422 Unprocessable Entity when neither tag_uid nor tray_uuid is provided."""
        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=MagicMock()),
        ):
            resp = await async_client.patch(
                f"{INVENTORY_API}/spools/1/tag",
                json={},
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_not_found_returns_404(self, async_client: AsyncClient):
        """404 when Spoolman reports the spool does not exist."""
        mock_client = MagicMock()
        mock_client.get_all_spools = AsyncMock(return_value=[])
        mock_client.get_spool = AsyncMock(side_effect=SpoolmanNotFoundError("Spool 999 not found"))

        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.patch(
                f"{INVENTORY_API}/spools/999/tag",
                json={"tag_uid": "AABB1122334455FF"},
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_unavailable_returns_503(self, async_client: AsyncClient):
        """503 when Spoolman is unreachable during the tag link (duplicate check fails first)."""
        mock_client = MagicMock()
        mock_client.get_all_spools = AsyncMock(side_effect=SpoolmanUnavailableError("Spoolman down"))

        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.patch(
                f"{INVENTORY_API}/spools/42/tag",
                json={"tag_uid": "AABB1122334455FF"},
            )

        assert resp.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_400_when_spoolman_disabled(self, async_client: AsyncClient):
        """400 when Spoolman integration is not enabled (no settings in DB)."""
        resp = await async_client.patch(
            f"{INVENTORY_API}/spools/42/tag",
            json={"tag_uid": "AABB1122334455FF"},
        )

        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_4_byte_uid_writes_to_extra_tag(self, async_client: AsyncClient):
        """PATCH with 8-char (4-byte Bambu Lab) tag_uid writes correctly to Spoolman extra.tag."""
        import json as _json

        mock_client = self._mock_client(42)

        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.patch(
                f"{INVENTORY_API}/spools/42/tag",
                json={"tag_uid": "2728C17B"},  # 4-byte / 8-char Bambu Lab hardware UID
            )

        assert resp.status_code == 200
        mock_client.update_spool_full.assert_called_once()
        _, kwargs = mock_client.update_spool_full.call_args
        assert kwargs.get("extra", {}).get("tag") == _json.dumps("2728C17B")

"""``connected`` describes Spoolman, not this process's memory (issue #2903).

``GET /spoolman/status`` used to report ``connected`` by looking for a client
object left behind by some earlier request. Around twenty call sites build one
lazily, so the answer turned on which page had been loaded rather than on
anything about Spoolman -- and the Settings page builds one as a side effect of
saving, which is how enabling the integration came to report "connected" before
anything had been set up.

The UI reads the flag twice, offering the Connect button only while
disconnected and the AMS sync section only while connected, so an answer that
depends on request ordering puts those two controls into states the user cannot
predict or explain.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.fixture
async def spoolman_enabled(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
async def spoolman_disabled_but_configured(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="false"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


def _client(*, healthy: bool = True, base_url: str = "http://localhost:7912") -> MagicMock:
    client = MagicMock()
    client.base_url = base_url
    client.health_check = AsyncMock(return_value=healthy)
    return client


def _patch(get_returns, init_returns=None, init_side_effect=None):
    """Patch the route module's client accessors."""
    init = AsyncMock(return_value=init_returns, side_effect=init_side_effect)
    return (
        patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=get_returns)),
        patch("backend.app.api.routes.spoolman.init_spoolman_client", init),
        init,
    )


class TestItAsksSpoolmanRatherThanItself:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_it_reports_connected_without_a_prior_client(self, async_client: AsyncClient, spoolman_enabled):
        """Nothing has built a client yet -- the status must still be the truth."""
        healthy = _client()
        get_patch, init_patch, init = _patch(None, init_returns=healthy)

        with get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.status_code == 200
        assert response.json()["connected"] is True
        init.assert_awaited_once_with("http://localhost:7912")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_it_asks_the_url_configured_now_not_the_one_cached(self, async_client: AsyncClient, spoolman_enabled):
        """A client left pointing at the previous URL must not answer for the new one."""
        stale = _client(base_url="http://old-host:7912")
        fresh = _client()
        get_patch, init_patch, init = _patch(stale, init_returns=fresh)

        with get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.json()["connected"] is True
        init.assert_awaited_once_with("http://localhost:7912")
        stale.health_check.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_matching_client_is_reused(self, async_client: AsyncClient, spoolman_enabled):
        existing = _client()
        get_patch, init_patch, init = _patch(existing)

        with get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.json()["connected"] is True
        init.assert_not_awaited()
        existing.health_check.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_unreachable_spoolman_reports_disconnected(self, async_client: AsyncClient, spoolman_enabled):
        """The Connect button is a retry affordance, so this is the case that shows it."""
        get_patch, init_patch, _ = _patch(_client(healthy=False))

        with get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.json() == {
            "enabled": True,
            "connected": False,
            "url": "http://localhost:7912",
        }


class TestItStaysQuietWhenThereIsNothingToAsk:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_disabled_integration_is_never_probed(
        self, async_client: AsyncClient, spoolman_disabled_but_configured
    ):
        """A stale client used to make a switched-off integration report "Connected"."""
        leftover = _client()
        get_patch, init_patch, init = _patch(leftover)

        with get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.json()["enabled"] is False
        assert response.json()["connected"] is False
        leftover.health_check.assert_not_awaited()
        init.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_url_configured_is_not_probed(self, async_client: AsyncClient, db_session):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        await db_session.commit()
        get_patch, init_patch, init = _patch(None)

        with get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.json()["connected"] is False
        init.assert_not_awaited()


class TestWhenTheUrlCannotBeUsed:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_ssrf_rejected_url_reports_disconnected_rather_than_erroring(
        self, async_client: AsyncClient, spoolman_enabled
    ):
        """The guard raises ValueError; a status poll must not become a 500."""
        get_patch, init_patch, _ = _patch(None, init_side_effect=ValueError("blocked"))

        with get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.status_code == 200
        assert response.json()["connected"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_ssrf_rejection_says_so_rather_than_reading_as_a_generic_fault(
        self, async_client: AsyncClient, spoolman_enabled, caplog
    ):
        """A rejected URL is the admin's to fix, so the log has to name it.

        Both failure branches return the same body, so behaviour alone cannot
        tell them apart -- only the line each one logs can, and a URL the guard
        refuses needs different words from a client that would not open.
        """
        get_patch, init_patch, _ = _patch(None, init_side_effect=ValueError("blocked"))

        with caplog.at_level("WARNING"), get_patch, init_patch:
            await async_client.get("/api/v1/spoolman/status")

        assert "SSRF guard" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_client_that_cannot_be_opened_reports_disconnected(
        self, async_client: AsyncClient, spoolman_enabled, caplog
    ):
        """Replacing a client closes the old one, and httpx's aclose() may raise.

        A poll that runs every 30 seconds must not answer 500 when it can
        answer the truth instead -- and must still say why in the log.
        """
        get_patch, init_patch, _ = _patch(None, init_side_effect=RuntimeError("event loop is closed"))

        with caplog.at_level("WARNING"), get_patch, init_patch:
            response = await async_client.get("/api/v1/spoolman/status")

        assert response.status_code == 200
        assert response.json()["connected"] is False
        assert "Could not open a Spoolman client" in caplog.text
        assert "SSRF guard" not in caplog.text

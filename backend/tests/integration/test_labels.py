"""Integration tests for the spool-label routes (#809).

Covers both ``POST /inventory/labels`` (local DB) and ``POST /spoolman/labels``
(Spoolman-backed). The renderer itself has its own unit tests; these tests
focus on auth, request validation, mode gating, and the wiring between route
and renderer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    """Factory to create test spools."""
    _counter = [0]

    async def _create_spool(**kwargs):
        _counter[0] += 1
        defaults = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Polymaker",
            "color_name": f"Test {_counter[0]}",
            "rgba": "FF8800FF",
            "label_weight": 1000,
            "weight_used": 0,
        }
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create_spool


# ── /inventory/labels (local DB) ─────────────────────────────────────────────


class TestLocalInventoryLabels:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_renders_pdf_for_local_spools(self, async_client: AsyncClient, spool_factory):
        s1 = await spool_factory()
        s2 = await spool_factory(material="PETG", brand="Sunlu")

        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [s1.id, s2.id], "template": "box_62x29"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")
        assert int(resp.headers["content-length"]) == len(resp.content)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_all_four_templates_succeed(self, async_client: AsyncClient, spool_factory):
        s = await spool_factory()
        for template in ("ams_30x15", "box_62x29", "avery_5160", "avery_l7160"):
            resp = await async_client.post(
                "/api/v1/inventory/labels",
                json={"spool_ids": [s.id], "template": template},
            )
            assert resp.status_code == 200, f"{template} returned {resp.status_code}: {resp.text}"
            assert resp.content.startswith(b"%PDF")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_template_rejected(self, async_client: AsyncClient, spool_factory):
        s = await spool_factory()
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [s.id], "template": "totally_made_up"},
        )
        # Pydantic Literal validation → 422
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_spool_ids_rejected(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [], "template": "box_62x29"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_spool_id_returns_404(self, async_client: AsyncClient, spool_factory):
        s = await spool_factory()
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [s.id, 99999], "template": "ams_30x15"},
        )
        assert resp.status_code == 404
        assert "99999" in resp.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preserves_request_order(self, async_client: AsyncClient, spool_factory):
        """Caller's `spool_ids` order should match the on-screen list — important
        for Avery sheet layouts where users curate the layout via filtering."""
        s1 = await spool_factory()
        s2 = await spool_factory()
        s3 = await spool_factory()

        # Reverse order; assert the route doesn't sort them. We can't peek
        # inside the PDF for assertion, but we can call render_labels directly
        # under the same patches and compare bytes deterministically.
        from backend.app.api.routes import labels as labels_module

        captured = {}

        original = labels_module.render_labels

        def _capture(template, data_list):
            captured["ids"] = [d.spool_id for d in data_list]
            return original(template, data_list)

        with patch.object(labels_module, "render_labels", side_effect=_capture):
            resp = await async_client.post(
                "/api/v1/inventory/labels",
                json={"spool_ids": [s3.id, s1.id, s2.id], "template": "avery_l7160"},
            )
        assert resp.status_code == 200
        assert captured["ids"] == [s3.id, s1.id, s2.id]


# ── /spoolman/labels (Spoolman-backed) ───────────────────────────────────────


class TestSpoolmanLabels:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_400_when_spoolman_disabled(self, async_client: AsyncClient):
        # Default state in tests: spoolman_enabled is unset / "false"
        resp = await async_client.post(
            "/api/v1/spoolman/labels",
            json={"spool_ids": [1], "template": "box_62x29"},
        )
        assert resp.status_code == 400
        assert "Spoolman" in resp.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_503_when_spoolman_unreachable(self, async_client: AsyncClient, db_session: AsyncSession):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        await db_session.commit()

        with patch("backend.app.api.routes.labels.get_spoolman_client", AsyncMock(return_value=None)):
            resp = await async_client.post(
                "/api/v1/spoolman/labels",
                json={"spool_ids": [1], "template": "box_62x29"},
            )
        assert resp.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_renders_pdf_from_spoolman_data(self, async_client: AsyncClient, db_session: AsyncSession):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        await db_session.commit()

        spoolman_spool = {
            "id": 42,
            "filament": {
                "name": "PolyTerra Sapphire Blue",
                "material": "PLA",
                "color_hex": "0033AA",
                "vendor": {"name": "Polymaker"},
            },
            "location": "Shelf 5, slot C",
        }
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.get_spools = AsyncMock(return_value=[spoolman_spool])

        with patch(
            "backend.app.api.routes.labels.get_spoolman_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.post(
                "/api/v1/spoolman/labels",
                json={"spool_ids": [42], "template": "avery_l7160"},
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_404_when_spool_missing_from_spoolman(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.get_spools = AsyncMock(return_value=[{"id": 1, "filament": {"name": "X", "material": "PLA"}}])

        with patch(
            "backend.app.api.routes.labels.get_spoolman_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.post(
                "/api/v1/spoolman/labels",
                json={"spool_ids": [99], "template": "box_62x29"},
            )
        assert resp.status_code == 404
        assert "99" in resp.text


# ── Validation cross-cutting ─────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_request_body_size_capped(self, async_client: AsyncClient):
        """spool_ids is bounded to MAX_LABELS_PER_REQUEST so a runaway client
        can't flood the renderer."""
        from backend.app.api.routes.labels import MAX_LABELS_PER_REQUEST

        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={
                "spool_ids": list(range(1, MAX_LABELS_PER_REQUEST + 2)),
                "template": "box_62x29",
            },
        )
        assert resp.status_code == 422

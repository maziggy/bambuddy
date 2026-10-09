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
        for template in (
            "ams_holder_74x33",
            "ams_holder_75x55",
            "box_62x29",
            "avery_5160",
            "avery_l7160",
            "avery_3490",
        ):
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
            json={"spool_ids": [s.id, 99999], "template": "ams_holder_74x33"},
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

        def _capture(template, data_list, **kwargs):
            captured["ids"] = [d.spool_id for d in data_list]
            return original(template, data_list, **kwargs)

        with patch.object(labels_module, "render_labels", side_effect=_capture):
            resp = await async_client.post(
                "/api/v1/inventory/labels",
                json={"spool_ids": [s3.id, s1.id, s2.id], "template": "avery_l7160"},
            )
        assert resp.status_code == 200
        assert captured["ids"] == [s3.id, s1.id, s2.id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forwards_sheet_starting_position(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()

        from backend.app.api.routes import labels as labels_module

        captured = {}
        original = labels_module.render_labels

        def _capture(template, data_list, **kwargs):
            captured["starting_position"] = kwargs["starting_position"]
            return original(template, data_list, **kwargs)

        with patch.object(labels_module, "render_labels", side_effect=_capture):
            resp = await async_client.post(
                "/api/v1/inventory/labels",
                json={"spool_ids": [spool.id], "template": "avery_5160", "starting_position": 8},
            )

        assert resp.status_code == 200
        assert captured["starting_position"] == 8


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
    @pytest.mark.parametrize(
        ("template", "starting_position"),
        (("avery_5160", 0), ("avery_5160", 31), ("avery_l7160", 22), ("box_62x29", 2)),
    )
    async def test_invalid_starting_position_rejected(
        self,
        async_client: AsyncClient,
        template: str,
        starting_position: int,
    ):
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [1], "template": template, "starting_position": starting_position},
        )
        assert resp.status_code == 422

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


# ── Fields, PNG and preview (#2981) ──────────────────────────────────────────


def _spoolman_on(db_session: AsyncSession):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))


def _spoolman_client(spools: list[dict]) -> MagicMock:
    client = MagicMock()
    client.is_connected = True
    client.get_spools = AsyncMock(return_value=spools)
    return client


class TestLabelFieldsAndFormats:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_omitted_fields_print_the_default_set(self, async_client: AsyncClient, spool_factory):
        from backend.app.api.routes import labels as labels_module
        from backend.app.services.label_renderer import DEFAULT_LABEL_FIELDS

        spool = await spool_factory()
        captured = {}
        original = labels_module.render_labels

        def _capture(template, data_list, **kwargs):
            captured["fields"] = kwargs["fields"]
            return original(template, data_list, **kwargs)

        with patch.object(labels_module, "render_labels", side_effect=_capture):
            resp = await async_client.post(
                "/api/v1/inventory/labels", json={"spool_ids": [spool.id], "template": "box_40x30"}
            )
        assert resp.status_code == 200
        assert captured["fields"] == DEFAULT_LABEL_FIELDS

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chosen_fields_and_new_spool_data_reach_the_renderer(self, async_client: AsyncClient, spool_factory):
        from backend.app.api.routes import labels as labels_module

        spool = await spool_factory(material_number="MN-15", nozzle_temp_min=190, nozzle_temp_max=230, note="Dry first")
        captured = {}
        original = labels_module.render_labels

        def _capture(template, data_list, **kwargs):
            captured["fields"] = kwargs["fields"]
            captured["data"] = data_list[0]
            return original(template, data_list, **kwargs)

        with patch.object(labels_module, "render_labels", side_effect=_capture):
            resp = await async_client.post(
                "/api/v1/inventory/labels",
                json={
                    "spool_ids": [spool.id],
                    "template": "box_40x30",
                    "fields": ["temps", "material_number", "temps"],
                },
            )
        assert resp.status_code == 200
        assert captured["fields"] == {"temps", "material_number"}
        data = captured["data"]
        assert (data.material_number, data.nozzle_temp_min, data.nozzle_temp_max) == ("MN-15", 190, 230)
        assert (data.label_weight, data.note) == (1000, "Dry first")
        assert data.added is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_field_rejected(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [spool.id], "template": "box_40x30", "fields": ["brand", "password"]},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("body", [{"format": "gif"}, {"format": "png", "dpi": 72}, {"spool_ids": [0]}])
    async def test_invalid_output_options_rejected(self, async_client: AsyncClient, body: dict):
        resp = await async_client.post(
            "/api/v1/inventory/labels", json={"spool_ids": [1], "template": "box_40x30", **body}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_one_label_as_png(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [spool.id], "template": "box_40x30", "format": "png", "dpi": 203},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert "bambuddy-labels-box_40x30.png" in resp.headers["content-disposition"]
        assert resp.content.startswith(b"\x89PNG")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_several_roll_labels_come_as_a_zip_named_by_spool(self, async_client: AsyncClient, spool_factory):
        import io
        import zipfile

        s1 = await spool_factory()
        s2 = await spool_factory()
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            json={"spool_ids": [s2.id, s1.id], "template": "box_62x29", "format": "png"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert zf.namelist() == [f"label-{s2.id}.png", f"label-{s1.id}.png"]
            assert all(zf.read(n).startswith(b"\x89PNG") for n in zf.namelist())

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sheet_pngs_are_numbered_by_page(self, async_client: AsyncClient, spool_factory):
        import io
        import zipfile

        s1 = await spool_factory()
        s2 = await spool_factory()
        resp = await async_client.post(
            "/api/v1/inventory/labels",
            # 21 per L7160 page; starting at 21 puts the second label on page two.
            json={
                "spool_ids": [s1.id, s2.id],
                "template": "avery_l7160",
                "format": "png",
                "starting_position": 21,
            },
        )
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert zf.namelist() == ["sheet-1.png", "sheet-2.png"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preview_is_one_png(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()
        resp = await async_client.post(
            "/api/v1/inventory/labels/preview",
            json={"spool_id": spool.id, "template": "avery_5160", "fields": ["brand", "qr"]},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.headers["cache-control"] == "no-store"
        assert resp.content.startswith(b"\x89PNG")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preview_of_unknown_spool_is_404(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/labels/preview", json={"spool_id": 99999, "template": "box_40x30"}
        )
        assert resp.status_code == 404


class TestSpoolmanLabelParity:
    """A Spoolman spool's label is built from the same mapping the inventory
    page shows, so it carries what a built-in spool's label does."""

    SPOOL = {
        "id": 7,
        "filament": {
            "name": "PLA Matte",
            "material": "PLA",
            "color_hex": "000000",
            "vendor": {"name": "Bambu Lab"},
            "article_number": "MN-15",
            "settings_extruder_temp": 220,
            "weight": 1000,
        },
        "extra": {"bambu_color_name": '"Charcoal"'},
        "comment": "Dry first",
        "registered": "2026-09-01T10:00:00Z",
        "location": "Shelf 2",
    }

    async def _captured(self, async_client: AsyncClient, db_session: AsyncSession, spool: dict):
        from backend.app.api.routes import labels as labels_module

        _spoolman_on(db_session)
        await db_session.commit()
        captured = {}
        original = labels_module.render_labels

        def _capture(template, data_list, **kwargs):
            captured["data"] = data_list[0]
            return original(template, data_list, **kwargs)

        with (
            patch.object(labels_module, "render_labels", side_effect=_capture),
            patch(
                "backend.app.api.routes.labels.get_spoolman_client",
                AsyncMock(return_value=_spoolman_client([spool])),
            ),
        ):
            resp = await async_client.post(
                "/api/v1/spoolman/labels", json={"spool_ids": [spool["id"]], "template": "box_40x30"}
            )
        assert resp.status_code == 200
        return captured["data"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_carries_subtype_colour_name_and_the_new_fields(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        from datetime import date

        data = await self._captured(async_client, db_session, self.SPOOL)
        assert (data.material, data.subtype, data.name, data.brand) == ("PLA", "Matte", "Charcoal", "Bambu Lab")
        assert (data.material_number, data.nozzle_temp_min, data.label_weight) == ("MN-15", 220, 1000)
        assert (data.note, data.added, data.storage_location) == ("Dry first", date(2026, 9, 1), "Shelf 2")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_a_colour_name_the_filament_name_is_used(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """As before: the name line falls back to the filament name, not to the
        subtype the mapping synthesises as a colour name."""
        spool = {**self.SPOOL, "extra": {}}
        data = await self._captured(async_client, db_session, spool)
        assert data.name == "PLA Matte"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preview_uses_spoolman(self, async_client: AsyncClient, db_session: AsyncSession):
        _spoolman_on(db_session)
        await db_session.commit()
        with patch(
            "backend.app.api.routes.labels.get_spoolman_client",
            AsyncMock(return_value=_spoolman_client([self.SPOOL])),
        ):
            resp = await async_client.post(
                "/api/v1/spoolman/labels/preview", json={"spool_id": 7, "template": "box_40x30"}
            )
        assert resp.status_code == 200
        assert resp.content.startswith(b"\x89PNG")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preview_refused_when_spoolman_is_off(self, async_client: AsyncClient):
        resp = await async_client.post("/api/v1/spoolman/labels/preview", json={"spool_id": 7, "template": "box_40x30"})
        assert resp.status_code == 400

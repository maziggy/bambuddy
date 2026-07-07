"""Unit tests for persisting SpoolCode rows discovered via barcode/SKU resolution.

`_persist_spool_codes` stores the scanned/typed code (`is_primary=True`) plus
every sibling discovered via cross-referencing (see `_resolve_barcode`/
`_external_all_codes` in `routes/inventory.py`), deduped on (spool_id, code).
`_persist_barcode_codes_for_spool` wires that up to a live external
cross-reference lookup for the create/update/bulk-create write paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.api.routes.inventory import _persist_barcode_codes_for_spool, _persist_spool_codes
from backend.app.models.spool import Spool
from backend.app.models.spool_code import SpoolCode


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Spool.__table__.create)
        await conn.run_sync(SpoolCode.__table__.create)
    yield eng
    await eng.dispose()


async def _insert_spool(session: AsyncSession, spool_id: int) -> None:
    session.add(
        Spool(
            id=spool_id,
            material="PLA",
            label_weight=1000,
            core_weight=250,
            weight_used=0,
            weight_used_baseline=0,
            weight_locked=False,
        )
    )
    await session.commit()


async def _codes_for(session: AsyncSession, spool_id: int) -> list[SpoolCode]:
    result = await session.execute(select(SpoolCode).where(SpoolCode.spool_id == spool_id))
    return list(result.scalars().all())


class TestPersistSpoolCodes:
    async def test_persists_primary_and_siblings(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await _persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[
                    {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                    {"code": "6938936716786", "kind": "gtin", "is_refill": True},
                    {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                ],
            )
            codes = await _codes_for(session, 1)

        by_code = {c.code: c for c in codes}
        assert set(by_code) == {"6938936716785", "6938936716786", "ALZMNTABS01"}
        assert by_code["6938936716785"].is_primary is True
        assert by_code["6938936716786"].is_primary is False
        assert by_code["6938936716786"].is_refill is True
        assert by_code["ALZMNTABS01"].kind == "sku"

    async def test_dedupes_against_existing_rows(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            session.add(SpoolCode(spool_id=1, code="6938936716785", kind="gtin", is_primary=True))
            await session.commit()

            await _persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[{"code": "6938936716785", "kind": "gtin", "is_refill": False}],
            )
            codes = await _codes_for(session, 1)

        assert len(codes) == 1

    async def test_no_siblings_still_persists_primary(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await _persist_spool_codes(
                session, spool_id=1, primary_code="ALZMNTABS01", primary_kind="sku", all_codes=[]
            )
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].code == "ALZMNTABS01"
        assert codes[0].kind == "sku"
        assert codes[0].is_primary is True

    async def test_second_call_with_new_siblings_adds_only_new_rows(self, engine):
        """A later scan that discovers additional sibling codes for an already-
        persisted primary must add just the new rows, not duplicate existing ones."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await _persist_spool_codes(
                session, spool_id=1, primary_code="6938936716785", primary_kind="gtin", all_codes=[]
            )
            await _persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[
                    {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                    {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                ],
            )
            codes = await _codes_for(session, 1)

        assert {c.code for c in codes} == {"6938936716785", "ALZMNTABS01"}


class TestPersistBarcodeCodesForSpool:
    async def test_no_barcode_is_a_no_op(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch("backend.app.api.routes.inventory._external_all_codes", new=AsyncMock()) as mock_external:
                await _persist_barcode_codes_for_spool(session, spool_id=1, barcode=None)
            mock_external.assert_not_called()
            assert await _codes_for(session, 1) == []

    async def test_persists_primary_plus_cross_referenced_siblings(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            external = (
                {"material": "PLA"},
                "ofd",
                [
                    {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                    {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                ],
            )
            with patch("backend.app.api.routes.inventory._external_all_codes", new=AsyncMock(return_value=external)):
                await _persist_barcode_codes_for_spool(session, spool_id=1, barcode="06938936716785")
            codes = await _codes_for(session, 1)

        assert {c.code for c in codes} == {"6938936716785", "ALZMNTABS01"}
        primary = next(c for c in codes if c.code == "6938936716785")
        assert primary.is_primary is True

    async def test_no_external_hit_still_persists_scanned_code_alone(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch("backend.app.api.routes.inventory._external_all_codes", new=AsyncMock(return_value=None)):
                await _persist_barcode_codes_for_spool(session, spool_id=1, barcode="ALZMNTABS01")
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].code == "ALZMNTABS01"
        assert codes[0].kind == "sku"

    async def test_cross_reference_failure_still_persists_primary_alone(self, engine):
        """An external-lookup exception must degrade to persisting just the
        scanned code, not lose it entirely (mirrors _resolve_barcode's own
        try/except-around-external-lookups behavior)."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch(
                "backend.app.api.routes.inventory._external_all_codes",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                await _persist_barcode_codes_for_spool(session, spool_id=1, barcode="ALZMNTABS01")
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].code == "ALZMNTABS01"
        assert codes[0].kind == "sku"

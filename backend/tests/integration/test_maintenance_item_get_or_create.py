"""A maintenance row created on first read is taken, not duplicated (FO-C14).

Two reads of a new printer's maintenance -- two dashboards, or a dashboard
and the background check -- could each create the same row. With the unique
index the second insert is refused; the read then uses the row the other one
created, and its session carries on.
"""

import pytest
from sqlalchemy import func, select

from backend.app.api.routes.maintenance import _get_or_create_maintenance_item, ensure_default_types
from backend.app.models.maintenance import MaintenanceType, PrinterMaintenance

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_the_row_another_read_created_is_used(db_session, printer_factory):
    printer = await printer_factory()
    await ensure_default_types(db_session)
    type_id = (await db_session.scalars(select(MaintenanceType.id).limit(1))).first()
    theirs = PrinterMaintenance(
        printer_id=printer.id, maintenance_type_id=type_id, enabled=True, last_performed_hours=3.0
    )
    db_session.add(theirs)
    await db_session.commit()

    item = await _get_or_create_maintenance_item(db_session, printer.id, type_id)

    assert item.id == theirs.id
    count = await db_session.scalar(
        select(func.count()).select_from(PrinterMaintenance).where(PrinterMaintenance.printer_id == printer.id)
    )
    assert count == 1
    # The refused insert was rolled back to its savepoint only.
    await db_session.commit()


async def test_a_missing_row_is_created(db_session, printer_factory):
    printer = await printer_factory()
    await ensure_default_types(db_session)
    type_id = (await db_session.scalars(select(MaintenanceType.id).limit(1))).first()

    item = await _get_or_create_maintenance_item(db_session, printer.id, type_id)

    assert item.id is not None
    assert (item.enabled, item.last_performed_hours) == (True, 0.0)


async def test_a_second_assign_racing_the_first_is_refused_as_assigned(db_session, printer_factory):
    """Both pass the "already assigned?" check (a double click); the index now
    refuses the second insert, which answers the route's own 400, not a 500."""
    from unittest.mock import MagicMock, patch

    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.api.routes.maintenance import assign_maintenance_type

    printer = await printer_factory()
    await ensure_default_types(db_session)
    type_id = (await db_session.scalars(select(MaintenanceType.id).limit(1))).first()
    db_session.add(PrinterMaintenance(printer_id=printer.id, maintenance_type_id=type_id, enabled=True))
    await db_session.commit()

    real_execute = AsyncSession.execute

    async def check_sees_nothing(self, statement, *args, **kwargs):
        sql = str(statement)
        if "FROM printer_maintenance" in sql and "maintenance_type_id" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        return await real_execute(self, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", check_sees_nothing), pytest.raises(HTTPException) as refused:
        await assign_maintenance_type(printer.id, type_id, db_session, None)

    assert refused.value.status_code == 400

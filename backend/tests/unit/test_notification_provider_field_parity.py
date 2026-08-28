"""Every notification toggle column must be reachable through the API (#2945).

The defect this pins is not a wrong value, it is a field that exists everywhere
except the modules a toggle has to cross. `on_stock_reorder_alert` and
`on_stock_break_alert` had model columns, service producers, templates, UI
toggles and a frontend test asserting the PATCH — and no schema field, so
Pydantic dropped them, the PATCH answered 200, and nothing was written. #1184
introduced that gap and every layer it did touch worked, which is why it went
unnoticed for months.

These are structural checks of structural facts: a column that is absent from a
schema cannot be sent at all, whatever the routes do with it afterwards. The
behaviour behind them — create, both read routes and PATCH, over the same
derived column list — is covered through the API in
`backend/tests/integration/test_notifications_api.py`.

The two schemas here are the whole of it. The update route needs nothing
beyond `NotificationProviderUpdate`, because it applies changes with a generic
`model_dump(exclude_unset=True)` + `setattr` loop rather than a third
hand-maintained map: once the field survives the schema, it is written.
"""

from __future__ import annotations

import pytest

from backend.app.models.notification import NotificationProvider
from backend.app.schemas.notification import (
    NotificationProviderCreate,
    NotificationProviderUpdate,
)

EVENT_COLUMNS = sorted(c.name for c in NotificationProvider.__table__.columns if c.name.startswith("on_"))


def test_there_are_event_columns_to_check() -> None:
    """Guard the guard: an empty enumeration would make every test below vacuous."""
    assert len(EVENT_COLUMNS) > 20


@pytest.mark.parametrize("column", EVENT_COLUMNS)
def test_every_event_column_is_settable_on_create(column: str) -> None:
    """Absent from the Create schema, the field is silently dropped from the POST."""
    assert column in NotificationProviderCreate.model_fields


@pytest.mark.parametrize("column", EVENT_COLUMNS)
def test_every_event_column_is_settable_on_update(column: str) -> None:
    """This is the one the report hit: the PATCH succeeds and writes nothing."""
    assert column in NotificationProviderUpdate.model_fields

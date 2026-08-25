"""Every notification toggle column must be reachable through the API (#2945).

The defect this pins is not a wrong value, it is a field that exists everywhere
except the modules a toggle has to cross. `on_stock_reorder_alert` and
`on_stock_break_alert` had model columns, service producers, templates, UI
toggles and a frontend test asserting the PATCH — and no schema field, so
Pydantic dropped them, the PATCH answered 200, and nothing was written. #1184
introduced that gap and every layer it did touch worked, which is why it went
unnoticed for months.

Both directions of notifications.py are hand-maintained field-by-field maps, so
this is a standing hazard rather than one incident: `on_ha_sensor_alert` reached
the schema but neither map, and was in that state at the same time. Enumerating
the columns is the only check that fails on the *next* one of these rather than
on the one already reported.
"""

from __future__ import annotations

import inspect

import pytest

from backend.app.api.routes import notifications as notifications_routes
from backend.app.models.notification import NotificationProvider
from backend.app.schemas.notification import (
    NotificationProviderBase,
    NotificationProviderUpdate,
)

EVENT_COLUMNS = sorted(c.name for c in NotificationProvider.__table__.columns if c.name.startswith("on_"))


def test_there_are_event_columns_to_check() -> None:
    """Guard the guard: an empty enumeration would make every test below vacuous."""
    assert len(EVENT_COLUMNS) > 20


@pytest.mark.parametrize("column", EVENT_COLUMNS)
def test_every_event_column_is_settable_on_create(column: str) -> None:
    """Absent from the Create schema, the field is silently dropped from the POST."""
    assert column in NotificationProviderBase.model_fields


@pytest.mark.parametrize("column", EVENT_COLUMNS)
def test_every_event_column_is_settable_on_update(column: str) -> None:
    """This is the one the report hit: the PATCH succeeds and writes nothing."""
    assert column in NotificationProviderUpdate.model_fields


@pytest.mark.parametrize("column", EVENT_COLUMNS)
def test_every_event_column_is_serialised_in_the_response(column: str) -> None:
    """Absent from _provider_to_dict, the response model fills the field from its
    default, so a stored True reads back as False and the toggle appears to have
    forgotten itself."""
    source = inspect.getsource(notifications_routes._provider_to_dict)
    assert f'"{column}": provider.{column},' in source


@pytest.mark.parametrize("column", EVENT_COLUMNS)
def test_every_event_column_is_passed_through_on_create(column: str) -> None:
    """The create route builds the model field by field rather than from a dump,
    so a column missing here takes its database default however the POST was
    written."""
    source = inspect.getsource(notifications_routes.create_notification_provider)
    assert f"{column}=provider_data.{column}," in source

"""The per-event notification toggles, derived from the model's own columns (#2945).

A hardcoded list only covers the toggles someone remembered to add to it, so it
carries the same hazard as the field maps it is meant to police, one layer up.
Derived, a toggle added tomorrow is exercised the day its column lands.

This lives here rather than in either test module because the unit parity checks
and the integration round-trips need the same two answers, and a derivation
duplicated across files is a list with extra steps.
"""

from __future__ import annotations

from sqlalchemy import Boolean
from sqlalchemy.sql.schema import Column

from backend.app.models.notification import NotificationProvider

# The Boolean check is not decoration. An on_* column of some other type would
# turn `{field: True}` into a 422 and break every test parametrised off this
# list, in whichever unrelated PR happened to add that column -- and a spurious
# failure is how a derivation gets edited until it passes.
_EVENT_TOGGLES: list[Column] = [
    column
    for column in NotificationProvider.__table__.columns
    if column.name.startswith("on_") and isinstance(column.type, Boolean)
]

EVENT_TOGGLE_COLUMNS: list[str] = sorted(column.name for column in _EVENT_TOGGLES)


def _model_default(column: Column) -> bool:
    """The value a row gets for this column when the caller sends nothing."""
    if column.default is None:
        return False
    return bool(column.default.arg)


# The value each toggle has to be driven to for the assertion to mean anything.
#
# Nine of these columns default to True on the model and on the response schema,
# so a test that sends True and asserts True is answered by the default alone:
# a field dropped by a hand-maintained map still reads back True, and the
# mutation that would prove the map is load-bearing survives. Driving each
# toggle to whatever its default is not makes the round trip the only thing that
# can supply the answer.
TOGGLE_TARGET: dict[str, bool] = {column.name: not _model_default(column) for column in _EVENT_TOGGLES}

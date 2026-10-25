"""Update schemas treat an explicit null for a NOT NULL column as "unchanged" (schemas/not_null.py).

The route tests are in tests/integration/test_patch_null_on_required_fields.py.
The guard below keeps every PATCH/PUT body that writes an ORM row on the mixin,
so a new route can't bring the 500 back.
"""

from fastapi.routing import APIRoute
from pydantic import Field

from backend.app.schemas.not_null import IgnoresNullForRequiredColumns, _table

# PATCH/PUT bodies that don't update a row of their own, and why.
NOT_A_ROW_UPDATE = {
    "AppSettingsUpdate": "key/value settings; rejects null itself",
    "SupplierUpdate": "rejects a null name itself with a 422 (its only NOT NULL field)",
    "ArchivePurgeSettings": "a setting, every field required",
    "TrashSettings": "a setting, every field required",
    "DisplaySettingsRequest": "SpoolBuddy device display, every field required",
    "ManyfoldConfigUpdate": "stored as settings",
    "CostCenterBudgetUpdateRequest": "replaces the budget rows as a whole",
    "AmsLabelBody": "upserts a label, every field required",
    "LinkTagRequest": "links a tag through its own rules",
    "ShoppingListItemStatusUpdate": "one required field",
    "ExternalLinkReorder": "a list of ids",
    "ExternalLinkBadgeSet": "an app's count for its links, every field required",
    "SlicerSettingUpdate": "a Bambu Cloud setting, not a database row",
    "SpoolmanInventoryUpdate": "a Spoolman spool, not a database row",
    "SpoolWeightUpdate": "a Spoolman spool, not a database row",
    "SpoolTagLinkRequest": "a Spoolman spool, not a database row",
    "SpoolmanFilamentPatch": "a Spoolman filament, not a database row",
    "FilamentProfileDefaultWrite": "replaces a default as a whole; its validator reads a null key field as empty",
}


def test_every_row_update_body_ignores_null_for_required_columns():
    from backend.app.main import app

    unguarded = []
    for route in app.routes:
        if not (isinstance(route, APIRoute) and route.methods & {"PATCH", "PUT"} and route.body_field):
            continue
        schema = route.body_field.field_info.annotation
        if not isinstance(schema, type) or not hasattr(schema, "model_fields"):
            continue  # a list or dict body
        if issubclass(schema, IgnoresNullForRequiredColumns) or schema.__name__ in NOT_A_ROW_UPDATE:
            continue
        unguarded.append(f"{schema.__name__} ({route.path})")
    assert unguarded == []


def test_every_declared_table_exists():
    from backend.app.main import app

    tables = {
        route.body_field.field_info.annotation.__orm_table__
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.body_field
        and isinstance(route.body_field.field_info.annotation, type)
        and issubclass(route.body_field.field_info.annotation, IgnoresNullForRequiredColumns)
    }
    assert tables
    for name in tables:
        _table(name)  # raises KeyError for a misspelt table


class _PlugUpdate(IgnoresNullForRequiredColumns):
    __orm_table__ = "smart_plugs"

    name: str | None = None  # NOT NULL column
    username: str | None = None  # nullable column
    note_for_client: str | None = None  # no column at all
    # NOT NULL column; routes write it under the field name, clients may send the alias
    off_delay_minutes: int | None = Field(default=None, alias="offDelay")


def test_null_for_a_not_null_column_counts_as_not_sent():
    update = _PlugUpdate(name=None, username="x")
    assert update.model_dump(exclude_unset=True) == {"username": "x"}


def test_null_for_a_nullable_column_clears_it():
    update = _PlugUpdate(username=None)
    assert update.model_dump(exclude_unset=True) == {"username": None}


def test_null_for_a_field_with_no_column_is_left_alone():
    assert _PlugUpdate(note_for_client=None).note_for_client is None


def test_a_field_sent_under_its_alias_is_checked_by_its_column():
    assert _PlugUpdate.model_validate({"offDelay": None}).model_dump(exclude_unset=True) == {}


def test_leaving_a_field_out_is_not_null():
    assert _PlugUpdate.model_validate({"username": "x"}).model_dump(exclude_unset=True) == {"username": "x"}

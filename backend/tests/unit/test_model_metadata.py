"""The schema has to be sortable, because backup and restore sort it.

`metadata.sorted_tables` is asked for the order in three places: the backup
export, the restore's import loop, and the loop that puts foreign keys back
afterwards. Three nullable SET NULL links used to close a loop --
print_archives.library_file_id -> library_files.folder_id ->
library_folders.archive_id -> print_archives -- and SQLAlchemy answered a
sort it could not make, with a warning on every backup and every restore:

    Cannot correctly sort tables; there are unresolvable cycles between
    tables "library_files, library_folders, print_archives" ... this warning
    may raise an error in a future release.

Two things were wrong with living on that. The order it returns can place a
child before its parent, which is what once imported library_files ahead of
library_folders and killed a restore on a ForeignKeyViolation. And the
sentence at the end is a promise: if it ever becomes an error, backup and
restore break on the same upgrade.

One edge of the loop is marked use_alter, which takes it out of the sort
graph without taking the constraint out of the database.
"""

from __future__ import annotations

import importlib
import pkgutil
import warnings

from sqlalchemy import create_engine, inspect

from backend.app.core.database import Base


def _all_models_imported() -> None:
    """Base.metadata is filled by imports, so a partial import means a partial
    schema -- and a cycle in a table nobody imported would not be found here."""
    import backend.app.models as models

    for module in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"backend.app.models.{module.name}")


def test_the_schema_sorts_without_a_cycle_warning():
    _all_models_imported()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert Base.metadata.sorted_tables

    cycles = [str(w.message) for w in caught if "cycles" in str(w.message)]
    assert not cycles, (
        f"a new foreign key has closed a loop in the schema; backup and restore sort these tables: {cycles}"
    )


def test_the_tables_that_used_to_cycle_sort_parents_first():
    """The property the warning took away. Order is what the restore's import
    loop follows, and a child ahead of its parent is a FK violation."""
    _all_models_imported()

    order = [t.name for t in Base.metadata.sorted_tables]

    assert order.index("library_folders") < order.index("library_files"), (
        "library_files.folder_id points at library_folders"
    )
    assert order.index("library_files") < order.index("print_archives"), (
        "print_archives.library_file_id points at library_files"
    )


def test_the_altered_constraint_still_exists_on_sqlite():
    """use_alter asks for ALTER TABLE ADD CONSTRAINT, and SQLite has no such
    statement. It inlines the key into CREATE TABLE instead -- but if that ever
    stopped being true, deleting an archive would leave a dangling
    library_folders.archive_id rather than nulling it, silently."""
    _all_models_imported()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    keys = inspect(engine).get_foreign_keys("library_folders")

    archive_link = [k for k in keys if k["referred_table"] == "print_archives"]
    assert archive_link, f"library_folders lost its archive key: {keys}"
    assert archive_link[0]["constrained_columns"] == ["archive_id"]
    assert archive_link[0]["options"].get("ondelete") == "SET NULL"

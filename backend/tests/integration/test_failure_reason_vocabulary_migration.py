"""Migration tests for issue #2974 — one vocabulary for ``failure_reason``.

Three writers used to put three spellings of one cause into the column: the
backend wrote English display labels ("Layer shift"), older builds of the
archive editor wrote the *translated* label in whatever locale that user was
running, and two stale-archive paths wrote English prose sentences. The Failure
Analysis widget groups on the raw column, so one real cause occupied several
buckets.

Measured on a live install before this landed: ``print_log_entries`` held 91
rows reading ``"User cancelled"`` beside 1 reading ``"userCancelled"``. In an
English UI those render as the same words twice with different counts, which is
why nobody spotted it; in any other locale one of the two stays English.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import (
    _LEGACY_FAILURE_REASON_LABELS,
    _migrate_failure_reason_vocabulary,
)
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry

# The columns each model needs beyond ``failure_reason``. Rows stay minimal on
# purpose -- these tests exercise the UPDATE, not the schema.
_REQUIRED = {
    # nosec B108 - a column value, not a path anything opens. The row exists
    # to be UPDATEd; nothing in these tests touches the filesystem.
    "PrintArchive": {"filename": "x.3mf", "file_path": "/tmp/x.3mf", "file_size": 1},  # nosec B108
    "PrintLogEntry": {},
}


async def _seed(session: AsyncSession, model, values: list[str | None]) -> list[int]:
    """Insert one row per value through the ORM and return their ids, in order."""
    required = _REQUIRED[model.__name__]
    rows = [model(status="failed", failure_reason=v, **required) for v in values]
    session.add_all(rows)
    await session.commit()
    ids = []
    for row in rows:
        await session.refresh(row)
        ids.append(row.id)
    return ids


async def _read(session: AsyncSession, model, ids: list[int]) -> list[str | None]:
    """Read ``failure_reason`` back for ``ids``, in the order given."""
    session.expire_all()
    result = await session.execute(select(model.id, model.failure_reason).where(model.id.in_(ids)))
    got = {row[0]: row[1] for row in result.fetchall()}
    return [got[i] for i in ids]


async def _run(session: AsyncSession) -> None:
    """Drive the migration over the session's own connection.

    TEST_DATABASE_URL is in-memory SQLite on a shared pool, so opening a second
    connection would not see the seeded rows -- and production calls this with
    an ``AsyncConnection`` inside an open transaction anyway, which is exactly
    what ``session.connection()`` hands over.
    """
    await _migrate_failure_reason_vocabulary(await session.connection())
    await session.commit()


# ---------------------------------------------------------------------------
# The map itself
# ---------------------------------------------------------------------------


def test_every_mapped_value_is_a_canonical_key() -> None:
    """A label may only ever fold onto a key the rest of the stack accepts."""
    from backend.app.api.routes.print_log import _FAILURE_REASON_KEYS

    offenders = sorted(set(_LEGACY_FAILURE_REASON_LABELS.values()) - _FAILURE_REASON_KEYS)
    assert not offenders, f"map targets values nothing else recognises: {offenders}"


def test_the_map_is_unambiguous() -> None:
    """No label may resolve to two different keys.

    This is what makes the conversion exact rather than a guess, and it is the
    property that let the migration be written at all -- the reporter's open
    question was what to do with a value matching no key.
    """
    assert len(_LEGACY_FAILURE_REASON_LABELS) == len(set(_LEGACY_FAILURE_REASON_LABELS))


def test_the_map_covers_every_writer_that_ever_existed() -> None:
    """The three historical vocabularies, by example."""
    m = _LEGACY_FAILURE_REASON_LABELS
    # 1. Backend English display labels.
    assert m["Layer shift"] == "layerShift"
    assert m["Filament runout"] == "filamentRunout"
    assert m["Clogged nozzle"] == "cloggedNozzle"
    assert m["User cancelled"] == "userCancelled"
    # 2. Legacy archive-editor writes of a *translated* label. Not English --
    #    that is the whole reason a locale-dependent reverse lookup could not
    #    fix this on read.
    assert m["Schichtversatz"] == "layerShift"
    assert m["Сдвиг слоёв"] == "layerShift"
    # 3. The two stale-path prose sentences.
    assert m["Stale - print likely cancelled or failed without status update"] == "noStatusUpdate"
    assert m["Stale - reconciled after reconnect, end time unknown"] == "noStatusUpdate"


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [PrintArchive, PrintLogEntry])
async def test_labels_fold_onto_keys(db_session: AsyncSession, model) -> None:
    ids = await _seed(db_session, model, ["Layer shift", "Schichtversatz", "layerShift"])
    await _run(db_session)
    assert await _read(db_session, model, ids) == ["layerShift", "layerShift", "layerShift"]


@pytest.mark.parametrize("model", [PrintArchive, PrintLogEntry])
async def test_the_live_split_collapses(db_session: AsyncSession, model) -> None:
    """The exact shape measured on the maintainer's instance."""
    ids = await _seed(db_session, model, ["User cancelled"] * 3 + ["userCancelled"])
    await _run(db_session)
    assert set(await _read(db_session, model, ids)) == {"userCancelled"}


@pytest.mark.parametrize("model", [PrintArchive, PrintLogEntry])
async def test_both_stale_sentences_become_one_key(db_session: AsyncSession, model) -> None:
    ids = await _seed(
        db_session,
        model,
        [
            "Stale - print likely cancelled or failed without status update",
            "Stale - reconciled after reconnect, end time unknown",
        ],
    )
    await _run(db_session)
    assert await _read(db_session, model, ids) == ["noStatusUpdate", "noStatusUpdate"]


@pytest.mark.parametrize("model", [PrintArchive, PrintLogEntry])
async def test_unrecognised_values_are_left_alone(db_session: AsyncSession, model) -> None:
    """Free text and NULL survive untouched.

    Guessing at a value the map does not know would be worse than leaving one
    honest string in its own bucket -- it still renders through the
    ``defaultValue`` fallback in the editor and the Statistics breakdown.
    """
    ids = await _seed(db_session, model, ["Custom legacy reason", None, ""])
    await _run(db_session)
    assert await _read(db_session, model, ids) == ["Custom legacy reason", None, ""]


@pytest.mark.parametrize("model", [PrintArchive, PrintLogEntry])
async def test_running_twice_changes_nothing(db_session: AsyncSession, model) -> None:
    """Self-terminating, which is why it carries no one-shot settings flag.

    A user restoring an older database, or upgrading through this version
    twice, must still get their legacy rows converted -- a flag would skip them
    forever.
    """
    ids = await _seed(db_session, model, ["Layer shift", "Custom legacy reason"])
    await _run(db_session)
    first = await _read(db_session, model, ids)
    await _run(db_session)
    assert await _read(db_session, model, ids) == first == ["layerShift", "Custom legacy reason"]

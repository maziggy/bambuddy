"""A project saved as ``.gcode.3mf`` is still a project (#3435).

Studio saves an unsliced project under whatever name it is given. One called
``Foo.gcode.3mf`` landed in the library as a printable file, and printing it to
a cost centre was refused with "Estimated cost is required for cost center
prints" -- eleven times, eleven duplicate uploads, nothing saying why. The file
holds no G-code, so there is nothing to estimate and nothing to print.
"""

import zipfile

import pytest
from sqlalchemy import text

from backend.app.api.routes.library import classify_file_type
from backend.app.models.library import LibraryFile
from backend.app.utils.threemf_tools import is_unsliced_3mf

SLICED = ["3D/3dmodel.model", "Metadata/plate_1.gcode", "Metadata/slice_info.config"]
PROJECT = ["3D/3dmodel.model", "Metadata/slice_info.config", "Metadata/project_settings.config"]


def _zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    return path


class TestClassification:
    def test_the_reported_file_is_a_project(self, tmp_path):
        path = _zip(tmp_path / "ImageToStl.com_Unbenannt.gcode.3mf", PROJECT)
        assert classify_file_type(path.name, path) == "3mf"

    def test_a_sliced_file_stays_sliced(self, tmp_path):
        path = _zip(tmp_path / "Benchy.gcode.3mf", SLICED)
        assert classify_file_type(path.name, path) == "gcode.3mf"

    def test_a_file_that_cannot_be_read_goes_by_its_name(self, tmp_path):
        broken = tmp_path / "x.gcode.3mf"
        broken.write_bytes(b"PK\x03\x04truncated")
        assert is_unsliced_3mf(broken) is False
        assert is_unsliced_3mf(tmp_path / "absent.gcode.3mf") is False
        assert classify_file_type("x.gcode.3mf", broken) == "gcode.3mf"


async def _library_file(
    db_session, tmp_path, monkeypatch, *, names, filename="Unbenannt.gcode.3mf", file_type="gcode.3mf"
):
    from backend.app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    _zip(tmp_path / filename, names)
    lib_file = LibraryFile(
        filename=filename,
        file_path=filename,
        file_type=file_type,
        file_size=1,
        file_hash=filename,
        source_type="uploaded",
        file_metadata={"sliced_for_model": "H2D"},
    )
    db_session.add(lib_file)
    await db_session.commit()
    return lib_file


@pytest.mark.asyncio
@pytest.mark.integration
class TestTheQueueSaysWhy:
    async def test_post_queue_names_the_cause(self, async_client, db_session, printer_factory, tmp_path, monkeypatch):
        printer = await printer_factory(model="H2D")
        lib_file = await _library_file(db_session, tmp_path, monkeypatch, names=PROJECT)

        response = await async_client.post(
            "/api/v1/queue/", json={"library_file_id": lib_file.id, "printer_id": printer.id}
        )

        assert response.status_code == 400
        assert (
            response.json()["detail"]
            == "Unbenannt.gcode.3mf isn't sliced yet. Slice it first, then print the sliced file."
        )

    async def test_ahead_of_the_cost_centre_message(
        self, async_client, db_session, printer_factory, tmp_path, monkeypatch
    ):
        """With billing on the estimate was the first thing missing."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="billing_enabled", value="true"))
        await db_session.commit()
        printer = await printer_factory(model="H2D")
        lib_file = await _library_file(db_session, tmp_path, monkeypatch, names=PROJECT)

        response = await async_client.post(
            "/api/v1/queue/", json={"library_file_id": lib_file.id, "printer_id": printer.id, "cost_center_id": 1}
        )

        assert response.status_code == 400
        assert "isn't sliced yet" in response.json()["detail"]

    async def test_a_sliced_file_is_queued(self, async_client, db_session, printer_factory, tmp_path, monkeypatch):
        printer = await printer_factory(model="H2D")
        lib_file = await _library_file(db_session, tmp_path, monkeypatch, names=SLICED, filename="Benchy.gcode.3mf")

        response = await async_client.post(
            "/api/v1/queue/", json={"library_file_id": lib_file.id, "printer_id": printer.id}
        )

        assert response.status_code == 200, response.text

    async def test_bulk_add_judges_the_content(self, async_client, db_session, printer_factory, tmp_path, monkeypatch):
        printer = await printer_factory(model="H2D")
        project = await _library_file(db_session, tmp_path, monkeypatch, names=PROJECT)
        # A sliced plate exported under a plain .3mf name prints (#2993).
        exported = await _library_file(
            db_session, tmp_path, monkeypatch, names=SLICED, filename="Plate.3mf", file_type="gcode.3mf"
        )

        response = await async_client.post(
            "/api/v1/library/files/add-to-queue", json={"file_ids": [project.id, exported.id], "printer_id": printer.id}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert [e["error"] for e in body["errors"]] == [
            "Unbenannt.gcode.3mf isn't sliced yet. Slice it first, then print the sliced file."
        ]
        assert [r["file_id"] for r in body["added"]] == [exported.id]


@pytest.mark.asyncio
class TestRowsAlreadyStored:
    @pytest.fixture(autouse=True)
    def _sqlite(self, monkeypatch):
        from backend.app.core import database as database_module, db_dialect

        monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
        monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
        monkeypatch.setattr(database_module, "is_sqlite", lambda: True)

    @pytest.fixture
    async def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import Base
        from backend.tests.unit.test_library_file_type_backfill_migration import _register_all_models

        _register_all_models()
        eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield eng
        await eng.dispose()

    async def _insert(self, conn, file_id, filename, path, file_type="gcode.3mf", external=0):
        await conn.execute(
            text(
                "INSERT INTO library_files (id, filename, file_path, file_type, file_size, is_external, print_count) "
                "VALUES (:id, :f, :p, :t, 0, :e, 0)"
            ),
            {"id": file_id, "f": filename, "p": str(path), "t": file_type, "e": external},
        )

    async def _types(self, engine):
        async with engine.connect() as conn:
            return dict((await conn.execute(text("SELECT id, file_type FROM library_files ORDER BY id"))).fetchall())

    async def test_only_readable_projects_are_re_typed(self, engine, tmp_path):
        from backend.app.core.database import run_migrations

        async with engine.begin() as conn:
            await self._insert(conn, 1, "Unbenannt.gcode.3mf", _zip(tmp_path / "a.gcode.3mf", PROJECT))
            await self._insert(conn, 2, "Benchy.gcode.3mf", _zip(tmp_path / "b.gcode.3mf", SLICED))
            await self._insert(conn, 3, "Gone.gcode.3mf", tmp_path / "missing.gcode.3mf")
            await self._insert(conn, 4, "Ext.gcode.3mf", _zip(tmp_path / "c.gcode.3mf", PROJECT), external=1)
        async with engine.begin() as conn:
            await run_migrations(conn)

        assert await self._types(engine) == {1: "3mf", 2: "gcode.3mf", 3: "gcode.3mf", 4: "gcode.3mf"}

    async def test_a_later_boot_does_not_turn_them_back(self, engine, tmp_path):
        """The #1600 name backfill used to run on every boot and would flip a
        re-typed project straight back to printable."""
        from backend.app.core.database import run_migrations

        async with engine.begin() as conn:
            await self._insert(conn, 1, "Unbenannt.gcode.3mf", _zip(tmp_path / "a.gcode.3mf", PROJECT))
        async with engine.begin() as conn:
            await run_migrations(conn)
        async with engine.begin() as conn:
            await run_migrations(conn)

        assert await self._types(engine) == {1: "3mf"}

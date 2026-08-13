"""Slicing a file on an external mount writes the result to that mount (#2810).

Uploads learned to respect external folders in #1112 and moves in its
follow-up; ``slice_and_persist`` was the last write path that still sent
everything to managed storage. It kept giving the new row the external
folder's ``folder_id``, so the sliced file appeared in the right folder in the
File Manager while the share it was supposed to land on stayed empty -- which
is why the bug could not be reproduced from the web UI at all.

The fallback cases matter as much as the happy path. A slice costs minutes of
CPU, so an unwritable mount must not throw the bytes away; it stores them in
the managed library and *says so*, because filing the output somewhere the user
is not looking with no signal is the failure this issue was made of.
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes.library import (
    _resolve_slice_destination,
    _unique_external_name,
    slice_and_persist,
)
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.schemas.slicer import SliceRequest
from backend.app.services.slicer_api import SliceResult


def _external_folder(path: Path, *, readonly: bool = False) -> LibraryFolder:
    return LibraryFolder(
        name="NAS",
        parent_id=None,
        is_external=True,
        external_path=str(path),
        external_readonly=readonly,
    )


class TestResolveSliceDestination:
    def test_managed_folder_keeps_the_uuid_name(self, tmp_path):
        folder = LibraryFolder(name="Models", parent_id=None, is_external=False)

        path, is_external, fallback = _resolve_slice_destination(folder, "Bidoof.gcode.3mf")

        assert is_external is False
        assert fallback is None
        # Managed storage is content-addressed by uuid: the display name lives
        # on the DB row, so two files of the same name can coexist.
        assert path.name.endswith(".gcode.3mf")
        assert path.name != "Bidoof.gcode.3mf"

    def test_no_folder_at_all_is_managed(self):
        path, is_external, fallback = _resolve_slice_destination(None, "Bidoof.gcode.3mf")

        assert is_external is False
        assert fallback is None
        assert path.name.endswith(".gcode.3mf")

    def test_writable_external_folder_gets_the_real_filename(self, tmp_path):
        mount = tmp_path / "share"
        mount.mkdir()

        path, is_external, fallback = _resolve_slice_destination(_external_folder(mount), "Bidoof.gcode.3mf")

        assert is_external is True
        assert fallback is None
        # The point of the whole fix: next to the source, under a name a human
        # can find on the share.
        assert path == mount / "Bidoof.gcode.3mf"

    def test_read_only_mount_falls_back_instead_of_failing(self, tmp_path):
        mount = tmp_path / "share"
        mount.mkdir()

        path, is_external, fallback = _resolve_slice_destination(
            _external_folder(mount, readonly=True), "Bidoof.gcode.3mf"
        )

        assert is_external is False
        assert fallback == "external_readonly"
        assert path.parent != mount

    def test_vanished_mount_falls_back(self, tmp_path):
        missing = tmp_path / "unplugged-nas"  # deliberately not created

        _path, is_external, fallback = _resolve_slice_destination(_external_folder(missing), "Bidoof.gcode.3mf")

        assert is_external is False
        assert fallback == "external_unreachable"

    def test_folder_with_no_path_configured_falls_back(self):
        folder = LibraryFolder(name="NAS", parent_id=None, is_external=True, external_path=None)

        _path, is_external, fallback = _resolve_slice_destination(folder, "Bidoof.gcode.3mf")

        assert is_external is False
        assert fallback == "external_no_path"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the write bit")
    def test_unwritable_mount_falls_back(self, tmp_path):
        mount = tmp_path / "share"
        mount.mkdir()
        mount.chmod(0o500)
        try:
            _path, is_external, fallback = _resolve_slice_destination(_external_folder(mount), "Bidoof.gcode.3mf")
        finally:
            mount.chmod(0o700)

        assert is_external is False
        assert fallback == "external_not_writable"

    def test_a_name_that_escapes_the_mount_lands_in_managed_storage(self, tmp_path):
        mount = tmp_path / "share"
        mount.mkdir()

        path, is_external, fallback = _resolve_slice_destination(_external_folder(mount), "../escaped.gcode.3mf")

        # Never write outside the configured mount, whatever the name claims.
        assert is_external is False
        assert fallback == "external_invalid_name"
        assert path.parent.resolve() != tmp_path.resolve()


class TestUniqueExternalName:
    def test_free_name_is_used_as_is(self, tmp_path):
        assert _unique_external_name(tmp_path, "Bidoof.gcode.3mf") == "Bidoof.gcode.3mf"

    def test_collision_suffixes_before_the_compound_extension(self, tmp_path):
        (tmp_path / "Bidoof.gcode.3mf").write_bytes(b"first slice")

        # Not "Bidoof.gcode (2).3mf" -- the whole ".gcode.3mf" is the extension
        # and splitting it would produce a name the printer path won't accept.
        assert _unique_external_name(tmp_path, "Bidoof.gcode.3mf") == "Bidoof (2).gcode.3mf"

    def test_it_keeps_counting_past_the_first_collision(self, tmp_path):
        (tmp_path / "Bidoof.gcode.3mf").write_bytes(b"first")
        (tmp_path / "Bidoof (2).gcode.3mf").write_bytes(b"second")

        assert _unique_external_name(tmp_path, "Bidoof.gcode.3mf") == "Bidoof (3).gcode.3mf"

    def test_re_slicing_never_overwrites_what_is_already_on_the_share(self, tmp_path):
        (tmp_path / "Bidoof.gcode.3mf").write_bytes(b"do not lose me")

        chosen = _unique_external_name(tmp_path, "Bidoof.gcode.3mf")

        assert (tmp_path / chosen).exists() is False
        assert (tmp_path / "Bidoof.gcode.3mf").read_bytes() == b"do not lose me"


class TestSliceAndPersistWritesToTheMount:
    """End to end through ``slice_and_persist`` with the slicer stubbed out."""

    @staticmethod
    def _patched_slicer(content: bytes = b"PK\x03\x04 not-a-real-3mf"):
        return patch(
            "backend.app.api.routes.library._run_slicer_with_fallback",
            AsyncMock(return_value=(SliceResult(content, 3600, 12.5, 4200.0), False)),
        )

    async def _slice_into(self, db_session, folder: LibraryFolder):
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        with self._patched_slicer():
            response = await slice_and_persist(
                db_session,
                model_bytes=b"source model",
                model_filename="Bidoof.3mf",
                folder_id=folder.id,
                extra_metadata=None,
                request=SliceRequest(printer_preset_id=1, process_preset_id=2, filament_preset_id=3),
                current_user_id=None,
            )
        file_row = await db_session.get(LibraryFile, response.library_file_id)
        return response, file_row

    @pytest.mark.asyncio
    async def test_the_bytes_land_on_the_share(self, db_session, tmp_path):
        mount = tmp_path / "share"
        mount.mkdir()

        response, file_row = await self._slice_into(db_session, _external_folder(mount))

        assert (mount / "Bidoof.gcode.3mf").exists()
        assert response.external_write_fallback is None
        # The row has to agree with the disk, or the next move/scan/delete
        # works on a path that isn't there.
        assert file_row.is_external is True
        assert file_row.file_path == str(mount / "Bidoof.gcode.3mf")
        assert file_row.filename == "Bidoof.gcode.3mf"

    @pytest.mark.asyncio
    async def test_the_row_records_the_suffixed_name_on_a_collision(self, db_session, tmp_path):
        mount = tmp_path / "share"
        mount.mkdir()
        (mount / "Bidoof.gcode.3mf").write_bytes(b"an earlier slice")

        _response, file_row = await self._slice_into(db_session, _external_folder(mount))

        assert file_row.filename == "Bidoof (2).gcode.3mf"
        assert file_row.file_path == str(mount / "Bidoof (2).gcode.3mf")
        assert (mount / "Bidoof.gcode.3mf").read_bytes() == b"an earlier slice"

    @pytest.mark.asyncio
    async def test_a_managed_folder_is_unaffected(self, db_session, tmp_path):
        folder = LibraryFolder(name="Models", parent_id=None, is_external=False)

        response, file_row = await self._slice_into(db_session, folder)

        assert response.external_write_fallback is None
        assert file_row.is_external is False
        # Managed rows stay relative to base_dir so the install stays portable.
        assert not Path(file_row.file_path).is_absolute()

    @pytest.mark.asyncio
    async def test_a_read_only_mount_still_yields_a_usable_file_and_says_why(self, db_session, tmp_path):
        mount = tmp_path / "share"
        mount.mkdir()

        response, file_row = await self._slice_into(db_session, _external_folder(mount, readonly=True))

        # Minutes of slicing must not be discarded because the mount is
        # read-only -- but the user has to learn where the file went.
        assert response.external_write_fallback == "external_readonly"
        assert file_row.is_external is False
        assert (file_row.file_metadata or {}).get("external_write_fallback") == "external_readonly"
        assert list(mount.iterdir()) == []

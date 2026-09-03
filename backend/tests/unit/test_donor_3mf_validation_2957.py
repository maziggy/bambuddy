"""A same-named 3MF is not automatically this print's 3MF (#2957).

When a print's own 3MF could not be fetched, the usage tracker looks for one in
the library or in a previous archive and matches on the filename stem. Bambu
Studio writes the printer-side filename from the project's ``Title`` metadata,
so every plate of a project reaches the printer under one name however the user
renamed the file on disk -- filename equality says almost nothing.

The reporter's archive 94 was handed archive 92's file that way. The real print
used one filament; the donor plate declared three, and three spools were debited
for material they never extruded. Nothing in the archive said the numbers were
someone else's.

The plate is the only sound discriminator available here, and these tests pin
both halves of it: a donor holding a different plate is refused, and an
all-plates export is refused unless it actually carries the plate that is
running. The tempting second check -- comparing the donor's filament count
against the slicer's ``ams_mapping`` -- is deliberately absent and has a test of
its own saying why: that field is indexed by the *project's* filament slots, so
a genuine single-filament print reports ``[0, -1, -1, -1]``.

Where the plate cannot be known at all, which is the reporter's own firmware,
the donor is still accepted on its name and a warning says so. That is the
honest limit of what the data supports.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.services.usage_tracker import (
    _donor_3mf_conflicts,
    _expected_plate_for_print,
    _resolve_3mf_fallback,
)


def _write_3mf(path: Path, *, plate: int, filaments: int) -> Path:
    """A single-plate 3MF declaring *filaments* filaments on plate *plate*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        f"<filament id='{i + 1}' type='PLA' color='#00AE42' used_g='10' used_m='3.4'/>" for i in range(filaments)
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            "<?xml version='1.0' encoding='UTF-8'?><config><plate>"
            f"<metadata key='index' value='{plate}'/>"
            f"<metadata key='prediction' value='3600'/>{rows}"
            "</plate></config>",
        )
    return path


def _write_multiplate_3mf(path: Path, plates: dict[int, int]) -> Path:
    """An all-plates export: ``{plate index: filament count}``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ""
    for plate, filaments in plates.items():
        rows = "".join(
            f"<filament id='{i + 1}' type='PLA' color='#00AE42' used_g='10' used_m='3.4'/>" for i in range(filaments)
        )
        body += f"<plate><metadata key='index' value='{plate}'/><metadata key='prediction' value='60'/>{rows}</plate>"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", f"<?xml version='1.0' encoding='UTF-8'?><config>{body}</config>")
    return path


class TestWhatRulesADonorOut:
    def test_a_donor_holding_a_different_plate(self, tmp_path):
        donor = _write_3mf(tmp_path / "donor.3mf", plate=2, filaments=1)

        conflict = _donor_3mf_conflicts(donor, expected_plate=1)

        assert conflict is not None
        assert "plate 2" in conflict

    def test_an_all_plates_export_without_the_running_plate(self, tmp_path):
        """Left alone this is the silent one: the plate is looked for
        downstream, found missing, and every filament in the file is summed onto
        a single plate's print."""
        donor = _write_multiplate_3mf(tmp_path / "donor.3mf", {1: 1, 2: 3})

        assert _donor_3mf_conflicts(donor, expected_plate=5) is not None

    def test_an_unreadable_donor_is_not_rejected_on_that_alone(self, tmp_path):
        """Refusing a file we merely could not parse would take the fallback
        away from every 3MF variant this parser does not understand -- and an
        unreadable file is not evidence about which plate it holds. "No plates
        found" must not be read as "not your plate". The parse failure surfaces
        downstream as "no filament usage data" instead.
        """
        donor = tmp_path / "broken.3mf"
        donor.write_bytes(b"PK\x03\x04not-really-a-3mf")

        assert _donor_3mf_conflicts(donor, expected_plate=None) is None
        assert _donor_3mf_conflicts(donor, expected_plate=2) is None


class TestWhatMustStillBeAccepted:
    def test_the_matching_plate(self, tmp_path):
        donor = _write_3mf(tmp_path / "donor.3mf", plate=2, filaments=2)

        assert _donor_3mf_conflicts(donor, expected_plate=2) is None

    def test_an_all_plates_export_that_carries_the_running_plate(self, tmp_path):
        """``peek_plate_index_in_3mf`` returns None for a multi-plate file --
        "which plate is this" has no answer (#2522) -- so the file is judged on
        whether it holds the plate instead."""
        donor = _write_multiplate_3mf(tmp_path / "donor.3mf", {1: 3, 2: 1})

        assert _donor_3mf_conflicts(donor, expected_plate=2) is None

    def test_nothing_known_accepts_anything(self, tmp_path):
        """The reporter's firmware echoes only the 3MF filename and the print
        was not one Bambuddy dispatched, so the plate is unknowable. Accepting
        is the pre-existing behaviour and stays -- refusing here would retire
        the fallback recovery this same issue asked for -- but it is logged."""
        donor = _write_3mf(tmp_path / "donor.3mf", plate=7, filaments=4)

        assert _donor_3mf_conflicts(donor, expected_plate=None) is None


class TestTheCheckThatIsDeliberatelyNotMade:
    def test_the_filament_count_is_not_compared(self, tmp_path):
        """A donor whose plate matches is accepted however many filaments it
        declares, and this is the load-bearing reason why.

        ``ams_mapping`` is indexed by the *project's* filament slots, not the
        plate's -- ``slot_to_tray[slot_id - 1]`` in the same module -- so a
        genuine single-filament print publishes ``[0, -1, -1, -1]``. Comparing
        its length against a plate's filament count would reject correct donors
        far more often than wrong ones, on every multi-filament project.
        """
        donor = _write_3mf(tmp_path / "donor.3mf", plate=2, filaments=3)

        assert _donor_3mf_conflicts(donor, expected_plate=2) is None


class TestWhereTheExpectationsComeFrom:
    def test_the_plate_column_wins_when_it_is_set(self):
        assert _expected_plate_for_print(3, "Metadata/plate_1.gcode") == 3

    def test_otherwise_the_gcode_path_the_printer_echoed(self):
        assert _expected_plate_for_print(None, "Metadata/plate_2.gcode") == 2

    def test_a_p1s_that_echoes_only_the_3mf_name_knows_no_plate(self):
        """Verbatim from the report: ``PRINT START detected - file:
        Desktop_Goose.gcode.3mf``. There is no plate in that."""
        assert _expected_plate_for_print(None, "Desktop_Goose.gcode.3mf") is None


@pytest.mark.asyncio
class TestTheLookupItself:
    async def _seed(self, engine, tmp_path, *, donor_plate: int):
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        donor_rel = "archive/1/donor.gcode.3mf"
        _write_3mf(tmp_path / donor_rel, plate=donor_plate, filaments=1)
        async with maker() as db:
            # A successfully archived print keeps the 3MF's name; the fallback
            # row keeps whatever the printer echoed, which here is the plate
            # path that tells us which plate is running.
            donor = PrintArchive(
                printer_id=1,
                filename="Trent.gcode.3mf",
                file_path=donor_rel,
                file_size=1,
                print_name="Trent",
                status="completed",
            )
            fallback = PrintArchive(
                printer_id=1,
                filename="Metadata/plate_2.gcode",
                file_path="",
                file_size=0,
                print_name="Trent",
                status="printing",
                extra_data={"no_3mf_available": True},
            )
            db.add_all([donor, fallback])
            await db.commit()
            await db.refresh(donor)
            await db.refresh(fallback)
            return maker, donor.id, fallback.id

    async def test_a_wrong_donor_is_refused(self, test_engine, tmp_path):
        maker, _, fallback_id = await self._seed(test_engine, tmp_path, donor_plate=1)
        async with maker() as db:
            archive = await db.get(PrintArchive, fallback_id)
            assert await _resolve_3mf_fallback(archive, db, tmp_path) is None

    async def test_a_matching_donor_is_still_used(self, test_engine, tmp_path):
        maker, _, fallback_id = await self._seed(test_engine, tmp_path, donor_plate=2)
        async with maker() as db:
            archive = await db.get(PrintArchive, fallback_id)
            resolved = await _resolve_3mf_fallback(archive, db, tmp_path)
            assert resolved is not None and resolved.name == "donor.gcode.3mf"

    async def test_the_library_branch_is_guarded_too(self, test_engine, tmp_path):
        """A library upload can be the wrong plate for exactly the same reason a
        previous archive can, and it is consulted first."""
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        _write_3mf(tmp_path / "library/Trent.3mf", plate=1, filaments=3)
        async with maker() as db:
            db.add(LibraryFile(filename="Trent.3mf", file_path="library/Trent.3mf", file_type="3mf", file_size=1))
            # The printer echoes the plate path, so the plate is knowable; the
            # search stem falls back to the print name, which is what finds the
            # library upload in the first place.
            archive = PrintArchive(
                printer_id=1,
                filename="Metadata/plate_2.gcode",
                file_path="",
                file_size=0,
                print_name="Trent",
                status="printing",
            )
            db.add(archive)
            await db.commit()
            await db.refresh(archive)

            assert await _resolve_3mf_fallback(archive, db, tmp_path) is None

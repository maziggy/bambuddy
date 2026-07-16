"""Reinstalling must not silently take away a writable path (#2544).

``ProtectSystem=strict`` means the unit's ``ReadWritePaths`` is the *complete*
list of places Bambuddy can write. An operator who backs up to a NAS adds their
share to it by hand — and both installers overwrite the unit file wholesale, so
that line used to vanish on the next install. The backups then failed with EROFS
every night, which looks like a NAS permission problem and is not one.

So the installers keep the operator's extra paths, and the unit says why they
matter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

INSTALLERS = ["install/install.sh", "spoolbuddy/install/install.sh"]


def _read(rel: str) -> str:
    path = REPO / rel
    assert path.is_file(), f"launcher moved or was removed: {rel}"
    return path.read_text()


class TestUnitTemplate:
    def test_readwritepaths_still_grants_the_three_app_dirs(self):
        unit = _read("deploy/bambuddy.service")
        line = next(line for line in unit.splitlines() if line.startswith("ReadWritePaths="))
        assert "DATA_DIR" in line and "LOG_DIR" in line and "INSTALL_PATH" in line

    def test_unit_explains_how_to_add_a_backup_share(self):
        """Whoever reads this unit next has to be able to work out why their NAS
        is read-only for the service but not for their shell.
        """
        unit = _read("deploy/bambuddy.service")
        assert "systemctl edit" in unit, "the unit should show how to add a writable path via a drop-in"


class TestInstallersPreserveCustomPaths:
    @pytest.mark.parametrize("installer", INSTALLERS)
    def test_generated_unit_appends_the_carried_over_paths(self, installer):
        script = _read(installer)
        line = next(line for line in script.splitlines() if line.startswith("ReadWritePaths="))
        assert "$extra_rw" in line, (
            f"{installer} writes ReadWritePaths without $extra_rw, so a NAS share the operator "
            "added to the unit is dropped on reinstall:\n" + line
        )

    @pytest.mark.parametrize("installer", INSTALLERS)
    def test_existing_unit_is_read_for_custom_paths_and_backed_up(self, installer):
        script = _read(installer)
        assert "ReadWritePaths=" in script and "extra_rw+=" in script, (
            f"{installer} no longer carries the previous unit's ReadWritePaths forward"
        )
        assert ".bak-" in script, f"{installer} overwrites the unit without backing it up first"

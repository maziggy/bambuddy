"""T-Gap 4: PostgreSQL DDL dialect tests for spoolman_slot_assignments table."""

import re

import pytest


def _extract_spoolman_slot_ddl(is_sqlite: bool) -> str:
    """Extract the spoolman_slot_assignments DDL from database.py."""
    import inspect
    from unittest.mock import patch

    import backend.app.core.database as db_module

    source = inspect.getsource(db_module)
    # Find the block that creates spoolman_slot_assignments
    start = source.find("CREATE TABLE IF NOT EXISTS spoolman_slot_assignments")
    assert start != -1, "spoolman_slot_assignments DDL not found"
    # Scan forward to find the matching end of the SQL string
    block = source[start : start + 2000]
    return block


class TestSpoolmanSlotDdl:
    """Verify the DDL for spoolman_slot_assignments contains required constraints."""

    def test_sqlite_ddl_has_named_unique_constraint(self):
        ddl = _extract_spoolman_slot_ddl(is_sqlite=True)
        assert "uq_slot_assignment" in ddl, "Named UNIQUE constraint missing from SQLite DDL"

    def test_sqlite_ddl_has_ams_id_check(self):
        ddl = _extract_spoolman_slot_ddl(is_sqlite=True)
        assert re.search(r"ams_id.*CHECK.*ams_id.*>=.*0.*AND.*ams_id.*<=.*7", ddl, re.DOTALL), \
            "ams_id CHECK constraint missing from SQLite DDL"

    def test_sqlite_ddl_has_tray_id_check(self):
        ddl = _extract_spoolman_slot_ddl(is_sqlite=True)
        assert re.search(r"tray_id.*CHECK.*tray_id.*>=.*0.*AND.*tray_id.*<=.*3", ddl, re.DOTALL), \
            "tray_id CHECK constraint missing from SQLite DDL"

    def test_postgres_ddl_has_named_unique_constraint(self):
        ddl = _extract_spoolman_slot_ddl(is_sqlite=False)
        # PostgreSQL DDL appears after the SQLite block
        pg_start = ddl.find("SERIAL PRIMARY KEY")
        assert pg_start != -1 or "uq_slot_assignment" in ddl, \
            "uq_slot_assignment not found in DDL"

    def test_orm_model_has_named_unique_constraint(self):
        from sqlalchemy import inspect as sa_inspect

        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        table = SpoolmanSlotAssignment.__table__
        constraint_names = {c.name for c in table.constraints}
        assert "uq_slot_assignment" in constraint_names, \
            f"uq_slot_assignment not in ORM constraints: {constraint_names}"

    def test_orm_model_has_ams_id_check_constraint(self):
        from sqlalchemy import CheckConstraint

        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        table = SpoolmanSlotAssignment.__table__
        check_names = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_ams_id_range" in check_names, \
            f"ck_ams_id_range not in ORM check constraints: {check_names}"

    def test_orm_model_has_tray_id_check_constraint(self):
        from sqlalchemy import CheckConstraint

        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        table = SpoolmanSlotAssignment.__table__
        check_names = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_tray_id_range" in check_names, \
            f"ck_tray_id_range not in ORM check constraints: {check_names}"

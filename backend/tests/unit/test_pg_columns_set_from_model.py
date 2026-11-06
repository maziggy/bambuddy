"""_migrate_pg_columns_to_model takes each listed column's default from the model.

It runs on PostgreSQL only (covered by scripts/check_postgres_upgrade.py); this
checks that the list still matches the models, which a model change could break
without any PostgreSQL at hand: a column without a server_default would be given
DEFAULT None.
"""

from sqlalchemy.dialects import postgresql

from backend.app.core.database import _PG_COLUMNS_SET_FROM_MODEL
from backend.tests.unit.test_code_quality import _load_all_models


def test_every_listed_column_exists_and_has_a_server_default():
    tables = _load_all_models().metadata.tables
    dialect = postgresql.dialect()
    compiler = dialect.ddl_compiler(dialect, None)
    missing = []
    for table, column in _PG_COLUMNS_SET_FROM_MODEL:
        model_column = tables[table].c.get(column) if table in tables else None
        if model_column is None:
            missing.append(f"{table}.{column}: not in the models")
        elif compiler.get_column_default_string(model_column) is None:
            missing.append(f"{table}.{column}: no server_default")
    assert missing == []

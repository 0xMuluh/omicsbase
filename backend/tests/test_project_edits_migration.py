from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_project_edits_migration_round_trips_on_sqlite():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    projects = sa.Table("projects", metadata, sa.Column("id", sa.String(36), primary_key=True))
    metadata.create_all(engine)
    migration_path = Path(__file__).parents[1] / "alembic" / "versions" / "0022_project_edits.py"
    spec = importlib.util.spec_from_file_location("project_edits_migration", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        columns = {column["name"] for column in sa.inspect(connection).get_columns("project_edits")}
        assert {"project_id", "transaction_id", "files", "diagnostics"} <= columns
        migration.downgrade()
        assert "project_edits" not in sa.inspect(connection).get_table_names()

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_note_execution_idempotency_migration_round_trips_on_sqlite():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    revisions = sa.Table(
        "note_cell_revisions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    executions = sa.Table(
        "note_cell_executions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["note_cell_revisions.id"]),
    )
    metadata.create_all(engine)
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0023_note_execution_idempotency.py"
    )
    spec = importlib.util.spec_from_file_location("note_execution_idempotency", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("note_cell_executions")}
        assert "idempotency_key" in columns
        constraints = inspector.get_unique_constraints("note_cell_executions")
        assert any(item["name"] == "uq_note_cell_execution_idempotency" for item in constraints)
        migration.downgrade()
        fresh_inspector = sa.inspect(connection)
        assert "idempotency_key" not in {
            column["name"] for column in fresh_inspector.get_columns("note_cell_executions")
        }

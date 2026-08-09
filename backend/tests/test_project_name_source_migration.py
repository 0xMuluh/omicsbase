"""The project-name provenance migration preserves legacy names safely."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration_module():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0021_project_name_source.py"
    )
    spec = importlib.util.spec_from_file_location("project_name_source_migration", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_backfills_and_round_trips_on_sqlite():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    projects = sa.Table(
        "projects",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
    )
    metadata.create_all(engine)

    migration = _migration_module()
    with engine.begin() as connection:
        connection.execute(
            projects.insert(),
            [
                {"id": "placeholder", "name": "New project"},
                {"id": "legacy", "name": "Existing Scientific Study"},
            ],
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        rows = dict(
            connection.execute(
                sa.text("SELECT id, name_source FROM projects ORDER BY id")
            ).all()
        )
        assert rows == {"legacy": "user", "placeholder": "default"}
        assert {column["name"] for column in sa.inspect(connection).get_columns("projects")} >= {
            "id",
            "name",
            "name_source",
        }

        migration.downgrade()
        assert {column["name"] for column in sa.inspect(connection).get_columns("projects")} == {
            "id",
            "name",
        }

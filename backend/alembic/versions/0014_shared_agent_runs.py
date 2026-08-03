"""Add shared durable AgentRun, RunEvent, and telemetry state."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014_shared_agent_runs"
down_revision: Union[str, None] = "0013_bioc_qmd_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    required_tables = {"agent_runs", "agent_run_events", "agent_run_telemetry"}
    if required_tables.issubset(existing_tables):
        return

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("note_thread_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_scope", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("resumable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["note_thread_id"], ["note_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("surface IN ('workspace', 'notes')", name="ck_agent_run_surface"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_scope",
            "idempotency_key",
            name="uq_agent_run_idempotency",
        ),
    )
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_owner_id", "agent_runs", ["owner_id"])
    op.create_index("ix_agent_runs_project_id", "agent_runs", ["project_id"])
    op.create_index("ix_agent_runs_note_thread_id", "agent_runs", ["note_thread_id"])
    op.create_index(
        "ix_agent_runs_surface_status_created",
        "agent_runs",
        ["surface", "status", "created_at"],
    )
    op.create_index("ix_agent_runs_tenant_created", "agent_runs", ["tenant_id", "created_at"])

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_agent_run_event_idempotency"),
    )
    op.create_index("ix_agent_run_events_run_id", "agent_run_events", ["run_id"])
    op.create_index(
        "ix_agent_run_events_run_sequence",
        "agent_run_events",
        ["run_id", "sequence"],
    )

    op.create_table(
        "agent_run_telemetry",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_run_telemetry_run_id", "agent_run_telemetry", ["run_id"])
    op.create_index(
        "ix_agent_run_telemetry_run_created",
        "agent_run_telemetry",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_agent_run_telemetry_kind_created",
        "agent_run_telemetry",
        ["kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_telemetry_kind_created", table_name="agent_run_telemetry")
    op.drop_index("ix_agent_run_telemetry_run_created", table_name="agent_run_telemetry")
    op.drop_index("ix_agent_run_telemetry_run_id", table_name="agent_run_telemetry")
    op.drop_table("agent_run_telemetry")
    op.drop_index("ix_agent_run_events_run_sequence", table_name="agent_run_events")
    op.drop_index("ix_agent_run_events_run_id", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_index("ix_agent_runs_tenant_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_surface_status_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_note_thread_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_project_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_owner_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_tenant_id", table_name="agent_runs")
    op.drop_table("agent_runs")


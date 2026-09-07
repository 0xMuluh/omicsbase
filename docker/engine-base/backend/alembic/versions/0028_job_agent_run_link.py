"""Link compatibility jobs to durable agent runs."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0028_job_agent_run_link"
down_revision: Union[str, None] = "0027_note_title_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "agent_run_id" not in columns:
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.add_column(sa.Column("agent_run_id", sa.String(length=36), nullable=True))
            batch_op.create_index("ix_jobs_agent_run_id", ["agent_run_id"], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "agent_run_id" in columns:
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_index("ix_jobs_agent_run_id")
            batch_op.drop_column("agent_run_id")

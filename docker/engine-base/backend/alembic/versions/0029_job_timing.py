"""Track compatibility job lifecycle timing and heartbeats."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0029_job_timing"
down_revision: Union[str, None] = "0028_job_agent_run_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    missing = [
        ("started_at", sa.DateTime(timezone=True)),
        ("finished_at", sa.DateTime(timezone=True)),
        ("heartbeat_at", sa.DateTime(timezone=True)),
    ]
    with op.batch_alter_table("jobs") as batch_op:
        for name, column_type in missing:
            if name not in columns:
                batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    present = {"started_at", "finished_at", "heartbeat_at"} & columns
    if not present:
        return
    with op.batch_alter_table("jobs") as batch_op:
        for name in ("heartbeat_at", "finished_at", "started_at"):
            if name in present:
                batch_op.drop_column(name)

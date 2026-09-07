"""Allow project surfaces on agent_runs for fleet generation telemetry.

Revision ID: 0026_agent_run_project_surface
Revises: 0025_bioc_knowledge_embeddings
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_agent_run_project_surface"
down_revision: Union[str, None] = "0025_bioc_knowledge_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint("ck_agent_run_surface", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_run_surface",
            "surface IN ('workspace', 'notes', 'project')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint("ck_agent_run_surface", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_run_surface",
            "surface IN ('workspace', 'notes')",
        )

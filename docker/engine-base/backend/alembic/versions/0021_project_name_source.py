"""Track whether a project name is default, generated, or user-owned.

Revision ID: 0021_project_name_source
Revises: 0020_knowledge_term_df_rebuild
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021_project_name_source"
down_revision: Union[str, None] = "0020_knowledge_term_df_rebuild"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column(
                "name_source",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'default'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_projects_name_source",
            "name_source IN ('default', 'auto', 'user')",
        )

    # Existing non-placeholder names have unknowable provenance. Treat them as
    # user-owned so deploying this migration cannot unexpectedly rename them.
    op.execute(
        sa.text(
            """
            UPDATE projects
            SET name_source = CASE
                WHEN trim(COALESCE(name, '')) = '' OR name = 'New project' THEN 'default'
                ELSE 'user'
            END
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_constraint("ck_projects_name_source", type_="check")
        batch_op.drop_column("name_source")

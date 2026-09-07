"""Track whether a note title is default, generated, or user-owned.

Revision ID: 0027_note_title_source
Revises: 0026_agent_run_project_surface
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0027_note_title_source"
down_revision: Union[str, None] = "0026_agent_run_project_surface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("note_threads") as batch_op:
        batch_op.add_column(
            sa.Column("title_source", sa.String(length=20), nullable=False, server_default="default")
        )
        batch_op.create_check_constraint(
            "ck_note_threads_title_source",
            "title_source IN ('default', 'auto', 'user')",
        )
    op.execute(
        sa.text(
            """
            UPDATE note_threads
            SET title_source = CASE
                WHEN lower(trim(COALESCE(title, ''))) = 'untitled note' THEN 'default'
                ELSE 'user'
            END
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("note_threads") as batch_op:
        batch_op.drop_constraint("ck_note_threads_title_source", type_="check")
        batch_op.drop_column("title_source")

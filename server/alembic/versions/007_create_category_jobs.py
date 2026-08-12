"""Create durable category classification jobs.

Revision ID: 007
Revises: 006
Create Date: 2026-08-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "category_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("memory_id", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("catalog_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("memory_hash", sa.String(255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_category_jobs_memory_id", "category_jobs", ["memory_id"])
    op.create_index("ix_category_jobs_state", "category_jobs", ["state"])
    op.create_index(
        "uq_category_jobs_active_memory",
        "category_jobs",
        ["memory_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'processing', 'retrying')"),
    )
    op.create_index(
        "ix_category_jobs_claim",
        "category_jobs",
        ["state", "next_attempt_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_category_jobs_claim", table_name="category_jobs")
    op.drop_index("uq_category_jobs_active_memory", table_name="category_jobs")
    op.drop_index("ix_category_jobs_state", table_name="category_jobs")
    op.drop_index("ix_category_jobs_memory_id", table_name="category_jobs")
    op.drop_table("category_jobs")

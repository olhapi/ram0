"""Add multi-user account lifecycle state.

Revision ID: 008
Revises: 007
Create Date: 2026-08-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "user_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "uq_user_invitations_pending_email",
        "user_invitations",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    op.add_column("category_jobs", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_index("ix_category_jobs_owner_id", "category_jobs", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_category_jobs_owner_id", table_name="category_jobs")
    op.drop_column("category_jobs", "owner_id")
    op.drop_index("uq_user_invitations_pending_email", table_name="user_invitations")
    op.drop_table("user_invitations")
    op.drop_column("users", "disabled_at")

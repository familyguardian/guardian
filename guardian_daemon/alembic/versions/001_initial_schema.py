"""Initial schema with proper session ID handling

Revision ID: 001
Revises:
Create Date: 2025-11-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial tables with proper schema."""

    # Sessions table - ID is autoincrement, NOT the logind session ID
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("logind_session_id", sa.String(length=255), nullable=True),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("desktop", sa.String(length=255), nullable=True),
        sa.Column("service", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "username", "date", "start_time", name="uq_user_date_start"
        ),
    )
    # Individual column indexes (from index=True in models)
    op.create_index(
        op.f("ix_sessions_username"), "sessions", ["username"], unique=False
    )
    op.create_index(op.f("ix_sessions_date"), "sessions", ["date"], unique=False)
    # Composite indexes
    op.create_index("idx_username_date", "sessions", ["username", "date"])
    op.create_index(
        "idx_username_logind", "sessions", ["username", "logind_session_id"]
    )

    # User settings table
    op.create_table(
        "user_settings",
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("settings", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )

    # Metadata table
    op.create_table(
        "meta",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    # History table
    op.create_table(
        "history",
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("total_screen_time", sa.Integer(), nullable=False),
        sa.Column("login_count", sa.Integer(), nullable=False),
        sa.Column("first_login", sa.String(length=50), nullable=True),
        sa.Column("last_logout", sa.String(length=50), nullable=True),
        sa.Column("quota_exceeded", sa.Integer(), nullable=False),
        sa.Column("bonus_time_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("username", "date"),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("history")
    op.drop_table("meta")
    op.drop_table("user_settings")
    op.drop_index("idx_username_logind", table_name="sessions")
    op.drop_index("idx_username_date", table_name="sessions")
    op.drop_index(op.f("ix_sessions_date"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_username"), table_name="sessions")
    op.drop_table("sessions")

"""Создать тикеты и комментарии.

Revision ID: 0001
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

status = postgresql.ENUM(
    "open",
    "in_progress",
    "waiting_client",
    "resolved",
    "closed",
    name="ticket_status",
    create_type=False,
)
priority = postgresql.ENUM(
    "low", "medium", "high", "critical", name="ticket_priority", create_type=False
)


def upgrade() -> None:
    status.create(op.get_bind(), checkfirst=True)
    priority.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "tickets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", status, nullable=False, server_default="open"),
        sa.Column("priority", priority, nullable=False, server_default="medium"),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("author_email", sa.String(320), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("assignee_email", sa.String(320), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in ("status", "author_id", "assignee_id"):
        op.create_index(f"ix_tickets_{column}", "tickets", [column])
    op.create_table(
        "comments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.BigInteger(),
            sa.ForeignKey("tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("author_email", sa.String(320), nullable=False),
        sa.Column("author_role", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_comments_ticket_id", "comments", ["ticket_id"])


def downgrade() -> None:
    op.drop_table("comments")
    op.drop_table("tickets")
    priority.drop(op.get_bind(), checkfirst=True)
    status.drop(op.get_bind(), checkfirst=True)

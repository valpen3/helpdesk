"""Create SLA policies, records, and event idempotency tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sla_policies",
        sa.Column("priority", sa.String(length=20), primary_key=True),
        sa.Column("response_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint("response_minutes > 0", name="ck_sla_policy_response_positive"),
        sa.CheckConstraint("resolution_minutes > 0", name="ck_sla_policy_resolution_positive"),
    )
    op.bulk_insert(
        sa.table(
            "sla_policies",
            sa.column("priority", sa.String()),
            sa.column("response_minutes", sa.Integer()),
            sa.column("resolution_minutes", sa.Integer()),
        ),
        [
            {"priority": "critical", "response_minutes": 15, "resolution_minutes": 240},
            {"priority": "high", "response_minutes": 60, "resolution_minutes": 480},
            {"priority": "medium", "response_minutes": 240, "resolution_minutes": 1440},
            {"priority": "low", "response_minutes": 480, "resolution_minutes": 4320},
        ],
    )
    op.create_table(
        "sla_records",
        sa.Column("ticket_id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_due", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_due", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "paused_total_sec", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "response_breached", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "resolution_breached",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'critical')",
            name="ck_sla_record_priority",
        ),
    )
    op.create_index("ix_sla_records_priority", "sla_records", ["priority"])
    op.create_index("ix_sla_records_created_at", "sla_records", ["created_at"])
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
    op.drop_index("ix_sla_records_created_at", table_name="sla_records")
    op.drop_index("ix_sla_records_priority", table_name="sla_records")
    op.drop_table("sla_records")
    op.drop_table("sla_policies")

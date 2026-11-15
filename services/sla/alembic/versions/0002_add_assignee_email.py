"""Keep the current assignee email for breach notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sla_records", sa.Column("assignee_email", sa.String(length=320)))


def downgrade() -> None:
    op.drop_column("sla_records", "assignee_email")

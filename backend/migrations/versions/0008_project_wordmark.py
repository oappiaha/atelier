"""Optional compact collection wordmark.

Revision: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN wordmark text")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN wordmark")

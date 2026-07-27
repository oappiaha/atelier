"""media.cutout_key — PhotoRoom background-removal derivative, mirroring
thumb_key (product decision "PhotoRoom × Wada", 2026-07-26: every image
entering Wada Studio gets a cutout first; cached once per sha).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media ADD COLUMN cutout_key text")


def downgrade() -> None:
    op.execute("ALTER TABLE media DROP COLUMN cutout_key")

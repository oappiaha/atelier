"""media.seg_subject — what the segmentation model saw (2026-08-06).

'single-product' | 'worn-on-model' | 'multiple-items', reported by Gemini in
the same call that finds regions (prompt v4). Drives the compositor's
residual-fill decision: a single product always fills to 100% of its
silhouette; on-model/collage bases never auto-fill (a person's skin must not
be recolored). NULL = scanned before v4 (legacy 30%-threshold behavior).

Revision: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media ADD COLUMN seg_subject text")


def downgrade() -> None:
    op.execute("ALTER TABLE media DROP COLUMN seg_subject")

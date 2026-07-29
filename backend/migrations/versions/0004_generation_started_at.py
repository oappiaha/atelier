"""studies.generation_started_at + colorways.generation_started_at — the
stuck-run watchdog (SHIP-1) needs to know WHEN a row entered 'generating';
§2 carries no updated-at timestamps, and created_at is plan/creation time
(a re-generate run on an old study would look instantly stuck without this).

Stamped by POST /generate + /generate-one (studies) and by the trie executor
per colorway; read by the on-request sweep in GET /studies/{id}/colorways.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE studies ADD COLUMN generation_started_at timestamptz")
    op.execute("ALTER TABLE colorways ADD COLUMN generation_started_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE studies DROP COLUMN generation_started_at")
    op.execute("ALTER TABLE colorways DROP COLUMN generation_started_at")

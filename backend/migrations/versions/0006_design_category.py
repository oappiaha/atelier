"""designs.category — the vault's top-level grouping (Bags, Shoes, Dresses…),
title-cased free text, nullable.

The Obsidian vault organizes designs under category folders; the importer has
carried a per-design category in its plan since the rework ("a future
`category` field will consume it" — this is that field). Nullable because
pre-import designs and hand-created ones don't have to declare one; the UI
only offers a category filter when at least two distinct values exist. Free
text (no CHECK/enum): categories are Beezy's folder names, not app vocabulary.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE designs ADD COLUMN category text")


def downgrade() -> None:
    op.execute("ALTER TABLE designs DROP COLUMN category")

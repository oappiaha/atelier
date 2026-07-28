"""gen_nodes per-step guard columns (M7-T1 gate condition 2).

The chain-drift gate (GO, 2026-07-27) requires every trie node to record the
outcome of its post-composite guards: byte-identity outside the feathered
mask support (lock_verified — §8.10's guarantee measured per step, not just
per colorway), plus ghost fraction and in-region median ΔE2000 for
observability. latency_ms mirrors the colorways column.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-27
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gen_nodes ADD COLUMN lock_verified boolean NOT NULL DEFAULT true")
    op.execute("ALTER TABLE gen_nodes ADD COLUMN ghost_fraction real")
    op.execute("ALTER TABLE gen_nodes ADD COLUMN delta_e_in real")
    op.execute("ALTER TABLE gen_nodes ADD COLUMN latency_ms integer")


def downgrade() -> None:
    op.execute("ALTER TABLE gen_nodes DROP COLUMN latency_ms")
    op.execute("ALTER TABLE gen_nodes DROP COLUMN delta_e_in")
    op.execute("ALTER TABLE gen_nodes DROP COLUMN ghost_fraction")
    op.execute("ALTER TABLE gen_nodes DROP COLUMN lock_verified")

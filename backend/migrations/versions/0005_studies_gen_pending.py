"""studies.gen_pending — how many colorway child tasks a generation run still
owes (PARALLEL-1: colorways fan out to one Celery task each).

A run no longer ends when "the task" returns: N children run concurrently and
the LAST one to finish must recompute the study's terminal status. Nothing in
§2 records how many children were dispatched — colorway rows cannot: a colorway
queued but not yet started is indistinguishable from a deferred 'planned' ghost,
and marking them 'generating' up front would make the SHIP-1 watchdog fail
never-started ghosts on a lost run.

Set by the enqueue site (POST /generate, /generate-one) inside the same
transaction that flips the study to 'generating'; decremented (floored at 0) by
every child in a finally, so a child that raises still hands the baton on. It is
a WHEN-to-recompute latch, not the answer: closure itself is a pure recompute
from the colorway rows, so an extra decrement can only cause an idempotent
re-close. The watchdog zeroes it when it declares a run dead.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE studies ADD COLUMN gen_pending smallint NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE studies DROP COLUMN gen_pending")

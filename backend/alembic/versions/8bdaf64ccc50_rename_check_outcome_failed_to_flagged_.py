"""rename check outcome failed to flagged in stored scans

Revision ID: 8bdaf64ccc50
Revises: 057785183867
Create Date: 2026-09-10 21:13:31.362732

The check outcome that means "the check ran and found a problem" was renamed
from ``failed`` to ``flagged`` — "failed" read as "the tool broke", which is
what ``errored`` already means. ``check_results`` is a JSONB column, so there
is no enum type to alter; only the stored rows carry the old string.

This does NOT touch ``scan_status`` or ``category_status`` — a scan or a
category genuinely can fail, and those keep the word.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8bdaf64ccc50'
down_revision: Union[str, Sequence[str], None] = '057785183867'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rewrite(old: str, new: str) -> None:
    # Rebuild each check_results array, swapping the outcome on the entries that
    # carry the old value. The containment guard skips rows with nothing to do,
    # including the empty-array rows that jsonb_agg would otherwise turn to NULL.
    op.execute(
        f"""
        UPDATE scans
        SET check_results = (
            SELECT jsonb_agg(
                CASE WHEN elem->>'outcome' = '{old}'
                     THEN jsonb_set(elem, '{{outcome}}', '"{new}"')
                     ELSE elem END
            )
            FROM jsonb_array_elements(check_results) AS elem
        )
        WHERE check_results @> '[{{"outcome": "{old}"}}]'
        """
    )


def upgrade() -> None:
    _rewrite("failed", "flagged")


def downgrade() -> None:
    _rewrite("flagged", "failed")

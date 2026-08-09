"""add report cache key to scans

Revision ID: 057785183867
Revises: c2aea0cb546b
Create Date: 2026-08-10 00:53:05.633659

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '057785183867'
down_revision: Union[str, Sequence[str], None] = 'c2aea0cb546b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no server_default, and both are deliberate. Null means "no
    # report has been stored for this scan", which is true of every row that
    # already exists — so there is nothing to backfill and no rewrite of the
    # table. Every existing scan simply renders once on its next request.
    op.add_column('scans', sa.Column('report_key', sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Drops the pointer, not the stored objects. Storage is outside the
    # database and outside a transaction, so a downgrade leaves the PDFs
    # orphaned in the bucket or the reports directory rather than deleting
    # files a re-upgrade might have wanted. They are a cache: unreferenced,
    # they cost space and nothing else.
    op.drop_column('scans', 'report_key')

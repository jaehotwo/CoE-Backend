"""Merge duplicate heads after langflow schema updates

Revision ID: a0b1c2d3e4f5
Revises: 8e9f0a1b2c3d
Create Date: 2025-09-23 12:00:00

"""
from typing import Sequence, Union

# Alembic helpers
from alembic import op
import sqlalchemy as sa  # noqa: F401  # imported for Alembic compatibility

# revision identifiers, used by Alembic.
revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, tuple[str, ...], None] = "8e9f0a1b2c3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Schema state already converged; placeholder merge."""
    pass


def downgrade() -> None:
    # Nothing to undo for a merge-only revision.
    pass

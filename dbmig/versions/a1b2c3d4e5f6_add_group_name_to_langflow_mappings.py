"""Add group_name and indexes to langflow_tool_mappings

Revision ID: a1b2c3d4e5f6
Revises: a0b1c2d3e4f5
Create Date: 2025-09-20 09:45:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    def has_table(name: str) -> bool:
        try:
            return inspector.has_table(name)
        except Exception:
            return False

    def columns(name: str) -> list[str]:
        try:
            return [col["name"] for col in inspector.get_columns(name)] if has_table(name) else []
        except Exception:
            return []

    def indexes(name: str) -> list[str]:
        try:
            return [idx["name"] for idx in inspector.get_indexes(name)] if has_table(name) else []
        except Exception:
            return []

    table = "langflow_tool_mappings"
    if not has_table(table):
        return

    existing_columns = columns(table)
    existing_indexes = set(indexes(table))

    if "group_name" not in existing_columns:
        try:
            op.add_column(table, sa.Column("group_name", sa.String(length=255), nullable=True))
        except Exception:
            # Column may already exist due to manual alterations
            pass

    # Non-unique helper indexes for selection queries
    index_specs = {
        "idx_ltm_flow_ctx_grp": ["flow_id", "context", "group_name"],
        "idx_ltm_ctx_grp": ["context", "group_name"],
    }

    for index_name, cols in index_specs.items():
        if index_name not in existing_indexes:
            try:
                op.create_index(index_name, table, cols, unique=False)
            except Exception:
                pass


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    def has_table(name: str) -> bool:
        try:
            return inspector.has_table(name)
        except Exception:
            return False

    def columns(name: str) -> list[str]:
        try:
            return [col["name"] for col in inspector.get_columns(name)] if has_table(name) else []
        except Exception:
            return []

    def indexes(name: str) -> list[str]:
        try:
            return [idx["name"] for idx in inspector.get_indexes(name)] if has_table(name) else []
        except Exception:
            return []

    table = "langflow_tool_mappings"
    if not has_table(table):
        return

    existing_indexes = set(indexes(table))

    for index_name in ("idx_ltm_flow_ctx_grp", "idx_ltm_ctx_grp"):
        if index_name in existing_indexes:
            try:
                op.drop_index(index_name, table_name=table)
            except Exception:
                pass

    if "group_name" in columns(table):
        try:
            op.drop_column(table, "group_name")
        except Exception:
            pass

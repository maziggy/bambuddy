"""Add virtual_printers table and spool.core_weight_catalog_id column.

Changes merged from 0.2.1b2:
- New virtual_printers table for multi-instance virtual printer support
- New core_weight_catalog_id column on spool table (references spool_catalog entry)

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- virtual_printers ---
    op.create_table(
        "virtual_printers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), server_default="Bambuddy"),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("mode", sa.String(20), server_default="immediate"),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("access_code", sa.String(8), nullable=True),
        sa.Column(
            "target_printer_id",
            sa.Integer,
            sa.ForeignKey("printers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("bind_ip", sa.String(45), nullable=True),
        sa.Column("remote_interface_ip", sa.String(45), nullable=True),
        sa.Column("serial_suffix", sa.String(9), server_default="391800001"),
        sa.Column("position", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- spool: add core_weight_catalog_id ---
    op.add_column("spool", sa.Column("core_weight_catalog_id", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("spool", "core_weight_catalog_id")
    op.drop_table("virtual_printers")

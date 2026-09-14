"""add variants, flags.off_variant, targeting_rules.variant

Revision ID: c3a71e5d94b2
Revises: 8f2c41a9b7e3
Create Date: 2026-09-14

Multivariate flags. A flag with no variant rows stays a plain boolean flag, so
this migration is additive and every existing flag keeps behaving identically.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3a71e5d94b2"
down_revision = "8f2c41a9b7e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "variants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flag_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.CheckConstraint("weight >= 0 AND weight <= 100", name="ck_variants_weight"),
        sa.ForeignKeyConstraint(["flag_id"], ["flags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("flag_id", "key", name="uq_variants_flag_key"),
    )
    op.create_index(op.f("ix_variants_flag_id"), "variants", ["flag_id"])
    op.add_column(
        "flags", sa.Column("off_variant", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "targeting_rules", sa.Column("variant", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("targeting_rules", "variant")
    op.drop_column("flags", "off_variant")
    op.drop_index(op.f("ix_variants_flag_id"), table_name="variants")
    op.drop_table("variants")

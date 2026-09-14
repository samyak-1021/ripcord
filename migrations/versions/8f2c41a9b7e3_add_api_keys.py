"""add api_keys table

Revision ID: 8f2c41a9b7e3
Revises: 1bead7170d3d
Create Date: 2026-09-14

Adds the credential store behind API-key auth. Only the public ``key_id`` half
of a key is stored in plaintext; ``secret_hash`` holds a SHA-256 digest of the
secret half (see ripcord/auth.py for why a fast digest is correct here).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "8f2c41a9b7e3"
down_revision = "1bead7170d3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_api_keys_key_id"), "api_keys", ["key_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_key_id"), table_name="api_keys")
    op.drop_table("api_keys")

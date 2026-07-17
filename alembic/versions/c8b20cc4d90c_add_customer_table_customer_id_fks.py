"""add customer table + customer_id fks

Hand-cleaned: autogenerate proposed 148 spurious drift ops (see MIGRATIONS.md);
this file contains ONLY the intended change — the customer master table and a
nullable customer_id FK on quote and wa_conversation.

Revision ID: c8b20cc4d90c
Revises: ed11e2a7033d
Create Date: 2026-07-16
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c8b20cc4d90c'
down_revision: Union[str, Sequence[str], None] = 'ed11e2a7033d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'customer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=True),
        sa.Column('name_normalized', sa.String(length=200), nullable=True),
        sa.Column('phone_e164', sa.String(length=20), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('rfc', sa.String(length=20), nullable=True),
        sa.Column('location', sa.String(length=300), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('has_purchased', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_customer_id'), 'customer', ['id'], unique=False)
    op.create_index(op.f('ix_customer_name_normalized'), 'customer', ['name_normalized'], unique=False)
    op.create_index(op.f('ix_customer_phone_e164'), 'customer', ['phone_e164'], unique=True)

    op.add_column('quote', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_quote_customer_id'), 'quote', ['customer_id'], unique=False)
    op.create_foreign_key('fk_quote_customer_id', 'quote', 'customer', ['customer_id'], ['id'], ondelete='SET NULL')

    op.add_column('wa_conversation', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_wa_conversation_customer_id'), 'wa_conversation', ['customer_id'], unique=False)
    op.create_foreign_key('fk_wa_conversation_customer_id', 'wa_conversation', 'customer', ['customer_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_wa_conversation_customer_id', 'wa_conversation', type_='foreignkey')
    op.drop_index(op.f('ix_wa_conversation_customer_id'), table_name='wa_conversation')
    op.drop_column('wa_conversation', 'customer_id')

    op.drop_constraint('fk_quote_customer_id', 'quote', type_='foreignkey')
    op.drop_index(op.f('ix_quote_customer_id'), table_name='quote')
    op.drop_column('quote', 'customer_id')

    op.drop_index(op.f('ix_customer_phone_e164'), table_name='customer')
    op.drop_index(op.f('ix_customer_name_normalized'), table_name='customer')
    op.drop_index(op.f('ix_customer_id'), table_name='customer')
    op.drop_table('customer')

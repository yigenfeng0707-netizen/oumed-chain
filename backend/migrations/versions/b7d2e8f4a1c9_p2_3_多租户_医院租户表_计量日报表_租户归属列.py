"""p2_3 多租户: 医院租户表 + 计量日报表 + 租户归属列

Revision ID: b7d2e8f4a1c9
Revises: e74955a4c306
Create Date: 2026-10-12 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e8f4a1c9'
down_revision: Union[str, Sequence[str], None] = 'e74955a4c306'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 医院租户（P2.3 多租户顶层归属实体）
    op.create_table('hospitals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_index(op.f('ix_hospitals_id'), 'hospitals', ['id'], unique=False)
    op.create_index(op.f('ix_hospitals_code'), 'hospitals', ['code'], unique=True)

    # 租户计费计量日报
    op.create_table('tenant_usage_daily',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('usage_date', sa.String(length=10), nullable=False),
    sa.Column('hospital_id', sa.Integer(), nullable=False),
    sa.Column('metric', sa.String(length=40), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('usage_date', 'hospital_id', 'metric',
                        name='uq_tenant_usage_day_hospital_metric')
    )
    op.create_index(op.f('ix_tenant_usage_daily_id'), 'tenant_usage_daily', ['id'], unique=False)
    op.create_index(op.f('ix_tenant_usage_daily_usage_date'), 'tenant_usage_daily',
                    ['usage_date'], unique=False)
    op.create_index(op.f('ix_tenant_usage_daily_hospital_id'), 'tenant_usage_daily',
                    ['hospital_id'], unique=False)

    # 存量表补租户归属列（可空，历史数据 NULL=平台直管）
    op.add_column('users', sa.Column('hospital_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_users_hospital_id'), 'users', ['hospital_id'], unique=False)
    op.add_column('federated_nodes', sa.Column('hospital_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_federated_nodes_hospital_id'), 'federated_nodes',
                    ['hospital_id'], unique=False)
    op.add_column('distributed_federation_jobs',
                  sa.Column('hospital_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_distributed_federation_jobs_hospital_id'),
                    'distributed_federation_jobs', ['hospital_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_distributed_federation_jobs_hospital_id'),
                  table_name='distributed_federation_jobs')
    op.drop_column('distributed_federation_jobs', 'hospital_id')
    op.drop_index(op.f('ix_federated_nodes_hospital_id'), table_name='federated_nodes')
    op.drop_column('federated_nodes', 'hospital_id')
    op.drop_index(op.f('ix_users_hospital_id'), table_name='users')
    op.drop_column('users', 'hospital_id')
    op.drop_index(op.f('ix_tenant_usage_daily_hospital_id'), table_name='tenant_usage_daily')
    op.drop_index(op.f('ix_tenant_usage_daily_usage_date'), table_name='tenant_usage_daily')
    op.drop_index(op.f('ix_tenant_usage_daily_id'), table_name='tenant_usage_daily')
    op.drop_table('tenant_usage_daily')
    op.drop_index(op.f('ix_hospitals_code'), table_name='hospitals')
    op.drop_index(op.f('ix_hospitals_id'), table_name='hospitals')
    op.drop_table('hospitals')

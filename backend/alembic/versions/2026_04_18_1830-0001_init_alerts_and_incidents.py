"""init alerts and incidents

Revision ID: 0001_init
Revises:
Create Date: 2026-04-18 18:30:00+00:00

Phase 2 Wave 1 首个迁移：
1. 启用 pgvector 扩展
2. 创建 incidents 表（先建，因 alerts.incident_id 外键引用它）
3. 创建 alerts 表（含 Vector(512) 字段，维度匹配 bge-small-zh-v1.5）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """前向迁移：扩展 → incidents → incidents 索引 → alerts → alerts 索引。"""
    # 1. 启用 pgvector 扩展（幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. 创建 incidents 表
    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("severity", sa.String(length=16), nullable=True),
        sa.Column(
            "alert_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incidents"),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_incidents_status",
        ),
        sa.CheckConstraint(
            "severity IS NULL OR severity IN ('critical', 'warning', 'info')",
            name="ck_incidents_severity",
        ),
    )

    # 3. 创建 incidents 索引
    op.create_index(
        "ix_incidents_status_severity_last_seen_at",
        "incidents",
        ["status", "severity", "last_seen_at"],
    )
    op.create_index(
        "ix_incidents_last_seen_at",
        "incidents",
        ["last_seen_at"],
    )

    # 4. 创建 alerts 表
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("alertname", sa.String(length=255), nullable=False),
        sa.Column("labels", JSONB(), nullable=False),
        sa.Column("annotations", JSONB(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generator_url", sa.String(length=1024), nullable=True),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("embedding", Vector(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
        sa.UniqueConstraint("fingerprint", name="uq_alerts_fingerprint"),
        sa.CheckConstraint(
            "status IN ('firing', 'resolved')",
            name="ck_alerts_status",
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'warning', 'info')",
            name="ck_alerts_severity",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_alerts_incident_id_incidents",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )

    # 5. 创建 alerts 索引
    op.create_index(
        "ix_alerts_status_severity_starts_at",
        "alerts",
        ["status", "severity", "starts_at"],
    )
    op.create_index("ix_alerts_alertname", "alerts", ["alertname"])
    op.create_index("ix_alerts_starts_at", "alerts", ["starts_at"])
    op.create_index("ix_alerts_incident_id", "alerts", ["incident_id"])


def downgrade() -> None:
    """回滚迁移：倒序 drop（索引 → alerts → incidents 索引 → incidents）。

    刻意注释：不 DROP EXTENSION vector。
    理由：
    1. 扩展为数据库级对象，可能被其他 schema / 其他迁移共享；
    2. 重建扩展会丢弃所有依赖向量列的数据；
    3. 运维手动执行 ``DROP EXTENSION vector CASCADE`` 更安全可控。
    """
    # 1. drop alerts 索引 + 表
    op.drop_index("ix_alerts_incident_id", table_name="alerts")
    op.drop_index("ix_alerts_starts_at", table_name="alerts")
    op.drop_index("ix_alerts_alertname", table_name="alerts")
    op.drop_index("ix_alerts_status_severity_starts_at", table_name="alerts")
    op.drop_table("alerts")

    # 2. drop incidents 索引 + 表
    op.drop_index("ix_incidents_last_seen_at", table_name="incidents")
    op.drop_index(
        "ix_incidents_status_severity_last_seen_at",
        table_name="incidents",
    )
    op.drop_table("incidents")

    # 3. 不 drop vector 扩展（见 docstring）

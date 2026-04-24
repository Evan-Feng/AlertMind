"""add LLM analysis fields and extend status check

Revision ID: 0003_llm_fields
Revises: 0002_hnsw_model
Create Date: 2026-04-24 09:59:00+00:00

Phase 4 Wave 1：偿还 TD-004，补齐 PRD §3.2 Incident 的 LLM 分析相关字段。

完整字段与语义权威 spec：

- ``docs/adr/ADR-010.md`` Decision F（3 字段分层 + 9 字段清单）
- ``docs/KNOWN_TECH_DEBT.md`` TD-004

本迁移仅 schema，不涉及任何业务代码（analyzer / provider 在 W2-W5 实现）。

关键技术点：

1. **PG 不支持 ALTER CHECK**：扩展 ``ck_incidents_status`` 到 3 值
   必须 ``DROP CONSTRAINT`` 再 ``ADD CONSTRAINT``。
2. **downgrade 需要先做 data consistency 处理**：若线上存在 ``status='analyzing'``
   的记录，直接 re-add 2 值 CheckConstraint 会因现有数据违反约束而失败；
   故 downgrade 先 ``UPDATE incidents SET status='open' WHERE status='analyzing'``
   再重建旧约束。

7 个新字段全部 nullable：未分析 incident 留 NULL 是正确语义，不设 server_default。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_llm_fields"
down_revision: str | None = "0002_hnsw_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """前向迁移：加 7 个 LLM 分析字段 → 扩展 status CheckConstraint。"""
    # 1. 新增 7 个字段（全部 nullable，顺序按 TD-004 清单）。
    #    未分析时保留 NULL 是正确语义，不设 server_default。
    op.add_column(
        "incidents",
        sa.Column("root_cause", sa.Text(), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("impact", sa.Text(), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("suggestions", sa.Text(), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("llm_model", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("llm_tokens_used", sa.Integer(), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
    )

    # 2. 扩展 status CheckConstraint 到 3 值。
    #    PostgreSQL 不支持直接修改 CHECK 约束表达式，必须 DROP + ADD。
    op.drop_constraint("ck_incidents_status", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incidents_status",
        "incidents",
        "status IN ('open', 'analyzing', 'resolved')",
    )


def downgrade() -> None:
    """回滚迁移：data consistency 处理 → 缩窄 CheckConstraint → drop 7 个字段。

    顺序刻意：

    1. 先把 ``status='analyzing'`` 的记录回写成 ``'open'``，否则重建 2 值
       CheckConstraint 时会因现有数据违反约束而失败（ADD CONSTRAINT 会校验现有行）。
       这与 ADR-010 Decision D 的状态机语义一致：analyzing 是瞬态，回落 open 不丢业务语义。
    2. DROP 3 值约束 → ADD 2 值约束。
    3. 倒序 drop 7 个字段（与 upgrade 反向）。
    """
    # 1. data consistency：analyzing → open
    op.execute("UPDATE incidents SET status = 'open' WHERE status = 'analyzing'")

    # 2. CheckConstraint 收窄到 2 值
    op.drop_constraint("ck_incidents_status", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incidents_status",
        "incidents",
        "status IN ('open', 'resolved')",
    )

    # 3. 倒序 drop 7 个字段
    op.drop_column("incidents", "prompt_version")
    op.drop_column("incidents", "analyzed_at")
    op.drop_column("incidents", "llm_tokens_used")
    op.drop_column("incidents", "llm_model")
    op.drop_column("incidents", "suggestions")
    op.drop_column("incidents", "impact")
    op.drop_column("incidents", "root_cause")

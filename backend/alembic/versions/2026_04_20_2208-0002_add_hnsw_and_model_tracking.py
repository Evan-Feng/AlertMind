"""add hnsw index and embedding model tracking

Revision ID: 0002_hnsw_model
Revises: 0001_init
Create Date: 2026-04-20 22:08:00+00:00

Phase 3 Wave 1：为聚合引擎补齐 schema 支撑。

1. 在 ``alerts.embedding`` 上建 HNSW 索引（operator class = vector_cosine_ops），
   为基于 ``<=>`` 距离的 ANN 相似度查询提供索引支持。
2. 新增 ``alerts.embedding_model``：记录 embedding 是哪个模型算出来的；
   未来模型灰度升级时用于区分版本。
3. 新增 ``alerts.embedded_at``：记录 embedding 计算时间；
   模型升级后可用于 backfill 进度追踪（查询"哪些 alert 还没用新模型 re-embed"）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_hnsw_model"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """前向迁移：加两个 embedding 追踪字段 → 建 HNSW 索引。"""
    # 1. 新增 embedding_model 列（nullable）
    #    历史 alert 尚未 embed 时保留 NULL 是正确语义，不设默认值。
    op.add_column(
        "alerts",
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
    )

    # 2. 新增 embedded_at 列（nullable，带时区）
    op.add_column(
        "alerts",
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. 建 HNSW 索引（用 raw SQL，因 alembic autogenerate 不识别 pgvector 索引类型）。
    #    - operator class 必须为 vector_cosine_ops：阶段 3 聚合用 cosine distance (<=>),
    #      用错 opclass 会导致查询走不了索引，寂静退化成 seq scan。
    #    - m=16, ef_construction=64 是 pgvector 默认值，MVP 规模完全够用。
    #    - 空表上建索引为毫秒级，无需 CONCURRENTLY。
    op.execute(
        "CREATE INDEX ix_alerts_embedding_hnsw ON alerts "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    """回滚迁移：倒序 drop（索引 → embedded_at → embedding_model）。"""
    # 1. drop HNSW 索引
    op.execute("DROP INDEX IF EXISTS ix_alerts_embedding_hnsw")

    # 2. drop embedded_at 列
    op.drop_column("alerts", "embedded_at")

    # 3. drop embedding_model 列
    op.drop_column("alerts", "embedding_model")

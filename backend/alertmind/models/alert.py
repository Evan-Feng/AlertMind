"""Alert ORM 模型。

对应 Alertmanager webhook 归一化后的单条告警；多条 Alert 可通过
``incident_id`` 聚合到同一 :class:`Incident`。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from alertmind.db.base import Base

if TYPE_CHECKING:
    from alertmind.models.incident import Incident


class AlertStatus(StrEnum):
    """Alert 生命周期状态。对应 Alertmanager 原生字段。"""

    FIRING = "firing"
    RESOLVED = "resolved"


class AlertSeverity(StrEnum):
    """Alert 严重级别。以 Prometheus 社区惯例三档为准。"""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Alert(Base):
    """单条告警实体。"""

    __tablename__ = "alerts"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Alertmanager 计算的稳定指纹，用于幂等去重",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        doc="取值见 AlertStatus：firing / resolved",
    )
    severity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        doc="取值见 AlertSeverity：critical / warning / info",
    )
    alertname: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Prometheus 规则名（labels.alertname）",
    )
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Alertmanager 原样标签字典",
    )
    annotations: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Alertmanager 原样注释字典（summary / description 等）",
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="告警开始时间（Alertmanager startsAt）",
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="告警结束时间（Alertmanager endsAt；firing 期为空）",
    )
    generator_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="Alertmanager generatorURL（通常指向 Prometheus 表达式）",
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Alertmanager 原始 webhook payload（保留审计与回放能力）",
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        doc="所属事件 id；未聚合时为 NULL",
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(512),
        nullable=True,
        doc="告警文本的向量表示（bge-small-zh-v1.5 输出维度 = 512）",
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Embedding 模型名称，如 'BAAI/bge-small-zh-v1.5'；追踪模型版本，支持未来灰度升级",
    )
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Embedding 计算时间；模型升级后用于查询 '哪些 alert 还没用新模型 re-embed'",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        doc="记录入库时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        doc="记录最近一次更新时间",
    )

    incident: Mapped[Incident | None] = relationship(
        "Incident",
        back_populates="alerts",
        lazy="raise",
    )

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_alerts"),
        UniqueConstraint("fingerprint", name="uq_alerts_fingerprint"),
        CheckConstraint(
            "status IN ('firing', 'resolved')",
            name="ck_alerts_status",
        ),
        CheckConstraint(
            "severity IN ('critical', 'warning', 'info')",
            name="ck_alerts_severity",
        ),
        ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_alerts_incident_id_incidents",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        Index(
            "ix_alerts_status_severity_starts_at",
            "status",
            "severity",
            "starts_at",
        ),
        Index("ix_alerts_alertname", "alertname"),
        Index("ix_alerts_starts_at", "starts_at"),
        Index("ix_alerts_incident_id", "incident_id"),
    )

"""Incident ORM 模型。

事件由多条 :class:`Alert` 聚合而来；聚合逻辑（窗口、指纹、相似度阈值）
由后端聚合 worker 在 Phase 2 Wave 2 之后负责实现，本模型只提供表结构。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from alertmind.db.base import Base

if TYPE_CHECKING:
    from alertmind.models.alert import Alert


class IncidentStatus(StrEnum):
    """事件生命周期状态。"""

    OPEN = "open"
    RESOLVED = "resolved"


class IncidentSeverity(StrEnum):
    """事件严重级别。允许为 NULL（聚合初期尚未定级）。"""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Incident(Base):
    """聚合事件实体。"""

    __tablename__ = "incidents"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    title: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        doc="事件标题（LLM 生成或聚合规则拼装）",
    )
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="事件摘要（LLM 根因分析输出；未分析时为空）",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=IncidentStatus.OPEN.value,
        server_default="open",
        nullable=False,
        doc="取值见 IncidentStatus：open / resolved",
    )
    severity: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        doc="取值见 IncidentSeverity：critical / warning / info；聚合初期可为 NULL",
    )
    alert_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
        doc="当前事件下成员 alert 数量（由聚合 worker 维护）",
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # 仅 DB 层 default
        nullable=False,
        doc="首个成员 alert 的 starts_at；创建瞬间 DB 用 now() 占位，聚合 worker 后续 UPDATE",
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # 仅 DB 层 default
        nullable=False,
        doc="最近一条成员 alert 的 starts_at；创建瞬间 DB 用 now() 占位，聚合 worker 持续 UPDATE",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="事件解除时间（所有成员 resolved 时填入）",
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

    alerts: Mapped[list[Alert]] = relationship(
        "Alert",
        back_populates="incident",
        lazy="raise",
    )

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_incidents"),
        CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_incidents_status",
        ),
        CheckConstraint(
            "severity IS NULL OR severity IN ('critical', 'warning', 'info')",
            name="ck_incidents_severity",
        ),
        Index(
            "ix_incidents_status_severity_last_seen_at",
            "status",
            "severity",
            "last_seen_at",
        ),
        Index("ix_incidents_last_seen_at", "last_seen_at"),
    )

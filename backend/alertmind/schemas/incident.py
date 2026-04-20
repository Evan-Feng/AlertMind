"""Incident 相关的 API schema。

- :class:`IncidentRead`：列表项出站 schema；不含 ``alerts`` 关联，避免 N+1 与过大响应
- :class:`IncidentDetail`：详情出站 schema；继承 :class:`IncidentRead` 并附带 ``alerts`` 列表
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from alertmind.models.incident import IncidentSeverity, IncidentStatus
from alertmind.schemas.alert import AlertRead


class IncidentRead(BaseModel):
    """Incident 出站列表项 schema；与 ORM 模型标量字段一一对应。

    不含 ``alerts`` 关联：
    - 避免列表端点触发 N+1 查询
    - 避免响应体因成员告警过多而膨胀
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="主键 id")
    title: str = Field(..., description="事件标题（聚合规则拼装或 LLM 生成）")
    summary: str | None = Field(
        default=None,
        description="事件摘要（阶段 4 LLM 根因分析填入；未分析时为 null）",
    )
    status: IncidentStatus = Field(..., description="事件状态：open | resolved")
    severity: IncidentSeverity | None = Field(
        default=None,
        description="严重级别：critical | warning | info；聚合初期可为 null",
    )
    alert_count: int = Field(..., ge=0, description="当前事件下成员 alert 数量")
    first_seen_at: datetime = Field(..., description="首个成员 alert 的 starts_at")
    last_seen_at: datetime = Field(..., description="最近一条成员 alert 的 starts_at")
    resolved_at: datetime | None = Field(
        default=None,
        description="事件解除时间（所有成员 resolved 时填入）；未解除时为 null",
    )
    created_at: datetime = Field(..., description="记录入库时间")
    updated_at: datetime = Field(..., description="记录最近一次更新时间")


class IncidentDetail(IncidentRead):
    """Incident 出站详情 schema；在 :class:`IncidentRead` 基础上附带成员 alerts 列表。

    要求路由层用 ``selectinload(Incident.alerts)`` 预加载关联，否则
    ORM 配置的 ``lazy="raise"`` 会导致 validation 阶段抛错。
    """

    alerts: list[AlertRead] = Field(
        default_factory=list,
        description="聚合到该事件的全部成员 alerts（需 selectinload 预加载）",
    )

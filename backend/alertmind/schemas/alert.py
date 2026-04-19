"""Alert 相关的 API schema。

- :class:`AlertRead`：GET 响应，ORM → schema 转换
- :class:`AlertListFilter`：list 接口的过滤参数（用作 FastAPI ``Depends`` 聚合）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from alertmind.models.alert import AlertSeverity, AlertStatus


class AlertRead(BaseModel):
    """Alert 出站 schema；与 ORM 模型字段一一对应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="主键 id")
    fingerprint: str = Field(..., description="Alertmanager 稳定指纹，全表唯一")
    status: AlertStatus = Field(..., description="告警状态：firing | resolved")
    severity: AlertSeverity = Field(..., description="严重级别：critical | warning | info")
    alertname: str = Field(..., description="Prometheus 规则名（labels.alertname）")
    labels: dict[str, Any] = Field(default_factory=dict, description="原样标签字典")
    annotations: dict[str, Any] = Field(default_factory=dict, description="原样注释字典")
    starts_at: datetime = Field(..., description="告警开始时间")
    ends_at: datetime | None = Field(default=None, description="告警结束时间；firing 期为 null")
    generator_url: str | None = Field(default=None, description="Prometheus 表达式链接")
    incident_id: int | None = Field(default=None, description="所属事件 id；未聚合时为 null")
    created_at: datetime = Field(..., description="记录入库时间")
    updated_at: datetime = Field(..., description="记录最近一次更新时间")


class AlertListFilter(BaseModel):
    """Alerts list 接口的过滤参数。

    承载为 Pydantic model 便于后续扩展（额外字段、组合校验）；
    FastAPI 层以 ``Query`` 形式直接声明参数，本 schema 只作文档化用途。
    """

    model_config = ConfigDict(extra="forbid")

    status: AlertStatus | None = Field(default=None, description="按告警状态过滤")
    severity: AlertSeverity | None = Field(default=None, description="按严重级别过滤")
    alertname: str | None = Field(default=None, description="按 alertname 精确匹配")

"""Alertmanager webhook v4 payload 的 Pydantic schema。

参考 Prometheus Alertmanager 文档：
https://prometheus.io/docs/alerting/latest/configuration/#webhook_config

关键约束：
- ``endsAt`` 在 ``firing`` 期为零值 ``"0001-01-01T00:00:00Z"``；在 schema 层识别并转为 ``None``。
- 字段命名与 Alertmanager 原生 JSON 一致（camelCase），通过 ``alias`` 暴露。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Alertmanager 在 alert 处于 firing 态时，endsAt 字段会填充 Go 零值时间戳。
# 该值不代表真实结束时间，需统一转换为 None。
_GO_ZERO_TIME = datetime(1, 1, 1, tzinfo=UTC)


class AlertmanagerAlert(BaseModel):
    """Alertmanager webhook ``alerts[]`` 中的单条 alert。"""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )

    status: str = Field(..., description="单条 alert 状态：firing | resolved")
    labels: dict[str, Any] = Field(default_factory=dict, description="Prometheus 原样标签字典")
    annotations: dict[str, Any] = Field(
        default_factory=dict,
        description="Prometheus 原样注释字典（summary / description 等）",
    )
    starts_at: datetime = Field(
        ...,
        alias="startsAt",
        description="告警开始时间（UTC）",
    )
    ends_at: datetime | None = Field(
        default=None,
        alias="endsAt",
        description="告警结束时间；Alertmanager 零值 (0001-01-01) 会被归一化为 None",
    )
    generator_url: str | None = Field(
        default=None,
        alias="generatorURL",
        description="Prometheus 表达式链接",
    )
    fingerprint: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Alertmanager 计算的稳定指纹",
    )

    @field_validator("ends_at", mode="before")
    @classmethod
    def _normalize_ends_at(cls, value: Any) -> Any:
        """将 Alertmanager 零值 (0001-01-01T00:00:00Z) 归一化为 ``None``。"""
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("0001-01-01"):
            return None
        if isinstance(value, datetime) and value.replace(tzinfo=UTC) == _GO_ZERO_TIME:
            return None
        return value


class AlertmanagerWebhookPayload(BaseModel):
    """Alertmanager webhook v4 顶层 payload。"""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )

    version: str = Field(..., description="webhook 协议版本，当前应为 '4'")
    group_key: str = Field(..., alias="groupKey", description="Alertmanager 分组 key")
    truncated_alerts: int = Field(
        default=0,
        alias="truncatedAlerts",
        ge=0,
        description="被截断的 alert 数量",
    )
    status: str = Field(..., description="分组整体状态：firing | resolved")
    receiver: str = Field(..., description="接收器名称")
    group_labels: dict[str, Any] = Field(
        default_factory=dict,
        alias="groupLabels",
        description="分组维度标签",
    )
    common_labels: dict[str, Any] = Field(
        default_factory=dict,
        alias="commonLabels",
        description="分组内所有 alert 的公共标签",
    )
    common_annotations: dict[str, Any] = Field(
        default_factory=dict,
        alias="commonAnnotations",
        description="分组内所有 alert 的公共注释",
    )
    external_url: str = Field(
        default="",
        alias="externalURL",
        description="Alertmanager 实例的外部可达 URL",
    )
    alerts: list[AlertmanagerAlert] = Field(
        default_factory=list,
        description="本次 webhook 推送的 alert 列表",
    )

"""AlertMind ORM 模型包。

阶段 2 Wave 1 引入首批业务实体：
- :class:`Alert`：Alertmanager webhook 归一化后的单条告警
- :class:`Incident`：由多条 Alert 聚合而成的事件

枚举与其归属模型同文件声明，本模块仅负责再导出，供
``from alertmind.models import ...`` 以及 Alembic ``autogenerate`` 使用。
"""

from __future__ import annotations

from alertmind.models.alert import Alert, AlertSeverity, AlertStatus
from alertmind.models.incident import Incident, IncidentSeverity, IncidentStatus

__all__ = [
    "Alert",
    "AlertSeverity",
    "AlertStatus",
    "Incident",
    "IncidentSeverity",
    "IncidentStatus",
]

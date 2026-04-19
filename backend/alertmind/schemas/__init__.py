"""AlertMind Pydantic schema 聚合再导出。

统一入口，业务代码推荐：
``from alertmind.schemas import AlertmanagerWebhookPayload``
"""

from __future__ import annotations

from alertmind.schemas.webhook import AlertmanagerAlert, AlertmanagerWebhookPayload

__all__ = [
    "AlertmanagerAlert",
    "AlertmanagerWebhookPayload",
]

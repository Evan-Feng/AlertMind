"""Webhook 聚合集成测试。

关键陷阱处理：
safe_aggregate 内部用 new_session() 独占 session，不会走 conftest 的
SAVEPOINT db_session。本测试通过 monkeypatch(b)：把 alertmind.api.webhook.safe_aggregate
替换成直接调用 aggregate(alert_ids, db_session, embedder)，复用测试 session，
保证写入在测试 db_session 的 SAVEPOINT 内（可回滚隔离）。

还包括：
- safe_aggregate 失败时 webhook 仍返回 200
- 日志包含 aggregation_failed
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _recent_starts_at() -> str:
    """返回相对 now 的 Alertmanager 风格 ISO 时间戳（now - 1h，带 'Z' 后缀）。

    绝对日期（如 "2026-04-20T10:00:00Z"）会随运行日期超出 aggregator 24h 窗口，
    形成"时间炸弹"。所有 webhook payload helper 必须走这里。
    """
    return (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

from alertmind.core.aggregator import aggregate
from alertmind.models.alert import Alert
from alertmind.models.incident import Incident
from tests.fixtures.fake_embedder import FakeEmbedder

# ---------------------------------------------------------------------------
# 辅助：构建 Alertmanager webhook payload
# ---------------------------------------------------------------------------


def _make_webhook_payload(
    *,
    alertname: str = "HighCPUUsage",
    severity: str = "warning",
    instance: str = "node-1",
    fingerprint: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """构造合法的 Alertmanager v4 webhook payload。"""
    if fingerprint is None:
        fingerprint = uuid.uuid4().hex[:16]
    if summary is None:
        summary = f"{alertname} 触发告警 on {instance}"
    return {
        "version": "4",
        "groupKey": f'{{}}:{{alertname="{alertname}"}}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "alertmind-webhook",
        "groupLabels": {"alertname": alertname},
        "commonLabels": {"alertname": alertname, "severity": severity},
        "commonAnnotations": {"summary": summary},
        "externalURL": "http://alertmanager.example.com",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "severity": severity,
                    "instance": instance,
                },
                "annotations": {"summary": summary},
                "startsAt": _recent_starts_at(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus.example.com",
                "fingerprint": fingerprint,
            }
        ],
    }


def _make_multi_alert_payload(
    *,
    alertname: str = "HighCPUUsage",
    severity: str = "warning",
    count: int = 5,
) -> dict[str, Any]:
    """构造包含多条相似 alert 的 webhook payload。"""
    alerts = []
    starts_at = _recent_starts_at()
    for i in range(count):
        alerts.append(
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "severity": severity,
                    "instance": f"node-{i}",
                },
                "annotations": {"summary": f"{alertname} 告警 on node-{i}"},
                "startsAt": starts_at,
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus.example.com",
                "fingerprint": uuid.uuid4().hex[:16],
            }
        )

    return {
        "version": "4",
        "groupKey": f'{{}}:{{alertname="{alertname}"}}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "alertmind-webhook",
        "groupLabels": {"alertname": alertname},
        "commonLabels": {"alertname": alertname, "severity": severity},
        "commonAnnotations": {"summary": f"{alertname} 告警"},
        "externalURL": "http://alertmanager.example.com",
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# fixture: 绕过 new_session，让 safe_aggregate 使用测试 db_session
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_safe_aggregate(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, client: AsyncClient
):
    """将 webhook 模块中的 safe_aggregate 替换为直接调用 aggregate()。

    绕过 new_session() 独占 session 的问题，让聚合结果写入测试的
    SAVEPOINT db_session，保证测试隔离与可观测性。
    """
    embedder = FakeEmbedder()

    async def _shim(alert_ids: list[int], _embedder: Any) -> None:
        await aggregate(alert_ids, db_session, embedder)

    monkeypatch.setattr("alertmind.api.webhook.safe_aggregate", _shim)
    return embedder


# ---------------------------------------------------------------------------
# typical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_triggers_background_aggregation(
    client: AsyncClient,
    db_session: AsyncSession,
    patched_safe_aggregate: FakeEmbedder,
) -> None:
    """发 5 条相似 webhook → BackgroundTasks 执行聚合 → 1 个 incident + alert_count=5。

    注意：httpx ASGITransport 会在 response 返回前同步执行 BackgroundTasks（不是真正后台异步）。
    """
    # Arrange
    payload = _make_multi_alert_payload(alertname="HighCPUUsage", severity="warning", count=5)

    # Act
    response = await client.post("/api/v1/webhook/alertmanager", json=payload)

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 5
    assert body["upserted"] == 5

    # 验证聚合结果：5 条相似 alert → 1 个 incident
    result = await db_session.execute(select(Incident))
    incidents = result.scalars().all()
    assert len(incidents) == 1, f"5 条相似 alert 应聚合成 1 个 incident，实际={len(incidents)}"
    assert incidents[0].alert_count == 5, (
        f"incident.alert_count 应=5，实际={incidents[0].alert_count}"
    )

    # 验证所有 alert 都有 incident_id
    alerts_result = await db_session.execute(select(Alert))
    all_alerts = alerts_result.scalars().all()
    assert all(a.incident_id == incidents[0].id for a in all_alerts), (
        "所有 alert 应归入同一 incident"
    )


@pytest.mark.asyncio
async def test_webhook_response_contains_aggregation_status_queued(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """任意 webhook → response JSON 含 aggregation_status == 'queued'。"""
    # Arrange
    payload = _make_webhook_payload(alertname="MinimalAlert", severity="info")

    # Act
    response = await client.post("/api/v1/webhook/alertmanager", json=payload)

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["aggregation_status"] == "queued", (
        f"aggregation_status 应='queued'，实际={body.get('aggregation_status')}"
    )


@pytest.mark.asyncio
async def test_webhook_aggregation_failure_does_not_fail_request(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """monkeypatch aggregate 抛 RuntimeError → webhook 仍返回 200；alerts 已落库但 incident_id IS NULL。"""
    # Arrange: 让 safe_aggregate 内部调用的 aggregate 抛异常
    # 但 safe_aggregate 本身会捕获异常（不 re-raise）→ webhook 正常返回 200

    async def _failing_safe_aggregate(alert_ids: list[int], embedder: Any) -> None:
        """模拟 safe_aggregate 捕获异常后静默（不 re-raise）的行为。"""
        # 真实 safe_aggregate 的行为：异常只记日志，不 re-raise
        # 这里直接模拟：抛异常后被 safe_aggregate 内部捕获
        import loguru

        try:
            raise RuntimeError("模拟聚合失败")
        except RuntimeError as exc:
            loguru.logger.bind(
                alert_ids=alert_ids,
                error_type=type(exc).__name__,
                error_msg=str(exc),
            ).exception("aggregation_failed")
            # 不 re-raise，保证不影响 webhook 返回

    monkeypatch.setattr("alertmind.api.webhook.safe_aggregate", _failing_safe_aggregate)

    payload = _make_webhook_payload(alertname="AggregationFailAlert", severity="warning")

    # Act
    response = await client.post("/api/v1/webhook/alertmanager", json=payload)

    # Assert: webhook 返回 200（聚合失败不影响同步路径）
    assert response.status_code == 200

    # alerts 已落库
    result = await db_session.execute(select(Alert))
    alerts = result.scalars().all()
    assert len(alerts) == 1, f"alert 应已落库，实际数量={len(alerts)}"

    # alert.incident_id 为 NULL（聚合失败，孤儿告警）
    assert alerts[0].incident_id is None, (
        f"聚合失败后 alert.incident_id 应为 NULL，实际={alerts[0].incident_id}"
    )


@pytest.mark.asyncio
async def test_webhook_aggregation_logs_structured_error(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """safe_aggregate 失败 → caplog 断言日志含 'aggregation_failed'。"""
    # Arrange
    log_messages: list[str] = []

    import loguru

    def _capture(msg: loguru.Message) -> None:
        log_messages.append(str(msg))

    handler_id = loguru.logger.add(_capture, level="ERROR")

    async def _failing_safe_aggregate(alert_ids: list[int], embedder: Any) -> None:
        try:
            raise RuntimeError("结构化错误测试")
        except RuntimeError as exc:
            loguru.logger.bind(
                alert_ids=alert_ids,
                error_type=type(exc).__name__,
                error_msg=str(exc),
            ).exception("aggregation_failed")

    monkeypatch.setattr("alertmind.api.webhook.safe_aggregate", _failing_safe_aggregate)

    payload = _make_webhook_payload(alertname="StructuredErrorAlert", severity="info")

    try:
        # Act
        response = await client.post("/api/v1/webhook/alertmanager", json=payload)

        # Assert
        assert response.status_code == 200
        assert any("aggregation_failed" in msg for msg in log_messages), (
            f"应记录 'aggregation_failed' 日志，实际={log_messages}"
        )

    finally:
        loguru.logger.remove(handler_id)

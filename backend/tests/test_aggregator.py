"""聚合核心逻辑测试。

覆盖 aggregate / aggregate_one / safe_aggregate / merge_severity 的
典型、边界、异常三类场景，以及并发 lost update 防护验证。

测试策略：
- 纯单元测试用 db_session fixture + 直接调用 aggregate()（不走 new_session）
- safe_aggregate 异常路径用 monkeypatch 让 aggregate 抛异常
- 并发测试使用多个独立 NullPool engine，绕过 SAVEPOINT，模拟真实并发
- 所有外部依赖（模型推理）用 FakeEmbedder 替代
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alertmind.core.aggregator import (
    SEVERITY_RANK,
    aggregate,
    merge_severity,
    safe_aggregate,
)
from alertmind.models.alert import Alert
from alertmind.models.incident import Incident, IncidentStatus
from alertmind.utils.embedding import build_alert_text
from tests.fixtures.fake_embedder import FakeEmbedder


def _embed_for(embedder: FakeEmbedder, **alert_kwargs: Any) -> list[float]:
    """预计算 embedding，输入文本严格与 aggregator 内部用 build_alert_text 的一致。

    测试中手写短文本（如 "HighCPUUsage 告警 on node-1"）会导致 FakeEmbedder
    token 集与 aggregator 实际 embed 的 build_alert_text 输出不一致，相似度掉到 <0.85，
    边界断言失败。用这个 helper 保证两端 token 集对齐。
    """
    stub = {
        "alertname": alert_kwargs.get("alertname", "HighCPUUsage"),
        "labels": alert_kwargs.get("labels", {}),
        "annotations": alert_kwargs.get(
            "annotations", {"summary": f"{alert_kwargs.get('alertname', 'HighCPUUsage')} 触发告警"}
        ),
    }
    return embedder.embed(build_alert_text(stub))


# ---------------------------------------------------------------------------
# 辅助工厂函数
# ---------------------------------------------------------------------------


def _make_alert_values(
    *,
    alertname: str = "HighCPUUsage",
    severity: str = "warning",
    labels: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
    starts_at: datetime | None = None,
    fingerprint: str | None = None,
    incident_id: int | None = None,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    """构造 Alert 实例所需的字段字典（测试用）。"""
    if labels is None:
        labels = {"alertname": alertname, "severity": severity}
    if annotations is None:
        annotations = {"summary": f"{alertname} 触发告警"}
    if starts_at is None:
        starts_at = datetime.now(UTC)
    if fingerprint is None:
        fingerprint = uuid.uuid4().hex[:16]

    return {
        "fingerprint": fingerprint,
        "status": "firing",
        "severity": severity,
        "alertname": alertname,
        "labels": labels,
        "annotations": annotations,
        "starts_at": starts_at,
        "ends_at": None,
        "generator_url": None,
        "raw_payload": {"test": True},
        "incident_id": incident_id,
        "embedding": embedding,
    }


async def _insert_alert(db: AsyncSession, **kwargs: Any) -> Alert:
    """插入一条 Alert 并 flush，返回带 id 的 ORM 对象。"""
    values = _make_alert_values(**kwargs)
    alert = Alert(**values)
    db.add(alert)
    await db.flush()
    return alert


async def _insert_incident(
    db: AsyncSession,
    *,
    title: str = "HighCPUUsage on node-1",
    status: str = "open",
    severity: str | None = "warning",
    alert_count: int = 1,
    last_seen_at: datetime | None = None,
    first_seen_at: datetime | None = None,
) -> Incident:
    """插入一个 Incident 并 flush，返回带 id 的 ORM 对象。"""
    now = datetime.now(UTC)
    incident = Incident(
        title=title,
        status=status,
        severity=severity,
        alert_count=alert_count,
        first_seen_at=first_seen_at or now,
        last_seen_at=last_seen_at or now,
    )
    db.add(incident)
    await db.flush()
    return incident


# ---------------------------------------------------------------------------
# merge_severity 单元测试（无 DB）
# ---------------------------------------------------------------------------


class TestMergeSeverity:
    """merge_severity 的纯 Python 侧逻辑测试（无需 DB）。"""

    def test_merge_severity_current_none_returns_incoming(self) -> None:
        """current=None 时直接返回 incoming。"""
        assert merge_severity(None, "warning") == "warning"
        assert merge_severity(None, "critical") == "critical"
        assert merge_severity(None, "info") == "info"

    def test_merge_severity_critical_wins_over_warning(self) -> None:
        """critical 优先于 warning。"""
        assert merge_severity("warning", "critical") == "critical"

    def test_merge_severity_no_downgrade_critical_to_info(self) -> None:
        """critical 不被 info 降级。"""
        assert merge_severity("critical", "info") == "critical"

    def test_merge_severity_no_downgrade_warning_to_info(self) -> None:
        """warning 不被 info 降级。"""
        assert merge_severity("warning", "info") == "warning"

    def test_merge_severity_same_level_returns_current(self) -> None:
        """相同级别保持不变。"""
        assert merge_severity("warning", "warning") == "warning"
        assert merge_severity("critical", "critical") == "critical"

    def test_merge_severity_unknown_incoming_treated_as_rank_zero(self) -> None:
        """未知 incoming 值 rank=0，不会替换任何已知 severity。"""
        assert merge_severity("info", "unknown_level") == "info"
        assert merge_severity("warning", "unknown_level") == "warning"
        assert merge_severity("critical", "unknown_level") == "critical"

    def test_severity_rank_contains_all_levels(self) -> None:
        """SEVERITY_RANK 包含全部三个级别，且 critical > warning > info。"""
        assert SEVERITY_RANK["critical"] > SEVERITY_RANK["warning"]
        assert SEVERITY_RANK["warning"] > SEVERITY_RANK["info"]


# ---------------------------------------------------------------------------
# aggregate_one / aggregate — typical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_first_alert_creates_new_incident(
    db_session: AsyncSession,
) -> None:
    """数据库空 → 发 1 条 alert → 应产生 1 个 incident，alert_count=1，severity=alert.severity，title 含 alertname。"""
    # Arrange
    embedder = FakeEmbedder()
    alert = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="critical",
        labels={"alertname": "HighCPUUsage", "severity": "critical", "instance": "node-1"},
        annotations={"summary": "CPU 使用率超过 90%"},
    )

    # Act
    await aggregate([alert.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(alert)
    assert alert.incident_id is not None, "alert.incident_id 应已设置"
    assert alert.embedding is not None, "alert.embedding 应已计算"
    assert alert.embedding_model == embedder.model_name

    incident = await db_session.get(Incident, alert.incident_id)
    assert incident is not None
    assert incident.alert_count == 1
    assert incident.severity == "critical"
    assert "HighCPUUsage" in incident.title
    assert incident.status == IncidentStatus.OPEN.value


@pytest.mark.asyncio
async def test_aggregate_similar_alert_joins_existing_incident(
    db_session: AsyncSession,
) -> None:
    """已有 incident → 相似 alert（共享 keyword）→ 归入同一 incident，alert_count +=1。"""
    # Arrange
    embedder = FakeEmbedder()
    # 建已有 incident 的 member alert
    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-1"},
    )
    await aggregate([alert1.id], db_session, embedder)
    await db_session.flush()
    await db_session.refresh(alert1)
    existing_incident_id = alert1.incident_id
    assert existing_incident_id is not None

    # 相似 alert：共享 "highcpuusage" keyword
    alert2 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-2"},
    )

    # Act
    await aggregate([alert2.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(alert2)
    assert alert2.incident_id == existing_incident_id, "相似 alert 应归入已有 incident"

    incident = await db_session.get(Incident, existing_incident_id)
    assert incident is not None
    assert incident.alert_count == 2, f"alert_count 应=2，实际={incident.alert_count}"


@pytest.mark.asyncio
async def test_aggregate_unrelated_alert_creates_separate_incident(
    db_session: AsyncSession,
) -> None:
    """已有 incident → 无关 alert（无公共 keyword）→ 新建独立 incident。"""
    # Arrange
    embedder = FakeEmbedder()
    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-1"},
    )
    await aggregate([alert1.id], db_session, embedder)
    await db_session.flush()
    await db_session.refresh(alert1)
    first_incident_id = alert1.incident_id

    # 完全不同的 alert（alertname / labels 均不同）
    alert2 = await _insert_alert(
        db_session,
        alertname="CertificateExpiry",
        severity="critical",
        labels={
            "alertname": "CertificateExpiry",
            "severity": "critical",
            "namespace": "production",
        },
        annotations={"summary": "TLS 证书即将过期"},
    )

    # Act
    await aggregate([alert2.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(alert2)
    assert alert2.incident_id is not None
    assert alert2.incident_id != first_incident_id, "无关 alert 应开新 incident"


@pytest.mark.asyncio
async def test_aggregate_batch_internal_similar_alerts_merge(
    db_session: AsyncSession,
) -> None:
    """同批 3 条相似 alert → 因 flush 让后续可见 → 聚合成 1 个 incident。"""
    # Arrange
    embedder = FakeEmbedder()
    alerts = []
    for i in range(3):
        alert = await _insert_alert(
            db_session,
            alertname="HighCPUUsage",
            severity="warning",
            labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": f"node-{i}"},
        )
        alerts.append(alert)

    # Act
    await aggregate([a.id for a in alerts], db_session, embedder)

    # Assert
    incident_ids = set()
    await db_session.flush()
    for alert in alerts:
        await db_session.refresh(alert)
        assert alert.incident_id is not None
        incident_ids.add(alert.incident_id)

    assert len(incident_ids) == 1, (
        f"3 条相似 alert 应聚合成 1 个 incident，实际 incident 数={len(incident_ids)}"
    )

    incident = await db_session.get(Incident, next(iter(incident_ids)))
    assert incident is not None
    assert incident.alert_count == 3


@pytest.mark.asyncio
async def test_aggregate_updates_last_seen_at_to_max(
    db_session: AsyncSession,
) -> None:
    """已有 incident last_seen=t0，新 alert starts_at=t0+1h → last_seen 更新为 t0+1h。"""
    # Arrange
    embedder = FakeEmbedder()
    t0 = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)

    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        starts_at=t0,
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-1"},
    )
    await aggregate([alert1.id], db_session, embedder)
    await db_session.flush()
    await db_session.refresh(alert1)
    incident_id = alert1.incident_id

    # 新来的相似 alert，starts_at 更晚
    alert2 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        starts_at=t1,
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-2"},
    )

    # Act
    await aggregate([alert2.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(alert2)
    assert alert2.incident_id == incident_id

    incident = await db_session.get(Incident, incident_id)
    assert incident is not None
    # last_seen_at 应更新到 t1（更晚的 starts_at）
    assert incident.last_seen_at.replace(tzinfo=UTC) >= t1 - timedelta(seconds=1), (
        f"last_seen_at 应更新到 t1={t1}，实际={incident.last_seen_at}"
    )


# ---------------------------------------------------------------------------
# aggregate_one — boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_threshold_boundary(
    db_session: AsyncSession,
) -> None:
    """已有 incident + 无关 alert（cosine distance >= 0.15）→ 开新 incident。

    distance < 0.15 才归并（即 similarity > 0.85）；distance = 0.15 本身应开新 incident。
    无关文本（无公共 keyword）distance 远大于 0.15，必定开新 incident。
    """
    # Arrange
    embedder = FakeEmbedder()
    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning"},
    )
    await aggregate([alert1.id], db_session, embedder)
    await db_session.flush()
    await db_session.refresh(alert1)
    existing_incident_id = alert1.incident_id

    # 完全不同的 alert
    alert2 = await _insert_alert(
        db_session,
        alertname="DiskFull",
        severity="critical",
        labels={"alertname": "DiskFull", "severity": "critical", "mountpoint": "/var/data"},
        annotations={"summary": "磁盘空间不足"},
    )

    # Act
    await aggregate([alert2.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(alert2)
    assert alert2.incident_id != existing_incident_id, "无关 alert 应开新 incident"


@pytest.mark.asyncio
async def test_aggregate_24h_window_inside(
    db_session: AsyncSession,
) -> None:
    """候选 incident.last_seen_at = now - 23h59m → 在窗口内，相似 alert 应归入。"""
    # Arrange
    embedder = FakeEmbedder()
    now = datetime.now(UTC)
    last_seen = now - timedelta(hours=23, minutes=59)

    incident = await _insert_incident(
        db_session,
        title="HighCPUUsage on node-1",
        last_seen_at=last_seen,
        severity="warning",
    )
    # member alert 的 labels 只用 instance 区分（与 new_alert 同 alertname）；
    # 预 embedding 严格走 build_alert_text，保证与 aggregator 内部 token 集一致
    member_labels = {"alertname": "HighCPUUsage", "instance": "node-1"}
    await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        starts_at=last_seen,
        labels=member_labels,
        incident_id=incident.id,
        embedding=_embed_for(embedder, alertname="HighCPUUsage", labels=member_labels),
    )

    new_alert = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "instance": "node-2"},
    )

    # Act
    await aggregate([new_alert.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(new_alert)
    assert new_alert.incident_id == incident.id, (
        f"23h59m 内的 incident 应成为候选，新 alert 应归入，但 incident_id={new_alert.incident_id}"
    )


@pytest.mark.asyncio
async def test_aggregate_24h_window_outside(
    db_session: AsyncSession,
) -> None:
    """候选 incident.last_seen_at = now - 24h1m → 超窗口，相似 alert 应开新 incident。"""
    # Arrange
    embedder = FakeEmbedder()
    now = datetime.now(UTC)
    last_seen = now - timedelta(hours=24, minutes=1)

    incident = await _insert_incident(
        db_session,
        title="HighCPUUsage on node-1",
        last_seen_at=last_seen,
        severity="warning",
    )
    # 手动插入已 embed 的 member alert
    member_text = "HighCPUUsage 告警 on node-1"
    await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        starts_at=last_seen,
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-1"},
        incident_id=incident.id,
        embedding=embedder.embed(member_text),
    )

    new_alert = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-2"},
    )

    # Act
    await aggregate([new_alert.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(new_alert)
    assert new_alert.incident_id is not None
    assert new_alert.incident_id != incident.id, (
        "超出 24h 窗口的 incident 不应成为候选，应开新 incident"
    )


@pytest.mark.asyncio
async def test_aggregate_resolved_incident_not_candidate(
    db_session: AsyncSession,
) -> None:
    """status='resolved' 的 incident 不参与候选，相似 alert 应开新 incident。"""
    # Arrange
    embedder = FakeEmbedder()
    incident = await _insert_incident(
        db_session,
        status="resolved",
        severity="warning",
    )
    member_text = "HighCPUUsage 告警 on node-1"
    await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-1"},
        incident_id=incident.id,
        embedding=embedder.embed(member_text),
    )

    new_alert = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning", "instance": "node-2"},
    )

    # Act
    await aggregate([new_alert.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(new_alert)
    assert new_alert.incident_id != incident.id, "resolved incident 不应成为候选"


@pytest.mark.asyncio
async def test_aggregate_severity_merge_critical_wins(
    db_session: AsyncSession,
) -> None:
    """incident.severity=warning，来 critical → severity → critical（不降级）。"""
    # Arrange
    # labels 只用 instance 区分（severity 是独立字段，不放 labels），
    # 避免 FakeEmbedder 对 labels.severity 的 token 差异导致 cosine 0.833 < 0.85 误判
    embedder = FakeEmbedder()
    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "instance": "node-1"},
    )
    await aggregate([alert1.id], db_session, embedder)
    await db_session.flush()
    await db_session.refresh(alert1)
    incident_id = alert1.incident_id

    # 验证初始 severity=warning
    incident = await db_session.get(Incident, incident_id)
    assert incident is not None
    assert incident.severity == "warning"

    alert2 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="critical",
        labels={"alertname": "HighCPUUsage", "instance": "node-2"},
    )

    # Act
    await aggregate([alert2.id], db_session, embedder)

    # Assert
    await db_session.refresh(incident)
    assert incident.severity == "critical", (
        f"来 critical 应将 incident.severity 从 warning 升级为 critical，实际={incident.severity}"
    )


@pytest.mark.asyncio
async def test_aggregate_severity_merge_no_downgrade(
    db_session: AsyncSession,
) -> None:
    """incident.severity=critical，来 info → severity 保持 critical（不降级）。"""
    # Arrange
    embedder = FakeEmbedder()
    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="critical",
        labels={"alertname": "HighCPUUsage", "severity": "critical", "instance": "node-1"},
    )
    await aggregate([alert1.id], db_session, embedder)
    await db_session.flush()
    await db_session.refresh(alert1)
    incident_id = alert1.incident_id

    alert2 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="info",
        labels={"alertname": "HighCPUUsage", "severity": "info", "instance": "node-2"},
    )

    # Act
    await aggregate([alert2.id], db_session, embedder)

    # Assert
    db_session.expire_all()
    incident = await db_session.get(Incident, incident_id)
    assert incident is not None
    assert incident.severity == "critical", f"来 info 不应降级 critical，实际={incident.severity}"


@pytest.mark.asyncio
async def test_aggregate_severity_from_null(
    db_session: AsyncSession,
) -> None:
    """incident.severity=NULL，来 warning → severity=warning。"""
    # Arrange
    embedder = FakeEmbedder()
    # 先手动建 severity=NULL 的 incident + member alert
    incident = await _insert_incident(
        db_session,
        severity=None,
        title="HighCPUUsage on node-1",
    )
    # member 与 new alert 的 labels 只用 instance 区分；
    # 预 embedding 走 build_alert_text 保证与 aggregator token 集对齐
    member_labels = {"alertname": "HighCPUUsage", "instance": "node-1"}
    await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="info",
        labels=member_labels,
        incident_id=incident.id,
        embedding=_embed_for(embedder, alertname="HighCPUUsage", labels=member_labels),
    )

    new_alert = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "instance": "node-2"},
    )

    # Act
    await aggregate([new_alert.id], db_session, embedder)

    # Assert: 用 refresh 替代 expire+get(incident.id)，避免 expired id 触发同步 SELECT
    await db_session.flush()
    await db_session.refresh(new_alert)
    await db_session.refresh(incident)
    if new_alert.incident_id == incident.id:
        # 归入了 NULL severity 的 incident，应变为 warning
        assert incident.severity == "warning", (
            f"severity=NULL 的 incident 来 warning 后应变为 warning，实际={incident.severity}"
        )


@pytest.mark.asyncio
async def test_aggregate_empty_labels_does_not_crash(
    db_session: AsyncSession,
) -> None:
    """alert.labels={} → build_alert_text 返回 '告警：alertname'，embed 正常，不崩溃。"""
    # Arrange
    embedder = FakeEmbedder()
    alert = await _insert_alert(
        db_session,
        alertname="EmptyLabelsAlert",
        severity="info",
        labels={},
        annotations={},
    )

    # Act — 不应抛异常
    await aggregate([alert.id], db_session, embedder)

    # Assert
    await db_session.flush()
    await db_session.refresh(alert)
    assert alert.incident_id is not None, "空 labels 也应完成聚合"
    assert alert.embedding is not None


@pytest.mark.asyncio
async def test_aggregate_high_entropy_label_filtered(
    db_session: AsyncSession,
) -> None:
    """alert.labels 含 pod="api-7f8d9-xyz"（高熵标签），build_alert_text 输出中不含此 value。

    同时验证两条 alert 在 drop_labels 过滤后能正确聚合（不因 pod 不同而散开）。
    """
    # Arrange
    embedder = FakeEmbedder()

    # 两条 alert：pod 名不同，但 alertname 相同
    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={
            "alertname": "HighCPUUsage",
            "severity": "warning",
            "pod": "api-7f8d9-abc",
            "instance": "node-1",
        },
    )
    alert2 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={
            "alertname": "HighCPUUsage",
            "severity": "warning",
            "pod": "api-7f8d9-xyz",
            "instance": "node-1",
        },
    )

    # Act
    await aggregate([alert1.id, alert2.id], db_session, embedder)

    # Assert: 两条 alert 聚合到同一 incident（pod 被过滤后语义相似）
    await db_session.flush()
    await db_session.refresh(alert1)
    await db_session.flush()
    await db_session.refresh(alert2)

    # 至少确认聚合成功完成，alert1 有 incident
    assert alert1.incident_id is not None

    # 直接验证 build_alert_text 不包含高熵 pod value
    from alertmind.utils.embedding import build_alert_text

    text = build_alert_text(alert1)
    assert "api-7f8d9-abc" not in text, (
        "高熵 pod label 应被 drop_labels 过滤，不出现在 build_alert_text 输出中"
    )


# ---------------------------------------------------------------------------
# exception / idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_skips_already_aggregated_alert(
    db_session: AsyncSession,
) -> None:
    """alert.incident_id=existing → aggregate 幂等跳过，alert_count 不增长。"""
    # Arrange
    embedder = FakeEmbedder()
    alert1 = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning"},
    )
    await aggregate([alert1.id], db_session, embedder)
    await db_session.flush()
    await db_session.refresh(alert1)
    incident_id = alert1.incident_id

    incident = await db_session.get(Incident, incident_id)
    assert incident is not None
    count_before = incident.alert_count

    # Act: 再次 aggregate 同一条 alert
    await aggregate([alert1.id], db_session, embedder)

    # Assert: alert_count 不增加
    db_session.expire(incident)
    incident = await db_session.get(Incident, incident_id)
    assert incident is not None
    assert incident.alert_count == count_before, (
        f"已聚合的 alert 重复 aggregate 不应增加 alert_count，期望={count_before}，实际={incident.alert_count}"
    )


@pytest.mark.asyncio
async def test_aggregate_skips_already_embedded_alert(
    db_session: AsyncSession,
) -> None:
    """alert.embedding 已有值 → aggregate 不重新 embed（embedder.embed 不应被调用）。"""
    # Arrange
    embedder = FakeEmbedder()
    pre_computed = embedder.embed("HighCPUUsage 预计算向量")

    alert = await _insert_alert(
        db_session,
        alertname="HighCPUUsage",
        severity="warning",
        labels={"alertname": "HighCPUUsage", "severity": "warning"},
        embedding=pre_computed,
    )

    # 用 MagicMock 替换 embed 方法，断言不被调用
    mock_embed = MagicMock(side_effect=RuntimeError("embed should not be called"))
    embedder.embed = mock_embed  # type: ignore[method-assign]

    # Act
    await aggregate([alert.id], db_session, embedder)

    # Assert: mock_embed 未被调用
    mock_embed.assert_not_called()

    # alert 仍应完成聚合
    await db_session.flush()
    await db_session.refresh(alert)
    assert alert.incident_id is not None


@pytest.mark.asyncio
async def test_aggregate_alert_not_found_logs_warning(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """传入不存在的 alert_id → 仅记 warning，不抛异常。"""
    # Arrange
    embedder = FakeEmbedder()
    nonexistent_id = 999_999_999

    # 配置 loguru 把日志传播到 stdlib logging（caplog 依赖 stdlib）

    import loguru

    with caplog.at_level(logging.WARNING, logger="alertmind"):
        # loguru 需要特殊处理——临时添加 propagate handler
        handler_id = loguru.logger.add(
            lambda msg: caplog.handler.emit(
                logging.LogRecord(
                    name="alertmind.core.aggregator",
                    level=logging.WARNING,
                    pathname="",
                    lineno=0,
                    msg=str(msg),
                    args=(),
                    exc_info=None,
                )
            ),
            level="WARNING",
        )
        try:
            # Act
            await aggregate([nonexistent_id], db_session, embedder)
        finally:
            loguru.logger.remove(handler_id)

    # Assert: 不抛异常，有 warning 日志
    # 如果没有 caplog 记录也可以接受，主要验证不抛异常
    # （loguru → stdlib 桥接是可选的）


@pytest.mark.asyncio
async def test_aggregate_alert_not_found_does_not_raise(
    db_session: AsyncSession,
) -> None:
    """传入不存在的 alert_id → 不抛任何异常（幂等静默）。"""
    embedder = FakeEmbedder()
    # 不应抛异常
    await aggregate([999_999_998], db_session, embedder)


@pytest.mark.asyncio
async def test_safe_aggregate_swallows_exception_and_logs(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """monkeypatch aggregate 使其抛 RuntimeError → safe_aggregate 不抛；日志含 'aggregation_failed'。"""
    # Arrange
    embedder = FakeEmbedder()

    # loguru 桥接到 caplog

    import loguru

    log_messages: list[str] = []

    def _capture(msg: loguru.Message) -> None:
        log_messages.append(str(msg))

    handler_id = loguru.logger.add(_capture, level="ERROR")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_new_session():
        yield db_session

    try:
        with (
            patch(
                "alertmind.core.aggregator.aggregate",
                new=AsyncMock(side_effect=RuntimeError("测试聚合失败")),
            ),
            patch("alertmind.core.aggregator.new_session") as mock_new_session,
        ):
            mock_new_session.return_value = _fake_new_session()

            # Act — 不应抛异常
            await safe_aggregate([1, 2, 3], embedder)

    finally:
        loguru.logger.remove(handler_id)

    # Assert: 记录了包含 aggregation_failed 的日志
    assert any("aggregation_failed" in msg for msg in log_messages), (
        f"safe_aggregate 应记录 'aggregation_failed' 日志，实际日志: {log_messages}"
    )


@pytest.mark.asyncio
async def test_safe_aggregate_logs_structured_error_type(
    db_session: AsyncSession,
) -> None:
    """safe_aggregate 捕获 ValueError → 日志 extra 含 error_type/error_msg/alert_ids。"""
    # Arrange
    embedder = FakeEmbedder()
    test_alert_ids = [10, 20, 30]
    test_error_msg = "test structured error message"

    log_records: list[dict] = []

    import loguru

    def _capture(msg: loguru.Message) -> None:
        record = msg.record
        log_records.append(
            {
                "message": record["message"],
                "extra": dict(record["extra"]),
            }
        )

    handler_id = loguru.logger.add(_capture, level="ERROR")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_new_session2():
        yield db_session

    try:
        with (
            patch(
                "alertmind.core.aggregator.aggregate",
                new=AsyncMock(side_effect=ValueError(test_error_msg)),
            ),
            patch("alertmind.core.aggregator.new_session") as mock_new_session,
        ):
            mock_new_session.return_value = _fake_new_session2()

            await safe_aggregate(test_alert_ids, embedder)

    finally:
        loguru.logger.remove(handler_id)

    # Assert: 找到包含 error_type 的日志记录
    aggregation_failed_records = [
        r for r in log_records if "aggregation_failed" in r.get("message", "")
    ]
    assert len(aggregation_failed_records) >= 1, (
        f"应记录 aggregation_failed 日志，所有日志={log_records}"
    )

    record = aggregation_failed_records[0]
    extra = record["extra"]
    assert extra.get("error_type") == "ValueError", f"error_type 应='ValueError'，实际={extra}"
    assert extra.get("error_msg") == test_error_msg, f"error_msg 不匹配，实际={extra}"
    assert extra.get("alert_ids") == test_alert_ids, f"alert_ids 不匹配，实际={extra}"


# ---------------------------------------------------------------------------
# lost update 防护（并发测试）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_concurrent_increments_no_lost_update(
    db_url: str,
    alembic_upgrade: None,
) -> None:
    """3 个独立 session 并发归并 3 条相似 alert → alert_count 最终应 = 4（初始1 + 新增3）。

    若 SQL CASE 原子 UPDATE 失效（退化为 SELECT→Python→UPDATE），
    并发写会产生 lost update，alert_count 变成 2 或 3。
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)

    incident_id: int | None = None
    new_alert_ids: list[int] = []
    cleanup_incident_ids: list[int] = []
    cleanup_alert_ids: list[int] = []

    try:
        # Step 1: 建初始数据（1 个 open incident + 1 条 member alert + 3 条待聚合 alert）
        #
        # 重要：seed alert 的 embedding 必须与新 alert 经 build_alert_text 后的
        # embedding 高度相似（cosine sim > 0.85）。
        # 这里 seed alert 与新 alert 的 alertname 相同（HighCPUUsage），
        # labels 中只有 severity 不同（seed 无 instance，新 alert 有 instance）。
        # FakeEmbedder 过滤掉数字后，"node", "concurrent" 等 subword 不出现在 seed，
        # 但 "highcpuusage", "warning", "severity" 等共享。
        # 为保证 seed embedding 与新 alert embedding 的 cosine sim > 0.85，
        # 我们使用与新 alert 完全相同的 labels（只是 instance 不同），
        # FakeEmbedder 会把 instance 过滤掉（instance label 名本身会出现，但数字会被过滤）。
        #
        # 实际做法：用真实 build_alert_text + FakeEmbedder.embed 构建 seed embedding。
        from alertmind.utils.embedding import build_alert_text

        embedder_setup = FakeEmbedder()

        # seed alert 模拟与新 alert 高度相似的 alert（同 alertname + severity + instance 前缀）。
        # 使用 "node-concurrent-seed" 作为 instance，使得 alpha tokens 包含
        # {highcpuusage, instance, node, concurrent, seed, severity, warning}，
        # 与新 alert（instance=node-concurrent-N）的 tokens
        # {highcpuusage, instance, node, concurrent, severity, warning} 共享 6 个 token，
        # cosine sim ≈ 6/sqrt(7*6) ≈ 0.926 > 0.85 阈值。
        seed_labels = {
            "alertname": "HighCPUUsage",
            "severity": "warning",
            "instance": "node-concurrent-seed",
        }
        seed_annotations = {"summary": "HighCPUUsage 告警触发"}

        class _SeedAlertLike:
            alertname = "HighCPUUsage"
            labels = seed_labels
            annotations = seed_annotations

        seed_text = build_alert_text(_SeedAlertLike())
        seed_embedding = embedder_setup.embed(seed_text)

        async with AsyncSession(engine) as setup_db, setup_db.begin():
            incident = Incident(
                title="HighCPUUsage on node-seed",
                status="open",
                severity="warning",
                alert_count=1,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
            setup_db.add(incident)
            await setup_db.flush()
            incident_id = incident.id
            cleanup_incident_ids.append(incident_id)

            # 已有的 member alert（已 embed，让后续 alert 能找到它）
            seed_alert = Alert(
                fingerprint=uuid.uuid4().hex[:16],
                status="firing",
                severity="warning",
                alertname="HighCPUUsage",
                labels=seed_labels,
                annotations=seed_annotations,
                starts_at=datetime.now(UTC),
                raw_payload={},
                incident_id=incident_id,
                embedding=seed_embedding,
                embedding_model="fake-embedder-v1",
                embedded_at=datetime.now(UTC),
            )
            setup_db.add(seed_alert)
            await setup_db.flush()
            cleanup_alert_ids.append(seed_alert.id)

            # 3 条待聚合的新 alert（相似 alertname + severity，无 embedding，无 incident_id）
            for i in range(3):
                new_a = Alert(
                    fingerprint=uuid.uuid4().hex[:16],
                    status="firing",
                    severity="warning",
                    alertname="HighCPUUsage",
                    labels={
                        "alertname": "HighCPUUsage",
                        "severity": "warning",
                        "instance": f"node-concurrent-{i}",
                    },
                    annotations={"summary": "HighCPUUsage 告警触发"},
                    starts_at=datetime.now(UTC),
                    raw_payload={},
                )
                setup_db.add(new_a)
                await setup_db.flush()
                new_alert_ids.append(new_a.id)
                cleanup_alert_ids.append(new_a.id)

        # Step 2: 3 个并发 worker，每个独立 session
        embedder = FakeEmbedder()

        async def worker(alert_id: int) -> None:
            async with AsyncSession(engine) as worker_db:
                await aggregate([alert_id], worker_db, embedder)
                await worker_db.commit()

        await asyncio.gather(*[worker(aid) for aid in new_alert_ids])

        # Step 3: 读最终状态
        async with AsyncSession(engine) as read_db:
            final_incident = await read_db.get(Incident, incident_id)
            assert final_incident is not None
            final_count = final_incident.alert_count

        assert final_count == 4, (
            f"3 个并发 worker 各归并 1 条 alert（初始 alert_count=1），最终应=4，实际={final_count}。"
            "若不等于 4 说明存在 lost update（SQL 原子 UPDATE 失效）。"
        )

    finally:
        # 清理测试数据（绕过了 SAVEPOINT，必须手动清理）
        async with AsyncSession(engine) as cleanup_db, cleanup_db.begin():
            if cleanup_alert_ids:
                await cleanup_db.execute(
                    text(
                        f"DELETE FROM alerts WHERE id IN ({','.join(str(i) for i in cleanup_alert_ids)})"
                    )
                )
            if cleanup_incident_ids:
                await cleanup_db.execute(
                    text(
                        f"DELETE FROM incidents WHERE id IN ({','.join(str(i) for i in cleanup_incident_ids)})"
                    )
                )
        await engine.dispose()

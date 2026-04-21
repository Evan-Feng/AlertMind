"""Incidents API 测试。

覆盖 GET /api/v1/incidents 和 GET /api/v1/incidents/{incident_id} 的
典型、边界、异常三类场景。

关键验证：
- selectinload 生效：IncidentDetail.alerts 数量与 alert_count 一致
- severity=NULL 过滤行为
- 分页边界
- 排序：默认 last_seen_at DESC
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.models.alert import Alert
from alertmind.models.incident import Incident

# ---------------------------------------------------------------------------
# 辅助工厂函数
# ---------------------------------------------------------------------------


async def _create_incident(
    db: AsyncSession,
    *,
    title: str = "HighCPUUsage on node-1",
    status: str = "open",
    severity: str | None = "warning",
    alert_count: int = 0,
    last_seen_at: datetime | None = None,
    first_seen_at: datetime | None = None,
) -> Incident:
    """创建一个 Incident 并 flush。"""
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


async def _create_alert(
    db: AsyncSession,
    *,
    alertname: str = "HighCPUUsage",
    severity: str = "warning",
    incident_id: int | None = None,
    starts_at: datetime | None = None,
) -> Alert:
    """创建一个 Alert 并 flush。"""
    alert = Alert(
        fingerprint=uuid.uuid4().hex[:16],
        status="firing",
        severity=severity,
        alertname=alertname,
        labels={"alertname": alertname, "severity": severity},
        annotations={"summary": f"{alertname} 测试告警"},
        starts_at=starts_at or datetime.now(UTC),
        raw_payload={"test": True},
        incident_id=incident_id,
    )
    db.add(alert)
    await db.flush()
    return alert


# ---------------------------------------------------------------------------
# typical: list incidents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_incidents_returns_paginated_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """3 个 incident → {total:3, pages:1, items:3, page:1, page_size:20}。"""
    # Arrange
    for i in range(3):
        await _create_incident(db_session, title=f"Incident {i}", severity="warning")

    # Act
    response = await client.get("/api/v1/incidents")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["pages"] == 1
    assert len(body["items"]) == 3
    assert body["page"] == 1
    assert body["page_size"] == 20


@pytest.mark.asyncio
async def test_get_incident_by_id_returns_detail_with_alerts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /incidents/{id} 返回 IncidentDetail，含 alerts 数组。"""
    # Arrange
    incident = await _create_incident(db_session, alert_count=2, severity="critical")
    await _create_alert(db_session, alertname="HighCPUUsage", incident_id=incident.id)
    await _create_alert(db_session, alertname="HighCPUUsage", incident_id=incident.id)

    # Act
    response = await client.get(f"/api/v1/incidents/{incident.id}")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == incident.id
    assert body["title"] == incident.title
    assert "alerts" in body
    assert isinstance(body["alerts"], list)


@pytest.mark.asyncio
async def test_get_incident_selectinload_populates_alerts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """selectinload 生效：len(response.alerts) == incident.alert_count。

    若 selectinload 失效（lazy='raise'），整个 endpoint 会返回 500。
    """
    # Arrange
    incident = await _create_incident(db_session, alert_count=3, severity="warning")
    for _ in range(3):
        await _create_alert(db_session, alertname="DiskFull", incident_id=incident.id)

    # Act
    response = await client.get(f"/api/v1/incidents/{incident.id}")

    # Assert
    assert response.status_code == 200, f"selectinload 失效会导致 500，实际={response.status_code}"
    body = response.json()
    assert len(body["alerts"]) == 3, (
        f"alerts 数量应与 alert_count=3 一致，实际 len(alerts)={len(body['alerts'])}"
    )
    assert body["alert_count"] == 3


@pytest.mark.asyncio
async def test_list_incidents_filter_by_status_open(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """status=open 过滤：只返回 open 状态的 incident。"""
    # Arrange
    await _create_incident(db_session, status="open", title="Open Incident 1")
    await _create_incident(db_session, status="open", title="Open Incident 2")
    await _create_incident(db_session, status="resolved", title="Resolved Incident")

    # Act
    response = await client.get("/api/v1/incidents", params={"status": "open"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    for item in body["items"]:
        assert item["status"] == "open"


@pytest.mark.asyncio
async def test_list_incidents_filter_by_status_resolved(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """status=resolved 过滤：只返回 resolved 状态的 incident。"""
    # Arrange
    await _create_incident(db_session, status="open", title="Open Incident")
    await _create_incident(db_session, status="resolved", title="Resolved Incident 1")
    await _create_incident(db_session, status="resolved", title="Resolved Incident 2")

    # Act
    response = await client.get("/api/v1/incidents", params={"status": "resolved"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    for item in body["items"]:
        assert item["status"] == "resolved"


@pytest.mark.asyncio
async def test_list_incidents_filter_by_severity_critical(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """severity=critical 只返回 severity='critical' 的 incident。"""
    # Arrange
    await _create_incident(db_session, severity="critical", title="Critical Incident")
    await _create_incident(db_session, severity="warning", title="Warning Incident")
    await _create_incident(db_session, severity=None, title="Unknown Severity Incident")

    # Act
    response = await client.get("/api/v1/incidents", params={"severity": "critical"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_list_incidents_default_sort_by_last_seen_at_desc(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """默认按 last_seen_at DESC 排序：最近的 incident 排在最前面。"""
    # Arrange
    now = datetime.now(UTC)
    t1 = now - timedelta(hours=3)
    t2 = now - timedelta(hours=1)
    t3 = now - timedelta(minutes=10)

    await _create_incident(db_session, title="Oldest Incident", last_seen_at=t1)
    await _create_incident(db_session, title="Middle Incident", last_seen_at=t2)
    await _create_incident(db_session, title="Newest Incident", last_seen_at=t3)

    # Act
    response = await client.get("/api/v1/incidents")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    titles = [item["title"] for item in body["items"]]
    assert titles[0] == "Newest Incident", f"第一条应是最新的，实际顺序={titles}"
    assert titles[-1] == "Oldest Incident", f"最后一条应是最旧的，实际顺序={titles}"


# ---------------------------------------------------------------------------
# boundary: list incidents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_incidents_empty_database_returns_empty_items(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """0 incident → {total:0, items:[], pages:0}。"""
    # Act
    response = await client.get("/api/v1/incidents")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["pages"] == 0


@pytest.mark.asyncio
async def test_list_incidents_severity_null_returned_when_no_filter(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """有 incident.severity=NULL，不传 severity 过滤 → 应出现在 items 中。"""
    # Arrange
    incident_null = await _create_incident(db_session, severity=None, title="Unrated Incident")
    incident_warn = await _create_incident(db_session, severity="warning", title="Warning Incident")

    # Act
    response = await client.get("/api/v1/incidents")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    ids = {item["id"] for item in body["items"]}
    assert incident_null.id in ids, "severity=NULL 的 incident 在无过滤条件下应出现"
    assert incident_warn.id in ids


@pytest.mark.asyncio
async def test_list_incidents_severity_null_excluded_when_filter_critical(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """有 2 个 incident（severity=NULL + severity=critical），传 severity=critical → 只返回 critical。"""
    # Arrange
    incident_null = await _create_incident(db_session, severity=None, title="Unrated Incident")
    incident_crit = await _create_incident(
        db_session, severity="critical", title="Critical Incident"
    )

    # Act
    response = await client.get("/api/v1/incidents", params={"severity": "critical"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == incident_crit.id
    ids = {item["id"] for item in body["items"]}
    assert incident_null.id not in ids, "severity=NULL 的 incident 应被 critical 过滤排除"


@pytest.mark.asyncio
async def test_list_incidents_pagination_last_page_partial(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """total=25 + page_size=10 → page=3 返回 5 条，pages=3。"""
    # Arrange
    for i in range(25):
        await _create_incident(db_session, title=f"Incident {i:02d}")

    # Act
    response = await client.get("/api/v1/incidents", params={"page": 3, "page_size": 10})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 25
    assert body["pages"] == 3
    assert body["page"] == 3
    assert len(body["items"]) == 5, f"最后一页应有 5 条，实际={len(body['items'])}"


@pytest.mark.asyncio
async def test_list_incidents_pagination_page1(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """total=5 + page_size=3 → page=1 返回 3 条，pages=2。"""
    # Arrange
    for i in range(5):
        await _create_incident(db_session, title=f"Incident {i}")

    # Act
    response = await client.get("/api/v1/incidents", params={"page": 1, "page_size": 3})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["pages"] == 2
    assert len(body["items"]) == 3


# ---------------------------------------------------------------------------
# exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_incident_nonexistent_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /incidents/99999 → 404 + {detail: 'incident not found'}。"""
    # Act
    response = await client.get("/api/v1/incidents/99999")

    # Assert
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "incident not found"


@pytest.mark.asyncio
async def test_get_incident_invalid_id_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /incidents/not-an-int → 422（路径参数类型校验失败）。"""
    # Act
    response = await client.get("/api/v1/incidents/not-an-int")

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_incidents_invalid_page_zero_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?page=0 → 422（page 下限 ge=1）。"""
    # Act
    response = await client.get("/api/v1/incidents", params={"page": 0})

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_incidents_invalid_page_negative_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?page=-1 → 422（page 下限 ge=1）。"""
    # Act
    response = await client.get("/api/v1/incidents", params={"page": -1})

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_incidents_invalid_page_size_zero_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?page_size=0 → 422（page_size 下限 ge=1）。"""
    # Act
    response = await client.get("/api/v1/incidents", params={"page_size": 0})

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_incidents_invalid_page_size_over_limit_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?page_size=101 → 422（page_size 上限 le=100）。"""
    # Act
    response = await client.get("/api/v1/incidents", params={"page_size": 101})

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_incidents_invalid_severity_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?severity=fatal → 422（不在 IncidentSeverity 枚举内）。"""
    # Act
    response = await client.get("/api/v1/incidents", params={"severity": "fatal"})

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_incident_detail_alert_fields_correct(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """IncidentDetail.alerts 中每个 alert 的关键字段正确（alertname / severity / status）。"""
    # Arrange
    incident = await _create_incident(db_session, alert_count=1, severity="critical")
    alert = await _create_alert(
        db_session,
        alertname="CertificateExpiry",
        severity="critical",
        incident_id=incident.id,
    )

    # Act
    response = await client.get(f"/api/v1/incidents/{incident.id}")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert len(body["alerts"]) == 1
    alert_body = body["alerts"][0]
    assert alert_body["alertname"] == "CertificateExpiry"
    assert alert_body["severity"] == "critical"
    assert alert_body["id"] == alert.id

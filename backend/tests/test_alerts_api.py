"""Alerts 查询 API 测试套件。

覆盖场景：
- typical：分页列表、详情、按 status/severity/alertname 过滤
- boundary：空库、page_size 边界校验、分页计数、排序正确性
- exception：不存在的 id 404、非法分页参数 422、非法 status 过滤 422
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.models.alert import Alert

ALERTS_URL = "/api/v1/alerts"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_alert(
    fingerprint: str,
    status: str = "firing",
    severity: str = "warning",
    alertname: str = "TestAlert",
    starts_at: datetime | None = None,
) -> Alert:
    """构造一个 Alert ORM 对象，直接插入测试库。

    :param fingerprint: 唯一指纹（caller 负责唯一性）
    :param status: firing | resolved
    :param severity: critical | warning | info
    :param alertname: Prometheus 规则名
    :param starts_at: 告警开始时间，默认 UTC now
    :returns: 未持久化的 Alert 实例
    """
    if starts_at is None:
        starts_at = datetime.now(tz=UTC)
    return Alert(
        fingerprint=fingerprint,
        status=status,
        severity=severity,
        alertname=alertname,
        labels={"alertname": alertname, "severity": severity},
        annotations={"summary": f"{alertname} is alerting"},
        starts_at=starts_at,
        ends_at=None if status == "firing" else datetime.now(tz=UTC),
        generator_url=f"http://prometheus.example.com/graph?fp={fingerprint}",
        raw_payload={"test": True, "fingerprint": fingerprint},
    )


async def _insert_alert(db: AsyncSession, alert: Alert) -> Alert:
    """将 Alert 插入测试库并 flush，返回带 id 的对象。"""
    db.add(alert)
    await db.flush()
    await db.refresh(alert)
    return alert


# ---------------------------------------------------------------------------
# typical
# ---------------------------------------------------------------------------


async def test_list_alerts_returns_paginated_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """正常有数据时，列表接口应返回含 items/total/page/page_size/pages 的分页响应。"""
    # Arrange — 插入 3 条 alerts
    for i in range(3):
        await _insert_alert(db_session, _make_alert(fingerprint=f"list_page_fp{i:04d}"))

    # Act
    response = await client.get(ALERTS_URL)

    # Assert
    assert response.status_code == 200
    body = response.json()

    # 分页结构字段断言
    assert "items" in body, "response 缺少 items 字段"
    assert "total" in body, "response 缺少 total 字段"
    assert "page" in body, "response 缺少 page 字段"
    assert "page_size" in body, "response 缺少 page_size 字段"
    assert "pages" in body, "response 缺少 pages 字段（不是 size！）"
    assert "size" not in body, "response 不应包含 size 字段（已重命名为 page_size）"

    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 20  # 默认值
    assert body["pages"] == 1
    assert len(body["items"]) == 3


async def test_get_alert_by_id_returns_full_detail(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """按 id 获取单条 alert，应返回完整字段详情。"""
    # Arrange
    alert = await _insert_alert(
        db_session,
        _make_alert(
            fingerprint="detail_fp_0001",
            alertname="HighMemoryUsage",
            status="firing",
            severity="critical",
        ),
    )
    alert_id = alert.id

    # Act
    response = await client.get(f"{ALERTS_URL}/{alert_id}")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == alert_id
    assert body["fingerprint"] == "detail_fp_0001"
    assert body["alertname"] == "HighMemoryUsage"
    assert body["status"] == "firing"
    assert body["severity"] == "critical"
    assert body["ends_at"] is None  # firing 状态
    assert "created_at" in body
    assert "updated_at" in body


async def test_list_alerts_filter_by_status_works(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """按 ?status=firing 过滤时，只返回 firing 状态的 alerts。"""
    # Arrange — 插入 2 firing + 1 resolved
    await _insert_alert(db_session, _make_alert(fingerprint="status_f_fp001", status="firing"))
    await _insert_alert(db_session, _make_alert(fingerprint="status_f_fp002", status="firing"))
    await _insert_alert(db_session, _make_alert(fingerprint="status_r_fp001", status="resolved"))

    # Act
    response = await client.get(ALERTS_URL, params={"status": "firing"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert all(item["status"] == "firing" for item in body["items"])


async def test_list_alerts_filter_by_severity_works(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """按 ?severity=critical 过滤时，只返回 critical 级别的 alerts。"""
    # Arrange — 插入 1 critical + 2 warning
    await _insert_alert(db_session, _make_alert(fingerprint="sev_c_fp001", severity="critical"))
    await _insert_alert(db_session, _make_alert(fingerprint="sev_w_fp001", severity="warning"))
    await _insert_alert(db_session, _make_alert(fingerprint="sev_w_fp002", severity="warning"))

    # Act
    response = await client.get(ALERTS_URL, params={"severity": "critical"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["severity"] == "critical"
    assert body["items"][0]["fingerprint"] == "sev_c_fp001"


async def test_list_alerts_filter_by_alertname_works(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """按 ?alertname=HighCPUUsage 过滤时，只返回该规则名的 alerts。"""
    # Arrange — 插入 2 HighCPUUsage + 1 DiskSpaceLow
    await _insert_alert(
        db_session,
        _make_alert(fingerprint="aname_cpu_fp001", alertname="HighCPUUsage"),
    )
    await _insert_alert(
        db_session,
        _make_alert(fingerprint="aname_cpu_fp002", alertname="HighCPUUsage"),
    )
    await _insert_alert(
        db_session,
        _make_alert(fingerprint="aname_disk_fp001", alertname="DiskSpaceLow"),
    )

    # Act
    response = await client.get(ALERTS_URL, params={"alertname": "HighCPUUsage"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert all(item["alertname"] == "HighCPUUsage" for item in body["items"])


# ---------------------------------------------------------------------------
# boundary
# ---------------------------------------------------------------------------


async def test_list_alerts_empty_db_returns_empty_items_zero_total(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """空库时，列表接口应返回 items=[]、total=0、pages=0。"""
    # Act — 不插入任何数据
    response = await client.get(ALERTS_URL)

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0
    assert body["page"] == 1


async def test_list_alerts_page_size_max_100_enforced(
    client: AsyncClient,
) -> None:
    """?page_size=101 超出上限（100），应返回 422。"""
    # Act
    response = await client.get(ALERTS_URL, params={"page_size": 101})

    # Assert
    assert response.status_code == 422


async def test_list_alerts_page_size_min_1_enforced(
    client: AsyncClient,
) -> None:
    """?page_size=0 低于下限（1），应返回 422。"""
    # Act
    response = await client.get(ALERTS_URL, params={"page_size": 0})

    # Assert
    assert response.status_code == 422


async def test_list_alerts_pagination_correct_pages_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """插入 25 行，?page_size=10 时 pages=3；?page=3 应只返回 5 条。"""
    # Arrange — 插入 25 条 alerts
    base_time = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    for i in range(25):
        starts_at = base_time + timedelta(minutes=i)
        await _insert_alert(
            db_session,
            _make_alert(fingerprint=f"page25_fp{i:04d}", starts_at=starts_at),
        )

    # Act — 获取第一页元数据
    r1 = await client.get(ALERTS_URL, params={"page": 1, "page_size": 10})
    assert r1.status_code == 200
    body1 = r1.json()

    # Assert — 总页数
    assert body1["total"] == 25
    assert body1["pages"] == 3
    assert len(body1["items"]) == 10

    # Act — 第三页
    r3 = await client.get(ALERTS_URL, params={"page": 3, "page_size": 10})
    assert r3.status_code == 200
    body3 = r3.json()

    # Assert — 第三页只有 5 条
    assert len(body3["items"]) == 5


async def test_list_alerts_default_sort_by_starts_at_desc(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """列表接口默认按 starts_at DESC 排序，最新告警排第一。"""
    # Arrange — 插入 3 条不同 starts_at 的 alerts（故意乱序插入）
    base_time = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
    alerts_data = [
        ("sort_fp_001", base_time),
        ("sort_fp_002", base_time + timedelta(hours=2)),  # 最新
        ("sort_fp_003", base_time + timedelta(hours=1)),
    ]
    for fp, st in alerts_data:
        await _insert_alert(db_session, _make_alert(fingerprint=fp, starts_at=st))

    # Act
    response = await client.get(ALERTS_URL)

    # Assert — 第一条应是 starts_at 最大的（sort_fp_002）
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 3

    # 找到我们插入的 3 条，验证相对顺序
    our_fps = {
        i["fingerprint"]: i["starts_at"]
        for i in items
        if i["fingerprint"] in {fp for fp, _ in alerts_data}
    }
    assert len(our_fps) == 3

    # starts_at 应降序：sort_fp_002 > sort_fp_003 > sort_fp_001
    ordered_fps = sorted(our_fps, key=lambda fp: our_fps[fp], reverse=True)
    assert ordered_fps[0] == "sort_fp_002"
    assert ordered_fps[1] == "sort_fp_003"
    assert ordered_fps[2] == "sort_fp_001"


# ---------------------------------------------------------------------------
# exception
# ---------------------------------------------------------------------------


async def test_get_alert_nonexistent_id_returns_404(
    client: AsyncClient,
) -> None:
    """请求不存在的 alert id 时应返回 404，detail 含有明确提示。"""
    # Act — 使用极大 id，确保不存在
    response = await client.get(f"{ALERTS_URL}/9999999999")

    # Assert
    assert response.status_code == 404
    detail = response.json().get("detail", "")
    assert "alert" in detail.lower() or "not found" in detail.lower()


async def test_list_alerts_invalid_page_param_returns_422(
    client: AsyncClient,
) -> None:
    """?page=0 低于下限（1），应返回 422。"""
    # Act
    response = await client.get(ALERTS_URL, params={"page": 0})

    # Assert
    assert response.status_code == 422


async def test_list_alerts_invalid_negative_page_param_returns_422(
    client: AsyncClient,
) -> None:
    """?page=-1 为负数，应返回 422。"""
    # Act
    response = await client.get(ALERTS_URL, params={"page": -1})

    # Assert
    assert response.status_code == 422


async def test_list_alerts_invalid_status_filter_returns_422(
    client: AsyncClient,
) -> None:
    """?status=bogus 不是合法的 AlertStatus 枚举值，应返回 422。"""
    # Act
    response = await client.get(ALERTS_URL, params={"status": "bogus_status_value"})

    # Assert
    assert response.status_code == 422

"""Webhook 接收端测试套件。

覆盖场景：
- typical：正常 payload 入库、resolved upsert、endsAt 零值归一化
- boundary：多条 alerts、大 payload（接近上限）、幂等重放
- exception：无效 payload 422、Content-Length 超限 413、缺少必填字段 422
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.models.alert import Alert

WEBHOOK_URL = "/api/v1/webhook/alertmanager"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _count_alerts(db: AsyncSession) -> int:
    """查询当前测试 session 中 alerts 表行数。"""
    result = await db.execute(select(Alert))
    return len(result.scalars().all())


async def _get_alert_by_fingerprint(db: AsyncSession, fingerprint: str) -> Alert | None:
    """按 fingerprint 查询单条 alert。"""
    result = await db.execute(select(Alert).where(Alert.fingerprint == fingerprint))
    return result.scalar_one_or_none()


def _make_payload(
    fingerprint: str = "testfp0000000001",
    status: str = "firing",
    alertname: str = "TestAlert",
    severity: str = "warning",
    starts_at: str = "2026-04-20T10:00:00Z",
    ends_at: str = "0001-01-01T00:00:00Z",
    group_key: str = '{}:{alertname="TestAlert"}',
) -> dict[str, Any]:
    """构造最小合法 Alertmanager v4 payload，便于各测试定制字段。"""
    return {
        "version": "4",
        "groupKey": group_key,
        "truncatedAlerts": 0,
        "status": status,
        "receiver": "alertmind-webhook",
        "groupLabels": {"alertname": alertname},
        "commonLabels": {"alertname": alertname, "severity": severity},
        "commonAnnotations": {"summary": f"{alertname} is alerting"},
        "externalURL": "http://alertmanager.example.com",
        "alerts": [
            {
                "status": status,
                "labels": {"alertname": alertname, "severity": severity},
                "annotations": {"summary": f"{alertname} is alerting"},
                "startsAt": starts_at,
                "endsAt": ends_at,
                "generatorURL": "http://prometheus.example.com/graph",
                "fingerprint": fingerprint,
            }
        ],
    }


# ---------------------------------------------------------------------------
# typical
# ---------------------------------------------------------------------------


async def test_webhook_receives_valid_alertmanager_payload_returns_200_and_persists(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_payloads: dict[str, Any],
) -> None:
    """正常 firing payload 应返回 200，且 alert 被写入数据库。"""
    # Arrange
    payload = sample_payloads["firing_single"]

    # Act
    response = await client.post(WEBHOOK_URL, json=payload)

    # Assert — HTTP 层
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["upserted"] == 1

    # Assert — DB 层（直接查询，不绕路 API）
    # SAVEPOINT 模式下同一 connection 内的写入对后续 SELECT 立即可见
    alert = await _get_alert_by_fingerprint(db_session, "abc123def456abc1")
    assert alert is not None
    assert alert.fingerprint == "abc123def456abc1"
    assert alert.status == "firing"
    assert alert.severity == "critical"
    assert alert.alertname == "HighCPUUsage"


async def test_webhook_resolved_alert_updates_existing_row_via_upsert(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_payloads: dict[str, Any],
) -> None:
    """先 firing 后 resolved（同一 fingerprint），应 upsert 更新 status，不新增行。"""
    # Arrange — 先发 firing
    firing_payload = sample_payloads["firing_single"]
    r1 = await client.post(WEBHOOK_URL, json=firing_payload)
    assert r1.status_code == 200

    # Act — 再发 resolved（相同 fingerprint）
    resolved_payload = sample_payloads["resolved_single"]
    r2 = await client.post(WEBHOOK_URL, json=resolved_payload)
    assert r2.status_code == 200

    # Assert — DB 中仍只有 1 行，且 status 已更新为 resolved
    count = await _count_alerts(db_session)
    assert count == 1

    alert = await _get_alert_by_fingerprint(db_session, "abc123def456abc1")
    assert alert is not None
    assert alert.status == "resolved"
    assert alert.ends_at is not None  # resolved 时 ends_at 不应为 None


async def test_webhook_endsAt_zero_value_is_normalized_to_none(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """endsAt 为 '0001-01-01T00:00:00Z'（Go 零值）时，应归一化为 None 存入 DB。"""
    # Arrange
    payload = _make_payload(
        fingerprint="normfp0000000001",
        status="firing",
        ends_at="0001-01-01T00:00:00Z",
    )

    # Act
    response = await client.post(WEBHOOK_URL, json=payload)

    # Assert
    assert response.status_code == 200

    alert = await _get_alert_by_fingerprint(db_session, "normfp0000000001")
    assert alert is not None
    assert alert.ends_at is None, f"expected ends_at=None but got {alert.ends_at}"


# ---------------------------------------------------------------------------
# boundary
# ---------------------------------------------------------------------------


async def test_webhook_payload_with_multiple_alerts_persists_all(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_payloads: dict[str, Any],
) -> None:
    """单次 payload 包含 3 条 alerts 时，全部应写入 DB，response 计数正确。"""
    # Arrange
    payload = sample_payloads["multi_firing"]

    # Act
    response = await client.post(WEBHOOK_URL, json=payload)

    # Assert — HTTP 层
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 3
    assert body["upserted"] == 3

    # Assert — DB 层
    count = await _count_alerts(db_session)
    assert count == 3

    fingerprints = {"disk001prod0101", "disk002prod0202", "disk003prod0303"}
    for fp in fingerprints:
        alert = await _get_alert_by_fingerprint(db_session, fp)
        assert alert is not None, f"fingerprint {fp!r} not found in DB"


async def test_webhook_payload_at_size_limit_succeeds(
    client: AsyncClient,
) -> None:
    """Content-Length 略小于 10 MiB（9.5 MiB）的 payload 应被接受，返回 200 或 422。

    注意：9.5 MiB 的合法 JSON body 实际上几乎不可能通过 Pydantic 解析（内容无意义），
    所以只断言不是 413；422 也是合法（body 不合法）。
    """
    # Arrange — 9.5 MiB 的 body，通过 Content-Length 头声明
    body_size = int(9.5 * 1024 * 1024)
    # 用足够大的 JSON 字符串填充（不求合法，只求不触发 413）
    large_body = json.dumps({"version": "4", "padding": "x" * body_size}).encode()
    headers = {"content-length": str(len(large_body)), "content-type": "application/json"}

    # Act
    response = await client.post(WEBHOOK_URL, content=large_body, headers=headers)

    # Assert — 不应是 413（因为 Content-Length < 10 MiB 上限）
    assert response.status_code != 413, "9.5 MiB payload should NOT be rejected by size guard"


async def test_webhook_idempotent_duplicate_fingerprint_does_not_duplicate(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """同一 payload 发送两次，DB 中只应有 1 行（upsert 幂等性）。"""
    # Arrange
    payload = _make_payload(fingerprint="idemfp0000000001")

    # Act — 发送两次相同 payload
    r1 = await client.post(WEBHOOK_URL, json=payload)
    r2 = await client.post(WEBHOOK_URL, json=payload)

    # Assert
    assert r1.status_code == 200
    assert r2.status_code == 200

    count = await _count_alerts(db_session)
    assert count == 1, f"expected exactly 1 row after duplicate send, got {count}"


# ---------------------------------------------------------------------------
# exception
# ---------------------------------------------------------------------------


async def test_webhook_invalid_payload_returns_422(
    client: AsyncClient,
) -> None:
    """结构错误的 JSON（缺 version、alerts 等必填字段）应返回 422。"""
    # Arrange — 传一个完全不符合 AlertmanagerWebhookPayload schema 的 dict
    invalid_body = {
        "not_a_webhook": True,
        "random_field": "garbage_value",
    }

    # Act
    response = await client.post(WEBHOOK_URL, json=invalid_body)

    # Assert
    assert response.status_code == 422
    detail = response.json().get("detail")
    assert detail is not None


async def test_webhook_payload_over_10mb_returns_413(
    client: AsyncClient,
) -> None:
    """Content-Length 超过 10 MiB 时应直接返回 413，不读取 body。"""
    # Arrange — 通过 Content-Length 头声明超大 payload（实际 body 可以很小）
    over_limit = 11 * 1024 * 1024  # 11 MiB
    headers = {
        "content-length": str(over_limit),
        "content-type": "application/json",
    }
    # body 内容不重要，Content-Length 守卫在读 body 之前已经触发
    tiny_body = b"{}"

    # Act
    response = await client.post(WEBHOOK_URL, content=tiny_body, headers=headers)

    # Assert
    assert response.status_code == 413
    detail = response.json().get("detail", "")
    assert "10 MiB" in detail or "10" in str(detail)


async def test_webhook_missing_required_fields_returns_422(
    client: AsyncClient,
) -> None:
    """payload 缺少顶层必填字段（version、status、receiver）时应返回 422。"""
    # Arrange — 只有 groupKey，缺少 version / status / receiver
    incomplete_payload = {
        "groupKey": "{}:{}",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "MissingFieldsAlert"},
                "annotations": {},
                "startsAt": "2026-04-20T10:00:00Z",
                "fingerprint": "missingfp000001",
            }
        ],
    }

    # Act
    response = await client.post(WEBHOOK_URL, json=incomplete_payload)

    # Assert
    assert response.status_code == 422

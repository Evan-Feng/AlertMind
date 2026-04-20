"""Alertmanager Webhook 接收端。

职责：
- 接收 Alertmanager v4 webhook payload
- 将每条 alert 归一化后写入 ``alerts`` 表（按 ``fingerprint`` upsert）
- 返回 200 供 Alertmanager 判定投递成功

错误语义：
- payload 超过 10 MiB：413（不读 body，依据 ``Content-Length`` 头直接拒绝）
- payload 解析失败：422（由 FastAPI 自动产出，ValidationError handler 补充日志）
- 写库失败：500（抛出后由全局 handler 转 500，促使 Alertmanager 重试）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.api.deps import get_db
from alertmind.core.aggregator import safe_aggregate
from alertmind.models.alert import Alert, AlertSeverity, AlertStatus
from alertmind.schemas.webhook import AlertmanagerAlert, AlertmanagerWebhookPayload
from alertmind.utils.embedding import Embedder

router = APIRouter(prefix="/webhook", tags=["webhook"])

DbDep = Annotated[AsyncSession, Depends(get_db)]

# 单次 webhook payload 上限 10 MiB；Alertmanager 分组合理配置下远不会触达。
MAX_PAYLOAD_BYTES: int = 10 * 1024 * 1024

# severity label 合法值集合；不在其中时回落到 "info"（不阻断入库）。
_VALID_SEVERITIES: frozenset[str] = frozenset(item.value for item in AlertSeverity)
_VALID_STATUSES: frozenset[str] = frozenset(item.value for item in AlertStatus)


def _normalize_severity(raw: Any) -> str:
    """将 labels.severity 归一化到合法枚举值；无法识别时回落 ``info``。

    :param raw: labels 字典中原始 severity 值（可能是 None / 任意大小写字符串）
    :returns: critical | warning | info 三者之一
    """
    if not isinstance(raw, str):
        return AlertSeverity.INFO.value
    value = raw.strip().lower()
    if value in _VALID_SEVERITIES:
        return value
    return AlertSeverity.INFO.value


def _normalize_status(raw: str) -> str:
    """将 alert.status 归一化到合法枚举值；非法值回落 ``firing``（保守取 firing 避免误判解除）。"""
    value = raw.strip().lower()
    if value in _VALID_STATUSES:
        return value
    return AlertStatus.FIRING.value


def _extract_alertname(alert: AlertmanagerAlert) -> str:
    """从 labels 中提取 alertname；缺省时回落 ``"unknown"``。"""
    value = alert.labels.get("alertname")
    if isinstance(value, str) and value.strip():
        return value.strip()[:255]
    return "unknown"


def _build_alert_values(alert: AlertmanagerAlert, raw_payload: dict[str, Any]) -> dict[str, Any]:
    """把单条 Alertmanager alert 归一化成 Alert 表可直接 upsert 的字段字典。

    :param alert: 解析后的单条 Alertmanager alert
    :param raw_payload: 本条 alert 对应的原始 dict（写入 ``raw_payload`` 字段保留审计能力）
    :returns: 可直接作为 ``insert(Alert).values(**...)`` 入参的字典
    """
    return {
        "fingerprint": alert.fingerprint,
        "status": _normalize_status(alert.status),
        "severity": _normalize_severity(alert.labels.get("severity")),
        "alertname": _extract_alertname(alert),
        "labels": dict(alert.labels),
        "annotations": dict(alert.annotations),
        "starts_at": alert.starts_at,
        "ends_at": alert.ends_at,
        "generator_url": alert.generator_url,
        "raw_payload": raw_payload,
    }


async def _upsert_alert(session: AsyncSession, values: dict[str, Any]) -> int:
    """按 ``fingerprint`` 冲突策略 upsert 一条 alert，并返回该行主键 ``id``。

    冲突时只覆盖可变字段（status/severity/alertname/ends_at/generator_url/labels/
    annotations/raw_payload/updated_at）；不动 ``id`` / ``fingerprint`` /
    ``starts_at`` / ``created_at`` / ``incident_id`` / ``embedding`` /
    ``embedding_model`` / ``embedded_at``。

    :returns: 本行最终 ``alerts.id``；PostgreSQL 对 ``ON CONFLICT DO UPDATE``
        的 ``RETURNING`` 语义保证无论是新插入还是 update 都能拿到 id，
        供后续 BackgroundTasks 做异步聚合。
    """
    insert_stmt = pg_insert(Alert).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["fingerprint"],
        set_={
            "status": insert_stmt.excluded.status,
            "severity": insert_stmt.excluded.severity,
            "alertname": insert_stmt.excluded.alertname,
            "ends_at": insert_stmt.excluded.ends_at,
            "generator_url": insert_stmt.excluded.generator_url,
            "labels": insert_stmt.excluded.labels,
            "annotations": insert_stmt.excluded.annotations,
            "raw_payload": insert_stmt.excluded.raw_payload,
            "updated_at": func.now(),
        },
    ).returning(Alert.id)
    result = await session.execute(upsert_stmt)
    alert_id: int = result.scalar_one()
    return alert_id


def _enforce_content_length(request: Request) -> None:
    """按 ``Content-Length`` 头拒收超大 payload；拒收时抛 413。

    注意：``Content-Length`` 可能缺失（chunked transfer）。MVP 阶段 Alertmanager
    一定会带这个头；缺失时放行到 body 读取阶段（FastAPI/Starlette 自身会处理）。
    """
    header = request.headers.get("content-length")
    if header is None:
        return
    try:
        length = int(header)
    except ValueError:
        return
    if length > MAX_PAYLOAD_BYTES:
        logger.warning(
            "webhook rejected: payload too large, content-length={} limit={}",
            length,
            MAX_PAYLOAD_BYTES,
        )
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="payload exceeds 10 MiB limit",
        )


@router.post(
    "/alertmanager",
    summary="Alertmanager webhook receiver",
    status_code=status.HTTP_200_OK,
)
async def receive_alertmanager_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: DbDep,
) -> dict[str, Any]:
    """接收 Alertmanager v4 webhook，upsert 所有 alerts 到数据库。

    同步路径只做 upsert；embedding + 聚合由 :func:`alertmind.core.aggregator.safe_aggregate`
    通过 :class:`fastapi.BackgroundTasks` 异步触发，保证 Alertmanager 的重试窗口
    ≤ 200 ms（不等模型推理）。

    :param request: 原始 FastAPI Request，用于读取 ``Content-Length`` 头做体量守卫，
        以及访问 ``app.state.embedder`` 单例。
    :param background_tasks: FastAPI 注入的后台任务队列；本 handler 把聚合工作
        转给它执行。
    :param db: 数据库会话（依赖注入）。
    :returns: ``{"received": N, "upserted": N, "aggregation_status": "queued"}``。
    :raises HTTPException:
        - 413 payload 超过 10 MiB
        - 422 payload 解析失败（由 FastAPI 自动产出）
        - 500 DB 写入失败
    """
    _enforce_content_length(request)

    raw = await request.json()
    try:
        payload = AlertmanagerWebhookPayload.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError 捕获后由 FastAPI 转 422
        # FastAPI 内置 ValidationError 处理依赖 Body() 声明；我们走 Request.json()
        # 手动解析，需要显式兜底并落日志。
        logger.warning(
            "webhook payload validation failed: err={!r} body_preview={}",
            exc,
            _preview(raw),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid alertmanager payload",
        ) from exc

    raw_alerts: list[dict[str, Any]] = (
        list(raw.get("alerts") or []) if isinstance(raw, dict) else []
    )

    alert_ids: list[int] = []
    for index, alert in enumerate(payload.alerts):
        raw_alert = raw_alerts[index] if index < len(raw_alerts) else {}
        values = _build_alert_values(alert, raw_alert)
        try:
            alert_id = await _upsert_alert(db, values)
        except Exception as exc:
            logger.error(
                "webhook upsert failed: fingerprint={} err={!r}",
                alert.fingerprint,
                exc,
            )
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="failed to persist alert",
            ) from exc
        alert_ids.append(alert_id)

    try:
        await db.commit()
    except Exception as exc:
        logger.error("webhook commit failed: err={!r}", exc)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to commit alerts",
        ) from exc

    upserted = len(alert_ids)

    # 提交给后台任务：embed + 聚合。绝不能在同步路径做（会让 Alertmanager 超时重试）。
    embedder = _get_app_embedder(request)
    background_tasks.add_task(safe_aggregate, alert_ids, embedder)

    logger.info(
        "webhook accepted: group_key={} received={} upserted={} queued_for_aggregate={}",
        payload.group_key,
        len(payload.alerts),
        upserted,
        len(alert_ids),
    )
    return {
        "received": len(payload.alerts),
        "upserted": upserted,
        "aggregation_status": "queued",
    }


def _get_app_embedder(request: Request) -> Embedder:
    """从 ``app.state`` 取启动期加载的全局 Embedder 实例。

    不走依赖注入（:func:`alertmind.api.deps.get_embedder`）是为了让 webhook
    路由对 embedder 的获取逻辑与其他依赖解耦——BackgroundTasks 需要直接持有
    实例（异步路径没有 Request 可用），因此在 handler 里显式取出再透传给
    :func:`alertmind.core.aggregator.safe_aggregate`。

    注：未显式校验 ``isinstance(embedder, Embedder)``，是为了允许测试套件
    注入 duck-typed 替身（具备 ``embed`` / ``model_name``），避免每个测试
    都要真实加载 400 MB 的 sentence-transformers 权重。生产路径由 lifespan
    钩子保证写入的就是真实 Embedder。
    """
    embedder: Embedder | None = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise RuntimeError("embedder not initialized; lifespan hook missing?")
    return embedder


def _preview(raw: Any) -> str:
    """生成 payload 摘要（截断 200 字符），防止日志爆炸。"""
    text = repr(raw)
    if len(text) > 200:
        return text[:200] + "..."
    return text

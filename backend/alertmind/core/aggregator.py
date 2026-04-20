"""告警聚合核心逻辑。

功能：

- :func:`aggregate`：接收一批 alert id，按顺序逐条做 embedding + 归并 / 新建
  :class:`Incident`；事务由 caller 的 :class:`AsyncSession` 控制，本函数不
  显式 commit。
- :func:`safe_aggregate`：:mod:`fastapi.BackgroundTasks` 入口。独占一个新
  session（:func:`alertmind.db.session.new_session`），异常只落结构化日志、
  不 re-raise，保证 webhook 同步路径永不被后台失败反噬。

聚合决策（MVP v0.1）：

- 在 24h 窗口内、``status = 'open'`` 的 incidents 的成员 alerts 中，按
  ``pgvector`` 的 cosine distance 做最近邻查询（HNSW 索引）。
- 阈值：cosine similarity >= ``settings.aggregation_similarity_threshold``
  （对应 distance < ``1 - threshold``）。
- 命中即归并；否则新建 incident。
- 不做 ``group_by`` 硬分区（v0.2 再做）。

幂等性：同一条 alert 重复送进本模块不会重复计算 embedding / 重复归并，
通过 ``alert.incident_id IS NOT NULL`` 短路。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from loguru import logger
from sqlalchemy import case, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.config import settings
from alertmind.db.session import new_session
from alertmind.models.alert import Alert
from alertmind.models.incident import Incident, IncidentStatus
from alertmind.utils.embedding import Embedder, build_alert_text

# 严重度数值 rank；合并时取 rank 更大者。
# 不能用字符串字典序（会得到 c < i < w 的错误结果）。
SEVERITY_RANK: Final[dict[str, int]] = {"critical": 3, "warning": 2, "info": 1}

# 构造 incident title 时优先尝试的 label key（存在且非空即取）。
# 选择顺序反映「业务可辨识度」：具体实例 > 节点 > Pod > 集群 > 命名空间。
_TITLE_LABEL_PRIORITY: Final[tuple[str, ...]] = (
    "instance",
    "node",
    "pod",
    "cluster",
    "namespace",
    "service",
    "job",
)


def merge_severity(current: str | None, incoming: str) -> str:
    """合并 incident 现有严重度与新 alert 的严重度，取 rank 更高者。

    :param current: incident 当前 severity，允许 ``None``（初始未定级）。
    :param incoming: 新 alert 的 severity。
    :returns: 合并后的 severity 值。

    规则：

    - ``current`` 为 ``None`` → 直接返回 ``incoming``。
    - 未知的 severity 值按 rank=0 处理（默认比任何合法值都低）。

    注：本函数仅用于 Python 侧合并（如测试断言或单线程推导）。
    聚合主路径的写回走 :func:`_severity_merge_expr` 生成的 SQL 侧
    CASE 表达式，保证并发 BackgroundTasks 下严重度不会被低 rank 反向覆盖。
    """
    if current is None:
        return incoming
    current_rank = SEVERITY_RANK.get(current, 0)
    incoming_rank = SEVERITY_RANK.get(incoming, 0)
    return incoming if incoming_rank > current_rank else current


def _severity_rank_case(severity_expr: Any) -> Any:
    """构造 SQL CASE，把 severity 字符串映射为 :data:`SEVERITY_RANK` 的整数。

    ``NULL`` 或未知值映射为 0。用于合并时对比 rank。
    """
    return case(
        {value: literal(rank) for value, rank in SEVERITY_RANK.items()},
        value=severity_expr,
        else_=literal(0),
    )


def _severity_merge_expr(current_column: Any, incoming_value: str) -> Any:
    """生成「严重度合并」的 SQL 表达式：取 rank 更大者。

    :param current_column: 代表 ``incidents.severity`` 的 SQLAlchemy 列表达式。
    :param incoming_value: 新 alert 的 severity 字符串。
    :returns: 适用于 ``update(Incident).values(severity=...)`` 的列表达式。

    行为等价于 Python 的 :func:`merge_severity`，但由 DB 在 UPDATE 时原子评估，
    避免「A 读旧值 → B 写新值 → A 用旧值覆盖」的 lost update 场景。
    """
    incoming_rank = SEVERITY_RANK.get(incoming_value, 0)
    incoming_literal = literal(incoming_value)
    return case(
        (current_column.is_(None), incoming_literal),
        (_severity_rank_case(current_column) >= literal(incoming_rank), current_column),
        else_=incoming_literal,
    )


def _build_incident_title(alert: Alert) -> str:
    """生成 incident title。

    规则（MVP v0.1）：

    - 优先级遍历 :data:`_TITLE_LABEL_PRIORITY`，取第一个存在且非空的
      ``{alertname} on {label_value}``。
    - 若上述 label 全部缺失，回落到 ``{alertname}``。

    示例：``'HighCPUUsage on node-1'`` / ``'DiskFull on db-master-1'``。

    :param alert: 用于提取 ``alertname`` / ``labels`` 的 Alert 实例。
    :returns: 不超过 Incident.title 字段上限（512 字符）的标题字符串。
    """
    alertname = (alert.alertname or "unknown").strip() or "unknown"
    labels = alert.labels if isinstance(alert.labels, dict) else {}
    for key in _TITLE_LABEL_PRIORITY:
        value = labels.get(key)
        if isinstance(value, str) and value.strip():
            title = f"{alertname} on {value.strip()}"
            return title[:512]
    return alertname[:512]


async def aggregate_one(alert_id: int, db: AsyncSession, embedder: Embedder) -> None:
    """处理单条 alert：必要时计算 embedding，归并到最近邻 incident 或新建一个。

    幂等：

    - ``alert`` 不存在 → 仅记 warning，静默返回。
    - ``alert.incident_id`` 已非空 → 直接返回（已经聚合过）。
    - ``alert.embedding`` 已非空 → 跳过模型计算，直接用现有向量查询。

    事务语义：本函数只做 :meth:`AsyncSession.flush`，不 :meth:`commit`。

    :param alert_id: ``alerts.id``。
    :param db: 由 caller 管理提交的异步 session。
    :param embedder: 复用 ``app.state`` 上的全局 Embedder，不可在本函数内新建。
    """
    alert = await db.get(Alert, alert_id)
    if alert is None:
        logger.warning("aggregate skipped: alert not found alert_id={}", alert_id)
        return
    if alert.incident_id is not None:
        # 已聚合过，直接跳过保证幂等。
        return

    # Step 1: 必要时计算 embedding 并写回。
    if alert.embedding is None:
        text = build_alert_text(alert)
        alert.embedding = embedder.embed(text)
        alert.embedding_model = embedder.model_name
        alert.embedded_at = datetime.now(UTC)
        # flush 让本次写入对后续 SELECT 可见，同时便于同批内「后到的相似 alert
        # 归入前一条刚开的 incident」的联动生效。
        await db.flush()

    # Step 2: 最近 24h 内、open incidents 的成员 alerts 中做最近邻检索。
    window_start = datetime.now(UTC) - timedelta(hours=settings.aggregation_window_hours)
    max_distance = 1.0 - settings.aggregation_similarity_threshold
    target_vec = alert.embedding

    stmt = (
        select(Incident)
        .join(Alert, Alert.incident_id == Incident.id)
        .where(Incident.status == IncidentStatus.OPEN.value)
        .where(Incident.last_seen_at >= window_start)
        .where(Alert.id != alert.id)
        .where(Alert.embedding.is_not(None))
        .where(Alert.embedding.cosine_distance(target_vec) < max_distance)
        .order_by(Alert.embedding.cosine_distance(target_vec))
        .limit(1)
    )
    result = await db.execute(stmt)
    candidate = result.scalars().first()

    # Step 3: 归并或新建 incident。
    if candidate is not None:
        alert.incident_id = candidate.id
        # 关键：alert_count / last_seen_at / severity 走单条原子 UPDATE，
        # 而不是「SELECT → Python 算 → ORM 回写」。后者在两个并发
        # BackgroundTasks 同时命中同一 incident 时会产生 lost update
        # （两次 SELECT 都读到 count=N，两次都写 count=N+1；严重度也可能
        # 被「更低 rank 的 tx」反向覆盖）。以下用 DB 侧的列表达式 +
        # CASE 合并，保证每条 alert 对这三个字段的贡献都是原子的。
        update_stmt = (
            update(Incident)
            .where(Incident.id == candidate.id)
            .values(
                alert_count=Incident.alert_count + 1,
                last_seen_at=func.greatest(Incident.last_seen_at, alert.starts_at),
                severity=_severity_merge_expr(Incident.severity, alert.severity),
            )
        )
        await db.execute(update_stmt)
        logger.info(
            "aggregate merged: alert_id={} -> incident_id={} incoming_severity={}",
            alert.id,
            candidate.id,
            alert.severity,
        )
        return

    new_incident = Incident(
        title=_build_incident_title(alert),
        status=IncidentStatus.OPEN.value,
        severity=alert.severity,
        alert_count=1,
        first_seen_at=alert.starts_at,
        last_seen_at=alert.starts_at,
    )
    db.add(new_incident)
    # flush 拿自增 id；也让后续同批 alert 的 SELECT 能看到这条新 incident。
    await db.flush()
    alert.incident_id = new_incident.id
    logger.info(
        "aggregate created: alert_id={} -> incident_id={} title={!r}",
        alert.id,
        new_incident.id,
        new_incident.title,
    )


async def aggregate(alert_ids: list[int], db: AsyncSession, embedder: Embedder) -> None:
    """批量聚合入口：按顺序逐条调用 :func:`aggregate_one`。

    注意：同批内若存在相似 alert，第一条会新建 incident，后续会因为
    :func:`aggregate_one` 内部的 ``flush`` 而匹配到这条新 incident，
    这是期望行为（保证单次 webhook 的批内一致性）。

    事务语义：本函数不 commit；由 caller（:func:`safe_aggregate` 或测试代码）
    通过 :func:`alertmind.db.session.new_session` 或显式 commit 来持久化。

    :param alert_ids: 需要聚合的 ``alerts.id`` 列表，按业务到达顺序排列。
    :param db: async session。
    :param embedder: 全局复用的 Embedder 实例。
    """
    if not alert_ids:
        return
    for alert_id in alert_ids:
        await aggregate_one(alert_id, db, embedder)


async def safe_aggregate(alert_ids: list[int], embedder: Embedder) -> None:
    """:mod:`fastapi.BackgroundTasks` 入口。

    行为约束：

    - 独占一个新 session（:func:`alertmind.db.session.new_session`），
      **不** 复用 webhook 同步路径的 session。
    - 成功时由 ``new_session`` 的上下文管理器自动 commit；
      抛异常时由其自动 rollback。
    - 异常仅写结构化日志（``alert_ids`` / ``error_type`` / ``error_msg``），
      **绝不** re-raise——否则 BackgroundTasks 会把错误吞到 stderr，且
      webhook 同步路径已经返回给 Alertmanager 200，上层无法感知此处失败。

    :param alert_ids: webhook 同步路径 upsert 后得到的 alert id 列表。
    :param embedder: 由 webhook handler 从 ``request.app.state.embedder`` 透传。
    """
    if not alert_ids:
        return
    try:
        async with new_session() as db:
            await aggregate(alert_ids, db, embedder)
    except Exception as exc:
        logger.bind(
            alert_ids=alert_ids,
            error_type=type(exc).__name__,
            error_msg=str(exc),
        ).exception("aggregation_failed")


__all__ = [
    "SEVERITY_RANK",
    "aggregate",
    "aggregate_one",
    "merge_severity",
    "safe_aggregate",
]

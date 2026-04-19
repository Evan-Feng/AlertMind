"""Alerts 查询 API。

- ``GET /api/v1/alerts``：支持按 status/severity/alertname 过滤 + 分页
- ``GET /api/v1/alerts/{alert_id}``：详情
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.api.deps import get_db
from alertmind.models.alert import Alert, AlertSeverity, AlertStatus
from alertmind.schemas.alert import AlertRead
from alertmind.schemas.common import Page

router = APIRouter(prefix="/alerts", tags=["alerts"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    summary="List alerts",
    response_model=Page[AlertRead],
)
async def list_alerts(
    db: DbDep,
    status_filter: Annotated[
        AlertStatus | None,
        Query(alias="status", description="按告警状态过滤"),
    ] = None,
    severity: Annotated[
        AlertSeverity | None,
        Query(description="按严重级别过滤"),
    ] = None,
    alertname: Annotated[
        str | None,
        Query(description="按 alertname 精确匹配"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="页码，1-based")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数，1~100")] = 20,
) -> Page[AlertRead]:
    """分页查询 alerts。

    :param db: 数据库会话（依赖注入）
    :param status_filter: 可选，按告警状态过滤（firing | resolved）
    :param severity: 可选，按严重级别过滤（critical | warning | info）
    :param alertname: 可选，按 alertname 精确匹配
    :param page: 页码，1-based
    :param page_size: 每页条数，1~100
    :returns: :class:`Page` 分页响应；默认按 ``starts_at DESC`` 排序
    """
    filters = []
    if status_filter is not None:
        filters.append(Alert.status == status_filter.value)
    if severity is not None:
        filters.append(Alert.severity == severity.value)
    if alertname is not None:
        filters.append(Alert.alertname == alertname)

    count_stmt = select(func.count()).select_from(Alert)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    list_stmt = select(Alert)
    if filters:
        list_stmt = list_stmt.where(*filters)
    list_stmt = (
        list_stmt.order_by(Alert.starts_at.desc()).limit(page_size).offset((page - 1) * page_size)
    )

    rows = (await db.execute(list_stmt)).scalars().all()
    items = [AlertRead.model_validate(row) for row in rows]

    return Page[AlertRead].from_query(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{alert_id}",
    summary="Get alert detail",
    response_model=AlertRead,
)
async def get_alert(alert_id: int, db: DbDep) -> AlertRead:
    """获取单条 alert 详情。

    :param alert_id: Alert 主键 id
    :param db: 数据库会话（依赖注入）
    :returns: :class:`AlertRead`
    :raises HTTPException: 404 若指定 id 不存在
    """
    row = (await db.execute(select(Alert).where(Alert.id == alert_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="alert not found",
        )
    return AlertRead.model_validate(row)

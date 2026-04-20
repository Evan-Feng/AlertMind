"""Incidents 查询 API。

- ``GET /api/v1/incidents``：支持按 status/severity 过滤 + 分页
- ``GET /api/v1/incidents/{incident_id}``：详情（含成员 alerts）

本模块仅提供只读端点；``analyze`` / ``resolve`` / ``patch`` 等修改操作
由后续阶段（阶段 4 / 阶段 7）补齐。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from alertmind.api.deps import get_db
from alertmind.models.incident import Incident, IncidentSeverity, IncidentStatus
from alertmind.schemas.common import Page
from alertmind.schemas.incident import IncidentDetail, IncidentRead

router = APIRouter(prefix="/incidents", tags=["incidents"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    summary="List incidents",
    response_model=Page[IncidentRead],
)
async def list_incidents(
    db: DbDep,
    status_filter: Annotated[
        IncidentStatus | None,
        Query(alias="status", description="按事件状态过滤（open | resolved）"),
    ] = None,
    severity: Annotated[
        IncidentSeverity | None,
        Query(description="按严重级别过滤（critical | warning | info）"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="页码，1-based")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数，1~100")] = 20,
) -> Page[IncidentRead]:
    """分页查询 incidents。

    :param db: 数据库会话（依赖注入）
    :param status_filter: 可选，按事件状态过滤（open | resolved）
    :param severity: 可选，按严重级别过滤（critical | warning | info）；
        不传时返回全部（含 ``severity IS NULL`` 行），不做默认过滤
    :param page: 页码，1-based
    :param page_size: 每页条数，1~100
    :returns: :class:`Page` 分页响应；默认按 ``last_seen_at DESC`` 排序，
        命中复合索引 ``ix_incidents_status_severity_last_seen_at``
    """
    filters = []
    if status_filter is not None:
        filters.append(Incident.status == status_filter.value)
    if severity is not None:
        filters.append(Incident.severity == severity.value)

    count_stmt = select(func.count()).select_from(Incident)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    list_stmt = select(Incident)
    if filters:
        list_stmt = list_stmt.where(*filters)
    list_stmt = (
        list_stmt.order_by(Incident.last_seen_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )

    rows = (await db.execute(list_stmt)).scalars().all()
    items = [IncidentRead.model_validate(row) for row in rows]

    return Page[IncidentRead].from_query(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{incident_id}",
    summary="Get incident detail",
    response_model=IncidentDetail,
)
async def get_incident(incident_id: int, db: DbDep) -> IncidentDetail:
    """获取单个 incident 详情，连带预加载成员 alerts 列表。

    由于 :class:`Incident.alerts` 关系配置为 ``lazy="raise"``，直接访问
    会抛 ``InvalidRequestError``；本端点通过 ``selectinload`` eager 加载
    以保证 Pydantic 序列化成功。

    :param incident_id: Incident 主键 id
    :param db: 数据库会话（依赖注入）
    :returns: :class:`IncidentDetail`，含 ``alerts`` 列表
    :raises HTTPException: 404 若指定 id 不存在
    """
    stmt = select(Incident).options(selectinload(Incident.alerts)).where(Incident.id == incident_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="incident not found",
        )
    return IncidentDetail.model_validate(row)

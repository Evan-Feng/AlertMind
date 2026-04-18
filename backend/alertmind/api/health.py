"""健康检查路由。

- ``GET /health``：liveness probe，不依赖外部组件，永远 200。
- ``GET /health/ready``：readiness probe，并发探测 PostgreSQL 与 Redis，任一失败返回 503。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.api.deps import get_db, get_redis

router = APIRouter(tags=["health"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """返回进程存活状态；不做任何外部依赖探测。"""
    return {"status": "ok"}


async def _check_database(session: AsyncSession) -> None:
    """对数据库执行 ``SELECT 1``；失败时抛出原始异常。"""
    await session.execute(text("SELECT 1"))


async def _check_redis(client: Redis) -> None:
    """对 Redis 执行 ``PING``；失败时抛出原始异常。"""
    await client.ping()


@router.get("/health/ready", summary="Readiness probe")
async def ready(db: DbDep, redis_client: RedisDep) -> dict[str, str]:
    """并发探测 PostgreSQL 与 Redis，全部健康才返回 ``ready``。

    :returns: ``{"status": "ready", "database": "ok", "redis": "ok"}``
    :raises HTTPException: 任一依赖不可达时返回 503，detail 里标注各组件状态。
    """
    results = await asyncio.gather(
        _check_database(db),
        _check_redis(redis_client),
        return_exceptions=True,
    )
    db_result, redis_result = results

    db_ok = not isinstance(db_result, BaseException)
    redis_ok = not isinstance(redis_result, BaseException)

    if not db_ok:
        logger.warning("readiness: database check failed: {!r}", db_result)
    if not redis_ok:
        logger.warning("readiness: redis check failed: {!r}", redis_result)

    if not (db_ok and redis_ok):
        detail: dict[str, Any] = {
            "status": "not_ready",
            "database": "ok" if db_ok else "fail",
            "redis": "ok" if redis_ok else "fail",
        }
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    return {"status": "ready", "database": "ok", "redis": "ok"}

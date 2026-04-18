"""FastAPI 依赖注入集合。

- :func:`get_db`：产出一个 :class:`AsyncSession`，请求结束后自动关闭。
- :func:`get_redis`：产出一个 ``redis.asyncio.Redis`` 客户端（阶段 1 不做连接池）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.config import settings
from alertmind.db.session import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供一个 SQLAlchemy AsyncSession。

    :yields: 与请求生命周期绑定的 AsyncSession，离开作用域后自动 ``close()``。
    """
    session: AsyncSession = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI 依赖：提供一个 Redis 异步客户端。

    阶段 1 每次请求新建 client（不走全局连接池），简单够用；阶段 3 聚合器落地
    时再评估是否改造成全局连接池。

    :yields: ``redis.asyncio.Redis`` 客户端，离开作用域后自动 ``close()``。
    """
    client: Redis = from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()

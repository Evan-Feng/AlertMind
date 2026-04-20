"""FastAPI 依赖注入集合。

- :func:`get_db`：产出一个 :class:`AsyncSession`，请求结束后自动关闭。
- :func:`get_redis`：产出一个 ``redis.asyncio.Redis`` 客户端（阶段 1 不做连接池）。
- :func:`get_embedder`：从 ``app.state`` 取启动期加载的 Embedder 单例。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncSession

from alertmind.config import settings
from alertmind.db.session import AsyncSessionLocal
from alertmind.utils.embedding import Embedder


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


def get_embedder(request: Request) -> Embedder:
    """FastAPI 依赖：从 ``app.state`` 取启动期加载的 :class:`Embedder` 单例。

    模型在 FastAPI ``lifespan`` 启动钩子中一次性加载（见
    :func:`alertmind.main.lifespan`），以避免每次请求都付出加载成本。

    :param request: FastAPI 注入的当前请求对象。
    :returns: 全局共享的 :class:`Embedder` 实例。
    :raises RuntimeError: 若 lifespan 未正确挂载 embedder（理论上不会发生，
        除非测试绕过 lifespan 直接挂路由）。
    """
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise RuntimeError("embedder not initialized; lifespan hook missing?")
    assert isinstance(embedder, Embedder)
    return embedder

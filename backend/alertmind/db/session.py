"""数据库连接与会话工厂。

本模块提供全局复用的 async engine 与 :data:`AsyncSessionLocal` 会话工厂，
在 FastAPI 依赖注入层（见 ``alertmind.api.deps.get_db``）消费；
另提供 :func:`new_session` 供非 FastAPI 上下文（BackgroundTasks、CLI 等）使用。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alertmind.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)
"""应用级 async engine；关闭时由 FastAPI lifespan 统一 ``await engine.dispose()``。"""

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
"""异步 Session 工厂；不要在业务代码中直接持有实例，使用依赖注入。"""


@asynccontextmanager
async def new_session() -> AsyncGenerator[AsyncSession, None]:
    """供非 FastAPI 上下文使用的 session context manager。

    典型用途：BackgroundTasks、定时 worker、CLI 命令。与 FastAPI 依赖
    :func:`alertmind.api.deps.get_db` 的区别：

    - ``get_db`` 由 FastAPI 管理 yield/close，不自动 commit；
    - ``new_session()`` 成功时自动 ``commit``，异常时自动 ``rollback``。

    用法::

        async with new_session() as db:
            await db.execute(...)

    :yields: 绑定到 :data:`AsyncSessionLocal` 的 :class:`AsyncSession` 实例
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = ["AsyncSessionLocal", "engine", "new_session"]

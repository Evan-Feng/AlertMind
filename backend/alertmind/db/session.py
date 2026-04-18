"""数据库连接与会话工厂。

本模块提供全局复用的 async engine 与 :data:`AsyncSessionLocal` 会话工厂，
在 FastAPI 依赖注入层（见 ``alertmind.api.deps.get_db``）消费。
"""

from __future__ import annotations

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

__all__ = ["AsyncSessionLocal", "engine"]

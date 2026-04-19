"""FastAPI 应用入口。

- :func:`create_app` 为工厂函数，产出配置完成的 ``FastAPI`` 实例。
- :data:`app` 为模块级单例，供 ``uvicorn alertmind.main:app`` 直接加载。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from alertmind import __version__
from alertmind.api.alerts import router as alerts_router
from alertmind.api.health import router as health_router
from alertmind.api.webhook import router as webhook_router
from alertmind.db.session import engine
from alertmind.utils.logger import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI 生命周期钩子：启动时初始化日志，关闭时释放 DB 连接池。"""
    configure_logging()
    logger.info("AlertMind v{} starting", __version__)
    try:
        yield
    finally:
        logger.info("AlertMind v{} shutting down", __version__)
        await engine.dispose()


def create_app() -> FastAPI:
    """构造并返回一个配置好的 FastAPI 应用实例。

    :returns: 已挂载健康检查路由、绑定 lifespan 的 FastAPI 实例。
    """
    app = FastAPI(
        title="AlertMind",
        version=__version__,
        description="给 Prometheus Alertmanager 装上大脑的开源 AIOps 平台",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(webhook_router, prefix="/api/v1")
    app.include_router(alerts_router, prefix="/api/v1")
    return app


app = create_app()

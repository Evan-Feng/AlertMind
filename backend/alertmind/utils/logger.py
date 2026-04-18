"""loguru 日志封装。

使用 :func:`configure_logging` 在应用启动时统一初始化：移除默认 handler，
重新挂载一个带结构化格式、级别由 ``settings.log_level`` 控制的 stderr sink。
业务代码统一 ``from loguru import logger`` 使用，禁止 ``print``。
"""

from __future__ import annotations

import sys

from loguru import logger

from alertmind.config import settings

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def configure_logging() -> None:
    """配置 loguru 全局 logger。

    行为：
    - 移除默认 handler（避免重复输出）。
    - 按 ``settings.log_level`` 向 stderr 挂载一个新 handler。
    - 使用带色彩的中文友好格式。

    由 FastAPI lifespan 启动时调用一次；重复调用幂等。
    """
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        format=_LOG_FORMAT,
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )

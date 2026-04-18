"""Alembic 异步迁移环境入口。

本文件通过 ``alertmind.config.settings`` 动态注入数据库连接串，避免在
``alembic.ini`` 中硬编码敏感信息；同时基于 ``alertmind.db.base.Base`` 暴露
``target_metadata``，以支持 ``alembic revision --autogenerate``。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from alertmind.config import settings
from alertmind.db.base import Base

# TODO: 阶段 2 起在此处 import ORM 模型以支持 autogenerate，形如
#       ``from alertmind.models import *``（需附加 noqa:F401,F403）。
#       阶段 1 ``alertmind.models`` 仍为空包，故暂不导入。

# Alembic Config 对象，承载 alembic.ini 中的配置项。
config = context.config

# 运行时注入数据库 URL：ini 内留空，从 pydantic-settings 读取真实值，避免在
# 配置文件中硬编码密码 / 主机名。必须在 fileConfig 之前完成注入。
config.set_main_option("sqlalchemy.url", settings.database_url)

# 配置 Python logging（按 alembic.ini 的 [loggers] 段）。
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 的元数据来源；所有业务 ORM 模型都必须继承自该 Base。
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅凭 URL 生成 SQL 脚本，无需真实连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在同步 Connection 上执行迁移（由 async 驱动通过 run_sync 适配）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在线模式的异步主流程：创建 AsyncEngine → 执行迁移 → 释放连接。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口：驱动 asyncio 事件循环。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

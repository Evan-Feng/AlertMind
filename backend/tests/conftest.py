"""共享测试 fixtures。

设计要点：
- ``pg_container``（session scope）：用 testcontainers 启动 pgvector/pgvector:pg16 容器，
  session 结束后自动清理。整个测试运行只启动一次。
- ``alembic_upgrade``（session scope）：程序化运行 alembic upgrade head 建 schema。
- ``db_url``（session scope）：容器的 asyncpg 连接字符串，供 function scope fixtures 复用。
- ``db_session``（function scope）：每个测试独立创建 engine（NullPool），
  用 SAVEPOINT 包裹，测试结束自动回滚，保证测试间完全隔离。
- ``client``（function scope）：httpx.AsyncClient，通过 dependency_overrides 注入测试 session。

事件循环说明：
  pytest-asyncio asyncio_mode=auto 下，每个 async 测试函数拥有独立的事件循环。
  若在 session scope 创建 AsyncEngine（asyncpg 连接池会绑定到创建时的事件循环），
  在 function scope 的不同事件循环里使用该 engine 会触发
  "Future attached to a different loop" 错误。
  解决方案：db_session 里每次用 NullPool 创建 engine，用完即弃，不走连接池，
  事件循环隔离问题彻底消失。性能损失可忽略（测试用途）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

# conftest.py 位于 backend/tests/conftest.py
# BACKEND_ROOT = backend/
# FIXTURES_DIR = backend/tests/fixtures/
BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
ALEMBIC_DIR = BACKEND_ROOT / "alembic"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

# 在导入 alertmind 任何模块之前，确保环境变量已设置（避免 pydantic-settings 启动报错）
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://alertmind:alertmind@localhost:15432/alertmind",
)

from alertmind.api.deps import get_db
from alertmind.main import create_app

# ---------------------------------------------------------------------------
# 容器 + Alembic（session scope：整个测试运行只启动一次）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_container() -> Generator[PostgresContainer, None, None]:
    """启动 pgvector/pgvector:pg16 容器，session 结束后自动清理。

    使用 pgvector 官方镜像确保迁移中的 ``CREATE EXTENSION IF NOT EXISTS vector``
    能够成功执行。
    """
    with PostgresContainer(
        image="pgvector/pgvector:pg16",
        driver="asyncpg",
        username="test_user",
        password="test_pass",
        dbname="test_alertmind",
    ) as container:
        yield container


@pytest.fixture(scope="session")
def db_url(pg_container: PostgresContainer) -> str:
    """返回容器的 asyncpg 连接字符串（供 function scope fixtures 复用）。"""
    return pg_container.get_connection_url()


@pytest.fixture(scope="session")
def alembic_upgrade(pg_container: PostgresContainer, db_url: str) -> None:
    """在容器就绪后，程序化调用 alembic upgrade head 建 schema。

    设计说明：
    alembic/env.py 在执行时会调用 ``config.set_main_option("sqlalchemy.url",
    settings.database_url)``，其中 ``settings`` 是 pydantic-settings 已实例化的
    单例，在 conftest 导入时已用默认值（localhost:15432）构造完毕。

    解决方案：在调用 alembic.command.upgrade 之前，临时将 settings 单例的
    ``database_url`` 属性 monkeypatch 为容器 URL，迁移结束后恢复。
    这样 env.py 读到的 settings.database_url 就是测试容器的真实地址。
    """
    import alembic.command
    import alembic.config

    import alertmind.config as alertmind_config

    # 临时 patch settings 单例，确保 alembic env.py 读到容器 URL
    original_url = alertmind_config.settings.database_url
    object.__setattr__(alertmind_config.settings, "database_url", db_url)
    try:
        alembic_cfg = alembic.config.Config(str(ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        alembic_cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        alembic.command.upgrade(alembic_cfg, "head")
    finally:
        object.__setattr__(alertmind_config.settings, "database_url", original_url)


# ---------------------------------------------------------------------------
# db_session（function scope）
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_session(db_url: str, alembic_upgrade: None) -> AsyncGenerator[AsyncSession, None]:
    """每个测试用 SAVEPOINT 包裹的 AsyncSession。

    使用 NullPool（无连接池）创建 engine，避免 asyncpg 连接池绑定到创建时的事件循环，
    从而消除 "Future attached to a different loop" 报错。

    模式：
    1. 创建 NullPool AsyncEngine → 取一条 connection → begin() 外层事务
    2. 用 join_transaction_mode="create_savepoint" 创建 AsyncSession
    3. yield session
    4. 外层事务 rollback → 所有写入撤销 → engine dispose
    """
    engine = create_async_engine(db_url, echo=False, poolclass=NullPool)
    conn: AsyncConnection
    try:
        async with engine.connect() as conn:
            await conn.begin()
            session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                await session.close()
                await conn.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# client（function scope）
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """httpx.AsyncClient，通过 dependency_overrides 注入测试 db_session。

    每个测试请求走的 DB 操作都绑定到同一个 db_session（SAVEPOINT 隔离），
    测试结束后清理 dependency_overrides，防止跨测试状态污染。
    """
    app = create_app()

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        """覆盖 get_db，直接 yield 测试 session，不自行 close（由 db_session fixture 管理）。"""
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 辅助：加载 fixtures JSON
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sample_payloads() -> dict[str, Any]:
    """加载 tests/fixtures/sample_alerts.json，返回顶层 dict。"""
    import json

    with open(FIXTURES_DIR / "sample_alerts.json") as f:
        return json.load(f)  # type: ignore[no-any-return]

"""SQLAlchemy 2.0 声明式基类。

阶段 2 起的所有 ORM 模型都应继承 :class:`Base`，由 ``database-architect``
设计表结构后在 ``alertmind/models/`` 下实现，本阶段只提供基类骨架。
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """AlertMind 所有 ORM 模型的声明式基类。

    当前为空；后续可在此注入通用列（如 ``created_at`` / ``updated_at``）或
    元数据命名约定（metadata naming convention），由 ``database-architect`` 决定。
    """

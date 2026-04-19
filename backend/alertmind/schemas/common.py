"""通用 Pydantic schema 基础组件。

提供跨业务域复用的结构，目前仅包含泛型分页响应 :class:`Page`。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """泛型分页响应信封。

    :param items: 当前页条目列表（类型由 ``T`` 决定）
    :param total: 过滤条件下的总条数
    :param page: 当前页码（1-based）
    :param page_size: 每页条数
    :param pages: 总页数（``ceil(total / page_size)``；``total`` 为 0 时固定为 0）
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[T] = Field(default_factory=list, description="当前页条目")
    total: int = Field(..., ge=0, description="符合过滤条件的总条数")
    page: int = Field(..., ge=1, description="当前页码，1-based")
    page_size: int = Field(..., ge=1, description="每页条数")
    pages: int = Field(..., ge=0, description="总页数")

    @classmethod
    def from_query(
        cls,
        *,
        items: Sequence[T],
        total: int,
        page: int,
        page_size: int,
    ) -> Page[T]:
        """从查询结果构造 :class:`Page`。

        :param items: 当前页条目序列
        :param total: 过滤条件下的总条数
        :param page: 当前页码（1-based）
        :param page_size: 每页条数
        :returns: 填充 ``pages`` 字段后的分页响应
        """
        pages = math.ceil(total / page_size) if total > 0 else 0
        return cls(
            items=list(items),
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

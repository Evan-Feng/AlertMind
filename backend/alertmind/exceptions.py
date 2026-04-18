"""AlertMind 自定义异常层次。

约定：业务代码一律抛出本模块中的自定义异常，禁止直接 ``raise Exception``。
后续阶段会在此基础上扩展具体异常类型（聚合失败、通知失败等）。
"""

from __future__ import annotations


class AlertMindError(Exception):
    """AlertMind 所有自定义异常的基类。

    :param message: 异常说明信息（中文）
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LLMProviderError(AlertMindError):
    """LLM Provider 调用失败时抛出的异常占位。

    阶段 4 接入具体 Provider 后会扩展更细分的子类（超时、配额、返回格式非法等）。
    """

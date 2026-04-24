"""LLM provider 抽象层。

具体 provider 实现在 W4（openai / claude / qwen / ollama）。
本模块只包含基类抽象、错误体系、Pydantic schemas。
"""

from alertmind.llm.base import (
    AnalysisMetadata,
    AnalysisResult,
    LLMAuthError,
    LLMProvider,
    LLMProviderError,
    LLMProviderUnavailable,
    LLMRateLimitError,
    LLMResponseFormatError,
    LLMTimeoutError,
)

__all__ = [
    "AnalysisMetadata",
    "AnalysisResult",
    "LLMAuthError",
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderUnavailable",
    "LLMRateLimitError",
    "LLMResponseFormatError",
    "LLMTimeoutError",
]

"""LLM Provider 抽象层基础定义。

本模块定义 LLM 分析引擎的契约层：

- :class:`AnalysisResult`：LLM 结构化分析产出（字段对齐 ``incidents`` 表）
- :class:`AnalysisMetadata`：LLM 调用的附加信息（token 消耗、耗时、完成时间）
- 1 个基类 + 5 个错误子类（见 ADR-010 Decision A）
- :class:`LLMProvider`：抽象基类，具体 Provider 在 W4 实现

本文件只依赖 ``pydantic`` / ``abc`` / ``datetime`` 标准库；
**不引入**任何具体 Provider SDK（openai / anthropic / httpx），SDK 集成在 W4。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

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


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class AnalysisResult(BaseModel):
    """LLM 分析产出，字段对齐 Incident 表的 3 层分层（ADR-010 Decision F）。

    字段语义：

    - ``title``：Dashboard 卡片主标题（精确主题，≤60 字）
    - ``summary``：Dashboard 卡片副标题（现象 + 规模，≤150 字）
    - ``root_cause``：详情页正文——完整根因分析
    - ``impact``：详情页正文——影响范围
    - ``suggestions``：详情页正文——处置建议（markdown 列表）

    Validation 策略（ADR-010 Decision H truncated 防护）：

    - ``title`` / ``summary`` 有 ``max_length`` 硬性上限
    - ``root_cause`` / ``impact`` / ``suggestions`` 有 ``min_length`` 语义底线（防空洞响应）
    - ``root_cause`` / ``impact`` / ``suggestions`` 必须以句子终止符结尾
      （防 ``max_tokens`` 截断；见 :meth:`must_end_with_terminator`）

    ``min_length`` 起点数值（W3 prompt 调优时可能调整）：

    - ``root_cause``: 20（防"连接池满了"这种极短无深度响应）
    - ``impact``: 10（允许"下游 3 个服务延迟"这种短影响描述）
    - ``suggestions``: 20（防"重启"这种一词敷衍）
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Dashboard 卡片主标题：精确主题（≤60 字）",
    )
    summary: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Dashboard 卡片副标题：现象 + 规模（≤150 字，见 ADR-010 Decision F）",
    )
    root_cause: str = Field(
        ...,
        min_length=20,
        description="详情页：完整根因分析",
    )
    impact: str = Field(
        ...,
        min_length=10,
        description="详情页：影响范围分析",
    )
    suggestions: str = Field(
        ...,
        min_length=20,
        description="详情页：处置建议（markdown 列表格式）",
    )

    @field_validator("root_cause", "impact", "suggestions")
    @classmethod
    def must_end_with_terminator(cls, v: str) -> str:
        """防 ``max_tokens`` 截断：响应必须以完整句子终止符结尾（ADR-010 Decision H）。

        :param v: 字段原始值
        :returns: 原值（校验通过时）
        :raises ValueError: 值去除末尾空白后未以中英文句末标点结尾
        """
        terminators = ("。", ".", "!", "?", "！", "？")
        if not v.rstrip().endswith(terminators):
            raise ValueError(f"response appears truncated (does not end with any of {terminators})")
        return v


class AnalysisMetadata(BaseModel):
    """LLM 调用的附加信息，供 analyzer 写入 ``incidents`` 表。

    对应 incident 表字段（W1 alembic 0003 补齐）：

    - ``llm_model`` <- ``metadata.model``
    - ``llm_tokens_used`` <- ``metadata.total_tokens``
    - ``analyzed_at`` <- ``metadata.completed_at``

    ``completed_at`` 必须为 timezone-aware ``datetime``（与 ``incident.analyzed_at``
    的 ``DateTime(timezone=True)`` 对齐）；model 层不强制校验 tz，
    W4 provider 实现侧必须用 ``datetime.now(UTC)`` 生成。
    """

    model: str = Field(..., description="实际调用的模型标识，如 'gpt-4o-mini'")
    prompt_tokens: int = Field(..., ge=0, description="输入 prompt 消耗的 token 数")
    completion_tokens: int = Field(..., ge=0, description="输出 completion 的 token 数")
    total_tokens: int = Field(
        ...,
        ge=0,
        description="总 token 数（写入 incident.llm_tokens_used）",
    )
    completed_at: datetime = Field(
        ...,
        description="LLM 响应完成时间（UTC 带时区；写入 incident.analyzed_at）",
    )
    latency_ms: int = Field(..., ge=0, description="端到端耗时（毫秒）")


# ---------------------------------------------------------------------------
# 错误类型层次（ADR-010 Decision A）
# ---------------------------------------------------------------------------


class LLMProviderError(Exception):
    """所有 LLM provider 错误的基类。

    子类通过构造时硬编码 ``retriable`` 值描述重试策略，analyzer 层直接读
    ``exc.retriable`` 决定是否重试，避免发散的 ``isinstance`` 判断。

    Attributes:
        provider: 抛出错误的 provider 标识名（如 ``"openai"``），便于日志定位
        retriable: True 表示可重试（网络 / 临时服务不可用），
            False 表示不可重试（auth）
    """

    def __init__(self, message: str, *, provider: str, retriable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.retriable = retriable

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"provider={self.provider!r}, retriable={self.retriable}, "
            f"message={str(self)!r})"
        )


class LLMRateLimitError(LLMProviderError):
    """429 / throttle 类错误。

    ``retriable=True``：API 限流本质是临时性，退避后通常可恢复。
    provider 若返回 ``Retry-After`` 头，由具体 provider 层解析后填入
    ``retry_after_seconds``。
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message, provider=provider, retriable=True)
        self.retry_after_seconds = retry_after_seconds


class LLMAuthError(LLMProviderError):
    """401 / invalid key 类错误。

    ``retriable=False``：auth 错误不可能因重试而恢复（key 就是错的或权限不足），
    必须人工介入更换 API key。自动重试只会浪费 provider 配额。
    """

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retriable=False)


class LLMTimeoutError(LLMProviderError):
    """网络超时 / connection timeout。

    ``retriable=True``：timeout 多数为网络抖动或 provider 偶发延迟，
    退避后重试通常能成功。
    """

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retriable=True)


class LLMProviderUnavailable(LLMProviderError):
    """5xx 服务端错误 / connection refused / Ollama 模型未加载等兜底类。

    ``retriable=True``：服务端错误通常是临时性的（部署 / 重启 / 扩容过渡期），
    退避后重试有机会成功；若长时间持续失败会被分析主流程的最大重试次数拦截。
    """

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retriable=True)


class LLMResponseFormatError(LLMProviderError):
    """LLM 返回的 JSON 解析失败 / Pydantic 校验失败 / 句末截断等格式问题。

    ``retriable=True``：prompt nudge 后 LLM 可能改正（ADR-010 Decision D 允许
    最多 1 次重试）。原始响应通过 ``raw_response`` 字段保留用于调试。
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message, provider=provider, retriable=True)
        self.raw_response = raw_response


# ---------------------------------------------------------------------------
# Provider 抽象基类
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """LLM Provider 抽象基类。

    所有具体 provider（openai / claude / qwen / ollama）继承此类。
    具体实现在 W4 完成，W2 只负责接口定义。

    设计约束：

    - :meth:`analyze` 是 ``async``（LLM API 都是 IO bound）
    - 实现层必须捕获原生异常并映射到 5 个 :class:`LLMProviderError` 子类之一
      （不外泄原生异常）
    - 返回值必须是已 Pydantic 校验的 :class:`AnalysisResult`；
      校验失败抛 :class:`LLMResponseFormatError`
    - 参数用 keyword-only（``*`` 之后强制 keyword），避免位置参数顺序出错
    """

    @abstractmethod
    async def analyze(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> tuple[AnalysisResult, AnalysisMetadata]:
        """执行一次 LLM 分析调用。

        :param system_prompt: System message（如 "你是资深 SRE 工程师..."）
        :param user_prompt: User message（含 alert 序列化内容，W3 由 analyzer 构造）
        :param model: provider-specific 模型名
            （如 ``"gpt-4o-mini"`` / ``"claude-3-5-sonnet-20241022"``）
        :param max_tokens: 响应最大 token 数（避免 context overflow 与费用失控）
        :param temperature: 采样温度（默认 0.3 偏确定性，分析场景不需要创造性）
        :returns: ``(result, metadata)`` 元组：

            - ``result``：已通过 Pydantic 校验的 :class:`AnalysisResult`
            - ``metadata``：本次调用的 token 消耗 / 模型名 / 耗时
              （供 analyzer 写回 incident）

        :raises LLMRateLimitError: provider 返回 429
        :raises LLMAuthError: provider 返回 401（不可重试）
        :raises LLMTimeoutError: 网络超时
        :raises LLMProviderUnavailable: 5xx 或其他可重试的 provider 错误
        :raises LLMResponseFormatError: JSON 解析 / Pydantic 校验 / 句末截断
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 标识名，如 ``"openai"`` / ``"claude"`` / ``"qwen"`` / ``"ollama"``。

        用途：

        - 错误对象的 ``provider`` 字段
        - 结构化日志标签
        - 写入 ``incident.llm_model``（实际模型名由 ``metadata.model`` 提供；
          ``provider_name`` 仅做分类）
        """
        ...

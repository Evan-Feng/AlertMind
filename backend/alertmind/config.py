"""应用配置：使用 pydantic-settings 从项目根目录的 ``.env`` 加载环境变量。

所有业务代码必须通过 :data:`settings` 单例读取配置，禁止硬编码密钥或 URL。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AlertMind 全量配置对象。

    阶段 1 仅实际使用 Application / PostgreSQL / Redis 字段，其余字段为阶段 2–6
    预留，均给出合理默认值以保证阶段 1 可独立启动。
    """

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ===== Application =====
    app_env: str = Field(default="development", description="运行环境：development | production")
    log_level: str = Field(default="INFO", description="日志级别：DEBUG | INFO | WARNING | ERROR")
    app_host: str = Field(default="0.0.0.0", description="FastAPI 监听主机")
    app_port: int = Field(default=8000, description="FastAPI 监听端口")

    # ===== PostgreSQL =====
    postgres_user: str = Field(default="alertmind", description="PostgreSQL 用户名")
    postgres_password: str = Field(default="alertmind", description="PostgreSQL 密码")
    postgres_db: str = Field(default="alertmind", description="PostgreSQL 数据库名")
    database_url: str = Field(
        default="postgresql+asyncpg://alertmind:alertmind@localhost:15432/alertmind",
        description="SQLAlchemy async DSN，必须使用 asyncpg 驱动",
    )

    # ===== Redis =====
    redis_url: str = Field(
        default="redis://localhost:16379/0",
        description="Redis 连接 URL",
    )

    # ===== LLM Provider (阶段 4 使用) =====
    llm_provider: str = Field(
        default="openai",
        description="LLM Provider：openai | claude | qwen | ollama",
    )
    llm_api_key: str = Field(default="", description="LLM Provider API Key（阶段 4 启用）")
    llm_model: str = Field(default="gpt-4o-mini", description="LLM 模型名称")
    llm_base_url: str = Field(
        default="",
        description="自定义 Base URL；留空则使用 Provider 官方端点",
    )

    # ===== Aggregation (阶段 3 使用) =====
    aggregation_similarity_threshold: float = Field(
        default=0.85,
        description="告警聚合的余弦相似度阈值",
    )
    aggregation_window_hours: int = Field(
        default=24,
        description="告警聚合的时间窗口（小时）",
    )
    embedding_model: str = Field(
        default="BAAI/bge-small-zh-v1.5",
        description="本地 sentence-transformers Embedding 模型名",
    )
    embedding_drop_labels: list[str] = Field(
        default=[
            "pod",
            "container_id",
            "instance_id",
            "replica_id",
            "uid",
            "request_id",
            "trace_id",
        ],
        description="Embedding 拼接时过滤的高熵标签白名单，支持 JSON 数组或逗号分隔字符串",
    )

    @field_validator("embedding_drop_labels", mode="before")
    @classmethod
    def _parse_drop_labels(cls, value: Any) -> Any:
        """兼容逗号分隔字符串形式的 ``EMBEDDING_DROP_LABELS``。

        pydantic-settings 默认已支持 JSON 数组（``'["pod","container_id"]'``）；
        本 validator 额外兜底 ``pod,container_id`` 这种裸逗号分隔格式，使环境变量
        书写更符合运维直觉。
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            # JSON 数组形式交给 pydantic 原生解析
            if stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    # ===== Notifiers (阶段 6 使用) =====
    wecom_webhook_url: str = Field(default="", description="企业微信群机器人 Webhook URL")
    feishu_webhook_url: str = Field(default="", description="飞书群机器人 Webhook URL")


settings = Settings()

"""应用配置：使用 pydantic-settings 从项目根目录的 ``.env`` 加载环境变量。

所有业务代码必须通过 :data:`settings` 单例读取配置，禁止硬编码密钥或 URL。
"""

from __future__ import annotations

from pydantic import Field
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

    # ===== Notifiers (阶段 6 使用) =====
    wecom_webhook_url: str = Field(default="", description="企业微信群机器人 Webhook URL")
    feishu_webhook_url: str = Field(default="", description="飞书群机器人 Webhook URL")


settings = Settings()

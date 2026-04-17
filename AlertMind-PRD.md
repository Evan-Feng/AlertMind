# AlertMind 项目规格文档

> 本文档是给 Claude Code 的工作指令。请严格按照本文档的**分阶段路线图**执行，每完成一个阶段等待用户验收后再进入下一阶段。不要一次性生成所有代码。

---

## 0. 项目一句话定义

**AlertMind** 是一个给 Prometheus Alertmanager 装上「大脑」的开源 AIOps 平台——通过 LLM 实现告警聚合、根因分析与中文处置建议，接入零侵入，Docker Compose 一键启动。

**目标用户**：国内使用 Prometheus + K8s 的中小型团队 SRE/运维工程师。

**核心差异点**（必须在 README 和代码中体现）：
1. **零侵入**：只做 Alertmanager Webhook 接收端，不替换任何现有组件
2. **多模型**：OpenAI / Claude / 通义千问 / Ollama 都能接，用户自选
3. **中文优先**：默认中文 Prompt、中文 UI、中文文档
4. **知识库可插拔**：支持上传 Runbook 作为 RAG 上下文（v2 阶段再实现）

---

## 1. 技术栈（已锁定，不要自作主张更换）

### 后端
- **语言**：Python 3.11+
- **框架**：FastAPI + Uvicorn
- **ORM**：SQLAlchemy 2.0（异步模式） + Alembic（迁移）
- **数据校验**：Pydantic v2
- **数据库**：PostgreSQL 16 + pgvector 扩展
- **缓存/队列**：Redis 7
- **后台任务**：使用 FastAPI BackgroundTasks（MVP 阶段够用，不引入 Celery）
- **LLM SDK**：
  - OpenAI 官方 SDK
  - Anthropic 官方 SDK
  - 通义千问通过 OpenAI 兼容接口调用
  - Ollama 通过 HTTP API 调用
- **Embedding 模型**：`BAAI/bge-small-zh-v1.5`（中文效果好、体积小），通过 sentence-transformers 加载
- **日志**：loguru
- **配置管理**：pydantic-settings，从环境变量读取

### 前端
- **框架**：Vue 3 + TypeScript + Vite
- **UI 库**：Element Plus
- **HTTP**：axios
- **状态管理**：Pinia
- **路由**：Vue Router

### 部署
- **MVP 阶段**：Docker Compose（一键启动所有依赖）
- **v2 阶段**：提供 Helm Chart

### 开发工具链
- **Python 代码格式化**：ruff（format + lint 二合一，不要用 black）
- **Python 类型检查**：mypy（可选，但建议开启严格模式）
- **Python 包管理**：uv（比 pip 快很多）
- **Pre-commit**：启用 ruff + mypy 钩子
- **CI**：GitHub Actions（跑 lint + test + docker build）

---

## 2. 项目目录结构（严格遵守）

```
alertmind/
├── .github/
│   └── workflows/
│       ├── ci.yml              # lint + test
│       └── docker.yml          # 构建并推送镜像
├── backend/
│   ├── alertmind/              # 主包名
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI 入口
│   │   ├── config.py           # 配置（pydantic-settings）
│   │   ├── api/                # 路由层
│   │   │   ├── __init__.py
│   │   │   ├── deps.py         # 依赖注入
│   │   │   ├── webhook.py      # Alertmanager webhook 接收
│   │   │   ├── alerts.py       # 告警 CRUD
│   │   │   ├── incidents.py    # 聚合后的 Incident
│   │   │   └── health.py       # 健康检查
│   │   ├── core/               # 核心业务
│   │   │   ├── __init__.py
│   │   │   ├── aggregator.py   # 聚合引擎（Embedding 相似度）
│   │   │   ├── analyzer.py     # LLM 分析引擎
│   │   │   └── notifier.py     # 通知推送
│   │   ├── llm/                # LLM Provider 适配层
│   │   │   ├── __init__.py
│   │   │   ├── base.py         # 抽象基类
│   │   │   ├── openai_provider.py
│   │   │   ├── claude_provider.py
│   │   │   ├── qwen_provider.py
│   │   │   └── ollama_provider.py
│   │   ├── models/             # SQLAlchemy 模型
│   │   │   ├── __init__.py
│   │   │   ├── alert.py
│   │   │   └── incident.py
│   │   ├── schemas/            # Pydantic schemas
│   │   │   ├── __init__.py
│   │   │   ├── alert.py
│   │   │   └── incident.py
│   │   ├── db/                 # 数据库
│   │   │   ├── __init__.py
│   │   │   ├── session.py
│   │   │   └── base.py
│   │   ├── prompts/            # Prompt 模板（单独目录便于迭代）
│   │   │   ├── __init__.py
│   │   │   └── analyzer_zh.py  # 中文分析 Prompt
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── embedding.py    # Embedding 封装
│   │       └── logger.py
│   ├── alembic/                # 数据库迁移
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_webhook.py
│   │   ├── test_aggregator.py
│   │   └── fixtures/
│   │       └── sample_alerts.json  # 示例告警数据
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── api/                # axios 封装
│   │   ├── components/
│   │   ├── views/
│   │   │   ├── Dashboard.vue   # 告警列表
│   │   │   └── IncidentDetail.vue  # 告警详情 + AI 分析
│   │   ├── stores/             # Pinia
│   │   ├── router/
│   │   ├── App.vue
│   │   └── main.ts
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── Dockerfile
│   └── nginx.conf              # 前端容器用 nginx 托管
├── docker-compose.yml          # 一键启动：postgres + redis + backend + frontend
├── docker-compose.dev.yml      # 开发用（热重载）
├── docs/
│   ├── quickstart.md
│   ├── architecture.md
│   └── images/                 # README 用的图片/GIF
├── examples/
│   └── alertmanager-config.yml # 示例 Alertmanager 配置
├── scripts/
│   ├── init_db.sh
│   └── send_test_alert.sh      # 发送测试告警的脚本（用户 5 分钟体验用）
├── .gitignore
├── .env.example
├── LICENSE                     # MIT
├── README.md                   # 中英双语
└── CHANGELOG.md
```

---

## 3. 数据模型设计

### 3.1 Alert 表（原始告警）

```python
class Alert(Base):
    __tablename__ = "alerts"

    id: int (主键, 自增)
    fingerprint: str (Alertmanager 原生指纹, 索引)
    status: str  # "firing" | "resolved"
    severity: str  # "critical" | "warning" | "info"
    alertname: str
    labels: JSONB  # 所有原始 labels
    annotations: JSONB  # summary, description 等
    starts_at: datetime
    ends_at: datetime | None
    generator_url: str | None
    raw_payload: JSONB  # 完整原始数据,便于回溯
    incident_id: int | None  # 外键关联 Incident
    embedding: Vector(512)  # pgvector, bge-small-zh-v1.5 是 512 维
    created_at: datetime
    updated_at: datetime
```

### 3.2 Incident 表（聚合后的事件）

```python
class Incident(Base):
    __tablename__ = "incidents"

    id: int (主键)
    title: str  # 由 LLM 生成的中文标题
    status: str  # "open" | "analyzing" | "resolved"
    severity: str  # 取关联告警的最高级别
    alert_count: int  # 关联的告警数量
    root_cause: str | None  # LLM 分析结果 - 根因
    impact: str | None  # LLM 分析结果 - 影响范围
    suggestions: str | None  # LLM 分析结果 - 处置建议
    llm_model: str | None  # 使用的模型名称
    llm_tokens_used: int | None
    analyzed_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

**关系**：一个 Incident 对应多个 Alert（1:N）

### 3.3 聚合逻辑

新告警进入时：
1. 计算 Embedding（输入：`alertname + labels + summary` 拼接字符串）
2. 在最近 24 小时的 `open` 状态 Incident 中查找相似度最高的 Alert
3. 若余弦相似度 > 0.85（可配置），归入该 Incident
4. 否则创建新 Incident
5. 触发 LLM 分析（异步）

---

## 4. 核心 API 设计

### 4.1 Webhook 接收（必须）

```
POST /api/v1/webhook/alertmanager
Content-Type: application/json

# 接收 Alertmanager 原生 webhook 格式
# 响应 200 即可,避免 Alertmanager 重试
```

### 4.2 告警查询

```
GET /api/v1/alerts?status=firing&severity=critical&page=1&size=20
GET /api/v1/alerts/{alert_id}
```

### 4.3 Incident 管理

```
GET /api/v1/incidents?status=open&page=1&size=20
GET /api/v1/incidents/{incident_id}
POST /api/v1/incidents/{incident_id}/analyze   # 手动触发重新分析
POST /api/v1/incidents/{incident_id}/resolve   # 标记解决
```

### 4.4 健康检查

```
GET /health          # liveness
GET /health/ready    # readiness (检查 DB + Redis)
```

---

## 5. LLM Provider 抽象层

```python
# backend/alertmind/llm/base.py
from abc import ABC, abstractmethod
from pydantic import BaseModel

class AnalysisResult(BaseModel):
    title: str  # Incident 标题(中文,不超过30字)
    root_cause: str  # 根因分析
    impact: str  # 影响范围
    suggestions: str  # 处置建议(markdown 列表格式)
    tokens_used: int

class LLMProvider(ABC):
    @abstractmethod
    async def analyze(self, prompt: str) -> AnalysisResult:
        """分析告警并返回结构化结果"""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass
```

**实现要点**：
- 所有 Provider 都返回统一的 `AnalysisResult` 结构
- 使用 JSON mode / Structured Output 保证返回格式稳定
- 失败时抛出统一的 `LLMProviderError` 异常
- 配置通过环境变量：`LLM_PROVIDER=openai|claude|qwen|ollama`

---

## 6. 核心 Prompt 模板（中文）

```python
# backend/alertmind/prompts/analyzer_zh.py

ANALYZER_SYSTEM_PROMPT = """你是一位资深的 SRE 工程师,精通 Kubernetes、Prometheus、云原生架构。
你的任务是分析生产环境告警,给出专业、简洁、可执行的诊断报告。

要求:
1. 输出必须是合法的 JSON,包含 title/root_cause/impact/suggestions 四个字段
2. title: 用一句话概括事件(不超过30个汉字)
3. root_cause: 基于告警信息推测最可能的根因,如信息不足请明确说明
4. impact: 分析对业务和系统的影响范围
5. suggestions: 给出 3-5 条可执行的排查/处置步骤,使用 markdown 列表
6. 全部使用中文回复,保持专业且易懂
"""

ANALYZER_USER_PROMPT_TEMPLATE = """请分析以下告警事件:

## 告警数量
该事件聚合了 {alert_count} 条相关告警。

## 告警详情
{alert_details}

## 关联上下文
{context}

请按系统提示的 JSON 格式输出分析结果。
"""
```

**Prompt 迭代策略**：把 Prompt 作为独立文件,便于后续 A/B 测试。

---

## 7. 通知推送（MVP 仅做企微 + 飞书）

```python
# backend/alertmind/core/notifier.py
class Notifier(ABC):
    @abstractmethod
    async def send(self, incident: Incident) -> bool: ...

class WeComNotifier(Notifier):
    """企业微信机器人"""
    webhook_url: str

class FeishuNotifier(Notifier):
    """飞书机器人"""
    webhook_url: str
```

**消息格式**：Markdown 卡片,包含 title、severity、root_cause 摘要、查看详情链接。

---

## 8. 分阶段开发路线图 ⭐ 重要

> **这是给 Claude Code 的核心指令**：请严格按以下阶段执行,每个阶段完成后输出"阶段 N 完成,请验收"并停止,等待用户确认后再进入下一阶段。

### 🏁 阶段 1：项目骨架（预计 1 天）

**交付物**：
- 完整的目录结构（按第 2 节）
- `pyproject.toml` 包含所有依赖
- `docker-compose.yml`（只启动 postgres + redis,先不包含应用）
- `docker-compose.dev.yml`（开发用,后端热重载）
- `.env.example` 列出所有环境变量
- FastAPI 最小骨架,跑通 `/health` 接口
- 数据库连接 + Alembic 初始化
- README.md 基础版(项目简介 + 快速开始占位)
- `.gitignore`、`LICENSE` (MIT)、GitHub Actions CI 框架
- ruff 配置 + pre-commit 配置

**验收标准**：
- `docker compose -f docker-compose.dev.yml up` 能启动 postgres + redis
- `uv run uvicorn alertmind.main:app --reload` 能启动后端
- `curl http://localhost:8000/health` 返回 `{"status": "ok"}`
- `ruff check .` 通过

---

### 🏁 阶段 2：Webhook 接收 + 数据持久化（预计 1 天）

**交付物**：
- `Alert` 和 `Incident` 的 SQLAlchemy 模型
- Alembic 迁移脚本（包含 pgvector 扩展启用）
- `POST /api/v1/webhook/alertmanager` 接收端,能解析 Alertmanager 原生格式并存库
- `GET /api/v1/alerts` 列表接口（支持分页、状态过滤）
- `GET /api/v1/alerts/{id}` 详情接口
- 单元测试：`test_webhook.py` 至少 3 个测试用例
- `scripts/send_test_alert.sh`：一键发送模拟告警

**验收标准**：
- 执行 `scripts/send_test_alert.sh` 后,数据库能看到告警记录
- `curl http://localhost:8000/api/v1/alerts` 返回列表
- `pytest` 全部通过

---

### 🏁 阶段 3：Embedding + 聚合引擎（预计 2 天）

**交付物**：
- `utils/embedding.py`：封装 `BAAI/bge-small-zh-v1.5`,提供 `embed(text: str) -> list[float]`
- pgvector 扩展启用 + `alerts.embedding` 字段迁移
- `core/aggregator.py`:
  - 新告警 → 计算 Embedding
  - 查找最近 24h 内相似 Incident
  - 相似度阈值可配置（环境变量 `AGGREGATION_SIMILARITY_THRESHOLD=0.85`）
  - 归并或创建 Incident
- `GET /api/v1/incidents` 列表接口
- `GET /api/v1/incidents/{id}` 详情接口（包含关联 alerts）
- 测试：`test_aggregator.py`,验证相似告警正确聚合

**验收标准**：
- 连续发送 5 条相似告警,聚合为 1 个 Incident
- 发送 1 条不相关告警,创建新 Incident
- `pytest` 全部通过

---

### 🏁 阶段 4：LLM 分析引擎（预计 2 天）

**交付物**：
- `llm/base.py` 抽象基类 + `AnalysisResult` schema
- `llm/openai_provider.py`、`llm/claude_provider.py`、`llm/qwen_provider.py`、`llm/ollama_provider.py` 四个实现
- `prompts/analyzer_zh.py` 中文 Prompt
- `core/analyzer.py`：Incident 创建/更新后异步触发 LLM 分析
- 分析结果写回 Incident 表
- `POST /api/v1/incidents/{id}/analyze` 手动触发分析
- 配置项：`LLM_PROVIDER`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`

**验收标准**：
- 配置 OpenAI API Key 后,新 Incident 自动生成中文分析报告
- 切换到 Ollama（本地模型）也能工作
- 手动触发 `POST /incidents/{id}/analyze` 能重新分析

---

### 🏁 阶段 5：前端 Dashboard（预计 2 天）

**交付物**：
- Vue3 + TypeScript + Vite 项目
- 路由：`/` (Dashboard) + `/incidents/:id` (详情页)
- Dashboard 页面：
  - Incident 列表（表格展示）
  - 按状态/严重度筛选
  - 分页
  - 实时刷新（轮询或 WebSocket,MVP 用 10s 轮询即可）
- 详情页：
  - 事件信息卡片
  - AI 分析结果（Markdown 渲染）
  - 关联原始告警列表（可展开）
  - "重新分析" 按钮
- Element Plus 主题化,配色专业
- 响应式布局

**验收标准**：
- `docker compose up` 后访问 `http://localhost:3000` 能看到 Dashboard
- 完整走通：发送告警 → Dashboard 看到 Incident → 点详情看 AI 分析

---

### 🏁 阶段 6：通知推送（预计 1 天）

**交付物**：
- `core/notifier.py`：企微 + 飞书机器人
- Incident 分析完成后自动推送
- 配置项：`WECOM_WEBHOOK_URL`、`FEISHU_WEBHOOK_URL`（可同时启用多个）
- Markdown 卡片格式,含查看详情链接

**验收标准**：
- 配置机器人 Webhook 后,新 Incident 分析完能收到推送

---

### 🏁 阶段 7：打磨 + 文档（预计 2 天）

**交付物**：
- 完整的 `README.md`（中英双语）：
  - Banner/Logo 占位
  - 一句话介绍 + 动图演示占位
  - Features
  - Quick Start（5 分钟上手）
  - Architecture 图
  - Configuration 全部环境变量说明
  - Roadmap
  - Contributing
  - License
- `docs/quickstart.md`、`docs/architecture.md`
- `examples/alertmanager-config.yml` 示例配置
- 所有 API 的 OpenAPI 文档完善（FastAPI 自带,但需补充 description）
- 完整的 `docker-compose.yml`（含应用,真正一键启动）
- GitHub Actions：lint + test + docker build 全部绿色
- CHANGELOG.md 记录 v0.1.0

**验收标准**：
- 陌生人按 README 操作能在 5 分钟内跑起来
- `docker compose up` 一条命令启动完整系统
- CI 全绿

---

## 9. 代码规范

- Python 代码全部使用类型注解（Pydantic v2 + SQLAlchemy 2.0 类型注解语法）
- 所有函数/类必须有 docstring（中文即可）
- 异常统一从 `alertmind.exceptions` 导出自定义异常
- 日志使用 loguru,不要用 print
- 不要写过于"聪明"的代码,优先可读性
- 数据库查询全部异步（`AsyncSession`）
- 敏感配置从环境变量读取,不要硬编码

---

## 10. 测试策略

- 单元测试覆盖率目标 ≥ 60%（MVP 阶段不追求高覆盖）
- 核心模块必须有测试：`aggregator`、`analyzer`、`webhook`
- 使用 `pytest-asyncio` 测试异步代码
- 使用 `pytest-postgresql` 或 testcontainers 起测试数据库
- LLM 调用在测试中 mock,不要真实调用 API

---

## 11. 不要做的事 ❌

- 不要引入 Celery（MVP 用 BackgroundTasks 够用）
- 不要引入 Kafka（MVP 直接写库够用）
- 不要做用户系统 / 权限 / 多租户（v2 再说）
- 不要做对话式追问（v2 再说）
- 不要做告警自动修复执行（危险,后期再议）
- 不要用 Black,统一用 ruff format
- 不要自己造 ORM,用 SQLAlchemy 2.0
- 不要在 MVP 阶段做 Helm Chart（v2 再做）
- 不要一次性生成所有阶段的代码,严格按阶段推进

---

## 12. 第一步

请先阅读完本文档,然后执行**阶段 1：项目骨架**。完成后告诉我"阶段 1 完成,请验收",并列出交付清单。

在开始之前,请告诉我：
1. 你对本文档有没有疑问?
2. 你计划在阶段 1 采用什么具体的目录初始化方式?
3. 是否需要我补充任何不清楚的细节?

# Known Technical Debt

本文档记录已识别但暂未修复的技术债务。每条记录包含 git blame 来源、修复建议、计划偿还时机。

## ~~TD-001: Mapped[dict] 缺少类型参数（mypy 警告）~~ ✅ RESOLVED

**偿还**：commit `9a481e6` (2026-04-24)
**原因**：`|| true` 绕过与"时间炸弹守门员"精神矛盾；阶段 4 需要 mypy 作为实时守门员拦截 LLM Provider 代码的类型错误

以下为历史记录，保留供未来溯源：

- ~~**位置**~~：
  - ~~`backend/alertmind/models/alert.py:76` (labels)~~
  - ~~`backend/alertmind/models/alert.py:81` (annotations)~~
  - ~~`backend/alertmind/models/alert.py:101` (raw_payload)~~
- ~~**来源**：commit d55a78c9 (阶段 2 Wave 1, 2026-04-19)~~
- ~~**现状**：mypy 报 "Missing type arguments for generic type 'dict'"~~
- ~~**修复建议**：改为 `Mapped[dict[str, Any]]`~~
- ~~**影响**：仅 mypy 静态检查，运行时无影响~~
- ~~**计划偿还**：阶段 7 代码质量打磨~~

## ~~TD-002: Redis .ping() 返回类型不兼容 await~~ ✅ RESOLVED

**偿还**：commit `9a481e6` (2026-04-24)
**方案**：`cast(Awaitable[bool], client.ping())` 缩窄联合类型，不改 import（原 import 已是 `redis.asyncio.Redis`，无可升级空间）

以下为历史记录，保留供未来溯源：

- ~~**位置**：`backend/alertmind/api/health.py:39`~~
- ~~**来源**：commit a53aef55 (阶段 1, 2026-04-18)~~
- ~~**现状**：mypy 报 "Incompatible types in 'await'"~~
- ~~**修复建议**：使用 `cast(Awaitable[bool], redis_client.ping())` 或升级 redis-py 类型 stub~~
- ~~**影响**：仅 mypy 静态检查，运行时无影响~~
- ~~**计划偿还**：阶段 7 代码质量打磨~~

## TD-004: PRD §3.2 Incident 表字段未完整实现（LLM 相关字段）

- **位置**：
  - `backend/alertmind/models/incident.py`
  - `backend/alembic/versions/2026_04_18_1830-0001_init_alerts_and_incidents.py`

- **来源**：阶段 2 Wave 1（commit d55a78c9）

- **偿还时机**：**阶段 4 Wave 1 作为先行 schema 补齐 migration**（alembic 0003）

- **精确 spec**（由 ADR-010 Decision F 裁定；阶段 4 W1 派发 database-architect 时直接引用）：

### alembic 0003 字段清单

| # | 字段 | 类型 | Nullable | 来源 | 语义 |
|---|---|---|---|---|---|
| 1 | `title` | String(512) | NOT NULL | **已存在**（保留） | Dashboard 卡片标题（≤ 60 字），LLM 分析后覆盖占位串 |
| 2 | `summary` | Text | NULL | **已存在**（语义锁定） | 卡片副标题：现象 + 规模（≤ 150 字）。**不是** title 缩短版，**不是** root_cause 摘要 |
| 3 | `root_cause` | Text | NULL | 新增 | 详情页正文：完整根因分析（无长度上限） |
| 4 | `impact` | Text | NULL | 新增 | LLM 影响范围分析 |
| 5 | `suggestions` | Text | NULL | 新增 | LLM 处置建议（markdown 列表格式） |
| 6 | `llm_model` | String(128) | NULL | 新增 | 使用的模型名（如 `gpt-4o-mini`） |
| 7 | `llm_tokens_used` | Integer | NULL | 新增 | 本次分析 total token 数 |
| 8 | `analyzed_at` | DateTime(tz=True) | NULL | 新增 | 分析完成时间；判"是否已分析"以本字段 IS NOT NULL 为准 |
| 9 | `prompt_version` | String(64) | NULL | 新增 | Prompt 版本号，格式 `{name}_{lang}_v{N}`（如 `analyzer_zh_v1`） |

### status CheckConstraint 扩展

- 旧：`status IN ('open', 'resolved')`
- 新：`status IN ('open', 'analyzing', 'resolved')`
- **PostgreSQL 限制**：不支持直接 `ALTER CHECK`，migration 必须：
  1. `ALTER TABLE incidents DROP CONSTRAINT ck_incidents_status`
  2. `ALTER TABLE incidents ADD CONSTRAINT ck_incidents_status CHECK (status IN ('open', 'analyzing', 'resolved'))`
- **downgrade** 必须反向执行（回到 open/resolved 二值）；若下行时存在 `status='analyzing'`
  的数据必须先处理（例如 UPDATE 回 open），否则 ADD CONSTRAINT 会失败

### 其他约束

- 字段 2（`summary`）语义由 ADR-010 Decision F 锁定：副标题定位，长度 ≤ 150 字。
  **不允许**在 W1 迁移 docstring 之外使用 summary 存其他内容
- 字段 8（`analyzed_at`）与 `status` 字段**正交**：状态机见 ADR-010 Decision D
- 本次迁移**仅 schema**，不涉及业务代码；LLM 调用逻辑在 W2-W5 实现

### 跨 Agent 可见性

- 阶段 4 Wave 1 派发 database-architect 时必须引用：`docs/adr/ADR-010.md` Decision F
  + 本 TD-004（9 字段清单是 TD 本节的唯一事实来源）
- 阶段 4 Wave 2+ 派发 backend-engineer 时必须引用：本 TD 的字段清单 + ADR-010 决策 A-K

## TD-003: test_aggregate_24h_window_outside 测试强度不足

- **位置**：`backend/tests/test_aggregator.py::test_aggregate_24h_window_outside`（line 450+）
- **来源**：commit d288114 (阶段 3 Wave 5, 2026-04-20)
- **现状**：
  - 测试本意：验证"24h 窗口外的 incident 不参与聚合"
  - 实际行为：member alert 用 `embedder.embed(...)` 生成 embedding，新 alert 用 `_embed_for(...)` 生成
  - 两个 embedding 的 token 域不同，相似度本来就 <0.85
  - 即使**删除 aggregator 里的 24h 窗口逻辑**，测试仍会 PASS
- **风险**：未来重构 aggregator 若误删窗口逻辑，此测试无法作为回归守门员
- **修复建议**：
  - 让 member 也用 `_embed_for(...)` 构造，使相似度 ≈ 1.0
  - 让 aggregator "拒绝归入" 的唯一原因变成"窗口超时"
  - 这样测试才真正验证 24h 窗口的 aggregator 逻辑
- **影响**：当前测试依然有意义（至少验证 aggregator 不会对窗口外 incident 盲目归入），
  只是对窗口逻辑的保护不足
- **计划偿还**：阶段 7 代码质量打磨 / 或阶段 4 开始时顺手补（因为阶段 4 会频繁调 aggregator，
  此时发现回归成本低）

## TD-005: 测试风格守门员不支持 docstring 豁免

- **位置**：`backend/tests/test_style_guards.py::test_no_absolute_datetime_literals_in_fixtures`
- **来源**：commit a330871 (阶段 3 CI 修复 Commit C, 2026-04-24)
- **现状**：
  - 守门员的豁免逻辑仅覆盖以 `#` 开头的整行注释
  - docstring 内容（三引号字符串）会被视为普通代码扫描
  - 行尾注释（如 `x = 1  # 2026-04-20T...`）也会被扫描
- **当前 workaround**：
  - Commit C 中 `_recent_starts_at` 的 docstring 用叙述式"绝对 ISO 日期字面量"
    替代真实的反例字面量，避免守门员误伤
  - 该 workaround 依赖作者主动避坑
- **未来风险**：
  - 新写测试的人在 docstring 里写反例说明时，会被守门员误伤
  - 临时豁免会诱导开发者加 `# type: ignore` 式的跳过标记，侵蚀守门员价值
- **修复方向**：
  - 方案 A：用 `ast.parse()` + visitor 模式跳过所有 docstring 节点
  - 方案 B：regex 升级识别三引号字符串边界
  - 方案 C：保留现状，仅在新命中时手动处理（接受现实）
- **计划偿还**：阶段 7 代码质量打磨，或守门员首次出现误伤时立即处理

这条 TD 本身不阻塞任何当前功能，仅记录架构限制。

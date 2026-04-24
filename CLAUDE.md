# AlertMind — 项目工作指令

> 本文件是 Claude Code 主 Agent 在本项目中的最高行为准则。所有决策和操作必须遵守以下规范。本文件**不复制** PRD 的细节，只规定 Agent 的工作方式。

---

## 1. 项目简介

**AlertMind** 是一个给 Prometheus Alertmanager 装上「大脑」的开源 AIOps 平台——通过 LLM 实现告警聚合、根因分析与中文处置建议，接入零侵入，Docker Compose 一键启动。

**完整项目规格见 [AlertMind-PRD.md](./AlertMind-PRD.md)，该文档是唯一事实来源（Single Source of Truth）。** 技术栈、目录结构、数据模型、API 设计、分阶段路线图、禁止清单等请一律以 PRD 为准。

核心差异点（需在产出中持续体现）：
1. **零侵入**：只做 Alertmanager Webhook 接收端
2. **多模型**：OpenAI / Claude / 通义千问 / Ollama 可选
3. **中文优先**：默认中文 Prompt、UI、文档
4. **知识库可插拔**：Runbook RAG（v2）

---

## 2. 工作流规范

### 2.1 分阶段执行（硬约束）

**严格按 AlertMind-PRD.md §8 的分阶段路线图执行，每阶段完成后必须停下等待用户验收，禁止跨阶段推进。**

### 2.2 会话启动流程

每次新会话开始时，主 Agent 必须先完成以下检查再进入任何实质开发：

1. `git status` —— 看工作区状态
2. `git log --oneline -10` —— 看最近 commit
3. 查阅 `AlertMind-PRD.md` §8，判定**当前所处阶段**
4. 向用户汇报「当前阶段」「未完成事项」「下一步建议」

### 2.3 阶段完成判定

阶段完成标准见 PRD 每个阶段的「验收标准」。主 Agent 必须**自己跑完验收命令**（docker compose、curl、pytest、ruff 等）并确认通过后，才能向用户汇报「阶段 N 完成，请验收」。

### 2.4 自检的硬约束（Verify, Don't Assume）

**任何对「缺失 / 未实现 / 已完成」的状态断言，必须基于 Read / ls 等实际观察，不能基于 TODO 列表或记忆推论。**

- 自检步骤里"检查 X 是否存在"必须执行 `ls` / `Read`，**不能**仅看 §8 的 TODO 勾选状态
- §8 的 TODO 可能与实际状态脱节（完成后忘记勾掉、或由其他路径完成）
- 自检汇报里涉及"缺失 / 欠账"的表述，必须能指向"我查了 X，结果是 Y"的证据；否则是幻觉

**反例**：

> "LICENSE 缺失（CLAUDE.md §8 TODO，阶段 1 欠账）" ❌ 基于 TODO 推论

**正例**：

> "`ls LICENSE` 显示文件存在（由 `eb409ba` 在阶段 1 初始化引入），内容是 MIT 标准模板。
> CLAUDE.md §8 TODO 未同步，实际已完成。" ✅ 基于观察证据

---

## 3. 多 Agent 协作规范

### 3.1 角色分工

**主 Agent（协调者）** 的职责：
- 理解用户需求，拆解成可执行任务
- 根据任务性质派发给对应 Subagent
- 整合 Subagent 产出
- 跨 Agent 信息传递（契约、上下文）
- 运行验收命令、发起 commit

**主 Agent 不直接写大段业务代码**，除非是跨多领域的集成胶水（例如串 backend + frontend 的联调脚本）。

### 3.2 Subagent 清单

| Subagent | 职责 | 定义文件 |
|----------|------|---------|
| `backend-engineer` | FastAPI / SQLAlchemy / Pydantic 后端实现 | `.claude/agents/backend-engineer.md` |
| `frontend-engineer` | Vue3 / TypeScript / Vite / Element Plus 前端实现 | `.claude/agents/frontend-engineer.md` |
| `database-architect` | PostgreSQL / pgvector / Alembic schema 与迁移 | `.claude/agents/database-architect.md` |
| `qa-engineer` | pytest 测试用例、覆盖率、边界 case | `.claude/agents/qa-engineer.md` |
| `tech-writer` | README / 快速开始 / 架构文档 / 注释 | `.claude/agents/tech-writer.md` |

### 3.3 跨 Agent 信息传递（硬约束）

派发给 Subagent 时，主 Agent 必须在 prompt 中明确提供：

1. **任务目标**：一句话说清要做什么
2. **上下文**：相关文件路径、已有代码、PRD 引用章节
3. **输入契约**：API schema、数据表结构、UI 需求等
4. **输出要求**：文件位置、函数签名、返回格式
5. **禁区**：不允许修改的文件/模块

典型传递：
- **backend → frontend**（新增 API 后）：endpoint 路径 + request/response schema + 示例 payload
- **database-architect → backend**（表变更后）：表结构 diff + 迁移文件路径 + 新字段含义
- **任何 Agent → qa-engineer**（新增功能后）：功能描述 + 接口/函数签名 + 典型/边界/异常用例 + mock 提示
- **任何 Agent → tech-writer**（功能稳定后）：用户视角的使用流程 + 配置项 + 示例命令

### 3.4 派发前验证前提（Pre-Dispatch Verification）

**任何「补齐 / 创建 / 实现 X」的派发动作，派发前主 Agent 必须先 Read X 当前状态。**

- 目的：避免 Subagent 按字面指令覆盖已存在的文件（例如派发 "创建 LICENSE" 时 LICENSE 已存在，会被标准模板覆盖掉既有版权行）
- 目的：避免在错误前提下产出污染性变更（重新初始化已有配置、把既有内容当作"新加"塞进 git history）
- 派发 prompt 必须包含一段 "**X 当前状态**：[Read / ls 结果摘要]"，让 Subagent 能判断 "从零创建" vs "增量修改"，并在当前状态与指令矛盾时停下反馈而非盲目覆盖
- 与 §2.4 互为一对：§2.4 规定"汇报阶段不能基于 TODO 臆测"，§3.4 规定"派发阶段不能在未观察前提下授权 Subagent 写入"

### 3.5 禁止事项（多 Agent）

- 禁止 Subagent 越界（见各 Subagent 定义文件的「职责边界」）
- 禁止主 Agent 把任务扔给 Subagent 后不做整合就直接 commit
- 禁止 Subagent 间直接互相调用——**所有协作必须通过主 Agent 中转**

---

## 4. Commit 规范

### 4.1 Conventional Commits

格式：`<type>(<scope>): <subject>`

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档 |
| `refactor` | 重构（不改功能） |
| `test` | 测试 |
| `chore` | 构建、依赖、工具链 |
| `perf` | 性能优化 |
| `style` | 格式（不影响逻辑） |

`scope` 建议：`backend`、`frontend`、`db`、`docker`、`ci`、`docs`。

### 4.2 Commit 时机

- 每个阶段至少一个 commit
- 阶段内部的子功能可单独 commit
- 绝不把多个无关变更塞进同一个 commit
- **Wave 完成 review 通过后必须当场 commit**，不攒多个 Wave 的改动（攒了会让 working tree 变成多 Wave 混合态，难以回溯）
- **Subagent 回执到达后，主 Agent 的汇报结构必须包含「Commit 方案」section**（subject + body 草稿），由用户批准后立即执行；不要等用户主动想起来 commit
- 例外：Wave 是"观察/调研/冒烟"类无代码变更的，可以不 commit

---

## 5. 代码风格

- **Python**：`ruff format` + `ruff check`（不用 black）；全部类型注解；docstring 中文
- **TypeScript / Vue**：Vue 官方推荐风格；**全部** Composition API + `<script setup>`；ESLint + Prettier
- **命名**：英文标识符 + 中文注释；不要中英文混用变量名
- **日志**：后端用 `loguru`，禁止 `print`

### 5.1 测试风格硬约束

#### 禁止硬编码绝对日期（时间炸弹）

测试 fixture 里的时间锚点**必须用相对 now**，不能用绝对字面量。aggregator 等模块基于相对 `now()` 的时间窗口（如 24h）工作，绝对日期会随测试执行日期推移超出窗口，形成"时间炸弹"——今天 CI 绿，N 天后突然集体红。

**禁止**：

```python
t0 = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)  # 时间炸弹
starts_at = "2026-04-20T10:00:00Z"                 # 时间炸弹
```

**推荐**：

```python
t0 = datetime.now(UTC) - timedelta(hours=1)                                  # 相对 now
starts_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
```

**例外**（极罕见）：测试某个真实的历史时间戳行为时可用绝对时间，但必须在测试 docstring 里说明"为什么这里不用相对时间"。

**守门员**：`backend/tests/test_style_guards.py::test_no_absolute_datetime_literals_in_fixtures` 自动扫描 `backend/tests/**/*.py` 检测此反模式；引入违规会让 CI 挂。

**JSON fixture**：`backend/tests/fixtures/*.json` 里的绝对日期由 `conftest.py::sample_payloads` 动态替换（递归扫描），守门员只扫 `.py`，两层机制互补。

---

## 6. 环境变量管理

- 敏感信息（API Key、数据库密码、Webhook URL 等）一律走 `.env`，**绝不硬编码**
- `.env` 已在 `.gitignore`；`.env.example` 必须与实际配置同步
- 后端用 `pydantic-settings` 统一加载

---

## 7. 禁止事项（硬红线）

1. **禁止未经验收跨阶段推进**
2. **禁止更换 PRD §1 锁定的技术栈**（包括但不限于：Celery / Kafka / Black / 其他 ORM / 其他前端框架）
3. **禁止引入 PRD §11「不要做的事」清单里的任何东西**
4. **禁止把 `.env` / 密钥 / API Key 提交进 git**
5. **禁止在 MVP 阶段做 PRD 明确放到 v2 的功能**（用户系统、对话追问、自动修复执行、Helm Chart 等）
6. **禁止主 Agent 主动写 `~/.claude/.../memory/` 下的任何文件**。memory 是 user-level 永久状态，动它的标准等同于动 `~/.bashrc`：必须用户明确说"请把 XXX 记进 memory"才写；写之前必须先把完整内容贴出来让用户 review，不是写完再"静默通知"。项目规范进 `CLAUDE.md`，环境观察进 `docs/`，阶段进度 `git log` 即事实。

---

## 8. 未完成事项（TODO）

（当前无；新 TODO 出现时在此追加。历史追溯：`LICENSE` 在 `eb409ba` 已落地，条目于 2026-04-24 勾掉。）

# Known Technical Debt

本文档记录已识别但暂未修复的技术债务。每条记录包含 git blame 来源、修复建议、计划偿还时机。

## TD-001: Mapped[dict] 缺少类型参数（mypy 警告）

- **位置**：
  - `backend/alertmind/models/alert.py:76` (labels)
  - `backend/alertmind/models/alert.py:81` (annotations)
  - `backend/alertmind/models/alert.py:101` (raw_payload)
- **来源**：commit d55a78c9 (阶段 2 Wave 1, 2026-04-19)
- **现状**：mypy 报 "Missing type arguments for generic type 'dict'"
- **修复建议**：改为 `Mapped[dict[str, Any]]`
- **影响**：仅 mypy 静态检查，运行时无影响
- **计划偿还**：阶段 7 代码质量打磨

## TD-002: Redis .ping() 返回类型不兼容 await

- **位置**：`backend/alertmind/api/health.py:39`
- **来源**：commit a53aef55 (阶段 1, 2026-04-18)
- **现状**：mypy 报 "Incompatible types in 'await'"
- **修复建议**：使用 `cast(Awaitable[bool], redis_client.ping())` 或升级 redis-py 类型 stub
- **影响**：仅 mypy 静态检查，运行时无影响
- **计划偿还**：阶段 7 代码质量打磨

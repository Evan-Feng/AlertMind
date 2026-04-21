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

# aite 完善路线图

> 约定：每完成或变更一项，立即更新对应条目与文末进度记录。
> 顺序原则：核心功能优先，前端体验最后。

## 阶段 1：基线确认

- [x] 跑全量 `pytest tests/`，确认 145 用例状态 → 145 passed（10s，5 个无害警告）
- [x] 跑 `ruff check aite tests`，确认零 lint 错误 → All checks passed

验证方式：测试全绿、lint 零报错；如有失败先记录再决定修复。

## 阶段 2：补核心缺口

- [ ] Judge `pairwise` 补完（位置偏差双向对比，README 已承诺）
- [ ] Judge `multi` 补完（多模型一致性）
- [ ] 实现 `DELETE /suites/{name}`（当前 501，`aite/web/routers/suites.py:65`）
- [ ] `_expand_variants()` 加展开上限保护，防止 repeats×扰动组合爆炸

验证方式：每项新增对应单测并通过。

## 阶段 3：健壮性

- [ ] Judge 调用失败时的降级策略（标记 skipped/error 而非中断整个 run）
- [ ] Gateway `_cache_key()` 高并发冲突加固

验证方式：异常注入测试通过。

## 阶段 4：可运维

- [ ] 成本价格表从代码外置到配置（`aite/utils/cost.py`）
- [ ] 存储保留/清理策略（历史 run 数据）
- [ ] `run_metric_summary` 趋势查询索引确认与补齐

验证方式：新模型定价不改代码即可配；查询计划对比。

## 阶段 5：测试补齐

- [ ] trends API 测试
- [ ] langchain_agent 连接器集成测试
- [ ] 并发场景测试（限流器/信号量）

## 阶段 6：前端体验优化（最后）

- [ ] 整体视觉与交互升级（迁移路径见 web-ui/README.md：Vite + TS + AntD Vue）
- [ ] 仅在核心功能稳定后启动

## 已知薄弱点参考（决策依据）

1. Judge pairwise/multi 薄弱；2. 套件删除 501；3. 变体展开无上限、judge 无降级；
4. 价格表硬编码；5. Agent 连接器/trends/并发测试缺失；6. 无数据清理策略，限流缓存仅进程内。

## 进度记录

- 2026-08-28 建立路线图，完成架构盘点（模块地图/执行链路/能力清单见会话记录）。
- 2026-08-28 确立项目标准（架构冻结范围/解耦规则/开发节奏/git 规范），写入 CLAUDE.md；补 .gitignore；阶段 1 基线确认通过（145 passed + ruff clean），执行基线提交。

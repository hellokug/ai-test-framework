# aite 架构设计

> 定位：统一评测 LLM / Agent / RAG / 通用维度（性能·可靠性·功能·安全），支持公开基准 + 私有数据集，兼容 evalscope / lm-evaluation-harness（不 fork）。

## 分层

```
┌────────────────────────────────────────────────────────────┐
│ 接口层  CLI (Typer+Rich: aite run/report/serve…)           │
│         Web (FastAPI + Vue3/ECharts SPA)                   │
├────────────────────────────────────────────────────────────┤
│ 编排层  TestSuiteLoader(YAML→pydantic) → ExecutionEngine    │
│         (asyncio+httpx 并发/重试/限流) → Aggregator(pass/fail)│
├────────────────────────────────────────────────────────────┤
│ 插件层  Connectors │ Datasets │ Metrics │ Judges            │
│         (Registry 注册表 + entry-points 可插拔)             │
├────────────────────────────────────────────────────────────┤
│ 基础层  Storage(SQLAlchemy/SQLite) │ ReportRenderer        │
│         ModelGateway(judge/embedding 统一调用+计费) │ Config │
└────────────────────────────────────────────────────────────┘
```

关键约束（已实现）：

- **CLI 和 Web 都是 Core API 的薄壳**。核心入口是 `ExecutionEngine.run_suite(config)`
  （`aite/core/engine.py`）与 `Repository`（`aite/core/interfaces.py`）。
  `aite.core` 不 import Typer/FastAPI；CLI 位于 `aite/cli`，Web 位于 `aite/web`。
- **进度条抽象为 `ProgressReporter`**：Rich（CLI 实时条）/ Noop（`--quiet`）/ Callback
  （桥接 Web SSE 事件总线），见 `aite/cli/progress.py` 与 `aite/web/events.py`。

## 数据流（两阶段执行）

```
suite.yaml → loader(pydantic 校验) → Registry 解析组件
→ engine: 建库记录 → dataset.load() → 物化 repeats/perturbations 衍生 case
→ Phase A: 并发 invoke（信号量 + rpm 令牌桶 + per-case 超时 + 连接器内重试）
→ 物化所有 responses（test_case_result 落库）
→ Phase B: 并发计算指标
    · 规则指标逐 case 并行
    · wants_variants 指标拿到整个变体组（base + repeat-N + perturb-*）
    · Judge 走 LLMGateway（独立限流队列 + 成本熔断 + 结果缓存）
→ Aggregator 按 metric × dataset × group 聚合（含 overall 折叠桶）
→ 对照 thresholds 判 pass/fail → pass_strategy 汇总状态
→ 落库（run_metric_summary）→ Renderer(html/json/rich/junit) → CI 退出码
```

### 可靠性变体的实现（ExecutionPlan 思路）

`ExecutionEngine._expand_variants()` 把每个 base case 复制为：

- `repeat-1..N-1`：与 base 相同输入，用于 repeat_consistency / stability
- `perturb-<kind>`：`utils/text.perturb_text()` 确定性扰动（typo/case/whitespace/paraphrase，
  seed 由 case id 派生），用于 perturbation_robustness

聚合时这些变体通过 `variant_of` 归回原 case，只在 base case 上产出指标记录。

## 插件机制

三种种法（详见 docs/plugin-guide.md）：

1. 内置组件：`@register_connector/@register_metric/@register_dataset/@register_judge`
   装饰器，`aite.connectors/aite.metrics/aite.datasets` 包导入即注册。
2. YAML `plugins: [mypkg.metrics]` 显式导入模块（失败仅告警不中断）。
3. 安装包暴露 entry_points 组 `aite.plugins`（值为模块路径）。

## 存储与中间件策略

**数据库兼容性（SQLite ⇄ MySQL/PostgreSQL）**：

- 表定义全部使用标准 SQLAlchemy 2.0 类型（`aite/storage/models.py`），无 SQLite 特有语法。
- UUID 主键用 `GUID` TypeDecorator（底层 CHAR(36)，Python 侧为规范带连字符字符串）。
- JSON 列用 `sa.JSON`，MySQL 5.7+ / PG(JSONB) / SQLite 均原生支持，自动映射方言。
- 切换仅需改 `storage.url`：`sqlite+aiosqlite:///` → `mysql+aiomysql://` →
  `postgresql+asyncpg://`；Repository 层已隔离方言差异。
- SQLite 默认开 WAL + busy_timeout + 外键（连接事件中设置）。

**零中间件默认部署（不需要 Redis）**：

| 需求 | 默认方案 | 分布式预留接口 |
|---|---|---|
| 任务调度 | asyncio + bg_task 表轮询 | TaskQueue 抽象 |
| 限流 | 进程内令牌桶（`InProcessRateLimiter`） | `RateLimiter` Protocol |
| judge 结果缓存 | 磁盘文件缓存（`DiskCache`，`AITE_CACHE_DIR`） | `CacheBackend` Protocol |
| SSE 实时进度 | 进程内 `EventBus`（带历史回放） | `EventBus` 可替换为 Redis pub/sub |

仅多机分布式压测或多实例 Web 部署时引入 Redis（未来 `aite[distributed]` extra）。

## Web 架构

- `aite/web/app.py`：`create_app(storage_url, token)` 工厂；`AppState` 持有 repo/EventBus/任务表。
- routers（`/api/v1`）：runs（含 SSE stream）/ suites / cases / models / trends /
  reports（实时重渲染）/ manual_eval / tasks（bg_task + stats）。
- 可选 Bearer 鉴权：`AITE_WEB_TOKEN` 环境变量（设置后除 /health 外全部需要 token）。
- 前端：`aite/web/static/` 下的免构建 SPA（Vue3 + ECharts CDN、手写企业级样式）。
  页面：Runs 列表 / Run 详情（case 表格+指标）/ 趋势（折线+雷达）/ 用例管理 /
  套件编辑（YAML）/ 模型管理 / 人工评测 / 任务。`web-ui/README.md` 记录了迁移到
  Vite+TS+AntD 工程的路径。

## 目录结构

```
aite/
├── core/        types/interfaces/registry/loader/engine/aggregator/context
├── connectors/  base/openai_compatible/anthropic/gemini/custom_http/langchain_agent/tracing
├── datasets/    local/hf_hub/builtin(+data)/compat(lm_eval/evalscope/bench_import)
├── metrics/     rules/nlp_stats/performance/reliability/embedding/llm_based/
│                agent_metrics/rag_metrics/safety + judge(score/pairwise/multi)
├── gateway/     llm_gateway（限流/计费/熔断/缓存/嵌入）
├── storage/     db/models/repositories/migrations
├── reporting/   renderers(html/json/rich/junit)/rebuild/templates
├── cli/         main + progress
├── web/         app/events/deps/routers + static SPA
└── utils/       logging/async_utils/cost(计价表)/text
```

## 风险与对策（对应实现）

| 风险 | 对策落点 |
|---|---|
| 引擎复杂度 | 两阶段执行；judge 独立限流；`--concurrency 1` / `--quiet` 调试；engine 全链路 mock 单测 |
| Judge 不可复现 | gateway temp=0 + seed；judge prompt_version 落库（metric_result.raw）；multi-judge agreement；pairwise 双向取均值消除位置偏差 |
| 上游框架格式漂移 | compat 层独立模块 + 子进程桥接兜底（`aite bench import`） |
| Windows 环境 | 全 async + pathlib + 无 shell 调用；路径统一 str(Path) |
| 成本失控 | Gateway 全局计费 + `max_cost_usd` 熔断（judge 调用前阻断）+ 引擎侧 target 成本阻断 |
| SQLite 并发 | WAL + 单写者 session（每次操作独立 session）；Repository 抽象可换 PG |

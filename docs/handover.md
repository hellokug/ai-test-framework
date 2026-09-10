# 项目交接文档

> 目的：**新会话按模块认领工作，最小上下文开工**。做法：读本文对应模块小节 +
> `docs/roadmap.md` 对应条目（决策与踩坑都在那），即可动手；不必通读全部代码。
> 项目约定见 `CLAUDE.md`（架构冻结范围/开发节奏/git 规范），本文不重复。

## 1. 项目一页纸

**aite 是评测平台，不是评测引擎**（2026-09-01 定位）。四层架构：接口层
（core/interfaces.py + types.py，契约冻结）/ 编排层（core/engine.py）/ 插件层
（datasets · metrics · connectors · tools）/ 基础层（storage · reporting · web）。

三大评测域与路由（suite yaml 的 `engine` 字段）：

| 域 | engine | 执行者 | 指标 |
|---|---|---|---|
| 精度基准 | `evalscope` | evalscope（进程内 run_task） | mean_acc 等 |
| 功能测试 | `pytest` | pytest 子进程（harness 插件） | 场景级 pass_rate |
| 性能压测 | `evalscope-perf` | evalscope perf 子进程 | TTFT/吞吐/QPS 分档 |
| 自研引擎 | （不写 engine） | 内置引擎 | 规则指标/延迟/judge 等 |

统一链路：`aite run -c <yaml>` → 路由 → 结果入 `run_metric_summary`（场景行 +
overall 加权行）→ 阈值判定 → Web 趋势并列可比。**存储契约（storage/models.py）
未因工具域改动**。

## 2. 模块地图

### tools 工具适配层（`aite/tools/`）

- **base.py**：ToolRunRequest（含 tests_dir/cases/perf 字段）/ ToolMetricRow
  （含 higher_is_better 方向）/ ToolRunResult（含 failures）/ ToolAdapter(ABC) /
  `@register_tool` 注册表 / `api_endpoint` / `list_tools`（REST 引擎枚举）。
  **纯函数 build_config 可单测**是惯例。
- **evalscope_adapter.py**：精度基准。进程内 run_task，文件收集 reports/*.json。
- **pytest_adapter.py**：功能测试。逐场景 subprocess pytest + junit-xml 收集；
  tools.cases 用 nodeid 精确定向。
- **perf_adapter.py**：压测（evalscope-perf）。subprocess 调 evalscope CLI（包无
  __main__，须 shutil.which）；perf.dataset 导出逐行 prompt；四模式
  （baseline/ramp/probe/soak）；**时长双键**——duration=整场总时长（单档直传/
  多档均分/probe 看门狗截断）、level_duration=每档秒数（probe=每轮上限），互斥；
  时长驱动自动放大 number 缺省；collect 统一 glob `**/benchmark_summary.json`
  按并发聚合（两种产出布局，容错跳过截断损坏文件）。
- **locust_adapter.py**：通用 HTTP 压测备选（engine: locust）。复用同一 perf 段；
  probe 报错不支持、必配时长键、number 忽略、新增 spawn_rate；每档独立
  `locust --headless --csv --csv-full-history` 子进程，locustfile 由适配器生成
  （SSE 首包计时 TTFT 自定义事件）；collect 解析 _stats/_failures/history CSV
  → `locust:u<N>` 指标行（**取 POST 行**——Aggregated 被 POST+TTFT 双事件污染）
  + 错误明细 + 秒级时序。
- **负载三选一**：dataset / prompt / `synthetic: {count, prompt_length}`
  （确定性合成负载，两适配器共用 generate_synthetic_prompts）。
- **时序管线（F2 数据层）**：两适配器统一产出 work_dir/timeseries.json
  （evalscope-perf 从 benchmark_data.db 逐请求 5s 桶聚合 / locust history 1s
  透传）→ `GET /runs/{run_id}/timeseries` 端点；产出经 test_run.details 落库
  （models.py 新列 + storage/migrations.py 老库轻迁移）。
- **web/routers/tools.py + suites.py + runs.py**：四域 REST——GET
  /tools/engines、/tools/benchmarks；POST /suites|/runs 带 engine 自动路由
  工具域（parse_tool_suite 校验 / _execute_tool_run 后台执行）；DELETE
  /suites 实现。API 全表见 README Web 平台章节。
- 测试：`tests/unit/test_tools.py`（parse/overall）、各 adapter 各自文件
  （test_perf_adapter / test_locust_adapter / test_pytest_adapter）、
  web 冒烟在 tests/integration/test_reporting_web.py。

### functional 功能测试（`aite/functional/`）

- **harness.py**：pytest 插件（`-p aite.functional.harness` 加载，不走 conftest——
  confcutdir 陷阱）。四块：模型访问（_post 重试/SSE 流式/预期错误模式/请求构造
  控制）、数据驱动（Case/load_cases/expect DSL 全部断言键）、收集器
  （pytest_collect_file → CasesFile → CaseItem，nodeid=cases.jsonl::id）、
  失败详情（failure_detail 带预算截断）。
- **scenarios/**：8 场景 52 例 jsonl——能力域 4（basic_chat/structured_output/
  tool_calling/long_context）+ API 契约域 4（api_params 22/api_stream 4/
  api_limits 7/api_shape 5，严格按 OpenAI 规范，Ollama 已知差异 9 例 desc 已标注）。
- 文档：`docs/functional-testing.md`（架构深读）、README 功能测试章节（用法）。
- 测试：test_functional_cases.py（DSL 纯函数+fake server）、
  test_functional_scenarios.py（收集计数）。

### 自研引擎与平台其余部分（改动前先读 `docs/architecture.md`）

- core/engine.py（并发/RPM/熔断）、metrics/（含 performance.py 延迟指标族）、
  connectors/（Stopwatch 计时）、datasets/（local/builtin/HF，输入键约定
  input/question/instruction/prompt/query/user）、storage/（SQLite 默认）、
  web/ + cli/（前端阶段最后做）。

## 3. 验证命令速查（按"改动域只跑相关测试"约定）

```bash
# 功能测试域改动
pytest tests/unit/test_functional_cases.py tests/unit/test_functional_scenarios.py \
       tests/unit/test_pytest_adapter.py tests/unit/test_tools.py -q -W ignore
# 用例格式快速验证（零环境秒级）
pytest aite/functional/scenarios/<场景> --collect-only -q -p aite.functional.harness -p no:cacheprovider

# 压测域改动
pytest tests/unit/test_perf_adapter.py tests/unit/test_locust_adapter.py \
       tests/unit/test_tools.py -q -W ignore

# 工具层契约/共享链路改动（影响多域时跑全量）
pytest tests/ -q -W ignore
ruff check aite tests          # 每次提交前

# 真实端到端（本地 Ollama 需在线）
aite run -c examples/functional_pytest.yaml
aite run -c examples/perf_evalscope.yaml
aite tool benchmarks pytest            # 场景清单
```

## 4. 当前状态（2026-09-09）

- 模块 A 数据集治理：暂缓（需求已存档 requirements-datasetgov.md）
- 模块 B 指标标准性：收缩，仅剩 tokenize 中文分词（低优先）
- 模块 C evalscope 适配器：C0-C4b 完成（REST 四域任务 CRUD/运行打通）；
  **未完：C3 平台数据集灌入、C4c 文档收尾**
- 模块 D 功能测试：D1-D9 全部完成（两域 8 场景 52 例）
- 模块 E 性能压测：E1-E7 全部完成（数据集负载 + 四模式 + 时长统一双键
  duration/level_duration + locust 适配器）
- 模块 F 性能大屏：F2 数据层 + F3 合成负载完成；**F1 前端面板后置**
  （用户策略：前端最后统一对接）
- 模块 G 执行机纳管：**一期完成**（拉取式 agent + HTTP 回传 + 压测域白名单；
  agent 通道 REST 见 README；剩 G2 二期：标签调度/locust 原生分布式/重试）
- 模块 H 归档 / 模块 I 可靠性 chaos：立项未实施
- 运行环境：conda env `evalscope`（Python 3.10，aite+evalscope 同装）；
  本地 Ollama（qwen2.5:3b / deepseek-r1:8b）；locust 未装（单测离线，
  真实压测需 `pip install 'aite[locust]'`）

## 5. Backlog（按模块分组，新会话按条认领）

**tools/perf**：压测中途熔断（上游不支持，需 watchdog 或提 feature）；基线回归
判定（与历史 run 比退化%，perf 与功能通用）；perf 原始参数透传扩展（open-loop/
rate/sla-number-multiplier 细项）；locust worker 分布式形态（现单机多档）。

**前端与大屏（模块 F 剩余）**：任务创建表单（F1）+ 时序曲线大屏 UI——
REST/时序数据链路已全部就绪（/tools/engines、/tools/benchmarks、POST /runs
四域路由、/runs/{id}/timeseries），纯前端工作量；现有免构建 SPA（Vue3+ECharts
CDN，aite/web/static）加面板三处：index.html nav+section、app.js go()/mounted()
白名单、复用 .card/.tbl/.chart 样式类。

**执行机纳管（模块 G 二期）**：标签调度与负载选择、locust 原生 master/worker
分布式通道、任务重排队/重试、容量与并发上限（一期见 README 执行机纳管章节）。

**性能数据归档（模块 H）**：run 冷热分层、原始产出压缩归档与检索恢复。

**可靠性 chaos（模块 I）**：先调研 chaosblade/chaos-mesh/Litmus 可集成性，
不可行自研五能力（对象感知/注入/检测/恢复/业务观测），与 reliability 域打通。

**架构整改（2026-09-10 全部完成）**：service 层（run_service + web_service——
新增编排一律进 service，API 路由保持「校验 → service → 返回」）、tools/
perf_common.py + tools/subproc.py（压测适配器共享解析与子进程执行）、
aite/config.py（默认值收口，新默认值一律进 config）、契约补齐（collect 进
ToolAdapter、delete_suite 进 Repository、unregister_tool）、Metric 契约新增
needs_llm 类属性（LLM 型指标混入 LLMMetricMixin 即自动标记，core 无指标名单）、
scripts/audit_func_len.py（80 行函数审计）。写新代码前读 CLAUDE.md 架构原则节。

**tools/evalscope（模块 C 收尾）**：C3 平台数据集 → evalscope 自定义格式转换；
C4b `/api/v1/tools/...` REST 端点（与前端阶段一并）；C4c 文档复核。

**functional**：多工具连续调用断言（现只看首个 tool_call）；嵌套场景目录；
tools.cases 支持手写场景；能力域扩场景（multi_turn/math/chinese_language/
instruction_following 已有设计评审记录，未实施）。

**平台**：Backlog 原打磨项（judge pairwise 补完、DELETE /suites、变体展开上限、
指标按数据集覆盖等，见 roadmap「Backlog」节）；前端体验（最后做）。

## 6. 踩坑索引（详情都在 roadmap 对应条目，这里只给定位）

| 坑 | 出处 |
|---|---|
| pytest conftest confcutdir 陷阱 → 插件模块方案 | roadmap 模块 D 关键决策 |
| norecursedirs 对显式目录参数同样生效 → argv 清空兜底 | 同上 |
| Windows 子进程 env 必须合并 SystemRoot | 同上 |
| pytest 断言重写污染 str(exc) / pytest.Failed 非公开 / 异常链断链 | roadmap D8 |
| 工具层阈值方向（越低越好 <=） | roadmap 模块 E |
| evalscope 无 __main__；--number 与 --parallel 等长 | 同上 |
| probe 产出布局 sla_tuning/ 与普通 parallel_ 不同 | roadmap E5 |
| evalscope --duration = 单次 run 墙钟预算（软退出），非整场总时长；probe 每轮 number 被强制 = 并发×2（sla_number_multiplier 可调） | roadmap E6 |
| 看门狗截断的产出文件可能写盘损坏，collect 须容错跳过 | roadmap E6 |
| evalscope --duration 是单次 run 预算、probe 每轮 number 被强制 = 并发×2 | roadmap E6 |
| SuiteConfig extra=forbid——工具域 suite 必须经 engine 分支旁路 load_suite | roadmap C4b |
| locust Aggregated 行被 POST+TTFT 双事件污染，统计取 POST 行 | roadmap F2 |
| async engine 上 sa.inspect(sync_engine) 会 MissingGreenlet，须 conn.run_sync | roadmap 进度 2026-09-09 |
| SSE 发布端 channel=runs:{task_id}，run_id 须经 bg_task 反查再订阅 | roadmap 进度 2026-09-10 |
| 架构原则基线在 CLAUDE.md「架构设计原则」节，评审/写码照它执行 | 同上 |
| run_task 返回值不可依赖，一律走文件收集 | roadmap C2 |

## 7. 新会话开工模板

```
读 docs/handover.md 第<N>节 + docs/roadmap.md 的<模块>条目。
任务：<一句话任务>。
验证：<该域验证命令，见第 3 节>。
```

例：认领 locust 适配器 → 读本文 §2 tools + §5 perf 组 + roadmap 模块 E 关键
决策（引擎选型对比表），新 adapter 文件 + 注册 + 单测（fake server 模式参照
test_perf_adapter.py 的 _FakePerfHandler）。

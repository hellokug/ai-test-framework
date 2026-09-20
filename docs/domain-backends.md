# 四域独立后端 + 公共服务层（任务管理契约设计稿）

> 2026-09-19 立项。状态：**已确认**（四决策见 §9，决策已落 roadmap）。
> 背景：功能/精度/性能/可靠性四域各自独立建后端，每域具备「任务增删改查 / 运行·停止·重试 /
> 结果展示」三项能力；数据集管理、用例管理等做成公共服务，由四域调用。本稿即四域任务
> API 的契约基线，域内实现可自由演进，契约级内容（路径/动词/状态机/表结构）变更须回本稿记录。

## 1. 分层

```
公共服务层（四域都调，禁止域间互调）
  ├─ 数据集管理     aite/datasets（已有）
  ├─ 用例管理       suites/cases 演化为公共用例库（另股）
  ├─ 任务框架 TaskKit   aite/core/taskkit.py（本稿核心：一份实现，四域实例化）
  ├─ 结果公共存储    test_run / test_case_result / metric_result（已有）
  └─ connectors / metrics / tools 适配器 / storage（已有，均属公共件）
四个域后端（各自独立，可并行建）
  functional / accuracy / perf / reliability
  每域 = 任务表 + service（编排）+ router（/api/v1/{域}/tasks…）+ CLI 子命令 + 结果展示端点
```

任务框架**代码一份、数据四份**：每域独立任务表（而非一张通用表加 domain 列），
避免 models.py 成为四域冲突面；框架代码只写一份，不违反 DRY 与原则 7
（四个具体消费者已存在，达到抽象门槛）。

## 2. 统一任务 API 契约（四域同形）

`{D}` ∈ `functional` | `accuracy` | `perf` | `reliability`，前缀 `/api/v1`：

| 方法与路径 | 语义 | 约束 |
|---|---|---|
| `POST /{D}/tasks` | 创建任务（`status=pending`，不运行） | body：`{name?, spec}`，spec 为域自定义（§6） |
| `GET /{D}/tasks` | 列表 | query：`status`、`retry_of`、`limit`（默认 50） |
| `GET /{D}/tasks/{id}` | 详情 | 404 不存在 |
| `PATCH /{D}/tasks/{id}` | 改 name/spec | 仅 `pending`；其余状态 409 |
| `DELETE /{D}/tasks/{id}` | 删除 | 仅终态；非终态 409 |
| `POST /{D}/tasks/{id}/run` | 运行 | 仅 `pending`；202 返回任务快照，异步执行 |
| `POST /{D}/tasks/{id}/stop` | 停止 | 仅 `running`（幂等：`stopping` 重复调用返回当前态） |
| `POST /{D}/tasks/{id}/retry` | 重试 | 仅终态；**创建新任务**（复制 spec，`retry_of=原id`，`retry_count+1`），201 返回新任务 |
| `GET /{D}/tasks/{id}/result` | 结果（域形态，§6） | 非终态 409 |
| `GET /{D}/tasks/{id}/stream` | SSE 进度/终态 | 频道 `{D}:tasks:{id}`（§5） |

重试不改原任务记录（审计可追，符合原则 8 状态机闭合）。

## 3. 状态机（四域统一）

```
pending ──run──▶ running ─────────▶ succeeded
                  │    └──────────▶ failed
                stop│
                  ▼
              stopping ──────────▶ cancelled
running|stopping ──startup 扫描──▶ stale（进程死亡残留）
终态 ──retry──▶ 新 pending 任务
```

- 终态集：`succeeded / failed / cancelled / stale`。
- 唯一出口归属（原则 8）：`run`、`stop`、executor 完成/异常、startup `mark_stale` 各一条边，无旁路。
- 停止为协作式：TaskKit 置 `cancel_event` → 调 executor.cancel()；子进程型工具须
  kill 进程组，产物照常落盘。stopping 超时兜底（默认 60s）强转 `cancelled` 并在
  error 字段记 "stop timed out; cancelled forcibly"——不留半开任务。
- 进程内句柄（asyncio.Task）存 TaskKit 实例的 `dict[task_id, Task]`，仅是运行时
  句柄；权威状态在任务表，重启后靠 startup 扫描收敛（原则 8）。

## 4. TaskKit 接口（aite/core/taskkit.py）

```python
TASK_TERMINAL = {"succeeded", "failed", "cancelled", "stale"}

class TaskRunContext:
    """传给 executor 的运行上下文。"""
    task_id: str
    spec: dict
    repo: Any                     # 执行期落库/复用公共存储（Web 长连接 / CLI repo_session）
    cancel_event: asyncio.Event
    def emit(self, event: dict) -> None: ...      # → bus.publish(type=progress, **event)
    def emit_raw(self, event: dict) -> None: ...  # 引擎自带 type 的帧原样透传

class TaskExecutor(ABC):
    """域执行器：四域各实现一份，TaskKit 驱动。"""
    @abstractmethod
    async def execute(self, ctx: TaskRunContext) -> dict:
        """执行任务，返回 result payload（§6 域形态）。长任务经 ctx.emit 上报进度。"""
    async def cancel(self, ctx: TaskRunContext) -> None:
        """协作式停止；缺省实现仅依赖 ctx.cancel_event，子进程型须在此 kill 进程组。"""

class DomainTaskKit:
    """公共任务框架：CRUD + run/stop/retry + SSE 频道 + stale 恢复，四域实例化。"""
    def __init__(self, domain: str, executor: TaskExecutor, bus: EventBus): ...

    async def create(self, repo, spec: dict, name: str = "") -> dict
    async def list(self, repo, *, status: str | None = None,
                   retry_of: str | None = None, limit: int = 50) -> list[dict]
    async def get(self, repo, task_id: str) -> dict | None
    async def update(self, repo, task_id: str, *, name: str | None = None,
                     spec: dict | None = None) -> dict          # 仅 pending
    async def delete(self, repo, task_id: str) -> None          # 仅终态
    async def run(self, repo, task_id: str) -> dict             # pending→running + 派发
    async def stop(self, repo, task_id: str) -> dict            # running→stopping→cancelled
    async def retry(self, repo, task_id: str) -> dict           # 终态→新任务
    async def mark_stale(self, repo) -> int                     # startup 调用
    def channel(self, task_id: str) -> str                      # f"{domain}:tasks:{task_id}"
```

- `repo` 由调用方传入（Web 侧 `state.repo` 长连接，CLI 侧 `repo_session`），TaskKit
  不持有连接；DB 访问全部走 repository（原则 6）。
- `run()` 内部 `asyncio.create_task` 驱动 executor，结束时写终态、emit 终态帧、
  `bus.close(channel)`、清句柄——复刻现有 `_execute_run` 的出口清单。

## 5. SSE 事件契约（频道 `{D}:tasks:{id}`）

| 帧 | 时机 |
|---|---|
| `{"type": "started", "task_id"}` | run 生效 |
| `{"type": "progress", ...}` | executor 经 ctx.emit，域自定义字段 |
| `{"type": "succeeded", "result"}` / `{"type": "failed", "error"}` / `{"type": "cancelled"}` | 终态帧 |
| `{"type": "end"}` | bus.close（既有语义，订阅收尾） |

复用 `web/events.py` EventBus（历史回放 500 条、域无关），不动现有 `runs:*` 频道。

## 6. 表结构与域差异

`storage/models.py` 新增（additive-only，原则 14）：

```python
class TaskLifecycleMixin:            # 公共列，mixin 不建表
    name: str(256) = ""
    status: str(32) = "pending"      # 索引
    spec: JSON = {}                  # 域自定义任务定义
    result: JSON | None
    error: Text | None
    retry_of: GUID | None            # 索引
    retry_count: int = 0
    created_at / started_at / finished_at: UTC（原则 11）

class FunctionalTaskRow(Base, TaskLifecycleMixin):  __tablename__ = "functional_task"
class AccuracyTaskRow(Base, TaskLifecycleMixin):    __tablename__ = "accuracy_task"
class PerfTaskRow(Base, TaskLifecycleMixin):        __tablename__ = "perf_task"
class ReliabilityTaskRow(Base, TaskLifecycleMixin): __tablename__ = "reliability_task"
```

repository 增 6 个泛型方法（表类作参数，一份实现）：
`create_domain_task / update_domain_task / list_domain_tasks / get_domain_task /
delete_domain_task / mark_stale_domain_tasks`。

**结果存储策略**：功能、精度复用公共结果存储（test_run / test_case_result /
metric_result），任务表 `result` 只放引用与汇总；性能复用 metric_result 时序 +
报告文件引用；可靠性观测结果量小，内联 `result` JSON。四域不另建结果明细表
（只增不改，展示端点域化即可）。

| 域 | spec（任务定义） | result（结果形态，即展示差异） |
|---|---|---|
| functional | suite raw（engine=pytest）/ 用例集引用 | suite 统一概要¹ |
| accuracy | suite raw（engine=evalscope）/ benchmark 清单 | suite 统一概要¹ |
| perf | suite raw（engine=evalscope-perf/locust：jitter/arrival/时长/负载） | suite 统一概要¹ |
| reliability | observe body（target + faults + interval/duration/prompt/post_window） | `{events, probes, rto, summary, probe}`（内联 JSON） |

¹ 套件型三域一期统一存执行概要 `{run_id, run_name, status, total_cases,
error_cases, reports}`（executor 见 `service/suite_task.py`，明细在公共 run
存储）；按域差异化的结果视图/聚合在 C1-C4 展示端点层实现。

## 7. CLI 双形态（原则：接口新增一律 CLI + Python API）

`aite/cli/task_cli.py` 提供 `build_task_cli(kit) -> typer.Typer` 工厂，生成同形
七命令：`create / list / get / rm / run / stop / retry`（result 并入 get 详情
展示）。`cli/main.py` 经 `_mount_domain_tasks` 挂载，命令为三层：
**`aite {域} tasks <命令>`**（域组下挂 tasks，给域内其他命令留空间）：

```bash
aite perf tasks create --spec perf-suite.yaml --name 720s-long   # spec=suite YAML/JSON
aite perf tasks run <id>        # 运行并等待终态（exit 0=succeeded，CI 友好）
aite perf tasks stop <id>       # 协作式停止；60s 兜底强转 cancelled
aite perf tasks retry <id>      # 终态→新任务（retry_of 链）
aite perf tasks list / get / rm <id>
```

CLI `run` 为同步等待形态（kit.wait）；`--spec` 接受内联 JSON/YAML 或文件路径。
已挂载四域：functional / accuracy / perf / reliability（`service/{域}_tasks.py`
装配 executor，CLI 侧 EventBus 本地新建）。

## 8. 迁移与并行股道

- **C0 契约 PR（已完成，2026-09-19）**：`core/taskkit.py`（状态机 + 单测，含
  stale 收敛/stop 兜底/retry 链）+ models.py 四表 + repositories.py 泛型方法 +
  `cli/task_cli.py` 工厂。
- **C0.5 CLI 切片（已完成，2026-09-20）**：`service/suite_task.py` 套件型共享
  executor + `service/{域}_tasks.py` 四域装配 + `cli/main.py` 挂载
  （`aite {域} tasks …` 可用）+ CliRunner 端到端冒烟。`TaskRunContext` 增
  repo/emit_raw 透传；chaos.py 路由的 observe body→cfg 映射下沉
  `service/reliability_tasks.build_observe_cfg`（REST 与任务执行器共用）。
- **C1-C4 四股并行（待做）**：每股 = `web/routers/{域}_tasks.py`（任务 REST +
  result 展示端点 + `/stream` SSE）+ 域差异化结果视图 + stop 对子进程型引擎的
  进程组终止补强 + web/app.py 启动挂载各域 mark_stale + 前端页。
- **C5 收缩**：前端切到域 API 后，旧 `/runs` 提交与 `/tasks` 统一端点废弃
  （只读端点届时评估保留）。
- 执行机（模块 G agent）纳管域任务：本期不做，spec 预留 `node` 字段。
- 已知限制：工具域（pytest/evalscope-perf/locust 子进程）的 `stop` 当前只终止
  执行协程与状态机，子进程进程组强杀在 C1-C4 各域 executor.cancel 补强。

## 9. 决策记录（设计稿定稿前待确认）

1. `stopping` 中间态：**保留**（子进程 kill 有时延，超时兜底强转 cancelled）。
2. 重试语义：**新任务 + retry_of 链**，不改原记录。
3. 功能/精度结果：**复用公共 run 存储**，不建域结果明细表。
4. CLI run：**同步等待**形态，--detach 后续按需。
5. （2026-09-20 实调后增补）任务状态 = **执行语义**（succeeded=执行完成未崩，
   failed=执行器抛异常）；业务判定在 `result.status`（passed/failed/error），
   CLI `run` 退出码 0 ⇔ 任务 succeeded 且 result.status=passed（CI 友好）。
6. （2026-09-20 实调后增补）**跨进程停止桥**：CLI run 是独立进程，stop 请求
   落 DB 的 stopping；执行进程以 `stop_poll_interval`（2s）轮询发现后触发
   协作 cancel 并落 cancelled 终态（expect 守卫保证 stop 进程与执行进程
   双写竞态安全，实测孤儿进程场景收敛正确）。

## 10. 四域输入输出实录（2026-09-20 本机实调，Ollama qwen2.5:3b）

### 输入（spec，任务创建时写入）

套件型三域（functional / accuracy / perf）**同构**，仅 engine 与域段不同：

```yaml
engine: pytest|evalscope|locust|evalscope-perf   # 路由键
run_name: smoke-ft
target: {type: openai_compatible, base_url: ..., model: ..., stream: bool}
tools:  / perf:                                   # 域段：functional/accuracy 用 tools，
  datasets: [...]                                 #   perf 用 perf（dataset/prompt、
  ...                                             #   mode/baseline/ramp/soak、duration、jitter…）
```

reliability **独立形态**（observe body，与 POST /chaos/observe 同构）：

```json
{"target": {"base_url": "...", "model": "..."}, "faults": [],
 "interval": 1, "duration": 4, "prompt": "ping", "post_window": null}
```

### 输出（result，任务终态后可读）

套件型三域统一概要（明细在公共 run 存储，reports 为相对路径引用）：

```json
{"run_id": "...", "run_name": "smoke-locust", "status": "passed",
 "total_cases": 6, "error_cases": 0,
 "reports": {"timeseries.json": "reports/tool/locust/...", "locust_u2_report.html": "...", "locust.log": "..."}}
```

functional 实测 reports = junit xml + 场景 log；accuracy 实测错误路径
（evalscope 未装）→ `status: "error"` + CLI exit 1 + 显式 ModuleNotFoundError。

reliability 内联（不落 run 存储）：

```json
{"events": [], "probes": [{"t": ..., "ok": 1, "ttft_ms": 313.5, "latency_ms": 759.3,
  "content_len": 79, "error": null}],
 "rto": {"affected": false, "fail_count": 0, "first_fail": null, "rto_seconds": null},
 "summary": {"total": 3, "pass": 3, "fail": 0, "pass_rate": 1.0},
 "probe": {"url": ".../chat/completions", "model": "qwen2.5:3b", "interval": 1.0, "duration": 4.0}}
```

### 对接公共能力的 I/O 清单（由此实录推导，见 §11 讨论）

| 域 | 从公共拿（输入侧） | 给公共（输出侧） |
|---|---|---|
| functional | 数据集/用例集（cases.jsonl 语义）、target/模型配置 | suite 概要 + case 判定明细（test_run 系表）+ junit/log 文件 |
| accuracy | benchmark 清单（外部工具 registry）、target | suite 概要 + 指标明细（metric_result）+ 评分报告 |
| perf | 场景数据集（perf_dataset 产物）、target | suite 概要 + 时序/分位（metric_result + timeseries.json）+ html 报告 |
| reliability | target（探针端点）、weapons 目录 | **内联观测结果——不进 run 存储** |

## 11. 数据集分类与四域任务 I/O 梳理（2026-09-20 讨论稿，待共同评审）

### 11.1 三类数据集的本质差异

| | 功能型 | 精度型 | 性能型 |
|---|---|---|---|
| **本体** | 行为规约：请求 + 可观测断言 | 评测题库：问题 + 标准答案 + 分组元数据 | 流量特征：载荷分布（长度/权重/前缀共享） |
| **一行 schema** | `id/desc/messages/params/tools/expect(16键断言)` | `id/question/choices/answer/subject`（或 fields 映射的 input/expected） | 载荷 prompt + 长度桶/前缀组标记 |
| **内容语义** | 刺激 + 期望行为（答案不存，断言代替） | 答案必须正确（输出内容对照校验） | **内容语义无关**，分布特征是本体 |
| **来源/生命周期** | 人工编写（加用例只加行） | 开源导入（HF）+ 私有领域构建 + 离线化 | **生成器产物**（语料池+参数+seed，逐字节可复现） |
| **校验时机/器** | 导入时 harness.Case 校验 | 导入时 schema+字段映射校验 | 生成时参数校验 + stats.json |
| **现状** | 只在包内 scenarios，**未入数据集管理** | `datasets/` 体系完整（builtin/hf_hub/local/userstore） | `perf_dataset.py` 生成器完整，产物散在 perfdata 目录 |

结论：**三类区分开（schema/校验器/生命周期不同），共用一个管理外壳**（目录/注册表/引用/版本）。
可靠性**不设自有数据集**：引用性能型做背景流量，叠加故障场景（weapons/faults）与探针。

### 11.2 四种任务的输入输出差异

| 域 | 输入（spec 组成） | 执行语义 | 输出/判定语义 |
|---|---|---|---|
| functional | 功能型数据集引用（或内置场景名） | 逐 case 请求 → 断言 | **二元判定**：逐 case pass/fail → pass_rate vs 阈值 |
| accuracy | 精度型数据集引用 + 指标阈值 + **基线引用** | 请求 → **对照标准答案评分** | **分数 + 劣化判定**：绝对阈值 + vs 基线 delta（新能力） |
| perf | 性能型数据集引用 + 负载模型（mode/duration/并发） | 压测流量 | **性能指标 vs SLA**：方向性阈值（TTFT/TPOT/QPS/error_rate） |
| reliability | 性能型数据集引用（背景流量）+ 故障场景 + 探针配置 | **复合**：背景流量 → 注入 → 观测 → 恢复 | **RTO 判定**：注入窗 fail → 恢复后首个 pass 的时间；探针通过率 |

由表推导的两个新能力点：
1. **精度基线对比**：任务 spec 增 `baseline_ref`（基线 run/任务），result 增 vs 基线
   delta 与劣化判定——当前只有绝对阈值。
2. **可靠性复合任务**：当前 observe 只有探针流量；需组合「背景流量（性能语义） +
   注入 + 观测」。`run_with_chaos` 包裹业务执行已有组合先例（perf suite chaos_raw）。

### 11.3 公共数据集管理需要的形态（推导）

- **统一目录（catalog）**：一行一个数据集条目 `{id, type: functional|accuracy|perf,
  name, version/seed, source, schema_version, stats/元数据, path}`——type 是一等属性；
  payload 格式各异本就在文件层，目录统一即可（引用/检索/版本收敛一处）。
- **分类型校验器**：导入/生成时各自校验（功能=Case 校验器、精度=schema+字段映射
  预览、性能=参数+stats）。
- **分类型摄取入口**：functional 导入 cases.jsonl；accuracy add-hf/local（已有）；
  perf generate（已有）——产物与注册都收进目录。
- **引用方式升级**：任务 spec 用 `dataset id` 引用（替代场景名/裸路径散引）；
  reliability spec 引用 perf 型 id。

### 11.4 待共同拍板的决策点

1. 功能型数据集：独立 type + cases.jsonl 原生格式入目录？（建议：是）
2. 精度劣化判定的基线来源：spec 显式 `baseline_ref` vs 自动取最近 passed？（建议：
   显式引用，缺省回退最近 passed 同名任务）
3. 可靠性 spec 的背景流量：内嵌 perf 参数段 vs 直接引用 perf 数据集 id？（建议：
   引用数据集 id + 内嵌负载参数，两者都要——流量形态与语料分布是两个自变量）
4. 目录存储：统一 catalog（type 列）+ 分类型文件区？（建议：是，一张目录表）
5. 兼容：suite `tools.datasets`（场景名语义）保留为内置场景快捷方式，受管数据集
   走新引用？（建议：是，内置场景名是合法的"隐式功能数据集"）

> **2026-09-20 用户确认：五点均按建议执行。**落地契约见 §12。

## 12. 数据集管理契约（D 系列，2026-09-20 定稿待开工）

### 12.1 Catalog 表（models.py 增量，additive-only）

```python
class DatasetCatalogRow(Base):
    __tablename__ = "dataset_catalog"
    id: GUID pk
    type: str(16)              # functional | accuracy | perf（索引；可靠性引用 perf 型）
    name: str(256)             # 引用名，同 type 内唯一（索引）
    version: str(64) = ""      # 版本凭证：perf 型 = 生成 seed（逐字节可复现），其余=导入版本
    source: str(32)            # builtin | hf | local | generated
    schema_version: str(16) = "1"
    stats: JSON                # 类型相关元数据：case 数/断言键分布/科目分布/长度分桶
    path: str                  # 相对数据集根（./data/datasets/<type>/…）的路径
    notes: Text = ""
    created_at / updated_at
```

文件区（决策 4：统一 catalog 表 + 分类型文件区）：`./data/datasets/<type>/<name>/`。
精度型外部注册表 `./data/aite_datasets.json` 保留读取兼容，新登记一律走 catalog。

### 12.2 校验器与摄取（代码一份，按类型分派）

- **functional**：逐行过 `harness.Case` 真实校验器（断言词表封闭，坏行显式报错）；
  stats 记 case 数 + 断言键分布。粒度：一个功能数据集 = 一个场景的 cases.jsonl。
- **accuracy**：schema 预检（input/expected 可映射）+ 抽样预览；沿用 add-hf/local 通道。
- **perf**：`generate_perf_dataset` 参数校验（已有）+ 产物登记（stats.json → stats 列，
  seed → version 列）。

### 12.3 API/CLI（双形态；REST 随 D3）

```
aite dataset list [--type functional|accuracy|perf]   # catalog 权威，builtin 合并显示
aite dataset import-functional <cases.jsonl> --name n # 校验→落盘→登记
aite dataset show <id|name> / rm <id>
aite dataset add-hf …（已有 → 登记为 accuracy 型）
perf 生成入口 → 自动登记（generated 型）
```

REST（D3）：`GET /datasets?type=`、`GET /datasets/{id}`、`DELETE /datasets/{id}`、
按类型摄取端点。

### 12.4 任务 spec 引用（DatasetConfig 增 `ref` 字段）

```yaml
datasets:
  - ref: gsm8k-v1          # catalog 引用：解析出 type/path/fields/sample
  - type: builtin
    id: api_limits          # 内置场景名 = 隐式功能数据集（决策 5，保留快捷方式）
```

- loader 解析 `ref` → 查 catalog → 按 type 装配（functional→场景目录进 pytest 适配器、
  accuracy→TestCase 流、perf→perf 载荷）；`ref` 与 `type+path` 互斥。
- 可靠性 spec（决策 3：引用 + 内嵌参数并存；`engine` 缺省 locust，注入窗 =
  `background.perf.duration`，探针窗口自动对齐，时长上限 3600s）：

```json
{"target": {...}, "faults": [...], "interval": 3, "prompt": "ping",
 "background": {"engine": "locust", "dataset_ref": "<perf 型 id>", "run_name": "bg-1",
                "perf": {"mode": "baseline", "parallel": 4, "duration": 120}}}
```

复合任务 result = 纯观测形态（events/probes/rto/summary/probe，probe 带
`background: true` 与对齐后的 duration）+ `background` 键（套件概要
run_id/status/reports——背景流量落公共 run 存储）。编排实现：
`service/reliability_tasks.run_composite`（run_with_chaos 业务槽 = 背景流量与
探针并发跑满注入窗，恢复后补测窗口捕捉首个 pass → RTO）。

### 12.5 精度基线劣化判定（决策 2）

- accuracy 任务 spec 可选 `baseline_ref`（基线 run_id 或任务 id；缺省回退同名任务
  最近一次 succeeded 且 result.status=passed）。
- 执行完成取基线 run 同名指标汇总 → result 增：
  `{"baseline": {"ref": "...", "deltas": {"<metric>": {"base": x, "now": y, "delta_pct": z}}, "degraded": bool}}`。
- 劣化方向复用 LOWER_IS_BETTER 单一来源（原则 8）；容差 `baseline_tolerance` 可配，
  缺省 0（任何方向性劣化即 degraded）。

### 12.6 实施股道

- **D0（串行契约 PR，2026-09-20 完成）**：DatasetCatalogRow 表（type/name 唯一约束）
  + repository CRUD（接口同步演进）+ `datasets/catalog.py`（登记/引用解析：id →
  type:name → 唯一 name，跨类型重名显式报错）+ `functional/ingest.py`（逐行
  harness.Case 校验 → `./data/datasets/functional/<名>/cases.jsonl` → 登记）+
  dataset CLI（list[--type] 合并 builtin / import-functional / show / rm，add-hf
  自动登记 accuracy 型）+ perfdata REST 生成自动登记 perf 型 + DatasetConfig.ref
  （engine._load_cases 解析为 local 文件数据集）。实机验证：受管功能数据集经
  `tests_dir` 零改动被 pytest 适配器消费（2 case 真实模型运行，逐 case 判定）。
- **D1（精度流，待做）**：baseline_ref 判定（accuracy 域执行器后处理）。
- **D2（可靠性流，框架完成 2026-09-20；真实注入实弹打通）**：`run_composite` 编排（run_with_chaos
  业务槽 = 背景流量与探针并发，注入窗 = 背景时长，恢复后补测）+
  `build_background_suite`（dataset_ref→perf 型解析/target 继承/时长上限 3600s）+
  observer.`summarize_probes` 转公开（原则 6）+ suite_task.`suite_summary` 抽取
  （原则 3）。实机 E2E：locust 20s 背景 + 探针同窗，背景 run passed 落公共存储。
  **真实注入实弹**（process:suspend 对真实服务进程，10s 注入窗/timeout 3s）：注入→验证
  8ms→窗内 2 探针 ReadTimeout fail→恢复→连续 pass（延迟 3000ms→10ms）→
  **RTO=3.264s 实测**（affected/fail_count/first_fail/last_fail/recovered_at 全齐）。
  实弹揭出两修：validate_specs 幂等（FaultSpec 透传，双重校验曾炸 not subscriptable）；
  process 注入器改用 Process.suspend()/resume()（psutil 无模块级 suspend，此前单测
  全 mock 未暴露）；injector 实弹单测 +3（真子进程 suspend/kill/recover 往返）；
  observe body 增可选 `timeout`（探针超时秒数，缺省 30）。
- **D3（平台流，待做）**：REST 端点 + 前端数据集页。

## 13. 远端故障注入与环境纳管（R 系列，2026-09-20 讨论稿 v2，待共同评审）

> 两个平面的区分（用户澄清）：**执行机**（模块 G，agent_node）是固定跑测试
> 负载的机器——工作负载面；**目标环境**是被测系统所在之处，往往与测试框架
> 不同机——故障注入的对象面。环境纳管作为目标平面的建模单元，一并解决
> 目标对象建模与参数选择（URL / 命名空间 / api-key 从环境关联信息选取）。
>
> 边界现状：process 注入器 = psutil 本机执行；toxiproxy = 经 HTTP API 已天然
> 远程；discovery = 本机。v1 的"故障走执行机通道"修正为"故障走**环境**的
> 注入通道"——通道载体可以是部署在被测环境里的 aite agent（拉取通道复用
> 模块 G），但建模归属环境而非执行机。

### 13.1 环境纳管（EnvironmentRow，新增契约表）

```python
class EnvironmentRow(Base):
    __tablename__ = "environment"
    id: GUID pk
    name: str(128) unique          # 环境名（prod-1 / staging…）
    kind: str(16)                  # host | k8s（kube 通道一期占位）
    chaos_channel: str(16) = "none"# none | agent | toxiproxy | kube——注入路由依据
    agent_name: str | None         # chaos_channel=agent 时绑定执行机名（agent_node.name）
    params: JSON                   # 环境关联参数：base_url / namespace / api_key_env /
                                   #   toxiproxy_api / 按需扩展；api_key 走 api_key_env
                                   #   环境变量引用惯例，不入库明文
    notes: Text
    created_at / updated_at
```

CLI/REST：`aite env add/list/show/rm` + `/api/v1/environments` CRUD。

### 13.2 目标对象建模与发现（按环境执行）

- FaultSpec.target 增 `environment` 维度（环境名）→ 决定发现与注入路由。
- 发现：host 环境（绑定了 agent）→ master 下发 `chaos.discover`，agent 本地跑
  现成 discover_targets() 回传目标树（挂 env 维度）；k8s 环境 → namespace 内
  pod 树（kube 通道占位）。`GET /chaos/targets?environment=x` 按环境返回；
  本机树保留。

### 13.3 注入路由（按 chaos_channel 分派）

| chaos_channel | 进程类故障 | 网络类故障 | 通道 |
|---|---|---|---|
| agent | ✅ chaos.op（ RemoteInjector 同接口阻塞等待确认） | ✅（toxiproxy 部署在环境内时） | 模块 G 拉取通道复用：agent 本地复用现有注入器，回传 handle.ref 与 agent 侧时间戳 |
| toxiproxy | ❌ | ✅ api 取 env.params.toxiproxy_api | HTTP API 直连 |
| kube | 占位 | 占位 | 出现需求再立项（原则 7） |
| none | 显式 error：环境未启用故障注入通道 | 同左 | — |

时序语义不变：注入确认先于探针启动、恢复确认先于补测；注入延迟=心跳间隔
（等待中的 chaos.op 可短轮询 1s 压缩）；RTO 全取探针侧 master 单一时钟域，
故障注入点由 agent 事件戳单独记录；handle.ref 存 master 供跨重启恢复闭合。

### 13.4 参数选择：环境关联信息反哺任务（target 段引用环境）

```yaml
target:
  environment: prod-1          # base_url / api_key_env(/namespace) 从环境 params 解析
  model: qwen2.5:3b            # 显式值覆盖环境值
```

- reliability 复合任务的探针与背景流量同源一个环境——保证观测对象与被压
  对象一致；故障注入经同一环境的通道，形成「环境=被测系统全部接入信息」的闭环。
- 解析规则：显式值 > 环境值 > 报错（base_url 缺失且无环境时维持现状报错）。

### 13.5 安全（高危能力显式化）

- 环境绑定 agent 须 `--chaos-enabled` 显式开启，未开启时 chaos.op 回 error；
- agent 侧注入白名单/黑名单（禁 agent 自身、系统进程名）——远端防误杀同本机纪律；
- 环境 params 的密钥只存 `api_key_env` 环境变量名（原则 5，不入库明文）。

### 13.6 备选路线（评估过，不作主线）

- SSH 直执行：免 agent 部署，但凭据管理/跨平台命令差异/验证弱——轻量备选。
- K8s 原生注入（k8s exec / NetworkChaos）：kube 通道的实现候选，需求出现再立项。

### 13.7 股道

- R1 契约：EnvironmentRow 表 + FaultSpec.target.environment + target.environment
  解析规则 + chaos.op / handle 序列化格式（roadmap 记录）。
- R2 环境纳管：表 + repo + `aite env` CLI + REST CRUD。
- R3 agent chaos 通道：chaos.discover / chaos.op 两类任务执行器（复用本地注入器，
  `--chaos-enabled` 门禁）。
- R4 注入路由：RemoteInjector 按 env.chaos_channel 分派 + /chaos/targets 按环境
  聚合 + target.environment 参数解析。
- R5 双机实弹：环境绑定被测侧 agent，suspend 远端进程 → 探针 fail → RTO。

## 14. 故障实验用例（chaos experiment case，E 系列，2026-09-20 讨论稿 v1）

> 一条可靠性用例 = 五段式：**对象选择 → 故障定义 → 观测（探针/背景流量）→
> 断言（预期 RTO 等）→ 报告**。逐点回应用户清单，标注已有/需新增。

### 14.0 完整用例写法（对齐版，2026-09-20）

```yaml
# ═══ 0. 元信息 ═══
name: vllm-suspend-rto                # 实验名 = 可靠性任务名

# ═══ 1. 目标：环境 + 两级漏斗（Stage A 筛选 ≠ Stage B 注入确认）═══
target:
  environment: prod-1                 # [E] base_url/api_key_env 从环境解析，显式值覆盖
  kind: pod                           # [E] host 环境 = node（Stage A 退化为节点本身）
  select:                             # [E] Stage A：pod 筛选（发现/树，不做 exclude）
    match: {name: {contains: vllm}}
    mode: all                         # 命中多 pod：all|first|random(k)|percent(p%)
  process:                            # [E] Stage B：注入时刻活体确认
    match: {name: {contains: vllm}}
    exclude:                          # 排除非目标进程（router 等）——只属于这一级
      - {name: {contains: router}}
      - {system: true}
    select: {mode: first}             # pod 内注入几个：first|all|random(k)|percent(p%)

# ═══ 2. 故障 ═══
fault:
  injector: process                   # process | toxiproxy | custom（环境白名单登记）[custom=E]
  kind: suspend
  params: {}                          # 注入器参数（toxiproxy: latency_ms 等）
  fault_duration: 30                  # [E] 定时恢复；缺省=业务时长（现行为）。
                                      #     真 RTO 语义：恢复发生在背景流量持续中

# ═══ 3. 观测（键为现行已实现形态）═══
interval: 3                           # 探针间隔
timeout: 5                            # 单探针超时（挂起类故障需短超时）
prompt: ping
post_window: 15                       # 业务结束后补测窗口（捕捉恢复后首个 pass）
duration: 120                         # 仅无 background 时必配（观测窗）；
                                      #   有 background 时自动=背景时长
background:                           # 可选；有 = 复合任务
  engine: locust
  dataset_ref: vllm-traffic-v1        # perf 型数据集（catalog 引用）
  perf: {mode: baseline, parallel: 8, duration: 120, max_tokens: 256}

# ═══ 4. 断言（有 = 用例判定；无 = 观测实验）═══
expectations:                         # [E]
  rto_max_seconds: 30
  max_probe_failures: 2               # 注入窗内允许的失败探针数
  background_error_rate_max: 0.05     # 仅复合任务有效
```

**时间线语义**：t=0 注入并确认生效 → 探针/背景流量并发（故障窗内探针 fail、
背景报错率抬升）→ t=30 `fault_duration` 到点自动恢复（背景流量继续）→ 探针
捕捉恢复点 → RTO = last_fail → 恢复后首个 pass → 业务窗结束 → post_window
补测 → verdict 逐条断言。

**result 形态**（任务 result 内）：

```json
{"events": [...], "probes": [...], "rto": {"rto_seconds": 3.264, "...": "..."},
 "summary": {}, "probe": {"duration": 120, "background": true},
 "background": {"run_id": "...", "status": "passed", "reports": {}},
 "verdict": {"status": "passed",                     // [E]
             "checks": [{"name": "rto", "expected": "<=30",
                         "actual": 3.264, "passed": true}]}}
```

**字段状态**：interval/timeout/prompt/post_window/background/fault.injector·kind·
params 已实现；target 两级/fault_duration/expectations·verdict/custom 注入器/
environment 引用为 E 系列待实现。任务运行：`aite reliability tasks create
--spec <用例.yaml> && aite reliability tasks run <id>`。

### 14.1 对象选择：两级漏斗（筛选 ≠ 注入确认，v2 按用户澄清重写）

**Stage A —— 对象筛选（发现/树视图，粒度 = 承载对象）**：按关键字定位"承载故障
的容器类对象"。k8s 环境 = pod（按 pod 名称关键字匹配，命中的 pod 进树）；host
环境 = 节点本身（环境即节点）。**本阶段不做 exclude**——pod 内多个进程都带
vllm 关键字时，排除任何一个都可能让 pod 失配或隐藏事实；树里如实展示命中对象
的全部子进程供浏览。

**Stage B —— 注入确认（注入时刻，粒度 = 注入单元）**：对每个 Stage A 对象，
**在注入时刻对活体状态再确认一遍**（发现快照的 pid 到注入时可能已变，活体解析
是正确性要求）：对注入单元做 match + exclude + select。

```json
"target": {
  "environment": "prod-1",
  "kind": "pod",
  "select": {                                    // Stage A：pod 级筛选（发现/树）
    "match": {"name": {"contains": "vllm"}},
    "mode": "all"}                               // 命中多 pod：all | first | random(k) | percent(p%)
  "process": {                                   // Stage B：pod 内注入确认（注入时执行）
    "match":   {"name": {"contains": "vllm", "mode": "contains"}},
    "exclude": [{"name": {"contains": "router"}}, {"system": true}],
    "select":  {"mode": "first"}}                // 每个 pod 内：first | all | random(k) | percent(p%)
}
```

（host 环境：Stage A 退化为环境节点本身，Stage B 即节点内进程匹配。）

| 项 | 现状 | 需新增 |
|---|---|---|
| Stage A 筛选 | ✅ discovery filters 子串模糊 | pod 名称/label 匹配（kube 通道）；match.mode（exact/prefix/regex） |
| Stage B 注入时确认 | ❌（进程注入器发现态取首个匹配） | 注入时刻活体解析：match + **exclude 清单**（router 等非目标在此排除）+ 系统进程黑名单 |
| 多对象选择策略 | ❌ | 两级各带 select：Stage A 选 pod（all/first/random/percent），Stage B 选 pod 内注入单元（first/all/random/percent/count）；random 带 seed 可复现（同 perf_dataset 哲学） |

两级 resolve 后的选中清单（pod → pid 列表）写入实验 result——操作了哪些对象
审计可追。注入器从"单对象"升级为"对象集"（一 spec → N handle，事件时间线
逐条记录）。**exclude 只存在于 Stage B**：它是"注入哪些"的操作规则，不是
"发现哪些"的视图规则——这是本次修正的核心。

### 14.2 故障定义（持续时间是核心新增）

```json
"fault": {"injector": "process", "kind": "suspend",
          "fault_duration": 30}            // 缺省 = 业务时长（现行为）
```

| 项 | 现状 | 需新增 |
|---|---|---|
| 注入确认 | ✅ verify（注入失败/无效应区分，ChaosVerifyError 回滚） | 远端确认（§13 chaos.op 阻塞等待） |
| **持续时间** | ❌ 故障窗=业务全程（finally 才恢复） | **fault_duration 定时恢复**：注入后 N 秒自动 recover，**业务（背景流量）继续跑**——真恢复语义：RTO 在持续负载下测得，而非业务结束才见分晓。controller 增恢复定时器，恢复事件落在业务中途 |
| 恢复 | ✅ injector.recover + finally 兜底 + recover_failed 显式告警 | 不变（定时恢复与 finally 兜底并存，幂等） |

### 14.3 谁注入与自定义故障

- 谁注入：按 target.environment 的 chaos_channel 路由（§13.3——agent 通道
  chaos.op / toxiproxy API 直连 / kube 占位）。
- **自定义故障**：新增 `custom` 注入器（命令式契约）：

```json
{"injector": "custom", "kind": "<自定义名>",
 "inject_cmd": "...", "verify_cmd": "...", "recover_cmd": "...", "timeout": 10}
```

  命令在目标环境内执行（经 agent 通道），退出码 0=成功；verify_cmd 缺省
  信任 inject_cmd 退出码。安全：自定义注入器须在**环境 params 白名单**中
  显式登记（远端任意命令执行是最高危面，不接受 spec 内裸传未登记命令）。

### 14.4 断言（从"实验"变"用例"的关键）

```json
"expectations": {
  "rto_max_seconds": 30,
  "max_probe_failures": 2,
  "background_error_rate_max": 0.05
}
```

result 增 `verdict`：`{"status": "passed|failed", "checks": [{"name": "rto",
"expected": "<=30", "actual": 3.264, "passed": true}, ...]}`。无 expectations
= 观测实验（不判定）；有 = 用例（判定落 result.status，CLI/前端语义与全域
对齐：任务执行 succeeded、业务判定看 result.status）。

### 14.5 RTO 计算与结果呈现

- RTO 口径不变：last_fail → 恢复后首个 pass（精度 ±探针间隔，单一探针时钟域）；
  fault_duration 语义落地后 recovered 即定时恢复点附近，口径自然成立。
- **报告关键指标**（前端大屏按此优先级呈现，故障窗口在时间线上着色标注）：

| 指标 | 来源 | 重要性 |
|---|---|---|
| RTO（vs 预期） | compute_rto | ★★★ 判定核心 |
| 注入窗内可用性（pass_rate） | probes | ★★★ |
| 探针延迟劣化幅度（故障窗 TTFT/latency vs 恢复后基线） | probes | ★★ 劣化深度 |
| 故障时间线（injected/verified/recovered 时点） | events | ★★ 窗口标注与审计 |
| 背景流量错误率/QPS 恢复曲线 | background 套件 timeseries | ★★ 复合任务 |
| 选中对象清单（pid/pod + 选择策略回放） | resolve 阶段 | ★ 审计 |

### 14.6 股道

- E1 契约：两级 target（Stage A select / Stage B process：match+exclude+select）/
  fault_duration/expectations/custom 注入器 schema。
- E2 两级对象选择：Stage A 发现筛选（match.mode + pod 匹配）+ Stage B 注入时
  活体确认（match/exclude/select 在注入时刻解析，审计清单落 result）。
- E3 controller fault_duration 定时恢复 + 注入器对象集化（多 handle）。
- E4 custom 注入器（环境白名单门禁）。
- E5 verdict 断言 + 报告指标 + 前端故障窗标注。

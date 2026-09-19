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

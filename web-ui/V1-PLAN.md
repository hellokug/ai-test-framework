# aite Web UI · V1 页面规划（待评审稿）

> 依据：当前后端 API 实际能力（`aite/web/routers/`）+ roadmap 模块现状。
> 每个页面标注：**[P0]** V1 必做 / **[P1]** V1 争取 / **[V2]** 明确不做；
> 数据源标注后端就绪状态：✅ 已有 / ⚠️ 需后端补（缺口清单见文末）。
> 导航结构沿用 A2 模板（顶部一级模块 + 左侧二级菜单），主题沿用 A3 主题系统。

---

## 0. V1 范围总览

| 模块 | 页面 | 优先级 | API 就绪 |
|---|---|---|---|
| 精度 | 运行总览 | P0 | ✅ |
| 精度 | 运行详情 | P0 | ✅ |
| 精度 | 趋势对比 | P0 | ✅ |
| 精度 | 人工评测 | P1 | ✅ |
| 功能 | 运行总览 / 详情 | P0 | ✅（复用精度组件） |
| 功能 | 场景目录 | P0 | ✅ |
| 性能 | 压测任务（创建+列表） | P0 | ✅ |
| 性能 | 压测大屏 | P0 | ✅ |
| 性能 | 性能数据集 | P0 | ✅ |
| 可靠性 | 故障观测（表单+结果） | P1 | ✅ |
| 可靠性 | 实验历史 | P1 | ✅ |
| 公共 | 用例库 | P0 | ✅（回填数据 T2 待） |
| 公共 | 数据集管理 | P1 | ⚠️ catalog REST 缺（D3） |
| 公共 | 模型管理 | P0 | ✅ |
| 公共 | 执行机 | P1 | ✅ |
| 公共 | 后台任务 | P0 | ✅ |

原则：**P0 页面全部只依赖已有 API**，后端零改动即可开工；P1 页面里仅"数据集管理"需要等后端。

---

## 1. 全局壳（Shell）

```
┌──────────────────────────────────────────────────────────────┐
│ ⚡aite   功能 | 性能 | 精度 | 可靠性 | 公共       🔍 🎨 🔔 👤 │  ← 顶栏
├──────────┬───────────────────────────────────────────────────┤
│ 二级菜单  │  页面内容                                          │
│ (按模块)  │                                                   │
└──────────┴───────────────────────────────────────────────────┘
```

- **顶栏**：logo / 五模块 tab（切换 = 换左侧菜单，不换页）/ 全局搜索（V1 范围：run 名、套件名，回车跳转）/ 主题切换（A3 六套）/ 任务红点（有 running 态任务时亮，点击跳「后台任务」）/ 用户。
- **左侧菜单**：每模块 2~5 项，见下文各模块。
- **路由**：`/:module/:page`，运行详情类页面带 id：`/accuracy/runs/:id`、`/perf/runs/:id`。
- **SSE 全局订阅**：running 任务数 → 顶栏红点；进入详情页再订阅对应频道。

---

## 2. 精度模块（最成熟，V1 主战场）

左侧菜单：**评测运行 / 运行详情 / 趋势对比 / 人工评测 [P1]**

定位（2026-09-24 用户定稿）：精度测试本质 = **选数据集 + 选条数/随机种子 +
选模型 + 选并发压力**。任务创建全部做成可选按钮，提交后由**后台按选择自动
回填套件配置（YAML）并运行**——YAML 属后台语义，前台不感知、不提供任何
YAML 编辑；「评测套件」管理页移出 V1 前端（配置沉淀在后台，V2 再评估是否
需要高级配置页）。

### 2.1 评测运行 [P0]

回答的问题：跑过哪些评测？结果如何？——入口页。
**不展示成本**（2026-09-24 用户定稿）：token 用量可靠，但换算成钱需要各模型
价格表——本地/私有部署模型无公允单价、API 价格常变，价格表外置仍在后端
backlog；展示算不出来的数字会误导。后端继续记录 usage（token 数是准的），
待可靠计价落地再评估恢复（可先以 token 用量列替代）。

- 统计卡 ×4：累计运行、用例通过率、评测数据集、受测模型
- 筛选行：状态 chips + 套件下拉 + 模型关键字；表格与分页（列不含成本）
  （`GET /runs?domain=accuracy`、`GET /stats?domain=accuracy`）
- **「＋ 创建评测任务」→ 结构化表单（四段按钮组，无 YAML）**：
  1. **评测数据集**（可搜索、可多选列表）：四组筛选——**归属（开源 / 个人 / 客户）**、
     **存储位置（在线 / 离线）**、**能力评估维度（知识问答 / 数学推理 / 代码 / RAG /
     Agent / 鲁棒性 / 长上下文，可多选 OR）**、**数据集类型（LLM / Agent / RAG）**
     （2026-09-24 用户定稿）——
     开源 = 内置/公开基准，个人 = 自建导入，客户 = 客户业务数据；
     在线 = hf 仓库/对象存储引用按需拉取，离线 = 已落地本地（包内抽样版 /
     add-hf 全量 / gen-domain 产物）。数据来自公共·数据集管理（accuracy 型）+ 内置基准目录
  2. **抽样条数与随机种子**：条数预设（全量/100/300/500/自定义）+ seed 输入
     （同 seed 抽样可复现、跨 run 可比）
  3. **目标模型**（radio 卡片）：来自公共·模型管理注册的 gateway 别名（含端点提示）
  4. **并发压力**（radio 卡片）：1/4/8/16 预设 + 场景说明
  高级折叠（可选）：运行名称（缺省自动生成）、阈值判定（auto/不判定/自定义）
- **创建 ≠ 执行（2026-09-24 用户定稿）**：提交 = `POST /accuracy/tasks`（spec 为
  结构化表单形态，§10.1 注）→ 后端组装并**固化配置**（相当于后台写好 YAML），
  生成 task_id（pending），**此时不执行**；实际执行绑定 task_id。执行方式三选：
  **立即执行**（创建后自动 run）/ **定时执行**（scheduled_at，后台调度器到点
  触发 run，触发前可编辑/取消）/ **仅创建**（任务列表手动 run）。同一份配置
  再次执行 = 生成新 task_id（TaskKit retry 链，原配置不变），任务历史按配置
  可追溯多次执行。

### 2.2 运行详情 [P0]（2026-09-24 重构：实时运行视图）

回答的问题：现在跑到哪了？还要多久？哪些挂了为什么挂？日志在哪？

- **实时进度卡**（运行中首要）：进度条 + **总数据条数 / 已运行 / 剩余条数 /
  失败错误数 / 预计剩余时间**（按当前速率估算）+ 当前阶段（引擎 progress 事件，
  含阶段文本与 done/total）；数据源 = SSE（`GET /runs/{id}/stream`，progress 事件
  + total 回写）+ 5s 兜底轮询 run 行，终态自动刷新全页
- **指标汇总**：运行中标注「阶段性」（已完成例数的即时聚合，非最终判定）；
  完成后出最终判定与报告
- **全部失败用例输入输出**：独立卡（断言 → 输入 → 输出），附 failures.jsonl 下载
- **用例抽样结果复核**：按 seed 随机抽样 N 例展示 输入/输出/自动判定 + 人工复核
  标记（待复核/通过/问题），可重新抽样、送人工评测——防“分数对但行为怪”
- **运行日志**：页面只留实时尾部（SSE 追加 + 光标）；完整日志、输入输出明细
  （jsonl）、产物包落盘 work_dir（远端/本机存储），给下载链接——大文件不进页面
- **用例结果完整列表**：可展开行（输出全文 + usage + timings）+ 全部/仅失败过滤
- 基线对比（Δ 列、劣化标红）：已随版本质量对比提前（§2.3）

### 2.3 版本质量对比（趋势页重构，2026-09-24 定稿）

回答的问题：**同数据集、跨版本，分数有没有变差**——这是版本质量的核心判据；
**同模型不同版本精度不允许劣化**（低于基线 = 劣化，阻断项）。

- 图表：**横轴 = 版本号，纵轴 = 分数**；曲线由固定模型 × 固定数据集生成；
  **基线画虚线**（baseline_ref，缺省回退最近 passed run），数据点按
  ≥ 基线标绿 / < 基线标红 染色，劣化点就地标注 ▼ pp 差值
- 筛选：数据集（指标取该集推荐指标）+ 模型多选 + 对比模式：
  **横向**（同模型跨版本，聚焦劣化检测，模型单选）/
  **纵向**（跨模型同数据集，模型多选，各模型基线独立虚线）
- **版本质量判定表**：模型 × 版本 × 分数 × 基线 × Δ(pp) × 达标/劣化 徽章
- **劣化检测面板**：列出全部劣化项（模型/版本/差值/建议阻断发布并回溯变更）
- 移除：雷达图与时间维通过率曲线（非精度分析核心）
- 数据源：趋势查询按 (model, dataset, metric, version) 聚合；基线读取依赖
  D1 基线契约（**从 V2 提前**，见 §8.6）

### 2.4 人工评测 [P1]

回答的问题：机器分不准的，人来评。

- 筛选：待评/已评；任务卡片流（输入/期望/输出/rubric + 评分表单 0-10 + 评审人 + 备注）
- 顶部「导出评分 CSV」（`GET /manual-eval/export`）
- 入口衔接：运行详情用例行「送人工评测」按钮（`POST /manual-eval/tasks`）

---

## 3. 功能模块

左侧菜单：**运行总览 / 场景目录**

定位：pytest 引擎（场景级 pass_rate + 失败详情），与精度共用运行存储与组件。

### 3.1 运行总览 / 3.2 运行详情 [P0]

- 与精度**同一套组件**，差异配置化：
  - 列表列：用「场景数」（全站不展示成本列，同精度）
  - 详情指标表：每行 = 一个场景（basic_chat、api_params…）的 pass_rate + 阈值判定
  - 详情**失败详情块**（功能域特有，放用例表上方）：断言 → 输入（截断） → 输出（截断），
    来自 case results 的 error/details
  - （数据同走 `GET /runs`、`GET /runs/{id}/cases`，engine=pytest 的 run 天然只有场景级行）

### 3.3 场景目录 [P0]

回答的问题：现在能测什么？每个场景有多少用例？

- 场景卡片网格：场景名（basic_chat / api_stream / agent_tasks…）/ 说明 / 用例数 / 标签
  （`GET /tools/benchmarks?engine=pytest`）
- 卡片操作：「用例级过滤跑」→ 弹窗勾选用例 id（写进 suite 的 `tools.cases` 后创建套件）**[P1]**
- 顶部说明条：自定义场景 = 建目录放 `cases.jsonl`（跳转数据集管理 functional 型）

---

## 4. 性能模块（用户核心场景）

左侧菜单：**压测任务 / 性能数据集**

### 4.1 压测任务 [P0]（roadmap F1，纯前端）

回答的问题：怎么发起一次压测？跑到哪了？

布局：左「任务创建表单」+ 右（或下方）「任务历史列表」。

- **创建表单**（分组表单，全部控件来自已有 API/契约）：
  1. 引擎：evalscope-perf / locust（`GET /tools/engines`；probe 仅 perf）
  2. 模式：baseline 固定并发 / ramp 爬坡(from/to/step) / probe 摸高(SLA 指标集) / soak 长稳
  3. 负载（三选一）：固定 prompt / 平台数据集（perf 型选择器） / synthetic（count + prompt_length）
  4. 压力参数：并发数组 或 QPS(+qps 模式说明)、duration 总时长 / level_duration 每档、
     warmup、stream、jitter、arrival(poisson/spike/custom，仅 locust)
  5. 模型参数：base_url / model / api_key_env / max_tokens(标量或区间) / tokenizer_path
  6. 高级折叠：chaos 故障注入声明（复用可靠性模块的目标/武器选择器）、extra_args
- 提交 = 组装 suite config → `POST /runs`（engine 路由已通）→ 列表刷新 + SSE 进度
- **任务列表**：run 名 / 引擎 / 模式 / 目标模型 / 状态 / 时长 / 开始时间 / 操作（大屏、停止[待域任务 REST]、重跑）
  （`GET /runs` 过滤 engine；停止 **⚠️** 见缺口 2）

### 4.2 压测大屏 [P0]（版式已定稿，照搬）

回答的问题：这次压测的结论是什么？曲线长什么样？错在哪？

```
┌────────────────────────────────────────────────────────────┐
│ 统计结果表（全宽）：metric/档位/值/阈值/PASS·FAIL/N          │  ← 一眼看结论
├──────────────────────────────┬─────────────────────────────┤
│ TTFT avg+p95                 │ 延迟 avg+p95                │
├──────────────────────────────┼─────────────────────────────┤
│ TPOT avg+p95                 │ ITL avg+p95                 │
├───────────────┬──────────────┴──────────┬──────────────────┤
│ RPS           │ 吞吐 tok/s              │ 并发阶梯         │
├───────────────┴─────────────────────────┴──────────────────┤
│ 错误统计（错误码/文本 × 次数，柱状）                          │
└────────────────────────────────────────────────────────────┘
   chaos 注入窗 → 全部曲线图叠加阴影 markArea
```

- 数据：`GET /runs/{id}/timeseries`（series/combined/errors/summaries 一应俱全）
- 交互：图例点选档位；时间轴 brush 联动 **[P1]**；非压测 run 进入 → 空态引导去大屏外的详情
- 顶部加「导出报告 ↗」（`GET /runs/{id}/report.html`）

### 4.3 性能数据集 [P0]（模块 J 一期，API 已通）

回答的问题：压测负载从哪来？怎么造出"长前缀/定长输出"这种可控流量？

- 场景列表：名称 / 语料来源 / 长度分桶 / 前缀共享率 / 产物 stats 摘要 / 生成时间
  （`GET /perf/datasets`）
- 生成器表单（抽屉）：语料池（目录/jsonl）、input_lengths+weights 分桶、
  output_tokens（定长/区间）、prefix_sharing/prefix_groups、seed
  → 「校验」干跑（`POST /perf/datasets/validate`）→ 「生成」（`POST /perf/datasets`）
- stats.json 可视化：长度分布直方图 + 命中率示意 **[P1]**

---

## 5. 可靠性模块 [P1]

左侧菜单：**故障观测 / 实验历史**

### 5.1 故障观测 [P1]

回答的问题：注入故障后服务多久恢复（RTO）？期间表现如何？

- **表单**（三段）：
  1. 目标选择：`GET /chaos/targets` 树（节点→容器→进程，模糊过滤框），exclude 名单
  2. 武器与参数：`GET /chaos/weapons`（带 param_schemas 动态表单：latency_ms、
     select 策略 first/all/random(k)/percent(p)/count(n)、match 模式、fault_duration）
  3. 观测：探针 URL + interval + timeout + post_window、duration（≤600s）、
     背景流量（可选：引用 perf 数据集 + 并发 + duration，复合任务）
- 提交 `POST /chaos/observe`（≤600s 限制提示）→ 结果区实时刷新
- **结果区**：事件时间线（injected→verified→recovered 时间戳）+ 探针序列图
  （pass/fail 时间点 + 时延）+ RTO 卡片（affected / fail_count / rto_seconds）

### 5.2 实验历史 [P1]

- chaos run 列表（`GET /runs` 过滤 + chaos_events 有值的 run）→ 点击进结果视图
  （背景流量 run 可跳压测大屏看曲线，故障阴影自动叠加）

---

## 6. 公共模块

左侧菜单：**用例库 / 数据集管理 [P1] / 模型管理 / 执行机 [P1] / 后台任务**

### 6.1 用例库 [P0]（§15 文字用例库，API 已通）

- 筛选：domain / status / priority（`GET /test-cases`）
- 表格：case_no / 标题 / domain / 优先级 / 状态 / 版本 / 更新时间 / 操作
- 详情抽屉：步骤/预期 + **关联运行列表**（`GET /test-cases/{ref}/runs`，回填数据完整性
  依赖 T2 **⚠️**，V1 显示已有即可）
- CRUD：新建/编辑表单（`POST` / `PATCH`）、删除确认（`DELETE`）

### 6.2 数据集管理 [P1]

回答的问题：平台里有哪些数据（题库/规约/流量场景）？——统一目录（roadmap §12）。

- 四类 tab：功能（cases.jsonl 规约）/ 精度（题库）/ 性能（流量场景）/ 内置
- 列表：name / version(=seed) / source / stats（类型相关：case 数、长度分布等）/ path
- 详情：统计可视化 + 「去使用」（功能→套件、精度→套件、性能→压测表单）
- ⚠️ **catalog REST 未开放（D3）**：V1 先做 perf 型（`/perf/datasets` 已有）+ 精度内置
  题库只读列表（`/tools/benchmarks` 是基准目录不是数据集，仅作参考链接），
  全量目录等 D3。页面骨架先立，tab 逐个点亮。

### 6.3 模型管理 [P0]

- 表格：名称(gateway 别名) / 类型 / base_url / model / key env / 操作
- 新建/编辑弹窗（`GET/POST /models`、`DELETE /models/{id}`）
- 说明条：judge 模型与目标模型共用此处注册

### 6.4 执行机 [P1]

- 节点卡片：名称 / 标签 / 心跳时间（>30s 标 stale 灰显）/ 在跑任务
  （`GET /agents`，心跳即派发语义说明）
- 任务历史：`GET /agent-tasks` 表格 + 结果详情
- 引擎白名单展示

### 6.5 后台任务 [P0]

- 任务表：id / 类型 / 状态 / 关联 run / 创建时间 / 错误（`GET /tasks`）
- 详情抽屉：任务 JSON + **实时日志流**（`GET /tasks/{id}/stream` SSE → 终端风格面板）
- 顶部统计：active / done / error（`GET /stats`）

---

## 7. 组件复用映射（开发切分依据）

| 组件 | 使用方 |
|---|---|
| StatCard 统计卡 | 精度/功能运行总览、后台任务 |
| RunTable 运行表 | 精度/功能/性能 任务列表（列配置化） |
| RunDetail 运行详情骨架 | 精度/功能（性能走大屏变体） |
| MetricBadge / ThresholdTag | 全部结果表 |
| YamlEditor（含校验回显） | 功能域套件编辑（精度域已改结构化表单，无 YAML） |
| TaskCreateForm 表单骨架 | 性能（大）、精度（简）、可靠性观测 |
| TargetTree + WeaponForm | 可靠性、性能表单的 chaos 段 |
| ChartFamily 八图组 | 压测大屏、可靠性结果（探针曲线复用） |
| SSE 进度条 / 日志面板 | 新建运行弹窗、任务详情、详情页实时块 |
| 抽屉 Drawer（YAML/详情/表单） | 全站统一 |

## 8. 后端缺口清单（前端落地依赖，按阻塞面排序）

> 2026-09-24 起按「最终规划补齐 API」推进，缺口 1/2/4 已实现（契约见 §10），
> 状态标注更新如下。

1. **数据集 catalog REST（D3）**：✅ 已实现（§10.2）→「公共·数据集管理」全量目录可用。
2. **四域任务 REST（C1-C4 的 routers）**：✅ 已实现（§10.1）→ 域任务列表/创建/停止/重试
   可用（子进程进程组强杀仍待各域 executor.cancel 补强，见 domain-backends.md §8）。
3. 用例库执行回填（T2）：`/test-cases/{ref}/runs` 的数据完整性（V1 页面照做，空态提示）。
4. `/runs`、`/stats` 按域过滤：✅ 已实现（§10.3，`domain` 查询参数）。
5. **精度任务 spec 结构化表单形态**（2026-09-24 新增，随前端落地补）：
   `POST /accuracy/tasks` 的 spec 接受
   `{datasets:[ref…], limit_per_dataset, seed, model(别名), version(必填,被测版本), parallel, model_params{max_tokens?,temperature?,top_p?,top_k?,thinking?}, extra_model_params(JSON 合并), name?, thresholds?, execute{mode,at}?}`，
   accuracy 执行器先按表单**自动组装 suite raw**（数据集三级解析 / metrics: auto /
   seed 与 limit 写入 / 模型参数合并进请求体——留空不传、同名以 JSON 为准 /
   thinking 按引擎翻译：Qwen enable_thinking · OpenAI reasoning_effort · Claude budget）
   再走统一管道——YAML 回填发生在后台，前台零配置。并发不设档位（2026-09-24 用户
   定稿：用户自填数值，留空 = 引擎默认）。数据集条目带**归属（开源/个人/客户）**
   与**存储位置（在线/离线）**两个分类维度（目录页与选择器共用，catalog 表
   additive 加列，见 §12 契约扩展）。**创建 ≠ 执行**：创建请求体增
   `execute: {mode: "now"|"scheduled"|"manual", at?}`——now = 创建后立即 run；
   scheduled = 注册后台调度器（scheduled_at 到点触发 run，触发前 pending 可
   编辑/取消）；manual = 等 POST /{id}/run（**定时调度器为新增后端能力**）。
6. **版本趋势查询 + 基线（D1 提前，2026-09-24）**：趋势聚合从“按时间”改为
   “按 (model, dataset, metric, **version**)”——版本号来源 = 被测系统/模型的
   版本标注（run 或任务 spec 携带）；基线 = baseline_ref 显式指定或回退最近
   passed run 的同维度分数（roadmap §12.5 既有设计提前实施）；
   `GET /trends?model=&dataset=&metric=` 返回按版本聚合的序列 + 基线值。
7. **run 产物下载端点（2026-09-24 详情页实时视图）**：
   `GET /runs/{id}/artifacts/{log|failures.jsonl|bundle.zip}`——work_dir 内文件直出
   或打包流式下载（完整日志 / 失败明细 / 输入输出明细 / 全部产物）；进度数据
   既有 SSE + progress 事件已够（total 回写、阶段文本 09-23 已落地），预计剩余
   时间由前端按速率估算，无需后端；抽样复核 = 前端按 seed 从 cases 列表抽样
   （seed/N 写入任务 spec 留档即可复现）。

## 9. V1 明确不做（V2 备忘）

- 精度套件管理页 / 任何前台 YAML 编辑（2026-09-24 定稿：套件为后台语义，
  创建一律结构化表单，配置由后台自动回填）
- ~~基线对比 UI~~（2026-09-24 提前进 V1：版本质量对比页依赖基线，见 §2.3/§8.6）
- 登录/多用户/权限（保持可选 Bearer Token）
- 基线对比 UI（Δ 列/劣化标注，等后端 D1）
- 数据集治理全流程（模块 A：三池/审计/导入向导）
- ChaosBlade 注入器、故障编排 DSL、分布式注入拓扑图（模块 I 二期）
- 执行机标签调度与容量管理（G2）
- 全局命令面板（⌘K）、消息中心、暗暗色/自定义主题编辑器
- 移动端适配（只保证 1280+ 桌面）

## 10. 新增 API 契约（按最终规划补齐，2026-09-24 实现）

前端**只依赖本节契约**；四域任务契约与 `docs/domain-backends.md §2` 同形，
数据集契约与其 §12.3 同形。

### 10.1 四域任务 REST（`{D}` ∈ functional / accuracy / perf / reliability）

| 方法与路径 | 语义 | 状态码 |
|---|---|---|
| `POST /api/v1/{D}/tasks` | 创建任务（`{name?, spec}`，spec=域自定义） | 200 |
| `GET /api/v1/{D}/tasks` | 列表（`status` / `retry_of` / `limit`） | 200 |
| `GET /api/v1/{D}/tasks/{id}` | 详情 | 200 / 404 |
| `PATCH /api/v1/{D}/tasks/{id}` | 改 name/spec（仅 pending） | 200 / 404 / 409 |
| `DELETE /api/v1/{D}/tasks/{id}` | 删除（仅终态） | 200 / 404 / 409 |
| `POST /api/v1/{D}/tasks/{id}/run` | 运行（异步，返回任务快照） | 202 / 404 / 409 |
| `POST /api/v1/{D}/tasks/{id}/stop` | 停止（running→stopping→cancelled；幂等） | 200 / 404 / 409 |
| `POST /api/v1/{D}/tasks/{id}/retry` | 重试=新任务（`retry_of` 链） | 201 / 404 / 409 |
| `GET /api/v1/{D}/tasks/{id}/result` | 结果（仅终态；`{task_id,status,result,error}`） | 200 / 404 / 409 |
| `GET /api/v1/{D}/tasks/{id}/stream` | SSE 进度/终态（频道 `{D}:tasks:{id}`） | 200 |

状态机：`pending → running → succeeded/failed`，`running → stopping → cancelled`，
残留由启动扫描收敛为 `stale`。spec 形态 = 各域任务创建体（perf 域 = 压测表单
组装出的 suite raw，reliability = `/chaos/observe` body 同构）。

### 10.2 数据集统一目录 REST

| 方法与路径 | 语义 | 状态码 |
|---|---|---|
| `GET /api/v1/datasets?type=` | 目录列表（type ∈ functional/accuracy/perf） | 200 / 422 |
| `GET /api/v1/datasets/{ref}` | 详情（ref：id 或 `type:name` 或唯一 name） | 200 / 404 |
| `DELETE /api/v1/datasets/{ref}` | 删除登记（文件保留，同 CLI rm） | 200 / 404 |
| `POST /api/v1/datasets/functional/import` | 功能型摄取（`{name,jsonl,notes?,force?}`，过 harness.Case 真实校验） | 201 / 422 |

perf 型登记自动发生于 `POST /perf/datasets`（既有）；精度型经 `aite dataset add-hf` 登记后即可列出。

### 10.3 run / stats 按域过滤

- `GET /api/v1/runs?domain=`：domain ∈ functional/accuracy/perf/reliability；
  过滤规则（单一来源 `service/web_service.run_domain`）：`config.engine` ∈
  {evalscope-perf, locust} → perf；pytest → functional；其余带 engine（evalscope
  等）→ accuracy；无 engine 且带 chaos 声明 → reliability；无 engine → accuracy
  （自研评测 run）。带 domain 时按新→旧扫描上限 `config.MAX_DOMAIN_SCAN` 条后
  内存过滤再分页，`count` 为过滤后总数。
- `GET /api/v1/stats?domain=`：同规则过滤后聚合（前端各模块统计卡数据源）。


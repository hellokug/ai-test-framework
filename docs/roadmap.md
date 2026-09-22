# aite 完善路线图

> 约定：每完成或变更一项，立即更新对应条目与文末进度记录。
> 顺序原则：核心功能优先，前端体验最后。

## 架构定位（2026-09-01 重定：平台化）

**aite 是评测平台，不是评测引擎。** 精度基准跑分委托给专业工具
（evalscope / opencompass / lm-eval），平台核心价值 = 数据集管理 + 工具编排 +
结果统一。已确认的决策：

- 自研引擎**保留作补充**：性能延迟、安全探针、RAG、judge 闭环等外部工具
  覆盖不到的维度；精度基准（mmlu/ceval/gsm8k 跑分）走外部工具
- 首个接入 **evalscope**（pip 轻、Python API、原生 OpenAI 兼容端点、中文基准全）
- 执行形态：**进程内 Python API**（依赖版本冲突用隔离策略解决）

## v1 目标（2026-08-28 重定）

v1 按**能力模块**交付，不再按"补缺口清单"推进。每个模块独立走完
需求对齐 → 对象建模 → 测试 → 实现 → 文档，完成后单独提交 git。

### 模块 A：数据集治理子系统（需求已对齐，暂缓实施）

完整需求与对齐结论已存档：`docs/requirements-datasetgov.md`。
恢复实施时从 M0（对象建模 + 三池存储 + 审计）直接开始，无需重新对齐。

### 模块 B：指标标准性（2026-09-01 随平台化收缩）

精度分数已决定委托外部工具，本模块大部分作废，仅保留：

- [ ] tokenize 中文分词支持（jieba 可选依赖）——内置引擎自用的小修复，
      低优先级；sacrebleu 对拍项**取消**（不再需要）

### 模块 C：evalscope 适配器（进行中，平台化首个落地）

目标：suite 里指定外部引擎，精度基准由 evalscope 计算，结果回传 aite 统一存储。

- [x] C0 调研 evalscope 1.9.1（2026-09-01 完成）：
      进程内 `run_task(dict) -> {dataset: Report}`；模型接入 `eval_type: openai_api` +
      `api_url`（OpenAI 兼容端点直连）；**eval_backend 可选 Native/OpenCompass/VLMEvalKit/
      RAGEval——一个适配器覆盖四后端**；`limit` 语义是**每科目**条数（ceval limit=3 实跑
      52×3=156 题，适配器必须换算）；返回含 score/metrics(name,num,score) 与分科目明细，
      另产 HTML 报告与性能表（latency/tok/s/token 用量）；本地微跑验证：ceval 156 题
      mean_acc=0.6859，默认 5-shot 业界标准口径。用户已有独立 conda 环境
      D:/miniconda/envs/evalscope（依赖隔离考量见 C1）
- [x] C1 ToolAdapter 接口（aite/tools/base.py：ToolRunRequest/ToolRunResult/
      ToolAdapter + 注册表；evalscope 适配器实现两步契约 build_config/run）
- [x] C2 evalscope 内置基准直通（2026-09-02 完成）：suite 带 `engine: evalscope`
      即路由工具层，`aite run` 单一入口不变；结果解析自 reports/*.json 文件契约
      （兼容两种目录布局），写 run_metric_summary 并补 overall 行与自研引擎口径
      对齐；CLI 汇总/阈值判定（metric_thresholds）可用。统一运行环境：
      D:/miniconda/envs/evalscope（Python 3.10，aite+evalscope 同装，162→171 测试
      全绿）；真实验证 gsm8k 2 题 mean_acc=1.0 端到端通过。
      踩坑记录：evalscope no_timestamp 模式 json 布局为 reports/<model>/<ds>.json；
      run_task 返回值不可依赖（repr 字符串），一律走文件收集。
- [ ] C3 平台数据集 → evalscope 自定义数据格式转换（local/builtin 灌进去）
- [ ] C4 入口与文档
      - [x] C4a 基准目录接口：`aite tool benchmarks` CLI + `list_tasks()` Python API，
            数据形状 REST-ready（2026-09-02，见进度记录）
      - [x] C4b REST 端点（2026-09-09 完成）：`GET /api/v1/tools/engines`、
            `GET /api/v1/tools/benchmarks?engine=`；**四域任务 CRUD/运行打通**——
            POST /suites 与 /runs 补 engine 路由（此前工具域 suite 被
            SuiteConfig extra=forbid 挡死 422/误路由），DELETE /suites 实现
            （run.suite_id 置 NULL 保留审计）；详见进度记录
      - [ ] C4c 文档收尾（dataset-guide / README 复核）

验证方式：真实 evalscope 跑 ceval/gsm8k 小样本，结果入库、Web 趋势可见。

### 模块 D：功能测试（pytest 执行引擎，2026-09-03 完成）

目标：用 pytest 覆盖模型功能常见场景（真实调用模型 + 规则断言），复用工具层
路由 / 入库 / 阈值 / 趋势链路，不新增抽象层。首发 4 内置场景 14 例：
basic_chat / structured_output / tool_calling / long_context。

- [x] D1 契约层小改：ToolRunRequest 加 tests_dir；parse_tool_suite 支持
      tests_dir（此时 datasets 可空）；total_cases 回退改为首个 metric 组
      count 总和（多数据集/多场景求和）。
- [x] D2 功能场景包（aite/functional）：harness pytest 插件（chat/chat_raw
      fixture，AITE_FT_* 环境变量注入，网络/5xx 重试 1 次）+ 4 场景
      （抗 flaky：约束式 prompt + 确定性目标断言）。
- [x] D3 pytest 适配器（aite/tools/pytest_adapter.py）：逐场景 subprocess
      `python -m pytest --junitxml`，collect 解析 junit-xml 出场景级
      pass_rate；`aite tool benchmarks pytest` 枚举内置场景。
- [x] D4 打包与文档：`pip install "aite[functional]"`（pytest>=8.2）；
      examples/functional_pytest.yaml；README 功能测试章节。
- [x] D5 用例数据驱动化（2026-09-04 完成）：请求体外移到每场景一个
      `cases.jsonl`（一行 = id + 场景说明 + messages/tools/params + expect 断言），
      场景目录零 Python；harness 插件经 `pytest_collect_file` 自定义 File/Item
      收集，加用例只加 jsonl 行。expect 为紧凑 DSL（contains / json{path,type,
      eq,in,len,fields...} / tool / tool_args_contains），覆盖原 14 例全部断言；
      long_context 的确定性长文经 `vars.haystack` 占位符生成（16k 填充不进
      jsonl 明文）。数据格式错误（缺字段/未知断言键/id 重复）收集期报错带行号。
      手写 `test_*.py` 场景保留兼容（chat / chat_raw fixture）。
- [x] D6 质量类断言（2026-09-04 完成）：expect DSL 新增三个内置检查（保守口径，
      宁可漏报不误报，失败信息带证据片段）——`no_garbled`（U+FFFD/锟斤拷/控制
      字符/UTF-8-Latin1 误解码特征）、`no_leak`（ChatML/DeepSeek 全角/INST/SYS/
      think/tool_call 等内部标签泄露，`extra_patterns` 可扩展）、`no_repeat`
      （连续块复读/高频短块/整行重复）。JSON 输出格式正确性用 `json: {}`
      （空规格=仅解析）。更复杂的断言走手写 test_*.py（chat fixture 逃生门），
      不做自定义 checker 注册表（YAGNI）。
- [x] D7 用例级过滤（2026-09-05 完成）：suite yaml `tools.cases: [用例 id]`
      （省略 = 全部），单用例问题复现场景。语义：精确定向 `cases.jsonl::<id>`
      nodeid（不用 pytest `-k` 子串匹配，打错 id 在 build_config 显式报错并列出
      可用 id）；id 在任一选中场景存在即合法，无匹配用例的场景整场景跳过
      （日志可见，不入库无 pass_rate 行）；仅支持 cases.jsonl 数据驱动场景
      （手写场景报错）。harness 的 load_cases 懒导入（tools 包不硬依赖 pytest）。
- [x] D8 失败详情输出（2026-09-06 完成）：失败不再只有 pass_rate 数字——
      `failure_detail()` 组装「断言 → 输入 → 输出」，经 repr_failure 进 junit-xml
      与场景日志，适配器从 junit 提取（failure/error 都取）→ ToolRunResult.failures
      → CLI 汇总表后打印（Rich escape，防 `[id]` 被当标记吃掉）。体积预算：
      输入每条消息压平后头尾各 200 字符（16k haystack 标注总长）、输出 300、
      tool_call 参数 120、每例 <1KB、CLI 最多 20 例；入库不含 details 无膨胀。
      踩坑记录：① pytest 断言重写会给 `str(AssertionError)` 追加
      `assert False` 解释行（实测 `'custom msg\nassert False'`），插值异常前
      必须取首行；② `pytest.Failed` 不是公开属性，用 `pytest.fail.Exception`；
      ③ except 内 raise 需 `from None` 断链，否则异常链回溯混入 junit。
- [x] D9 API 契约域场景（2026-09-07 完成）：新增 9 场景 38 例，测试对象从
      模型能力扩展到 OpenAI 兼容层本身——api_params（参数校验 → 4xx/405）、
      api_optional_params（接受性/n=2/system 生效）、api_stream（SSE 拼接/
      finish_reason/[DONE]/分块）、api_limits（max_tokens→length/stop 序列/
      tool_calls 信号）、api_shape（响应体契约）、api_robustness（emoji/CJK/
      超上下文→400）、api_json_mode、api_tool_choice、api_long_output。
      断言口径：**严格按 OpenAI 规范**——Ollama 实测 9 例 FAIL 均为真实规范
      差异（n 忽略、role/tools/tool_choice 不校验、超上下文静默截断、
      max_completion_tokens 不认、json_mode 无防呆），desc 全部标注
      「已知差异，换标准端点应转绿」，这正是该域的价值：显性暴露服务端
      兼容性差异。配套 harness 扩展：预期错误模式（http_status——错误响应
      是被测对象，不重试不抛）、SSE 流式（params.stream 走 _post_stream
      拼接 delta 出伪响应 + chunks_min/sse_done 断言）、请求构造控制
      （params 下划线前缀：_omit_messages/_http_method/_raw_body）、
      resp 响应体断言（数字索引 choices.0、数组多断言、nonempty 支持非空
      集合）、not_contains/no_tool/finish_reason 键；messages 允许空数组
      （empty_messages 用例需要）。

关键决策（设计校验结论，实现时遵循）：

- **模型访问不走场景目录 conftest.py**（pytest confcutdir 陷阱：已安装环境下
  rootdir 之上的 conftest 不加载），改用 pytest 插件模块
  `aite.functional.harness`，subprocess 加 `-p aite.functional.harness`
  按导入路径加载，安装/源码环境行为一致
- `tools.datasets` 复用为场景名列表；tests_dir 模式下可省略（= 跑该目录全部
  含 test_*.py 的子目录），非空时作为子目录名筛选
- `limit_per_subset` 对 pytest 无语义，忽略（文档标注）
- 阈值：显式 `metric_thresholds: {pass_rate: x}` 优先；`auto` → 1.0
  （功能测试零容忍口径）；不配则无判定
- `list_tasks()` 只枚举内置场景（tests_dir 用户场景运行时才解析，文档说明）
- 结果粒度：场景级汇总——每场景一行 pass_rate 入 run_metric_summary，
  junit-xml + 执行日志留档 work_dir；skipped 剔除分母且 count 同口径
  （overall 加权平均才准确）；不动存储契约（storage/models.py 未改）
- subprocess argv 细节：`-o norecursedirs=`（主套件把 fixtures 加入
  norecursedirs 防误收集离线场景用例，pytest 对显式目录参数同样过滤，须清空
  兜底）+ `-p no:cacheprovider`（不在 rootdir 落 .pytest_cache 污染工作区）；
  env 必须与 os.environ 合并（Windows 缺 SystemRoot 会崩）

验证方式：pytest 全量 + 离线 fixtures 端到端；真实模型
`aite run -c examples/functional_pytest.yaml` 结果入库、CLI 汇总、趋势可见。

### 模块 E：性能压测（evalscope-perf 适配器，2026-09-08 完成）

目标：补齐性能测试域——自研引擎已有「单请求计时 + 指标化聚合」（Stopwatch/
latency_p95/ttft/tokens_per_sec 均在），缺的是压测编排层（并发梯度/QPS/warmup/
压测语义）。**压测客户端不自研**（单进程 asyncio 在高并发下自身即瓶颈），
委托 evalscope perf 子命令（aiohttp + 多 worker，vLLM 社区同源方案；LLM 原生
指标 TTFT/TPOT/ITL/吞吐分档），aite 负责编排与统一入库。

- [x] E1 契约与执行计划：ToolRunRequest 加 perf 段（prompt/parallel/number/
      max_tokens/warmup_num/stream/no_test_connection）；parse_tool_suite 带
      perf 段即免除 datasets 要求；build_config 纯函数映射 CLI 参数（number
      标量自动复制对齐 parallel 长度——evalscope 校验规则：两者等长）。
- [x] E2 执行与收集：subprocess 调 evalscope CLI（shutil.which 定位；包无
      __main__ 不能 python -m）；collect 解析各档
      `parallel_X_number_Y/benchmark_summary.json` + `benchmark_percentile.json`
      → 每档每指标一行 `dataset="perf:c<并发>"`（时延族 ttft/latency/tpot/itl
      均值 + p50/p90/p95/p99 多档位 + max（逐请求 db 取），qps/output_tps/
      error_rate）→ run_metric_summary，趋势页与精度/功能并列可比。
- [x] E3 文档与真实验证：examples/perf_evalscope.yaml、README 性能压测章节。
- [x] E4 负载层（2026-09-08 完成，用户评审：真实压测不用固定提示词）：`perf.dataset`
      指向本地 jsonl/csv（行数 = 条数，输入键同平台约定 input/question/instruction/
      prompt/query/user），适配器导出逐行 prompt 文件（evalscope `custom` 数据集）
      打流时逐条取用；`max_tokens` 支持标量定长与 `[min, max]` 区间均匀采样
      （输出长度分布）；`tokenizer_path` / `min/max_prompt_length` 透传。
- [x] E5 模式层（2026-09-08 完成，用户评审：缺爬坡/摸高/基准/长稳与时长配置）：
      `perf.mode` 四选一——baseline（固定并发）/ ramp（from/to/step 展开并发
      梯度，to 必含）/ probe（`--sla-auto-tune` SLA 下二分搜索最大并发，sla
      指标集 fail-fast 校验）/ soak（duration 驱动）；省略 mode = v1 直配兼容。
      **压测中途熔断上游不支持**（SLA 档间判定才有），记 backlog。
      踩坑：probe 产出布局为 `sla_tuning/sla_parallel_N_run_i/`（非
      `parallel_X_number_Y/`），collect 改统一 glob `**/benchmark_summary.json`
      按并发聚合（多 run 均值），两种布局都覆盖。
- [x] E6 时长参数统一（2026-09-09 完成，用户评审：所有场景都要能用 duration
      控制任务执行总时间——总时长与单档时长必须分开）：`duration` 全模式统一为
      **整场总时长**（单档直传 / 多档均分 / probe 轮数不可预知 → 看门狗：subprocess
      timeout=duration 到点截断，收集已完成轮次不报错，collect 容错跳过截断损坏
      的产出文件）；新增 `level_duration` 每档秒数（多档直传 / probe = 每轮墙钟
      上限，顺手补上 probe 每轮时长控制缺口——每轮采样数被 SLA 逻辑强制 = 并发×2，
      duration 是控每轮时长的唯一入口）；两键互斥 fail-fast。number 联动：时长
      驱动且未显式配 number → 放大安全上限（原 soak 专属规则扩展到全模式，修复
      非 soak 配 duration 被"先到为准"8 条掐死的隐藏坑）。v1 直配多档下 duration
      语义从每档秒数改为总量均分（无外部使用方，直接改）。
- [x] E7 locust 适配器（2026-09-09 完成，backlog 既有条目转正，用户终态需求：
      通用 HTTP/分布式备选）：`engine: locust`，复用同一 perf 配置段（同键同
      语义）；probe 明确报错不支持；必配时长键（locust 无请求数上限语义，
      number 忽略）；新增 spawn_rate。每档独立 `locust --headless -u/-r/-t
      --csv` 子进程；locustfile 由适配器生成（SSE 逐块读首包计时 TTFT，自定义
      TTFT 事件进统计，不污染延迟统计）；collect 解析 `_stats.csv`/`_failures.csv`
      → 指标行 `dataset="locust:u<N>"`（命名与 evalscope-perf 对齐趋势可比）+
      错误明细（错误文本×次数）进 failures。pyproject extras `locust`。

关键决策：

- **引擎选型**（用户评审挑战后修正）：原计划自研引擎扩展被否——客户端能力
  会污染测量结果；evalscope perf 与 locust 两候选对比后「两者都要，perf 先」：
  perf 零新依赖（平台已依赖 evalscope）+ LLM 原生指标 + 梯度/warmup/开环限速
  内置；locust 后置备选（通用 HTTP/极端规模分布式）
- 独立引擎名 `engine: evalscope-perf`（一引擎一适配器的 registry 惯例），
  而非 EvalscopeAdapter 内加 mode 分支——基准与压测的 build_config/run/collect
  逻辑完全不同
- 判定口径：可选阈值 + 记录（性能绝对阈值换环境即失效；基线回归对比后置 P1）
- overall 行接受 run_tool_suite 的跨档加权平均（复用链路；语义一般但 CLI
  可读），不特判
- 负载模型 P0 为固定 prompt；平台数据集引用（dataset 透传）后置

验证方式：离线端到端（fake OpenAI 端点跑真实 evalscope perf 子进程）+
真实 Ollama 端到端 `aite run -c examples/perf_evalscope.yaml`。

### 模块 F：前端任务编排与压测大屏（2026-09-09 立项，需求已对齐未实施）

用户终态需求（前端阶段核心场景）：创建压测任务表单——指定任务参数、选择测试
工具（evalscope-perf / locust）、并发模式/QPS 模式、基准/爬坡等模式、自定义
模型参数、选择数据集**或由框架自动生成固定输入输出**；执行中有大屏曲线——
TTFT / TPOT / 吞吐量 / 响应时间 / 响应码等关键指标实时曲线；请求报错统计
展示（哪些错误码、各多少个、是什么错）。

- [ ] F1 任务创建表单（前端后置，用户指示接口优先）：引擎选择器
      （`GET /tools/engines`）+ 场景/数据集选择器（`GET /tools/benchmarks`）+
      模式/时长/负载/模型参数表单 → 组装 suite config 走 `POST /runs`
      （四域路由已通，本项纯前端）
- [x] F2 压测时序数据层（2026-09-09 完成，前端大屏后置）：两适配器统一产出
      `timeseries.json`（work_dir 下；`GET /runs/{id}/timeseries` 读取）——
      evalscope-perf 从各档 benchmark_data.db 逐请求明细事后聚合（5s 桶
      可配，TTFT/延迟分位线性插值、rps/tok_s/errors）；locust 加
      `--csv-full-history` 原生 1s 打点透传。**顺带修口径 bug**：locust
      Aggregated 行被 POST+TTFT 双事件污染，终局指标改取 POST 行。
      产出经 `test_run.details` 落库（老库轻迁移补列），跨进程可找回
- [x] F3 负载自动生成（2026-09-09 完成）：`perf.synthetic: {count,
      prompt_length}` 与 dataset/prompt 三选一；确定性模板生成固定输入
      （输入一致 → 曲线可比），两适配器共用 generate_synthetic_prompts

### 模块 G：执行机纳管（一期 2026-09-11 完成）

形态经用户对齐：**拉取式 agent**（出站 HTTP 零入站端口）+ **结果 HTTP 回传**
（SQLite 兼容，不要求共享数据库）+ 一期压测域白名单（协议与域解耦）。

- [x] G1a 契约与地基：agent_node/agent_task 表；run_tool_suite 拆分
      execute_tool（纯执行）/ persist_tool_result（纯入库，支持复用 run_id）
- [x] G1b master：/agents 注册（重名接管）/心跳即派发（响应携带任务，免独立
      拉取端点）/回传入库（master 重建 reports/agent/<run> 目录，统一入库链路
      与大屏端点照常）/懒式 stale 回收（30s 心跳超时，状态机闭合）
- [x] G1c agent：aite agent serve——心跳后台线程 + 执行主循环（长任务期间
      心跳不中断防误判 stale）；引擎白名单双保险
- [x] G1d 入口与交付物：aite run --agent <节点> 派发轮询；
      deploy/agent.Dockerfile 分层镜像（base=locust / with-perf=evalscope）；
      README 执行机纳管章节
- [ ] G2 二期：标签调度与负载选择、locust 原生 master/worker 分布式通道、
      任务重排队/重试策略、执行机容量与并发上限

### 模块 H：性能数据归档（2026-09-09 立项，需求已对齐未实施）

- [ ] 归档策略：run 级冷热分层（活跃 run 在库、历史归档）、原始产出
      （benchmark_summary/percentile/明细）压缩归档与索引、按 run/时间/模型
      检索恢复；存储清理策略（backlog 既有项并入）

### 模块 I：可靠性 chaos 框架（骨架 2026-09-11 完成）

**调研结论（2026-09-11）**：网络层集成 Toxiproxy（用户态跨平台无 root，
Shopify 生产验证）；进程/资源层集成 ChaosBlade（CNCF Sandbox，v1.8.0 活跃，
单机 CLI 无需 K8s，Linux 执行机）；Chaos Mesh / Litmus 强依赖 K8s 排除；
Pumba 留容器场景二期。**自研部分 = aite 语义层编排**（开源工具均不提供）。

- [x] I1 骨架：FaultSpec 故障声明（injector/kind/target/params）+ 注入器
      注册表（register_injector，开闭点：新故障类型 = 新注入器文件）
- [x] I2 注入（一期两类）：toxiproxy（latency/timeout/bandwidth）、
      process（kill/suspend，psutil 懒加载）；ChaosBlade 包装二期
- [x] I3 检测：Injector.verify 生效确认契约（注入失败与无效应区分，
      verify 失败回滚已注入并抛 ChaosVerifyError）
- [x] I4 恢复：controller finally 兜底逆序恢复（幂等），恢复失败显式
      recover_failed 事件告警（防泄漏需人工跟进）
- [x] I5 观测：FaultEvent 时间线（注入/生效/恢复时间戳）进 run
      details.chaos_events，与 timeseries 曲线叠加（大屏 F1 画故障窗口）
- [x] I6a 发现层一期（2026-09-16）：aite/chaos/discovery.py——节点名/IP/
      容器名/容器IP/进程名 大小写不敏感子串模糊过滤 → 节点>容器>进程 树视图
      （psutil 本机进程 + docker CLI 子进程 JSON 契约 + docker top 容器内进程，
      全部懒加载优雅降级）；REST GET /chaos/targets + 武器目录 /chaos/weapons；
      process 注入器升级名称模糊定位（排除 aite 自身防误杀，多命中取 pid
      最小并告警）；pyproject 新增 chaos extra（psutil）
- [x] I6b 观测层一期（2026-09-16）：aite/chaos/observer.py——背景推理探针
      流式打点（interval 缺省 5s，pass=200 且内容非空）+ RTO 判定（last_fail
      → 恢复后首个 pass，精度 ±间隔；post_window 恢复后补测窗口保证可测）+
      observe_under_chaos 编排（run_with_chaos 复用）；REST POST /chaos/observe
      （duration ≤600s 防长请求占死连接）
- [ ] I6 二期剩余：ChaosBlade 注入器（进程 kill/CPU/内存/磁盘，Linux 执行机）、
      故障编排 DSL（时序/组合/按阶段注入）、agent 分布式发现与注入（贴近被测
      系统，跨节点拓扑聚合）、MTTR 自动计算报告

### 模块 J：性能数据集工程（2026-09-16 立项，未实施）

**动机（用户）**：压测的核心负载特征是输入/输出长度分布与前缀缓存命中率，
当前压测直接复用精度域 QA 数据集——输出长度随模型自由漂移（结果不可复现
不可比）、缓存命中只有一个固定档位（synthetic 刻意零命中）、无客户场景概念
（RAG 客户是"长共享前缀 + 短问题"，与 QA 零命中形态的 TTFT/吞吐曲线完全是
两回事）。已存档的数据集治理需求（requirements-datasetgov.md）通篇为精度域，
性能数据集工程是规划空白。

**能力形态**（perf.scenario 配置，产出带长度标注的 load/prompts.txt，两引擎
共用，确定性可复现、可导出场景快照）：

- 语料池（perf.corpus）+ 输入长度分桶采样（input_lengths/weights，token 级）
- 输出长度控制（target/jitter；vLLM 走 min_tokens+ignore_eos 精确定长，
  一般端点退化为区间采样 + "天然长输出"语料话术）
- 前缀共享率（prefix_sharing：0=零命中即现 synthetic 语义，1=同前缀全命中，
  模拟 RAG/Agent 场景）
- 场景快照导出/复用（长度分布 + 命中率 + 到达间隔分布的组合）

- [x] J1 语料池 + 长度分桶采样（2026-09-16 一期：aite/tools/perf_dataset.py
      纯函数生成器——目录/jsonl 语料池切块、input_lengths/weights 桶配比、
      同 seed 逐字节可复现；产物 jsonl 经 perf.dataset 两引擎直接消费）
- [x] J2 输出长度控制（2026-09-16 一期：output_tokens 写元数据 + perf.extra_body
      透传 vLLM {min_tokens, ignore_eos} 运行时定长；一般 OpenAI 兼容端点
      退化为 max_tokens 区间——上游能力边界）
- [x] J3 前缀共享率 + 场景快照（2026-09-16 一期：prefix_sharing/prefix_groups
      同组共享长前缀模拟 RAG KV cache 命中；产物 stats.json 摘要落盘；
      REST POST/GET /perf/datasets + validate 干跑校验供前端对接）

### 模块 K：RAGAS / Agent 评测框架（2026-09-21 完成）

**定位（用户对齐）**：与精度域同一评测管线（engine / reports / 趋势 / 存储全复用），
差异只在指标体系与数据集——RAG 评测走 RAGAS 指标族，Agent 评测走工具调用/轨迹
指标族。平台化定位不变：这是自研引擎保留的「RAG / judge 维度」的体系化补全。

- [x] K1 RAGAS 指标口径对齐：context_precision 升级 RAGAS 秩加权公式
      （Σ precision@k·rel_k / |relevant|；LLM 逐条相关性判定 / token_f1 规则回退）、
      context_recall 升级断言归因（LLM / token 覆盖回退）；新增 answer_correctness
      （metrics/ragas_metrics.py，事实重合 F1 ⊕ 语义相似加权，RAGAS 缺省 0.75/0.25）。
      faithfulness / answer_relevancy 沿用既有实现。五件套均「无 gateway 走确定性
      规则口径、有 gateway 自动升级 LLM 判定」。
- [x] K2 Agent 指标补齐：新增 tool_arg_correctness（期望工具参数要点 vs 实际调用
      参数子串匹配）；tool_call_correctness / agent_efficiency / task_completion /
      trajectory_quality 沿用。
- [x] K3 开源数据集对接（builtin 抽样版 + 上游指针 + 推荐指标，`metrics: auto`
      可用）：RAG——rgb（github:Chenmnicks/RGB，噪声/拒答/整合/反事实子集）、
      crudrag（github:IAAR-Shanghai/CRUD-RAG）、multihoprag（hf:yixuantt/MultiHopRAG）、
      financebench（hf:PatronusAI/financebench）；Agent——gaia（hf:gaia-benchmark/GAIA）、
      xlam_fc（hf:Salesforce/xlam-function-calling-60k）。全量版走 add-hf / hf_hub。
- [x] K4 私域数据集生成器（datasets/gen_domain.py + domain_corpora.py + CLI
      `aite dataset gen-domain`）：金融/政务/法律三域种子语料（公开通用事实性内容）
      → 确定性生成标准 RAG 形状 JSONL（gold 文档 + 同域干扰文档乱序），落
      ./data/datasets + 注册 userstore（带 RAGAS 推荐指标）+ catalog accuracy 条目
      （version=seed 可复现凭证）；与包内置重名显式报错。已实机生成
      finance_private / gov_private / legal_private（各 12 条，seed=42）。
- [x] K5 示例与文档：examples/ragas.yaml（RAGAS 五件套套件）、agent_eval.yaml 补
      tool_arg_correctness 与内置 agent 数据集；dataset-guide 增数据集地图与
      gen-domain 用法。

**真实联调揭出并修复（build_gateway 两处既有缺陷）**：①LLM 需求判定残留硬编码
指标名单（与 Metric.needs_llm 契约双轨、漏新指标，原则 8 违例）→ 改注册表驱动；
②只注册 judge 模型——未配 judge 时 gateway 零模型，faithfulness 等 LLM 指标全数
error（此前从未被真实 run 暴露，单测 MockGateway 无注册语义）→ 无 judge 时注册
target 为缺省自评模型（指标 params.model 可覆盖）。连带加固 judge 数值解析
（_as_int/_as_float：null/字符串计数回退最差分而非 TypeError）。实弹验证：qwen2.5:3b
× legal_private 4 例 RAGAS 五件套全链路（context_precision/context_recall 确认
mode=llm）。教训：集成缝隙的运行时行为离线 mock 测不到，真实联调必须先行（与
locust 首跑同一教训）。

## 基线（已完成）

- [x] pytest 全量 145 用例通过；ruff 零报错（2026-08-28）
- [x] 项目标准确立并写入 CLAUDE.md；基线 git 提交 `a916fc0`（2026-08-28）

## Backlog（原打磨项，v1 后重排）

- Judge pairwise/multi 补完、变体展开上限保护、
  Judge 失败降级、价格表外置、trends/Agent 连接器测试、存储清理策略、
  趋势查询索引。
- 指标按数据集覆盖（metrics_per_dataset）：当前 metrics 为 suite 级全局，
  混合数据集（数学+文本 QA）无法各配各的指标（2026-08-29 用户实际踩到）。
- local 数据集推荐指标：builtin 有 recommended metrics，local/外部导入无。
  （`metrics: auto` 自动填充已于 2026-08-31 实现，见进度记录；local 推荐仍缺）

## 前端体验优化（最后）

- [ ] 核心能力稳定后启动（迁移路径见 web-ui/README.md）

## 进度记录

- 2026-08-28 建立路线图，完成架构盘点（模块地图/执行链路/能力清单见会话记录）。
- 2026-08-28 确立项目标准（架构冻结范围/解耦规则/开发节奏/git 规范），写入 CLAUDE.md；补 .gitignore；基线确认通过（145 passed + ruff clean），执行基线提交。
- 2026-08-28 v1 重定义为按能力模块交付；首个模块"数据集治理子系统"立项（四模块 + 交付物清单），旧打磨项转入 Backlog。进入设计对齐阶段。
- 2026-08-28 数据集治理子系统完成需求对齐（位置/依赖/接口/补齐项均确认，见 requirements-datasetgov.md）后**暂缓实施**；焦点转回测试框架整体关键能力补齐。
- 2026-08-29 新增能力：HF 数据集离线导入（`aite dataset add-hf` + `add_hf_dataset()`，双形态接口标准写入 CLAUDE.md）。外部注册表 `./data/aite_datasets.json` 与内置 BUILTIN_INFO 合并，数据不进包源码。+9 离线单测。
- 2026-08-29 体验修复三连：报告显示 target/datasets 信息（rich/html）、报告目录改为 `模型/数据集/时间_短id` 三层结构。
- 2026-08-30 指标审计结论：规则类指标教科书级可靠；统计类（bleu/rouge_l）结构正确但平滑/BP 细节与标准实现有偏差；tokenize 按空格切分导致**中文场景统计类指标失效**。用户决策：评测终态要业界认可分数 → 立项模块 B（标准后端 + 中文分词 + 对拍验证）。
- 2026-08-31 修复两处健壮性问题：`metrics` 裸字符串被逐字符迭代（用户实际踩到）；未知指标名改为执行前 fail-fast（不再白跑用例）。实现 `metrics: auto`（builtin 推荐指标自动填充，带默认阈值；`auto`/`[auto]` 两种写法，无 builtin 数据集时报清晰错误）。161 测试全绿，含本地模型端到端验证。
- 2026-08-31 C-Eval 全集落地：HF `ceval/ceval-exam` 52 科目 val(1346)+dev(260) 共 1606 题转换为离线 `ceval_full`（A/B/C/D 独立列→choices 列表，subject 入 metadata 支持 group_by 分科目统计；test split 答案不公开不取）。转换脚本 scripts/convert_ceval.py（数据本体不入库：CC BY-NC-SA 许可不再分发，用脚本再生成），抽样 10 题 choice_accuracy=0.8 验证通过。注：A-D 多列→choices 的转换模式如再遇到，可并入 add-hf CLI（backlog）。
- 2026-09-01 沉淀《数据集使用指南》docs/dataset-guide.md（在线/离线跑法、add-hf 下载命令、特殊形状转换模式、参数速查），README 文档索引更新。实测并发结论：本地单卡 Ollama 并发 1/8/16 无显著差异（GPU 吞吐瓶颈），云端 API 才有线性加速。
- 2026-09-01 **架构转向：平台化**。用户决策：aite 定位评测平台而非引擎，精度跑分委托 evalscope/opencompass 等专业工具，平台核心 = 数据集管理 + 工具适配 + 结果统一；自研引擎保留作性能/安全/RAG/judge 维度补充；首个接入 evalscope（进程内 API）。模块 B 收缩（对拍取消），模块 C（evalscope 适配器）立项。同日修复 hf_hub 加载器未注册进 run 链路的 bug（aite/datasets/__init__ 导入时注册）。
- 2026-09-02 工具层支持 `metric_thresholds: auto`：选择题类基准用随机基线默认阈值（mmlu/ceval/cmmlu/arc/hellaswag=0.25，winogrande=0.5），开放生成题（gsm8k 等）不设默认避免误导；显式阈值优先。+3 单测（174 全绿）。
- 2026-09-02 开放工具基准目录接口（C4 部分）：`aite tool benchmarks` CLI + `list_tasks()` Python API，枚举 evalscope 214 个基准的 name/metrics/tags/few_shot_num/subsets/dataset_id（经 BENCHMARK_REGISTRY），指标名归一（evalscope `acc`→报告 `mean_acc`）。数据形状 REST-ready，前端阶段加薄封装即可。+1 单测（175 全绿）。
- 2026-09-03 **模块 D：功能测试（pytest 执行引擎）完成**。suite 写 `engine: pytest` 即用 pytest 跑功能场景（真实调用模型 + 规则断言），复用工具层路由/入库/阈值/趋势链路，不动存储契约。交付：`aite/functional`（harness 插件 + 4 内置场景 14 例：basic_chat / structured_output / tool_calling / long_context）、`aite/tools/pytest_adapter.py`（逐场景 subprocess + junit-xml 收集场景级 pass_rate）、`tools.tests_dir` 自建目录模式、`pip install "aite[functional]"`、examples/functional_pytest.yaml。关键决策与踩坑（conftest confcutdir 陷阱 → 插件模块方案；norecursedirs 对显式目录参数同样生效 → argv 清空兜底；Windows SystemRoot 必须合并 env）见模块 D 条目。+20 单测含离线 fixtures subprocess 端到端（195 全绿）。
- 2026-09-04 功能测试用例数据驱动化（模块 D 补强 D5）：请求体与断言外移到每场景一个 `cases.jsonl`（id + 场景说明 + messages/tools + expect 断言 DSL），内置 4 场景 14 例全部迁移，场景目录零 Python，加用例只加 jsonl 行。harness 增加 `pytest_collect_file` 自定义收集器与 vars 占位符（haystack 确定性长文生成）；适配器场景识别同时接受 cases.jsonl / test_*.py（手写形态保留兼容）。离线覆盖补齐：DSL/加载 23 单测 + fake 模型服务（stdlib http.server）跑通 jsonl 场景收集→请求→断言→pass_rate 全链路。
- 2026-09-04 expect DSL 增加质量类断言（模块 D 补强 D6）：`no_garbled`（乱码）/ `no_leak`（内部标签泄露，extra_patterns 可扩展）/ `no_repeat`（复读），检测口径保守（正常重音字符/HTML/少量"哈哈"均不误报，单测覆盖正反例）；`json: {}` 明确为 JSON 格式正确性断言（空规格=仅解析）。basic_chat/intro 演示组合质量断言。
- 2026-09-05 新增用例级过滤（模块 D 补强 D7）：suite yaml `tools.cases` 数组指定要执行的用例 id（默认全部），单用例问题复现不再需要绕过平台手动配 `AITE_FT_*` 环境变量直跑 pytest。实现为 pytest nodeid 精确定向（`cases.jsonl::<id>`），未知 id 显式报错、无匹配场景自动跳过。+8 单测（含 fake-server 单用例端到端）。
- 2026-09-06 失败详情输出（模块 D 补强 D8）：失败用例在 CLI 汇总表后直接打印「断言 → 输入 → 输出」（同样进 junit-xml 与日志），逐项截断预算控制体积（输入头尾各 200/输出 300/参数 120，每例 <1KB，CLI 最多 20 例）。+7 单测；踩坑（断言重写污染 str(exc)、pytest.Failed 非公开、异常链断链）见 D8 条目。
- 2026-09-06 沉淀《功能测试架构说明》docs/functional-testing.md：harness 关键类与方法详解（模型访问/数据驱动/收集器/失败详情四块）、任务下发全时序（主进程编排 → argv+env 下发 → 子进程收集/执行 → junit 回传 → 入库）、进程模型与退出码语义、两个执行时刻（收集期 vs 执行期）、设计决策记录（confcutdir/env 注入/junit 通道/nodeid/保守口径等 9 项）、数据契约速查与维护指引。README 文档索引更新。
- 2026-09-07 API 契约域 9 场景 38 例（模块 D 补强 D9）：功能测试从「模型能力」扩展到「OpenAI 兼容层协议正确性」，严格按规范断言。harness 扩展：预期错误模式/SSE 流式/请求构造控制/响应体断言（数字索引+数组多断言）。真实 Ollama 端到端实测 9 例 FAIL 均为真实规范差异（n 忽略/参数校验缺失/超上下文截断/tool_choice 支持不完整等，desc 已标注）——该域的价值就是显性暴露服务端兼容性差异。+27 单测（新断言键正反例、SSE 解析、预期错误模式、fake server e2e）。
- 2026-09-07 API 契约域场景整合（用户评审反馈：分得太细）：9 细场景合并为 4 大场景（38 例不变，用例 id 不变）——api_params（请求侧 22 例：参数校验+可选参数+json_mode+tool_choice+输入鲁棒性）、api_stream（流式 4）、api_limits（输出侧 7：限制+长输出）、api_shape（响应体契约 5）。口径：按请求侧/流式/输出侧/响应侧划分，场景粒度=一个 pass_rate 汇总主题。注意：历史 run 中的旧场景名（api_optional_params 等）不再产出新行，趋势对比按新场景名重建。
- 2026-09-08 **模块 E：性能压测（evalscope-perf 适配器）完成**。引擎选型经用户挑战修正：压测客户端不自研（客户端能力会污染测量），「两者都要，perf 先」——evalscope perf 打头阵（零新依赖、LLM 原生 TTFT/TPOT/ITL/吞吐分档、并发梯度/warmup/开环限速内置），locust 后置备选。交付：`aite/tools/perf_adapter.py`（perf yaml 段 → CLI 参数映射 / subprocess 执行 / benchmark_summary+percentile 解析 → 每档每指标一行 perf:c<并发> 入库）、examples/perf_evalscope.yaml、README 性能压测章节。+16 单测含离线端到端（真实 evalscope perf 子进程打 fake OpenAI 端点全链路 6.4s 跑通）。踩坑：① evalscope 包无 `__main__`（CLI 是 console script，须 shutil.which 定位子进程）；② `--number` 与 `--parallel` 必须等长（标量自动复制对齐）；③ **工具层阈值方向**——`passed >= 硬编码`对延迟/错误率类"越低越好"指标是反的（自研引擎本有 higher_is_better 语义，工具层一直没接；evalscope 全是准确率类未暴露，perf 一上就露馅），ToolMetricRow 加方向字段（默认 True 向后兼容）+ overall 行透传。
- 2026-09-08 模块 E 补强 E4+E5（用户评审：固定提示词不真实、缺压测模式与时长配置）：负载层数据集驱动（perf.dataset 导出逐行 prompt + max_tokens 区间采样）+ 模式层四选一（baseline 基准/ramp 爬坡/probe 摸高 SLA 二分搜索/soak 长稳 duration 驱动）；v1 直配写法保留兼容。collect 统一 glob 两种产出布局（普通 parallel_X_number_Y 与摸高 sla_tuning/sla_parallel_N_run_i）按并发聚合。+14 单测（模式映射/ramp 展开/sla 构造/导出/三场景离线 e2e）。中途熔断上游不支持，记 backlog。
- 2026-09-09 模块 E 补强 E6+E7 + 工具域 REST（C4b 切片）：①时长参数统一（用户评审：所有场景都要能用 duration 控制总时长，与单档时长分开）——duration 全模式 = 整场总时长（单档直传/多档均分/probe 看门狗截断收已完成轮次），新增 level_duration 每档秒数（probe = 每轮墙钟上限），两键互斥；number 联动扩展到全模式（时长驱动未显式配 → 放大安全上限，修复非 soak 配 duration 被 8 条先到为准掐死的坑）。②locust 适配器转正（engine: locust，复用 perf 段，probe 报错不支持，spawn_rate 新增，指标命名与 evalscope-perf 对齐，_failures.csv 错误明细进 failures）。③工具域 REST：GET /tools/engines + /tools/benchmarks（前端建任务表单数据源；任务执行复用 POST /runs + SSE）。立项模块 F（前端任务编排与大屏）/G（执行机纳管）/H（性能数据归档）/I（可靠性 chaos 框架），需求已对齐未实施。+16 单测（E6 时长语义/看门狗/截断容错）+16 单测（locust build_config/负载落盘/CSV 解析/执行守卫）+4 web 冒烟（322 全绿）。
- 2026-09-09 接口完善（用户指示：前端后置、接口优先；审计发现四域里只有自研引擎域的 CRUD/运行真实可用——SuiteConfig extra=forbid 把工具域 suite 挡死）：①POST /suites 与 POST /runs 补 engine 路由（工具域走 parse_tool_suite 轻 fail-fast，运行侧新增 _execute_tool_run 与 CLI 同源）；②DELETE /suites 实现（501 转正，run.suite_id 置 NULL 保留审计）；③test_run 加 details JSON 列（产出目录/报告文件/失败明细落库，老库 PRAGMA 轻迁移补列——顺带修复迁移代码路径首次执行暴露的 async inspect 错误）；④压测时序数据层（F2）：timeseries.json 统一形状（evalscope-perf 逐请求 db 事后 5s 桶聚合 / locust --csv-full-history 原生 1s），GET /runs/{id}/timeseries 端点；修 locust Aggregated 口径 bug（改取 POST 行）；⑤F3 synthetic 合成负载（三选一，确定性生成）。+10 单测（假工具 e2e 路由/timeseries/迁移/口径/时序聚合），332 全绿。
## 架构整改（2026-09-09 全码评审产出，P0 已完成）

> 七原则 + 补充原则已写入 CLAUDE.md「架构设计原则」节，为代码评审基线。
> 评审证据与问题清单见当日会话记录；以下为待落地项。

- [x] P1-1 service 层引入（2026-09-10）：`aite/service/run_service.py`——
      resolve_suite（engine 路由 + 校验，CLI/POST /suites/POST /runs 三处收口）、
      execute_suite（四域统一执行）；build_repository 上移 aite.storage；
      repo_session contextmanager 消除 CLI 五处样板；run_tool_suite 拆出
      build_overall_rows 聚合纯函数
- [x] P1-2 适配器 DRY 收敛（2026-09-10）：`aite/tools/perf_common.py`
      （expand_ramp/resolve_load/resolve_mode_parallel/resolve_durations/
      export_prompts 转公开/常量）+ `aite/tools/subproc.py`（run_tool_subprocess/
      which_or_fail/glob_report_files，pytest/perf/locust/evalscope 四适配器切换）
- [x] P1-3 超长函数拆分（2026-09-10）：4 个 >80 行函数全部拆解达标
      （perf build_config 164→<40、perf collect 111→<50、locust collect
      123→<60、engine _evaluate 91→<50）；新增 scripts/audit_func_len.py
      AST 审计脚本（全仓 ≤80 行，提交前可跑）
- [x] P2-1 config.py（2026-09-10）：DB URL ×5 / reports ×4 / 端口 / AITE_DB
      收口；app.py 版本号改用 aite.__version__
- [x] P2-2 契约补齐（2026-09-10）：collect 进 ToolAdapter ABC（带默认
      NotImplementedError 实现——编排方只调 run，collect 为 run 的收尾契约）；
      delete_suite 进 Repository 接口（契约同步演进，原则 10）；新增
      unregister_tool，测试不再 pop 私有 _TOOLS dict
- [x] P2-3 路由瘦身（2026-09-10）：新增 aite/service/web_service.py——cases
      的 JSONL 解析/转换（loader._to_cases 转公开 to_cases）、manual_eval 候选
      筛选、tasks 统计聚合、timeseries 磁盘读取全部下沉，四个路由瘦身为
      「校验 → service → 返回」；context.build_gateway 反感知——Metric 契约
      新增 needs_llm 类属性（LLMMetricMixin 经 __init_subclass__ 混入即标记，
      规避 MRO 遮蔽），core 硬编码指标名单删除，新增 LLM 型指标零修改此处；
      harness Case.check 按断言域拆三个方法（内容/结构/工具，保持可叠加语义
      与顺序——非类型分派，不做 dict 注册避免过度设计）
- 2026-09-10 架构全码评审（用户给定七原则）+ P0 整改完成：①原则入约——CLAUDE.md 新增「架构设计原则」节（七原则 + 5 条补充：单一事实来源/错误显式化/契约同步演进/时间 UTC 统一/外部边界纪律）与「测试策略」节（分域选测不默认全量）；②P0 修复——SSE 频道错位（stream_run 按 run_id 反查 bg_task 订阅正确频道，历史 run 回存态终态帧；此前订阅 runs:{run_id} 永无事件）、bg_task 状态机闭合（startup 标 stale + shutdown cancel 后回写 error，repo 新增 get_bg_task_by_run/mark_stale_bg_tasks）、harness docstring 非法转义 SyntaxWarning、_parse_dt 静默吞坏时间戳改显式告警；③评审修正——engine 成本熔断 check-then-act 是在途成本不可见的固有软预算近似（+= 均在同步段原子），加注释说明不改逻辑。P1/P2 整改项立「架构整改」节待认领。+4 测试（stale 标记/stream 解析/历史 run/404），分域验证 91 项全绿。
- 2026-09-10 P1 架构整改完成（三个独立提交）：①P1-2+P1-3——tools/perf_common.py 与 tools/subproc.py 落地（DRY 收敛 + 私有名转公开），4 个 >80 行函数全部拆解（build_config/_probe_args/_resolve_number/_assemble_args；collect 拆分组聚合/时序/报告收集；engine _evaluate 拆 _eval_entry/_run_judges），scripts/audit_func_len.py AST 审计入基线；②P1-1+P2-1——aite/service/run_service.py（resolve_suite/execute_suite 统一 CLI 与 Web 编排），aite/config.py 默认值收口（DB URL ×5/reports ×4/端口/版本号），storage.build_repository 上移 + repo_session 样板收口。踩坑：heredoc 写码时 
 转义会被吃掉（三次断行 SyntaxError），多行 Python 代码修改改用 Edit 工具。337 全绿 + ruff 干净 + 函数长度审计达标。
- 2026-09-10 P2 架构整改完成：①契约补齐——collect 以默认实现进 ToolAdapter ABC（编排方只调 run，collect 是 run 收尾步骤故不做 @abstractmethod），delete_suite 进 Repository 接口，unregister_tool 替代测试 pop 私有 dict；②路由瘦身——web_service.py 落地，四个路由的解析/筛选/聚合/文件 IO 下沉，_to_cases 转公开；③build_gateway 反感知——Metric 契约加 needs_llm 类属性，LLMMetricMixin 用 __init_subclass__ 混入即标记（踩坑：显式 ClassVar 在 MRO 中被前置的 Metric 基类 False 遮蔽，6 个 LLM 指标漏标，__init_subclass__ 直接写子类解决），删除 core 硬编码名单；④Case.check 按断言域拆三方法——评审后判断该链是可叠加断言（非类型分派），dict 注册属过度设计，按域分组保序即可。337 全绿 + ruff 干净 + 函数审计达标。架构整改全部完成。
- 2026-09-11 模块 G 一期完成（执行机纳管）：拉取式 agent（aite agent serve，心跳后台线程 + 执行主循环防长任务误判 stale）+ master 心跳即派发 + HTTP 回传统一入库（persist_tool_result 复用，master 侧重建 reports/agent/<run> 目录使大屏时序端点照常）；aite run --agent 派发入口；deploy/agent.Dockerfile 分层镜像；+10 测试（347 全绿）。二期（G2）：标签调度/locust 原生分布式/重试策略。
- 2026-09-11 模块 I 骨架完成：chaos 工具调研（Toxiproxy+ChaosBlade 可集成，Mesh/Litmus K8s 排除）+ aite/chaos 骨架——FaultSpec/FaultHandle/FaultEvent 契约、注入器注册表（toxiproxy 网络 latency/timeout/bandwidth + process kill/suspend）、run_with_chaos 编排（注入→生效确认→业务→finally 兜底逆序恢复，verify 失败回滚、恢复失败显式告警防泄漏）；REST 集成 POST /runs chaos 段包裹本地执行，agent 派发 suite_raw.chaos 在执行机侧注入；+13 测试（编排生命周期/回滚/兜底/mock toxiproxy）。二期 I6：ChaosBlade 注入器/编排 DSL/分布式注入/MTTR 报告。
- 2026-09-11 QPS 模式双引擎落地（前端「并发模式/QPS 模式」表单的后端侧）：①evalscope-perf `perf.rate`——开环固定到达率（`--open-loop --rate` 梯度替代 parallel sweep，并发无界过载真实显现；与 parallel/mode 互斥；开环 number 语义为每档请求数上限且自动放大防秒停；collect 开环档位标签 perf:q<rate> 从 summary Request Rate 字段读取，修复 Concurrency=-1 多档合并问题）；②locust `perf.qps`——闭环封顶（locustfile wait_time = constant_pacing(1/qps, constant_total_pacing=True)，服务端拖慢则 QPS 下降，user 数退化为并发上限）。语义差异即测试价值：开环施压 vs 闭环限速可对比。+8 测试（372 全绿）。
- 2026-09-12 首次 locust 真实端到端联调（examples/locust.yaml，11 档 ramp）暴露并确认 a7974f2 的 locustfile 类体断裂 P0：ChatUser 无任务 → 实跑 0 请求 → 全档 stats.csv 无 POST/Aggregated 行（log 实锤 No tasks defined on ChatUser）。该 bug 已于 c2445e5 修复（单类 + _wait_time()，AST 结构测试防回归）。真实产出另暴露一处格式假设错误：stats.csv 的 Aggregated 行 Type 列为空字符串而非 'Aggregated'，collect 兜底匹配改为按 Name 列识别。教训：生成产物的运行时行为离线 mock 测不到（locust 未装），真实联调必须先行。
- 2026-09-12 平均输入/输出长度统计双引擎落地：evalscope-perf 从 summary 的 Avg Input/Avg Output Tokens 直接映射（avg_input_tokens/avg_output_tokens 档级行）；locust 由 tokens.jsonl 聚合（usage 可用时精确，chunk 近似模式无输入长度不产出该指标），locust:total 行与 output_tps 同源。两引擎指标清单完全对齐（ttft/latency/itl/tpot/qps/output_tps/长度/error_rate）。
- 2026-09-13 两引擎指标全面对齐（F1 前置收口）：①evalscope-perf 补 tpot_p95_ms/itl_p95_ms（percentile json 的 TPOT/ITL 列一直存在，此前仅映射 TTFT/Latency 两列）；②时序点统一形状 {t, users, ttft_avg/p95, latency_avg/p95, tpot_avg/p95, itl_avg, rps, tok_s, errors}——evalscope 从 benchmark_data.db 的 time_per_output_token/inter_token_latencies 列聚合（itl 为摊平均值，p95 二期），locust 从 stats_history 的 TPOT/ITL 行（50%→avg、95%→p95）；③README 落两引擎指标对齐表为 F1 权威依据。+3 测试（373 全绿）。
- 2026-09-13 真实联调第二轮：修复时序秒桶结构漂移 KeyError（token 完成秒与 history 行不重合时，token 合并路径的 setdefault 用旧桶结构缺 tpot/itl 键——抽 _new_bucket 工厂消除两份字面量结构；回归测试覆盖 token-only 秒）。教训固化：同一数据结构的字面量在两处出现必然漂移，抽工厂；heredoc 写码的转义断裂问题反复出现，模板类修改一律 Edit 工具。374 全绿。
- 2026-09-13 双引擎受控对比实验（同端点本地 Ollama qwen2.5:3b / 同负载 business_qa / 同并发 4 / 同模式 baseline / 同 60s 时长）发现并修复 locust 重大观测缺陷：client.post 默认 stream=False——响应完整缓冲后一次读取，一切到达间隔为 0（ITL/TPOT 全废、tok/s 爆炸 21 万、TTFT≈延迟），而 evalscope（aiohttp）真流式测到 ITL/TPOT 23.4ms。修复：locustfile stream=True + LATENCY 自定义事件（stream 下 POST 原生统计退化为 TTFB 口径，端到端延迟经事件上报）+ stream_options include_usage（Ollama 流式回 usage，avg_input_tokens 从缺失变精确）。修复后对齐（evalscope/locust）：长度 ±1%、延迟 avg ±14%、TPOT p95 +15%、tok/s -14%、QPS -16%、TTFT p95 +62%（小样本长尾+顺序效应）、token 时延族 +18~27%（顺序效应与 ITL 噪声过滤同向叠加）——核心计量一致，无计点错误级缺陷；严格结论需交替多轮消除顺序效应（后续可选）。对比配置留存 reports/compare/。
- 2026-09-14 locust 真实联调第三轮：修复 _stream_metrics/_json_metrics 定义在 ChatUser 类外（模块级带 self 参数）导致 self 调用 AttributeError、实跑 0 请求——移入类内。教训升级：生成产物的结构校验从「方法在类内」扩展到「self.X 调用必须可解析」（AST 属性调用集合 ⊆ 类方法集 ∪ 基类属性）。372 全绿。
- 2026-09-14 locust 大屏链路四项收口（代码评审发现）：①perf.qps 静默失效修复——build_config 校验存储 qps、locustfile 读取 AITE_LOCUST_QPS，但 _exec 组装 base_env 从未注入，限速从未生效（单测只断言模板文本含变量名，注入链路无覆盖）→ 按 cfg["qps"] 条件注入 + fake_subprocess 断言注入值；②combined 阶梯曲线恢复——f6f6d64 jsonl 单通道重构遗失了 b1a3e93 经 stats_history.csv 的 combined 通道，大屏 ramp 多档只渲染首档 → 改从 jsonl 重建：各档点复制后按墙钟 ts（各档进程同源时钟）排序、t 重定基全场首秒，无档间偏移近似且与 series 点同源；③series 补 label 字段（locust:u<users>，与 perf 的 perf:c<并发> 对齐）；④重复并发档（如 [4,4] 稳定性复跑）产物路径消歧——metrics jsonl/csv/html 同前缀互相覆盖、jsonl 被 append 合并 → 全档加执行序后缀 locust_u<users>_<序>，collect 解析为 label u<users>#<序>。教训：cfg → env → 子进程读取的注入链三段都要断言，只测模板文本拦不住断链。+4 测试（372 全绿 + ruff 干净）；README 落「压测曲线图（大屏预览）」章节（四数据源 + 注意事项），/runs/{id}/timeseries 端点 docstring 补 combined 与 tpot/itl/ts 字段。
- 2026-09-15 双引擎公平对比联调收口（用户实测 deepseek-flash 同端点/同 8 并发/同 120s/同 max_tokens）：①perf_evalscope 示例显式 number: 4 与 duration 先到为准，实际 4 请求 1.4s 即停——曲线只有 1 个桶点（大屏"没数据"）+ 指标与 locust 满载 118s 不可比；去掉 number 后时长驱动生效（number 自动放大 1e6 上限）。②TPOT 对齐验证（公平负载下 6.6 vs 6.7ms，公式两侧同为 (latency−TTFT)/(tokens−1)）。③ITL 差 3 倍根因 = 采集口径：locust requests iter_lines 缺省 512B 缓冲把多个 SSE 事件合批（采样密度 0.35、均值 20.2ms 只反映脉冲间隙），evalscope aiohttp 逐网络包全量入样（密度 1.00、p50=0 真实反映 DeepSeek 脉冲式 flush）→ 模板改 iter_lines(chunk_size=1) 逐字节读 + 取消 <1ms 过滤，全量入样对齐 evalscope。④取消过滤后网关缓冲端点会产生 ITL≈0 假优数据 → 补上 README 已承诺但 jsonl 重构时丢失的守卫：_buffered_share（TTFT≥0.95×延迟占比 ≥90% 判假流式）→ 告警 + TPOT/ITL/gen_ms 就地置空，指标行与时序同步拒绝入库，TTFT 保留（退化为端到端口径）。+1 测试（372 全绿 + ruff 干净）。教训：测量口径差异要落到采样密度等可量化指标上对比（itl_n/tokens），而不是只看均值；README 承诺的行为要有断言兜着，重构会静默丢守卫。
- 2026-09-15 大屏版式定稿（用户需求：统计结果表置顶一眼看结论 + 指标族分图不混轴）：①timeseries.json 契约新增 `summaries` 字段（两引擎 collect 统一经 serialize_summaries 产出：overall 行 dataset=None 与 CLI 汇总表同源在前 + 档级行在后，字段 metric/dataset/value/count/threshold/passed/higher_is_better）——统计表数据随曲线数据同文件/同端点分发，旧版文件缺字段时大屏优雅降级提示重跑；②预览大屏重排版：顶部全宽统计结果表（overall 指标/值/阈值/PASS-FAIL 徽章/样本数 + 档级明细），下方八图按指标族拆分——TTFT/延迟/TPOT/ITL 各自 avg+p95 同族共图（实线 avg 虚线 p95），RPS、tok_s、并发阶梯、错误统计各自独立成图，废除原「TPOT+ITL 混图」「延迟+RPS 双轴混图」；chaos 故障阴影 markArea 覆盖全部曲线图。+2 断言（两引擎 collect 的 summaries 契约，372 全绿 + ruff 含 scripts 干净）；/runs/{id}/timeseries 端点 docstring 同步补 summaries 形状。
- 2026-09-16 ITL 修复后双引擎 2×2 交叉对比（deepseek-flash/8 并发/120s/max_tokens 1024；evalscope 22:56 与 23:43 两轮、locust 新模板 23:17 与 00:32 两轮，时间交替排除单调时段漂移）：①测量口径全部对齐——ITL 采样密度两侧均 1.00（iter_lines(chunk_size=1) 修复生效，修复前 0.35）、TPOT 8.3 vs 6.5、基础 TTFT 同量级（locust 首 8 请求均值 156ms vs evalscope p50 107ms）；②剩余差距为结构性到达形态差：locust 同步波次（完成峰值 6/s 聚堆、尖峰周期 5.5s≈请求时长）触发服务端排队/限流——13% 请求 TTFT>1s（p99 5050ms）→ QPS -30%、生成段全线 +28~30%；evalscope 平滑到达（完成峰值恒 5/s、231 请求 0 个 TTFT>1s、p99 247ms）不触发。两引擎各自两轮高度自洽（evalscope QPS 1.76/1.77、locust 1.18/1.28），排除时段效应，锁定为闭环起跑同相 + 服务端批处理正反馈锁死波次。③验证路径（已有能力零代码）：spawn_rate 调小错相启动，或 perf.qps 闭环限速（constant_total_pacing 全局均匀到达）。根治 = 到达抖动（wait_time 随机化），未实施待用户拍板。④模块 J 性能数据集工程立项（见模块 J 节）。

- 2026-09-16 三项压测能力落地（用户需求：波次修复 + 模块 J 一期 + 流量场景模拟）：①locust 到达抖动与到达模型——perf.jitter（每请求 U(0,jitter) 打散同步波次，治 2026-09-16 对比发现的 TTFT 尖峰/吞吐虚低）+ perf.arrival 到达模型（poisson 泊松到达 / spike 浪涌周期切换 / custom 自定义 QPS 阶梯曲线模拟现网流量），模板侧 _Pacer 全局节拍器（gevent 信号量 + admission 时刻集中算间隔，泊松逐时隙指数随机 = 真全局泊松过程；并发数退化为在途上限）；perf.extra_body 请求体扩展透传（vLLM ignore_eos/min_tokens 定长输出）；evalscope-perf 配 arrival 即报错（上游仅支持固定速率）。②模块 J 一期完成：aite/tools/perf_dataset.py 场景数据集生成器（目录/jsonl 语料池切块、输入长度分桶+权重配比、prefix_sharing/prefix_groups 同组共享长前缀模拟 RAG KV cache 命中、同 seed 逐字节可复现、产物 jsonl+stats.json）+ python -m 脚本入口 + REST 三端点（POST/GET /perf/datasets、validate 干跑校验，config.DEFAULT_PERFDATA_DIR 收口产物目录）。+14 测试（生成器 10 + 适配器到达模型/注入 3 + evalscope 守卫 1 + API 集成 4，389 全绿 + ruff 含 scripts 干净）。教训：负载形态（到达分布/长度分布/缓存命中）是与并发数同级的压测自变量，压测工具必须把三者都做成一等配置。

- 2026-09-16 用户复测仍见差距→复盘定责：①该轮 suite 未配 jitter（示例与用户 yaml 未同步），修复未激活——改 jitter 缺省 0.2s（显式 0 关闭，适配器单一生成源，env 恒注入）；②重审模板发现 keep-alive 真 bug：流式循环遇 [DONE] 提前 break，响应未读完 → urllib3 弃用连接 → 每请求重付 TCP+TLS 握手（TTFT p50 平抬 ~100ms，恰为 locust 191 vs evalscope 107 的量级）+ 服务端连接 churn 或加剧排队——改为 continue 读完至流自然结束；③补 Accept-Encoding: identity（requests 缺省 gzip，SSE 过压缩网关会重缓冲；aiohttp 缺省不压缩）。三修复均零配置生效。教训：跨引擎对比异常时，先核对"修复是否真的在该轮生效"，再查新问题；客户端行为差异（连接复用/压缩协商/到达形状）是压测工具自身污染测量的三大来源。
- 2026-09-16 双引擎收敛验证（jitter:0 形态对齐实验 + 同批对照）：①压力形态量化——用每请求起止时刻重建在途并发时间线：evalscope 零思考时间在途钉满（均值 7.37、=8 占比 48%、到达间隔 p50 395ms），locust 带 jitter 0.2 时在途均值仅 5.44 且到达间隔 p50 62ms（补发簇）——"压力形态不同"从猜想变实测。jitter:0（零思考时间）才是与 evalscope 同构的形态；但用户 yaml 未配键仍走缺省 0.2 的那轮反而完全收敛（在途 7.27、QPS 1.64 vs 1.68、TPOT +1.6%、ITL +5%、TTFT>1s 双零）——jitter 缺省 0.2 在 keep-alive 修复后代价仅 ~2%，保留。②收敛后残余：TTFT 全分位一致偏移 +36~47%（p50 差 ~45ms、p95 差 ~85ms）——requests/gevent 客户端栈的每请求固定开销（首包读路径），属口径差而非负载差，记录口径、不再追。③关键教训：同配置同代码的相邻两轮 locust（22:56 vs 23:16）行为完全不同（尖峰 9 vs 0、在途 5.4 vs 7.3）——DeepSeek 云端存在显著时段性状态，**任何单轮跨引擎对比都不可靠，交替 A/B/A/B 多轮取中位是云端对比的唯一可信方法**（此前多轮"差距大/收敛了"的反复摆动根源即此）。

- 2026-09-16 三件套落地（用户需求）：①agent 评测集——functional/scenarios/agent_tasks/cases.jsonl 12 例（工具选择与语义区分/参数抽取含中文与订单号精确抽取/多轮续作：工具结果→最终回答、补参二次调用/能力边界拒答 no_tool），全部客观断言（tool/tool_args_contains/no_tool/finish_reason），引擎场景发现零改动即生效（cases.jsonl 即场景）；②发现层 I6a：discovery.py 节点>容器>进程模糊查询树（psutil+docker CLI 子进程 JSON 契约+docker top，全部降级安全）+ REST GET /chaos/targets、/chaos/weapons（注入器 kinds ClassVar 武器目录）+ process 注入器名称模糊定位（排除自身防误杀、多命中取最小 pid 告警）；③观测层 I6b：observer.py 流式推理探针（interval 缺省 5s，200+内容非空=pass）+ RTO 判定（last_fail→恢复后首个 pass，post_window 补测窗口保证可测）+ observe_under_chaos 编排（复用 run_with_chaos）+ REST POST /chaos/observe（duration ≤600s）。+25 测试（数据集走 harness.Case 真实校验器、发现层真实进程自匹配、观测器 httpx.MockTransport 假 SSE、编排 mock 注入器、API 集成，414 全绿 + ruff 干净）。坑：psutil 在 venv 缺失且 pyproject 无声明——补 chaos extra；新增内置场景须同步 test_functional_scenarios.EXPECTED 计数与 test_pytest_adapter 场景名清单（两处硬编码清单）。

- 2026-09-17 evalscope 过夜压测超时复盘：duration 720s 跑在 23:44，~512s 时机器睡眠冻结进程树（日志 4851 行只有 23:44 的 6 行启动与 05:28 的 7 行唤醒），晨间唤醒后看门狗（monotonic 停走）立即判 3600s 超时杀进程——非框架 bug。数据核查：4205 请求中 3353 失败（唤醒后连接重启的失败爆发）、窗口 512/720s 被切断，**产物落盘但已被污染，作废不可出结论**。健壮性补丁：perf._exec 看门狗超时路径新增产物检查——benchmark_summary.json 已完整落盘（典型：睡眠发生在 summary 打印后、进程退出前）→ 显式告警后按完成处理继续 collect（污染数据经 error_rate 阈值显式 FAIL，不静默不丢弃）；无产物维持报错。跨夜压测 checklist：Windows 电源策略睡眠改"从不"/放服务器跑/extra.timeout 按需调整。+1 测试（415 全绿）。

- 2026-09-18 720s 长窗口对比复盘 + 交替对比工具化：①新对（evalscope 23:03 / locust 23:47，44min 间隔）吞吐与生成段稳定收敛（QPS -10%、TPOT +1.6%、ITL +4%），但 TTFT 尾部仍差 3~5 倍（37/1200 >1s）且 **p50 符号翻转（locust 99 < evalscope 119）——推翻前轮"45ms 客户端固定开销"结论**：差距全部是尾部瞬时排队尖峰，非固定开销。②jitter 缺省 0.2 → 0：evalscope 零思考时间在途钉满恒平滑，locust 缺省 0.2 制造在途空洞+补发簇（尖峰 0/9/37 高方差）；缺省 0 = 与 evalscope 形态同构（keep-alive 修复后到达自然错开），jitter 转为突发场景显式选项——注意该 parity 实验此前两次被缺省值劫持未真正跑过。③scripts/compare_perf.py 交替对比工具：--suites 交替驱动 L/E/L/E N 轮 + --runs/--names 分析模式（overall 行跨轮取中位+最大偏差表）——单轮对比不可靠的方法学固化。④工具首跑即暴露 output_tps 同名异口径：evalscope Output Throughput=Σtokens÷墙钟（聚合），locust 旧口径=Σtokens÷Σ生成秒（单流速率≈1000/ITL），比值≈在途并发——locust 对齐墙钟口径；连带更新缓冲守卫范围：墙钟聚合吞吐对缓冲端点仍可测，output_tps 移出拒绝入库清单（仅 TTFT/ITL/TPOT 依赖流式语义）。+1 测试（416 全绿 + ruff 干净）。教训：指标同名≠同口径，对齐要除到分母（墙钟 vs 生成时长）；缺省值会劫持用户的实验意图，parity 关键参数的缺省必须与对照对象同构。

- 2026-09-18 jitter:0 实测揭出逐字节读 CPU 饱和（用户报"TTFT 10 倍"）：720s×8 并发下 locust avg 1275ms vs evalscope 118（10.8×），拆解后 p50/p10 正常（178/97 vs 107/94）、差距全在尾部（30~38% 请求 >1s、最大 12.6s）且 QPS 虚低 32%、在途跌至 5.5——非" TTFT 全面 10 倍"。根因：chunk_size=1 逐字节读，微基准实测 16.7ms vs 0.2ms/请求（80×），真实 requests/urllib3 链路更高，8 路并发把 gevent 单线程 hub 打满 → 首包读取饿死秒级 + 新请求派发延迟。同时修正 09-15 的错误归因：ITL 对齐的功臣是"取消 <1ms 过滤全量入样"（合批 0 间隔与 evalscope ITL p50=0 同为真实到达形态），chunk_size=1 本身是叠加的冗余动作——当时把两个变量的效果记到了一个头上。修复：回退 iter_lines 缺省分块，保留全量入样；回归守卫改断言调用形态缺失（模板注释"勿改 chunk_size=1"提示保留）。教训：两个变量一起改时，收益归因必须单独验证（回退其一若仍对齐即证伪）；"更精细的测量粒度"不是免费的，粒度选择的成本上限是测量行为本身。

- 2026-09-19 原则增补 + 并行构建启动（用户需求：多模块并行构建，先审视原则体系是否够用）：①CLAUDE.md 新增原则 13 并行开发纪律（契约级文件先串行小 PR、共享收口文件段落化只追加、域内自包含）与原则 14 Schema 只增不改（migrations.py additive-only 惯例成文）；②原则 8 单一事实来源扩至口径/阈值方向定义（连修三天的 LOWER_IS_BETTER 漂移与 output_tps 口径错位即证据）；③原则 1 增 asyncio 桥接注脚（_dispatch_to_agent 每 3 秒重建 loop+engine 反例）；④原则 5 去除 config.py「待建」过时标注；⑤「当前阶段」由"前端放最后"改为多域并行构建。模块独立性盘点结论：性能/功能/可靠性/精度/数据集/前端域内代码横向零依赖，冲突面集中在 cli/main.py、service 层、storage、/api/v1 契约四处收口点，按原则 13 协调。

- 2026-09-19 四域独立后端立项（用户对齐：功能/精度/性能/可靠性各自独立任务管理，公共服务层共用）：架构转向——由"统一 bg_task + POST /runs"改为"四域各自任务管理 + 公共服务层"。每域三能力：任务增删改查 / 运行·停止·重试 / 结果展示（域形态）。契约设计稿 docs/domain-backends.md（用户已确认四决策：①保留 stopping 中间态+60s 兜底强转 cancelled；②重试=新任务+retry_of 链不改原记录；③功能/精度结果复用公共 run 存储不建域明细表；④CLI run 同步等待形态）。任务框架代码一份（core/taskkit.py DomainTaskKit + TaskExecutor ABC）数据四份（TaskLifecycleMixin + functional/accuracy/perf/reliability 四张任务表，additive-only）；repository 增 6 个泛型 domain_task 方法（表类作参数）；SSE 频道 {D}:tasks:{id} 复用 EventBus；CLI 工厂 cli/task_cli.py 生成同形七命令。股道：C0 契约 PR（串行）→ C1-C4 四域并行（service/{域}_tasks.py + routers/{域}_tasks.py，文件集互不相交）→ C5 前端切换后旧 /runs//tasks 收缩。过渡期统一链路保留；执行机纳管域任务本期不做，spec 预留 node 字段。

- 2026-09-20 C0 收口 + C0.5 CLI 切片（用户需求：命令行起任务 + 更新文档）：C0 全量 431 绿（顺带修 web/events.py 既有 bug：subscribe 回放历史遇 end 帧不终止，订阅已关闭频道永久挂起——现有代码先订阅后关闭未踩中，TaskKit 测试暴露；连带终态帧先于 end 帧的关闭时序收口）。C0.5：TaskRunContext 增 repo/emit_raw 透传（执行器落库/复用公共存储 + 引擎自带 type 帧原样透传）；service/suite_task.py 套件型共享执行器（spec=suite raw 同构 aite run，复用 execute_suite 管道，result 存执行概要）+ 四域装配文件 + cli/main.py _mount_domain_tasks 挂载（aite {域} tasks … 三层命令，域组下挂 tasks 给域内命令留空间）；reliability 域 ObserveTaskExecutor（spec=POST /chaos/observe body 同构）+ chaos.py 路由 body→cfg 映射下沉 service/reliability_tasks.build_observe_cfg/run_observe（REST 与任务执行器共用，原则 1 编排下沉）。测试 +5（ctx.repo 透传/emit_raw + CliRunner 四命令端到端冒烟走真实 tmp sqlite：create/list/get/run 失败重试链/rm 终态守卫），436 全绿 + ruff 干净 + aite.exe 实机四域 CRUD 冒烟（四表隔离验证）。文档：README 新增「四域独立任务管理（TaskKit）」章节、domain-backends.md §4/§6/§7/§8 与实现对齐（含已知限制：工具域子进程的进程组强杀待 C1-C4 executor.cancel 补强）。

- 2026-09-20 四域实调通（用户需求：先调通四域确认输入输出，再对接公共能力；Ollama qwen2.5:3b 实机）：①reliability ✅ observe 任务 succeeded（3 探针全 pass、probes/rto/summary 结构完整，首探针 19s 为 Ollama 冷加载）；②perf ✅ locust 20s 实压 passed（6 指标 case、timeseries/html/metrics/log 四件套落盘、CLI run exit 0）+ 120s 任务跨进程 stop 验证（stop 进程秒级 cancelled、孤儿 runner 的 expect 守卫防覆盖、产物照常落盘——**意外收获**：run 的 expect_status 条件更新同时防住了重复启动竞态，实测撞上即 TaskStateError）；③functional ✅ pytest 单用例（api_limits/max_tokens_one）passed（junit+log 落盘）；④accuracy ⚠️ 任务链路+错误路径 ✅（exit 1 跟随判定、ModuleNotFoundError 显式化），引擎真实跑分待 evalscope 进程内依赖装入运行环境（代码注释：统一 conda 环境）。实调揭出两 bug 修复：①subproc.which_or_fail 增解释器同目录（venv Scripts/bin）回退——CLI 在未激活 venv 的 shell 里跑，pip 装的 locust.exe 不在 PATH 致误判工具缺失；②CLI run 退出码跟随套件业务判定（result.status=passed 才 0，CI 友好）。新增跨进程停止桥：DomainTaskKit._watch_stop 轮询 DB stopping 状态（2s）触发协作 cancel 并落 cancelled 终态（CLI run 独立进程场景下 stop 真实生效；多写方 expect 守卫竞态安全）。测试 +2（which_or_fail 回退正反用例 + 跨进程停止桥），438 全绿 + ruff 干净。四域输入输出实录沉淀 domain-backends.md §10（I/O 清单表 + 对接公共能力的决策点）。

- 2026-09-20 数据集分类梳理 + 管理契约定稿（用户给四域数据集本质差异→梳理→五决策确认）：§11 讨论稿梳理三类数据集本体差异（功能=行为规约[请求+断言]、精度=题库[问题+标准答案]、性能=流量特征[分布语义无关、生成器产物]），可靠性不设自有数据集（引用性能型做背景流量+故障场景+探针）；四任务判定语义表（二元 pass-fail / 分数+劣化 / SLA 阈值 / RTO）推导两个新能力点（精度基线对比、可靠性复合任务）。用户五决策全按建议：①功能型独立 type + cases.jsonl 原生格式；②基线显式 baseline_ref 缺省回退最近 passed；③可靠性 background=数据集引用+内嵌负载参数并存；④统一 catalog 表（type 列）+ 分类型文件区；⑤内置场景名保留为隐式功能数据集。契约 §12：DatasetCatalogRow 表（type/name/version=seed/source/schema_version/stats/path）、分类型校验器（functional 过 harness.Case 真实校验器）、CLI list/import-functional/show/rm + add-hf 登记为 accuracy + perf 生成自动登记、DatasetConfig 增 ref 引用（与 type+path 互斥）、精度 result.baseline{deltas,degraded}（方向复用 LOWER_IS_BETTER 单一来源）、股道 D0 契约 PR→D1 精度基线→D2 可靠性复合→D3 REST/前端。

- 2026-09-20 D0 数据集目录契约 PR（§12.6 股道一）：DatasetCatalogRow（type/name 唯一约束 + version=seed 可复现凭证 + stats 类型相关元数据）+ repository 五方法（upsert 按 (type,name) 幂等 / get / find / list[type] / delete，接口同步演进）+ datasets/catalog.py（register_entry 类型白名单 + resolve_dataset_ref 三级解析 id→type:name→唯一 name，跨类型重名显式报错指引用 type:name）+ functional/ingest.py（逐行 harness.Case 真实校验器，坏行显式报错不静默；stats 记 case 数+断言键分布；文件区 ./data/datasets/functional/<名>/cases.jsonl——天然构成 pytest 适配器场景根，套件 tests_dir 指过去零改动消费）+ dataset CLI 升级（list[--type] catalog∪builtin 合并、import-functional、show catalog 优先回退 builtin、rm 文件保留、add-hf 自动登记 accuracy 型）+ perfdata REST 生成自动登记 perf 型（场景名指向最新产物，seed=version）+ DatasetConfig.ref 字段 + engine._load_cases 引用解析钩子（ref→local 文件数据集）。测试 +8（catalog CRUD/未知类型拒绝/摄取校验坏行拒绝/重名需 force/三级解析含重名与未知/CLI import-functional→list→show 冒烟），445 全绿 + ruff 干净；实机冒烟 import-functional→list→show→rm 全链 + **受管功能数据集 tests_dir 零改动消费实测**（2 case 真实 qwen2.5:3b 运行，逐 case 判定正确：c1 nonempty 过、c2 大小写敏感 contains 未配 ignore_case 判 fail——断言语义按设计工作）。README 数据集章节补统一目录用法；§12.6 D0 完成标注。

- 2026-09-20 D2 可靠性复合任务框架（用户指示先搭框架）：编排语义确立——run_with_chaos 业务槽 = 背景流量与探针观测并发，**注入窗 = 背景流量时长**（探针窗口自动对齐 build_background_suite 解析出的 perf.duration），业务收尾后 finally 逆序恢复 → 补测窗口捕捉首个 pass → RTO。落地：service/reliability_tasks.py 增 build_background_suite（background 段 → perf 套件 raw：dataset_ref 三级解析须 perf 型、target 继承顶层、duration 必配上限 3600s、缺省 engine=locust/mode=baseline/stream）+ run_composite（背景 perf 套件经 run_tool_suite 落公共 run 存储，概要并入 result.background；观测部分沿用 events/probes/rto/summary 形态，probe 标记 background=true）；ObserveTaskExecutor 按 spec 有无 background 分派纯观测/复合。配套：chaos/observer._summary 转公开 summarize_probes（原则 6）；suite_task 抽取 suite_summary 公共函数（原则 3，复合任务与套件执行器共用 result 形态）。测试 +5（编排语义 fake 替换 run_with_chaos/run_observation/run_tool_suite：dataset_ref 解析进 perf.dataset、注入窗对齐 [120, 6]、结果三路合并、duration 必配/类型须 perf/载荷来源必配三守卫、纯观测路径不回归）。实机 E2E（Ollama qwen2.5:3b）：locust 20s 背景流量 + 探针同窗 3 点全 pass + 背景独立 run passed（9 指标 case + 报告四件套）。

- 2026-09-20 chaos 真实注入实弹打通（用户问"chaos 怎么集成、怎么真正注入故障"，对 mock 推理服务做真实 process:suspend）：**实弹揭出两修**——①validate_specs 幂等化（FaultSpec 透传：observe_under_chaos 校验后传 FaultSpec 列表给 run_with_chaos，controller 入口再校验一次对 FaultSpec 取下标炸 'not subscriptable'；此前两条路径各自单测全 mock、组合从未真实执行）；②process 注入器改用 Process.suspend()/resume() 实例方法（psutil 无模块级 suspend/resume——同因：注入器单测全 mock，真实链路首跑才现形）。注入器实弹单测 +3（真子进程 suspend→verify(STATUS_STOPPED)→recover(resume) 往返、kill 生效语义与幂等恢复、目标缺失/缺 target 显式报错）；observe body 增可选 timeout（探针超时秒数缺省 30，挂起类故障需短超时快速失败）。**实弹结果（suspend 真实服务进程 10s 注入窗/timeout 3s/post 6s）**：事件时间线 injected→verified(8ms)→recovered；注入窗内 2 探针 ReadTimeout fail（挂起进程不响应），恢复后 3 探针连续 pass（延迟 3000ms→10ms）；rto affected=true/fail_count=2/**rto_seconds=3.264** 全字段实测。453 全绿 + ruff 干净。遗留：toxiproxy 网络故障注入器（latency/timeout/bandwidth）依赖外部 toxiproxy Go 二进制，本机未装未实弹；kill 注入器的真实目标演练需在指定环境做（杀真服务不可逆）。

- 2026-09-21 E 系列故障实验用例落地（§14 契约实现，本机实弹）：①两级对象选择引擎 chaos/selectors.py——发现层 target 声明实验对象（与 /chaos/targets 树过滤同源）、注入层 inject 注入时刻活体解析（match 四模式 contains/exact/prefix/regex：前两者匹配名+命令行后两者仅名；exclude 匹配清单+{system:true}+自身恒排除；select 五策略 first/all/random(k) seed 可复现/percent(p) 至少 1/count(n)；解析为空显式报错）；②controller fault_duration 定时恢复——注入完成 N 秒后自动 recover 业务继续跑（真 RTO 语义：持续负载下测恢复），recovered 集合使定时恢复与 finally 兜底双路互斥竞态安全；③注入器对象集化：resolve 逐 pid 展开多 FaultSpec，事件逐条带用例名，审计清单（pod→pid）落 result.cases.targets；④run_experiment_suite 套件编排：背景流量一次拉起全程持续+探针全程在线+用例顺序执行+逐用例 expect_rto 判定（verdict/checks）+套件 verdict；⑤注入器 param_schemas 声明（process 空 schema/toxiproxy latency_ms 必填等）+validate_params 解析期校验（未知/缺失/类型/范围 fail-fast）+list_weapons 增 schemas 键（保留 kinds 字符串表兼容既有消费方）+observe body 增可选 timeout。测试 +18（选择器真子进程实弹/套件编排真子进程+状态采样探针/定时恢复时序断言/param schema 校验/武器目录 schema）。实弹：mock 服务 suspend 套件用例——首跑 contains 命中 bash 包装进程（最小 pid）挂起无效、verdict 如实 failed（**非目标进程筛除价值的实证**）；补 exclude [bash,cmd] 后正确定位 python 服务进程，注入窗 2 探针 fail→恢复→4 pass→RTO=3.018s≤expect_rto 4→passed。466 全绿 + ruff 干净。悬留：E4 custom 注入器（随 R 系列环境白名单）、kube 通道 pod 匹配、前端故障窗标注（D3）。

- 2026-09-21 性能指标多档位 + 缓存命中率（用户需求：p95/均值之外补 p50/p90/p99/max；token 回传算缓存命中率）：①三域统一时延族档位——自研引擎 metrics/performance.py 重写（TTFT/延迟基类 + 子类只换 aggregation：ttft/latency/tpot/itl × 均值+p50/p90/p95/p99/max；新增 tpot_ms=(端到端−TTFT)/(tokens−1)、itl_ms 走 Timings.itl_samples_ms 逐 token 样本）、locust collect 逐请求 jsonl 现算全档位（模板逐请求补记 itl_p50/p90/p99/max 同档统计量，档级=再聚合、max 精确）、evalscope-perf 读 percentile json 50%/90%/95%/99% 行 + max 从 benchmark_data.db 逐请求记录取（多 run 取最大而非均值——均值会低估尾部）；LOWER_IS_BETTER 扩到全部档位名（单一来源）。②缓存命中率——TokenUsage.cached_tokens（None=未回传区别于 0=全 miss），parse_usage 收三家口径（OpenAI/vLLM prompt_tokens_details.cached_tokens、Anthropic cache_read_input_tokens、DeepSeek prompt_cache_hit_tokens）；自研引擎 cache_hit_rate 指标 + locust 档级 Σcached/Σprompt（token 加权）；evalscope 上游不采集缓存明细不产出。③口径修正两处——MetricResult.value 放宽可空：TTFT/Latency 不可测从 value=0.0 改为 None（0.0 会把 p50 拉成 0、均值拉低——宁缺毋错不算 0，聚合自动跳过）；chaos summarize_probes 补成功探针时延多档位概要（fail 探针的错误路径耗时不混入分布）。ITL 档级语义注：latency/ttft/tpot 每请求一样本档级分位即精确值；ITL 逐请求存同档统计量再聚合为近似（与既有 locust p95 口径一致），max 恒精确。
- 2026-09-21 架构盘点与缩减(1)（用户要求：整理功能/审视缩减/发现风险立即重构）：规模盘点——aite 16.3k 行/tests 7k 行/221 文件，repositories.py 925 行与 cli/main.py 801 行为两大 god-file（并行冲突热点），TODO/FIXME 为零，测试占比 43%。立即缩减：①根目录调试/预览产物移除（_schema_check_locust_raw CSV×2/demo_preview/real_preview/perf_dashboard_preview，零代码引用）+perf_dashboard_*.html gitignore；②EventBus 迁中立层 aite/events.py（web/events 兼容 shim）——纠正 4 个 service 域文件反向依赖 web 包的分层倒置。风险清单：R1[高]共享工作树多会话并行无隔离（git add -A 两次卷入对方 WIP，已两次拆分重做——纪律：共享树上只用显式路径提交，中期每域独立 worktree）；R2[中]repositories.py/interfaces.py 单类膨胀（每域追加 6-10 方法）；R3[中]cli/main.py 全命令集中；遗留排期：cli 按组拆模块/repositories 按域拆 mixin（各半天，需专门窗口）。510 tests + ruff clean。

- 2026-09-21 模块 K：RAGAS / Agent 评测框架（用户需求：与精度同构、差异主要在数据集，搭 RAGAS 与 Agent 评测框架 + 开源数据集对接 + 金融/政务/法律私域数据集生成）：①RAGAS 口径对齐——context_precision 升级秩加权公式（LLM 相关性判定/token_f1 回退）、context_recall 升级断言归因（LLM/token 覆盖回退）、新增 answer_correctness（metrics/ragas_metrics.py，F1⊕语义相似 0.75/0.25）；②Agent 指标补齐 tool_arg_correctness（期望参数要点子串匹配）；③开源数据集 builtin 抽样版 ×6（rgb/crudrag/multihoprag/financebench/gaia/xlam_fc，上游指针 + 推荐指标 metrics:auto 可用）；④私域生成器 gen_domain.py + domain_corpora.py + `aite dataset gen-domain`（金融/政务/法律种子语料 → 确定性 RAG JSONL + userstore/catalog 注册，已实机生成 finance_private/gov_private/legal_private 各 12 条）；⑤examples/ragas.yaml + agent_eval.yaml 更新 + dataset-guide 数据集地图。**真实联调（qwen2.5:3b × legal_private）揭出 build_gateway 两处既有缺陷并修复**——LLM 需求判定残留硬编码名单（与 needs_llm 契约双轨漏新指标）改注册表驱动；未配 judge 时 gateway 零模型致 faithfulness 等全 error（mock 单测测不到）改为注册 target 为缺省自评模型；连带加固 judge 数值解析（null 计数回退最差分而非 TypeError）。测试 +17（gateway 4/生成器 9/指标 4，本域全绿 + ruff 干净；全量 513 通过 + 2 失败均位于并行股道 in-flight 的 reliability 域文件，与本模块无关）。教训：与前轮 locust 首跑同源——集成缝隙离线 mock 测不到，真实联调必须先行。

- 2026-09-21 架构纪律机器化（用户确认落地 import-linter + 注册表契约测试）：①pyproject [tool.importlinter] 两合同——分层合同（cli/web → service → tools|chaos → functional → storage → gateway → 插件层 datasets|connectors|metrics|reporting → core → events|utils，12 条豁免全注理由：engine 组合根/内置注册 10 条 + loader metrics:auto 懒加载 1 条 + TokenUsage 沉降 1 条）+ 插件域禁止直接互 import（7 条豁免为经 core 的注册间接链，注册外移后撤销并升级 independence 合同）；实测暴露结构事实——core↔插件双向依赖集中于 engine.py 注册四处 + loader 懒加载 + utils.cost→TokenUsage，豁免清单即架构债台账。②dev extras 增 import-linter>=2.0，CLAUDE.md/README 验证命令补 lint-imports 提交门槛，gitignore 缓存目录。③tests/unit/test_registry_contract.py 注册表契约（cls.name 与注册名一致/Metric 类属性 aggregation·higher_is_better·needs_llm 合法/五类组件实现 ABC/注入器生命周期契约/武器目录 kinds×schemas 自洽）——首跑即抓中 3 个真实违例：local/builtin/hf_hub 三个数据集鸭子类型未挂 Dataset ABC（load 签名本一致，补继承即绿），原则 10 教训的复现实证。38 tests（本域 + datasets/loader/add_hf 回归）+ ruff clean + lint-imports 2 kept 0 broken。悬留（Phase 4 需专门窗口）：TokenUsage/CostLimitExceeded/InProcessRateLimiter 沉降中性层、内置注册外移入口层、build_repository 外移——逐条撤豁免后再动 R2/R3 god-file 拆分。

- 2026-09-22 SWE-bench 离线打分链路（用户需求演进：跑一次 SWE-bench → 集成进平台 → 用 DeepSeek Harness 等开源 agent → 先跑通全链路）：①`predictions_file` 连接器（connectors/predictions_file.py）——批量 agent（DeepSeek Harness/mini-swe-agent 等）的 predictions.jsonl 按 key_field 取补丁包装为 TargetResponse，离线产物与在线 agent 共用评测管线；缺预测=空输出（未解语义，非 error），文件缺失/损坏才显式 error，mtime 变更自动重载。②`swe_resolution` 指标（metrics/swebench.py）——原则 12 CLI 子进程 + 文件契约：单案补丁写 predictions.jsonl → `swebench.harness.run_evaluation --instance_ids <id>`（子进程 CWD 切 case 工作目录，harness 相对 logs/ 不污染仓库根）→ 回读 `<report_dir>/<model>.<run_id>.json`（schema v2）判定；值语义 1.0 resolved / 0.0 未解或空补丁（无补丁必不解，省容器开销）/ harness 失败·infra error·缺席报告=None 宁缺毋错；mean 聚合即 resolve rate；python_exe 参数解耦打分环境。③pyproject `[swe]` extras（swebench>=5.0）+ examples/swebench.yaml + README/config-reference 同步。④金补丁冒烟全链路实测（builtin swebench_lite 8 案，predictions 仅 pylint 一案 gold patch）：resolve rate 0.125=1/8 如预期——pylint-7228 容器内 patch_successfully_applied=true、FAIL_TO_PASS 2/2 转绿、PASS_TO_PASS 10/10 保持。环境两坑实录：daemon 配置的 docker.xuanyuan.me 镜像源对 swebench namespace 403 → 经 docker.1ms.run 代理拉取 retag；**上游 swebench v5.0.2 Windows 宿主 bug**——run_evaluation.py write_text 落 eval.sh 被 LF→CRLF 翻译，进 Linux 容器后 git apply/pytest 被行尾 \r 毒倒（patch does not apply / command not found），workaround：打分子进程经 runpy 引导在 harness 进程内对 Path.write_text 强制 newline="\n"（_HARNESS_BOOTSTRAP，-m 同构入口，flags 不变）。⑤顺带修复存量测试污染：test_registry 插件用例残留 fixture_hello_metric（裸类违反 Metric ABC）于注册表，全量跑按字母序先于契约测试执行必红、单独跑绿（顺序依赖），照 custom_test_metric 先例用后即删。测试 +15（连接器 5/指标含 bootstrap 10）+ 全量绿 + ruff clean。悬留：在线模式（DeepSeek Harness HTTP shim/custom_http 驱动，实时时延/成本采集）、resolve 之外轨迹指标对 shell 型 agent 的适配、sb-cli 云端打分通道。

- 2026-09-22 按模块归位目录（用户需求：数据集/report/example 相关文件分模块存放，不同模块的文件进不同目录）：①suite 新增可选 `module: accuracy|functional|perf|reliability` 字段（Schema 只增，config.EVAL_MODULES 单一来源校验）——`report_relpath` 前置一层模块目录（缺省 `misc/` 向后兼容），报告树变 `<模块>/<模型>/<数据集>/<时间戳>`；RunReport.config 本就是 SuiteConfig.model_dump，字段自动流转，引擎零改动。②数据集文件按 catalog 类型落子目录：add-hf/gen_domain 产物迁 `data/datasets/accuracy/`（userstore 新增 `dataset_dir(type)` 收口，functional 域 `data/datasets/functional/<名>/` 为既有约定、perf 生成默认目录 `./perf_datasets` → `./data/datasets/perf`），外部 builtin 解析点仅 builtin/__init__:182 按 `file` 字段原样拼——旧条目天然兼容，本机 6 条存量注册已迁移重写。③swebench 打分工作目录 `data/swebench/` → `data/accuracy/swebench/`（DEFAULT_SWEBENCH_WORK_DIR）。④examples 四域归位：accuracy ×10（含根目录 qa_suite.yaml 收编）/functional ×1/perf ×4/reliability ×1，代表性 suite 补 `module:` 标签；根目录 quickstart.yaml 为 `aite init` 生成产物的陈旧拷贝，git 移除（init 行为不变）。测试 +5（module 三态/report 层级两层），gen_domain 路径断言改新约定；490 passed + 3 skipped + ruff clean + lint-imports 2 kept 0 broken。悬留：web UI 报告树读取是否随 misc/ 分层调整展示（待真实使用反馈）。

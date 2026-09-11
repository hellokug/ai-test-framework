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
      → 每档每指标一行 `dataset="perf:c<并发>"`（ttft_ms/ttft_p95_ms/
      latency_avg_ms/latency_p95_ms/tpot_ms/itl_ms/qps/output_tps/error_rate）
      → run_metric_summary，趋势页与精度/功能并列可比。
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
- [ ] I6 二期：ChaosBlade 注入器（进程 kill/CPU/内存/磁盘，Linux 执行机）、
      故障编排 DSL（时序/组合/按阶段注入）、agent 分布式注入（贴近被测系统）、
      拓扑感知（注入点发现与展示）、MTTR 自动计算报告

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

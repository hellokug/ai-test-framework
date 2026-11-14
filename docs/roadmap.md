# aite 完善路线图

> 约定：每完成或变更一项，立即更新对应条目与文末进度记录。
> 顺序原则：核心功能优先，前端体验最后。

## 架构定位（2026-09-01 重定：平台化；2026-09-23 收紧：不造任何评估指标）

**aite 是测试框架 / 评测编排平台，不是评测引擎，不造任何评估指标。**
评测能力一律来自集成开源引擎，平台核心价值 = 数据集管理 + 工具编排 +
开源入口统一收口 + 开源输出统一格式化。已确认的决策：

- （2026-09-23 用户确认，覆盖下一条的部分口径）**不造任何评估指标**：指标算法
  一律委托开源引擎（evalscope / lm-eval / ragas / deepeval / swebench harness /
  locust / pytest…），新增评测能力 = 在 aite/tools/ 新增适配器；aite/metrics/
  冻结，不再新写指标算法
- ~~自研引擎**保留作补充**~~（2026-09-23 起收缩为存量兼容）：性能延迟、安全探针、
  RAG、judge 闭环等自研指标不再扩展，存量保留至迁移完成
- 首个接入 **evalscope**（pip 轻、Python API、原生 OpenAI 兼容端点、中文基准全）
- 执行形态：**进程内 Python API**（依赖版本冲突用隔离策略解决）

### 存量自研指标迁移（2026-09-23 立项，未排期）

`aite/metrics/` 约 2100 行自研实现与新定位冲突，典型：RAGAS 五件套手工重实现
（`ragas_metrics.py` 模块头自述「RAGAS 五件套在本框架的对应实现」；
`examples/accuracy/ragas.yaml` 名为 ragas 实则未调用 ragas 库）、BLEU/ROUGE-L/
chrF 手写（`nlp_stats.py` + `utils/text.py`）、hash embedding 回退
（`embedding.py`）、自研 judge prompt（`judge/`）、内置安全攻击集（`safety.py`）。
迁移方向（每项 = 接开源引擎适配器 + 存量指标标记 deprecated + 报告兼容过渡，
实施前逐项对齐）：

- [~] RAG 五件套 → ragas（或 evalscope RAGEval 后端）替换 rag_metrics/ragas_metrics
      ——**自研实现已删（2026-09-23）**：rag_metrics.py/ragas_metrics.py/embedding.py
      及 rag_eval.yaml/ragas.yaml 旧示例下线，BUILTIN_INFO 与 gen_domain recommend
      收缩为规则口径（token_f1/contains），本地注册表同步清理；**替换路径已定：
      evalscope RAGEval 的 RAGAS 后端**（官方内置五件套指标，复用既有 evalscope
      适配器入口，零新对接），适配器扩展待实装（编排方式 A 一任务两阶段 /
      B 引用既有答案，待用户拍板，见进度记录）
- [ ] Judge 三件（score/pairwise/multi）→ 评估 deepeval 等开源 judge 框架
- [ ] 统计类（BLEU/ROUGE-L/token F1）→ sacrebleu / 所接引擎自带口径，自研实现下线
- [ ] 安全 → 评估开源安全评测引擎（garak 类）
- [ ] 精度基准 → 已由 evalscope / lm-eval 集成覆盖，自研引擎降级为存量兼容
- [ ] Agent/可靠性自研指标（agent_metrics/reliability）→ 随所接开源框架评估去留
- 性能指标聚合（performance.py）与工具域收集属「输出统一格式化」，不在迁移范围

## v1 目标（2026-08-28 重定）

v1 按**能力模块**交付，不再按"补缺口清单"推进。每个模块独立走完
需求对齐 → 对象建模 → 测试 → 实现 → 文档，完成后单独提交 git。

### 模块 A：数据集治理子系统（需求已对齐，暂缓实施）

完整需求与对齐结论已存档：`docs/archive/requirements-datasetgov.md`。
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
两回事）。已存档的数据集治理需求（docs/archive/requirements-datasetgov.md）通篇为精度域，
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

### 模块 L：任务化协作与多 agent（2026-11-08 立项，待模块分层收口后探讨）

背景：owner 确立「管理者-执行者」协作模式（CLAUDE.md「协作与教学总纲」）——owner
提架构/需求痛点，执行者推进、整体验收、交付成品。随项目膨胀，单会话串行推进的
吞吐与上下文成本成为瓶颈，需要把「任务化管理」落成平台能力。

前置条件：模块分层收口（契约级文件稳定、共享收口文件拆分完成）——多 agent 并行
的冲突面正是共享收口文件（原则 13），先治本再并行。

待探讨方向（未定，不预设方案）：
- 任务单元：以「域 + 契约边界」切分任务（一个 agent 只动一个域的文件），任务书
  = 目标 + 影响面（地图爆炸半径）+ 验收命令（选域测试 + 四门禁）。
- 协作形态：主控 agent 拆解/分派/验收，执行 agent 域内实施；契约改动串行、域内
  改动并行（原则 13 的机器化）。
- 状态落库：复用平台已有的 bg_task/DomainTaskKit 状态机，而非另起体系（奥卡姆）。

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
- evalscope 总条数采样（tools.limit_total，2026-09-30 用户提出暂缓）：现状只有
  limit_per_subset（evalscope `limit` 原生每科目语义，1.12 源码 load_subset 逐子集
  传 limit，dataset_args 也不接受 limit 键）。「全数据集共 N 条」需要 aite 侧换算
  （子集钉选前 k 科 + 每子集条数均摊，代码当时已实现并带 10 项单测后按用户指示
  回退）。重启条件：evalscope 原生支持总条数，或用户再点名；换算须处理
  dataset_args 钉选尊重（total < 科目数显式报错）、TaskConfig.limit 全局唯一
  （仅单数据集套件）两个约束，实现方案见 2026-09-30 会话记录。

## 前端体验优化（最后）

- [ ] 核心能力稳定后启动（迁移路径见 web-ui/README.md）
- **前端主航道决策（2026-11-08 owner 确认）**：三条前端线收敛为一条——
  ①`web-ui/mockups/v1` 静态稿原地对接真后端为**主线**（已实弹接通精度/功能/通用三域）；
  ②包内免构建 SPA（`aite/web/static`）**冻结只修不增**；③Vite+TS 工程迁移**暂缓**，
  重启条件 = 静态稿四域全部对接完成且需要离线部署/深度定制。

## 架构债记账（2026-11-08 必要性审计产出，碍事时再动，不预先重构）

- `aite/cli/main.py`（~1100 行）与 `aite/storage/repositories.py`（~1080 行）是线性增长的
  共享收口文件（每个新能力都往里追加），并行开发冲突面最大。拆分方向：cli 按命令组
  拆子模块（main 只做挂载）；repositories 按聚合根拆 mixin。触发条件：下一次在其中
  追加超过 100 行，或模块 L 多 agent 并行启动前。
- 文档收割未完：`web-ui/HANDOVER.md`（其他会话仍以其为「新会话必读」）与
  `web-ui/TOOL-SUITE-PARAMS.md`（前端对接契约）暂不动，待前端主线稳定后收割进
  V1-PLAN / config-reference；`docs/domain-backends.md` §1-9 契约节待并入 architecture.md
  （代码注释仍引用其章节号，移动前须同步改引用）。
- 2026-11-11 存储层扩展性审核（20 表 + repository 查询面全量过了一遍）。当前量级实测：
  最大表 run_metric_summary 1460 行、全库 ~2.5K 行 / 2MB，所有查询微秒级——规模类优化
  **全部延后**（本节「碍事时再动」），逐项触发条件如下：
  ①索引批：test_run.started_at（list_runs 默认排序全表 filesort）、四域任务表
  (status, created_at)、run_metric_summary (metric, created_at)（trends 30 天窗口扫）；
  前置依赖 migrations 支持索引回填（create_all 不给存量表补索引，不先做①落不了地）
  → 触发：test_run > 1 万或 /runs 列表可感知变慢。
  ②test_run 加 domain 列：现按 config_snapshot JSON 推断 + MAX_DOMAIN_SCAN=2000 内存过滤
  （runs.py），超限**静默截断**不报错 → 触发：test_run > 1500（刻意早于 2000）。
  ③list_case_results 分页 + 列表接口字段裁剪（spec/result/config 大 JSON 全量回传）+
  input/expected 快照留底（现 join 活表 test_case，改用例后历史 run 展示穿越）
  → 触发：单 run 用例 > 2 千，或出现「改用例后翻旧 run」诉求。
  ④new_id() 换 UUIDv7（InnoDB 聚簇友好；最大两表 test_case_result/metric_result 已是
  自增 int 主键，此项只涉任务表）→ 触发：迁移 MySQL/PG 前一并做。
  ⑤归档/TTL（bg_task/agent_task/case_run 永久累积）→ 触发：DB 文件 > 500MB。
  ⑥分库分表：结构已友好——case/metric 结果表查询全带 run_id（天然分片键）、四域任务表
  即按域垂直分表、summary/version_result 已剥离跨 run 分析、DB 访问收口 repository；
  大概率时间分区（月）+ 归档就够，真分表仅当 MySQL 后单表千万级。
  另有两个**与量级无关的正确性洞**（小修，下次动到对应文件顺手做，不必等量级）：
  A. delete_suite 未级联 test_case（repositories.py：只解引用 test_run；FK ON 下删有
     case 的 suite 会 IntegrityError 500，现有测试用无 case 的 perf suite 没踩到）；
  B. agent_task.suite_raw 全量配置逐行内联，同一 suite 反复派发逐份复制大 JSON
     （低频可接受；若执行机派发高频化改存 suite 引用 + 版本号）。
- 2026-11-12 日志英文化（owner 决策：换环境部署遇 GBK 终端日志乱码）：增量代码的
  日志一律英文（原则已固化 CLAUDE.md 补充原则 15；透传的上游报错原文、用例数据
  内容不受限）。存量中文日志择期整改，现状 68 行——logger 调用含中文 47 行散布
  16 个文件（热点：service/agent_client.py 7、tools/locust_adapter.py 与
  tools/__init__.py 各 6、tools/perf_adapter.py 与 service/version_results.py 各 5），
  另 cli/main.py 中文 print 21 行。触发：发布前统一过一遍，或任一热点文件因其他
  改动被大改时顺手清掉该文件内的中文日志。

## 进度记录

- 2026-08-28 建立路线图，完成架构盘点（模块地图/执行链路/能力清单见会话记录）。
- 2026-08-28 确立项目标准（架构冻结范围/解耦规则/开发节奏/git 规范），写入 CLAUDE.md；补 .gitignore；基线确认通过（145 passed + ruff clean），执行基线提交。
- 2026-08-28 v1 重定义为按能力模块交付；首个模块"数据集治理子系统"立项（四模块 + 交付物清单），旧打磨项转入 Backlog。进入设计对齐阶段。
- 2026-08-28 数据集治理子系统完成需求对齐（位置/依赖/接口/补齐项均确认，见 docs/archive/requirements-datasetgov.md）后**暂缓实施**；焦点转回测试框架整体关键能力补齐。
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

- 2026-09-22 数据目录语义统一 + 单测并行化（用户需求：examples/data 看着像数据集、data/accuracy 与 datasets/accuracy 两层 module 歧义，统一之；随后按建议上 xdist）：①examples/data → examples/fixtures 正名——内容本就是跨模块共享的示例夹具（business_qa 被 perf/accuracy/reliability 三域 7 例引用、agent_tasks 归 agent_eval），不是数据集资产，8 处引用同步；②消除 data 下双 accuracy 层级：数据集（输入）与运行产物（输出）分离——data/ 只剩 datasets/<类型>/（输入）+ runs/<工具>/（产物）+ 注册表，swebench 打分工作区 data/accuracy/swebench → data/runs/swebench（DEFAULT_SWEBENCH_WORK_DIR），aite init 生成的 sample_qa 从 data/ 根归入 data/datasets/accuracy/（init 写盘路径与 qa_suite.yaml 引用同步，写盘自带 mkdir）；③pytest-xdist 实测——auto(20 worker) 110s 反超串行 78s（Windows 进程启动税：每 worker 全量导入），-n4 最优 65~75s（瓶颈在 16s 的场景收集单测与子进程型用例，非 CPU 并行度），addopts 定 -q -n 4（调试 -n0 关闭）；**嵌套并行防御**：功能域 pytest 适配器子进程会继承根 addopts，argv 增 `-o addopts=` 清空兜底（实证有效，与既有 norecursedirs 兜底并列），内层场景保持串行。dev extras 增 pytest-xdist>=3.5。490 passed + 3 skipped + ruff clean + lint-imports 2 kept 0 broken。悬留：16s 的 test_all_scenarios_collectable 可按需拆分收集轮次。

- 2026-09-22 调试数据集归拢（用户定论：examples/fixtures 本质也是数据集，只是调试用，归数据集目录）——examples/fixtures/{agent_tasks,business_qa,rgb_openbook}.jsonl → `data/datasets/debug/`（调试集横跨域的小样本桶，按 local 路径引用、不入 catalog 类型轴），10 处引用同步；gitignore 白名单链放开 `/data/datasets/debug/`（其余 data/ 仍不入库——导入数据集与运行产物照旧机器本地化），examples/ 从此只剩四域目录；rgb_openbook 系上轮 ragas 示例引用的悬空文件，随迁移正式入库补全。490 passed + ruff clean。负载观察：xdist 下全量 34~75s 区间波动，机器高负载窗口曾偶发 1 例超时型失败（未复现、未定位到名），后续留意。

- 2026-09-22 predictions_file 补 `.json` 格式（用户追问"谁来调、怎么拿 predictions"闭环）：mini-swe-agent 的 preds.json 形态 `{instance_id: 补丁串}`、`{instance_id: record}`、swebench 官方 list[record] 均可直接指向 target.params.path，与 `.jsonl` 并列自动按后缀路由；harness 批量模式即"调用方"，aite 收口判分。测试 +3。

- 2026-09-22 agent 评测通用能力定版（用户需求：两场景——①固定脚手架测模型 agent 能力（离线 predictions）②自研 agent 走接口（在线驱动），且不限于 swe-bench、后续适配其他 agent 基准与私域 agent 数据集，能力通用化）：定架构即三轴正交组合，全部走既有注册表、无新引擎——**数据集轴**（任务集：swe-bench 实例经 add-hf 入库 / 私域任务集 local/gen-domain，通用）× **驱动轴**（离线 predictions_file ↔ 在线 custom_http agent 契约，均为既有连接器）× **判卷轴**（按基准选指标：swe_resolution 官方 harness 判卷 / task_completion 等 agent 指标族 LLM 判卷 / 未来 terminal-bench 等新判卷器按开闭原则注册）。新增 examples/accuracy/swebench_online.yaml（场景 2 套件：custom_http + swe_resolution + agent_efficiency，timeout_s 3600），README 示例表双场景分列。悬留：私域 agent 数据集生成器（gen-domain 的 agent 形态）、terminal-bench 判卷器（第二个实现出现时再抽判卷器抽象，原则 7）。

- 2026-09-22 `engine: evalscope` 精度/agent 适配器小设计（用户定论：harness 手工操作太麻烦，平台必须一条命令；evalscope 官方已集成 SWE-bench，直接委托之——"用 evalscope 测"与"用 aite 测"合一）：**复用外部评测工具既有模式**（perf/locust/pytest 同款 ToolAdapter：build_config→子进程→文件契约收集）。已核实 CLI 面：`evalscope eval --model <m> --api-url <url> --api-key <k> --datasets swe_bench_verified[_mini|swe_bench_lite] --limit N`；Docker 细节走 dataset_args extra_params（build_docker_images/pull_remote_images_if_available/dockerhub_username/force_arch，对应镜像代理困境的官方旋钮）；推理集 princeton-nlp/SWE-bench_oracle（HF 需镜像）；单实例超时 1800s；内部依赖 swebench==4.1.0——**evalscope 独立 venv 部署**（.venv-evalscope，py3.10；base 包依赖 editdistance 无 py3.14 Windows wheel，3.14 装包失败实证），适配器经 python_exe/bin 参数定位（沿用 swe_resolution 的 python_exe 先例）。suite 形态：`engine: evalscope` + target(model/api_url/api_key) + datasets（aite builtin id ↔ evalscope 数据集名映射）+ params(limit/extra_params/env_root)。收集：outputs/ 结果 JSON → aite 报告/入库/对比。测试：argv 构造 + fixture 结果文件离线单测，实弹待模型端点（本机 Ollama qwen 可冒烟、DeepSeek API 出真实数字）。predictions_file + swe_resolution 通路保留——那是场景 2（自研 agent）的载体，与本适配器（场景 1 一条命令）互补。

- 2026-09-23 `engine: evalscope` 适配器补独立环境子进程执行模式（用户定论：evalscope 是执行引擎，下发必须走平台任务编排，不收手工 CLI）——实现发现 `engine: evalscope` 适配器本就存在（tools/evalscope_adapter.py，mmlu 等标准基准已通），缺的只是 swe_bench 系 agent 数据集的执行通道：①`tools.extra_args.python_exe` 指向 evalscope 独立 venv（本机 py3.14 装不上 editdistance 实证）→ 配置经 `task_cfg.json` 文件契约 + `run_task` 引导子进程执行（零 CLI 旗标漂移风险），`timeout_s` 可配（缺省 4h，config.DEFAULT_EVALSCOPE_TASK_TIMEOUT_S）；保留键不泄漏进 TaskConfig；无 python_exe 维持原进程内路径（ImportError 显式指引）。②任务编排零改动复用：resolve_suite 引擎路由 → parse_tool_suite → execute_tool → build_tool_report，`aite run`/REST 派发/agent 回传全兼容；`collect()` 的 reports/*.json 文件契约天然覆盖 swe_bench 报告。③examples/accuracy/swe_evalscope.yaml（场景 1 一条命令：Ollama qwen 冒烟限 1 例，真评换 DeepSeek + verified_mini_agentic 50 题）。测试 +4（保留键隔离/子进程写 cfg+收集/失败尾#error/进程内回退），498 passed + ruff clean（本域文件）。悬留：swe_bench 实弹（评估报告 json 字段与 collect 兼容性）。

- 2026-09-23 `engine: evalscope` 实弹首轮（平台接口下发）——`POST /api/v1/runs`（config_yaml 内联）→ 后台任务 running，任务编排/结果入库链路全通。实弹揪出两坑并修：①适配器子进程 cwd 切进 work_dir 后，task_cfg.json 相对路径错位（FileNotFoundError）——配置/产物一律 resolve 绝对路径，回归断言 is_absolute；②evalscope 钉死的 swebench==4.1.0 顶层 `import resource`（Unix 独有模块）致 Windows 宿主连 import 都失败——`.venv-evalscope` site-packages 打 no-op shim（仅 setrlimit/RLIMIT_NOFILE，两处使用点均无害化）。运行期事项：serve 必须带 HF_ENDPOINT（子进程继承环境）；报告解析待任务跑完后核对 collect 兼容性。

- 2026-09-23 实弹第二轮（用户 CLI 手跑）揪出中文 Windows 第三坑：docker-py 读 docker context meta（含本机用户名的 UTF-8 路径字节）默认 GBK 解码 → "corrupted meta file"——适配器子进程 env 注入 `PYTHONUTF8=1` 一揽子防御（测试断言 env），建议用户系统级设置；任务经 POST /api/v1/runs 下发后真跑起来（数据集镜像源加载 OK、进入 astropy 实例镜像拉取/构建），collect 兼容性核对待跑完。

- 2026-09-23 启动收敛补全（用户发现 runs 页多条 swe-evalscope 卡 running）——原则 8 的最后一处缺口：后台任务早有 mark_stale_bg_tasks 启动关账，但 **run 行无人收敛**（跨天累积：被强杀 serve 的 in-flight run、并行会话的 smoke-fail-visibility 四条等全部永久 running）。修法不动契约（仅用既有 list_runs/update_run）：service 层新增 `close_interrupted_runs(repo)`——单写者进程重启后 running 态 run 不可能存活，统一关账 error；接线 web AppState.startup（mark_stale_bg_tasks 之后）。重启实证：历史 6 条卡死 running 全部收敛、running 剩余 0。测试 +2（收敛/无残留不写库），502 passed + ruff clean + lint-imports 2 kept 0 broken。

- 2026-09-23（续）evalscope 环境完备清单定型（实弹逐层点亮）：.venv-evalscope = py3.10 + evalscope 1.12.0 + swebench==4.1.0（官方钉版）+ resource.py no-op shim（Windows 顶层导入）+ `evalscope[sandbox]` 附加包（ms_enclave，agent 沙箱环境缺它 ImportError）。链路实证至 agent 循环运行：平台下发 → 数据集加载（HF 镜像）→ 实例镜像拉取（swebench 官方 DockerHub 命名空间，本机镜像源慢拉可行）→ SandboxService docker 引擎启动 → 模型 openai_api（Ollama qwen2.5:3b）agent 循环进行中。镜像代理预拉 + retag 模式对 evalscope 拉取同样适用（首实例 astropy 慢拉约 9 分钟）。

- 2026-09-23 **定位收紧：不造任何评估指标**（用户确认，覆盖 2026-09-01「自研引擎保留作补充」）。全库审计结论：工程域（evalscope / pytest / evalscope-perf / locust / swebench 五个适配器，入口收口 + 输出归一）符合定位；`aite/metrics/` 约 2100 行为平台化决策前（08-28~09-01 期间）的历史存量，与新定位冲突——RAGAS 五件套手工重实现、BLEU/ROUGE-L 手写、hash embedding 回退、自研 judge prompt、内置安全攻击集；`examples/accuracy/ragas.yaml` 名为 ragas 实未调用 ragas 库。落地：定位红线写入 CLAUDE.md「架构与目标」与 README 顶部（含存量说明）；aite/metrics/ 冻结不扩展；立项「存量自研指标迁移」（见架构定位节，未排期，方向 ragas/deepeval/sacrebleu/安全引擎），每项实施前单独对齐。

- 2026-09-23 工具引擎运行期进度可见性（用户需求：running 期间前端/CLI 什么都不展示，不知道总用例数与当前用例）：①ToolRunRequest 增 `progress_cb` 可选回调（additive，既有适配器零影响）；②evalscope 子进程从阻塞 run 改为 Popen + 轮询——增量读 evalscope 自写 logs/eval_log.log，正则解析 tqdm 进度行（Evaluating/Running 的 done/total）→ progress 事件，stdout/stderr 落 proc_*.log 文件（超时/失败取尾，不再 PIPE 撑爆风险）；③run_tool_suite 接收 event_cb（service execute_suite 工具分支此前直接丢弃）——事件同形转发（原生引擎 progress 事件形状，前端零改动），total 已知即异步回写 run 行 total_cases；CLI run 工具分支实时打印进度行（--quiet 静默）。实测：SSE 流出 {"type":"progress","phase":"Evaluating[swe_bench_lite_agentic]","done":0,"total":1}、run 行 total_cases=1。测试 +6（解析/转发/total 回写/Popen 桩重构），504 passed + ruff clean + lint-imports 2 kept 0 broken。悬留：swe_bench agentic 报告 json 与 collect 的字段级兼容核验（实例跑完后）。

- 2026-09-23 存量迁移第一项执行：自研 RAGAS 五件套删除（用户指示「原本实现的那堆没用的删了」）——rag_metrics.py/ragas_metrics.py/embedding.py（含 hash embedding、answer_relevancy/embedding_similarity）+ rag_eval.yaml/ragas.yaml 旧示例下线；BUILTIN_INFO（rgb/crudrag/multihoprag/hotpotqa/financebench）与 gen_domain DEFAULT_RECOMMEND 收缩为存活规则口径（token_f1/contains）并注明 RAGAS 走 evalscope-rageval；本地 data/aite_datasets.json 三条私域集 recommend 同步清理；tests 删 TestRAG/TestRagas、registry/gateway 断言改用存活指标；metrics/__init__ 头注写明红线与冻结状态；dataset-guide 配套指标段改指 evalscope-rageval。**替换路径查证定案：evalscope RAGEval 官方内置 RAGAS 后端**（tool: "RAGAS"，五件套指标全，critic_llm/embeddings 配置化，run_task 同一入口，`pip install evalscope[rag]`）——复用既有 evalscope 适配器入口零新对接，扩展现有适配器即可。待拍板：编排方式 A（一任务两阶段：引擎先跑模型出答案→转 RAGEval 输入打分，用户无感）/ B（引用既有答案记录，贴合 ai_study RAG 系统现成输出场景）。tests 109 项相关域全绿 + ruff 干净（test_chaos_discovery 4 失败为环境敏感既有问题，与本次无关）。

- 2026-09-23 rageval 适配器实装（用户拍板 A/B 都要，「yaml 里一个配置的事」）：tools/rageval_adapter.py（engine: evalscope-rageval）——契约按本机 conda 环境 evalscope 1.9.1 源码 pin 死（backend/rag_eval/ragas：run_task 入口 + testset_file 加载器宽别名 question/answer/contexts + 产山 `<testset>_score.json` 逐 case 分数）；三块胶水零指标算法：字段映射（TestCase→user_input/response/retrieved_contexts/reference）、客户端注入（critic_llm/embeddings 翻译 + api_key_env 环境变量解析）、分数归一（逐 case 分数→均值指标行 + 缺分 case 进 failures，回归测试钉死「下标逐一对应」）；A/B 编排=数据集 records 有无 response 字段自动分派（混合形态显式报错），A 复用平台 connector 调端点（tools→connectors 分层合法）；metrics 只认官方 ragas 名 + relevancy/correctness 缺 embeddings fail-fast；ToolRunRequest.rag_eval 字段 + parse_tool_suite rageval 段 + pyproject rageval extra（evalscope[rag]）；examples/accuracy/ragas.yaml 回归（真调开源 ragas）。离线测试 11 项（不依赖 evalscope 安装：build_config 形状/api_key env 解析/B 模式 testset 落盘/混合形态拒绝/方案 A 缺 target 守卫/collect 均值计数失败明细/缺 score 文件显式失败/parse 分支）+ 62 项相关域全绿 + ruff 干净（test_tools 1 处 import 排序为并行股道 in-flight）。实弹待用户环境：evalscope conda env 补 pip install 'evalscope[rag]' 后 aite run -c examples/accuracy/ragas.yaml。

- 2026-09-23 运行期阶段事件流（用户反馈：实时进度在计数前是盲区、用例结果空置语义不明）：①evalscope 日志解析从"仅计数行"扩展为**计数 + 阶段双通道**——加载数据集/拉取实例镜像 ×N（计数）/基础与环境镜像就绪/构建实例镜像 ×N/沙箱已启动，阶段行仅 phase 无 done/total，前端渲染为当前阶段文本（有计数才画进度条）；②详情页"用例结果"标注语义——SWE-bench 结果=容器内测试是否通过，判分完成后逐条写入，运行中为空属评测语义非故障；③前端详情页运行中改 5s 轮询 run 行（CLI 与界面下发统一可见，details.progress 持久化是基础）。实弹教训一条：多层字符串嵌套转义（脚本→文件→import）把 \1 吃成 \x01，chr(92) 显式构造反斜杠根治。测试 +3（阶段行/拉取进度/期望更新），502 passed + ruff clean。悬留：per-instance 级实时（需 evalscope 回调钩子，非日志解析可得）。
- 2026-09-23 rageval 实弹排坑两连（用户环境，方案 A 全链路推进中）：①`resolve_connector` 导入路径错误（在 core.registry 不在 connectors 包）；②evalscope RAGASEvalConfig 的 embeddings 为必填字段（schema 无缺省值，纯 LLM 指标也必须提供）→ 适配器改无条件必配 + 示例启用；③embedding 模型选型坑：BAAI/bge-large-zh-v1.5 仅 .bin 权重，torch<2.6 被 transformers CVE-2025-32434 防护拒绝加载 → 示例换 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2（safetensors，多语言 ~470MB）并在 yaml 注明选型规则。DummyCustomModel 提示无害（RAGEval 不用 evalscope model 字段）。

- 2026-09-24 V1 前端规划定稿 + 后端 API 按规划补齐（用户指示「先规划、不对接，按最终规划补齐 API」）：①`web-ui/V1-PLAN.md` 定稿——17 页四域+公共页面排布/内容/数据源清单（P0×12 全部只依赖已有或本次新增 API），并新增 §10 前端依赖的新 API 契约（前端只依赖契约，原则：契约先于实现）；②四域任务 REST（C1-C4 的 REST 切片，契约=domain-backends.md §2）——`web/routers/domain_tasks.py` 一份工厂四实例（契约同形，原则 3），AppState.task_kits 启动装配（bus 共享 → SSE 频道同源）+ startup 各域 mark_stale 收敛（原则 8），错误映射 TaskNotFoundError→404/TaskStateError→409（PATCH 先查存在性区分 404/409，kit.update 两态同返 409 的契约偏差在路由层矫正）；SSE 助手上收 `web/sse.py`（runs._stream 同源复用，终态帧集合含四域新终态 cancelled/succeeded/failed）；③数据集目录 REST（D3 平台切片，契约=§12.3）——`web/routers/datasets.py`：GET /datasets?type=、GET/DELETE /datasets/{ref}（resolve_dataset_ref 三级解析单一来源）、POST /datasets/functional/import（service 层 web_service.import_functional_dataset 包临时文件→ingest_functional 真实校验，路由零磁盘 IO）；④/runs 与 /stats 增 domain 参数——`web_service.run_domain/filter_runs_by_domain` 单一来源（engine∈{evalscope-perf,locust}→perf、pytest→functional、其余 engine→accuracy、无 engine 带 chaos→reliability、无 engine→accuracy），带 domain 时按新→旧扫 config.MAX_DOMAIN_SCAN 条内存过滤再分页（业务过滤不进 SQL/repo，原则 1）。测试 +26（四域任务 CRUD/守卫/fail-fast→failed→retry 链/404-409 语义、数据集目录 CRUD/functional 摄取/坏用例 422、域过滤纯函数/路由接线），589 passed（1 failed 为工作区既有 WIP：report_relpath 加模块层后测试未同步，stash 验证与本次无关）+ ruff 干净 + lint-imports 2 kept 0 broken + 函数长度审计达标。悬留：四域 executor.cancel 的子进程进程组强杀（domain-backends.md §8 已知限制）、前端页面按 V1-PLAN 落地。

- 2026-09-27 全局 common 预制（用户驱动：「集成了 evalscope，最小参数量不应比原生多；模型 API 等参数可预制」）：`data/common.yaml` 两段式——`models:` 模型预制（端点/api_key_env/params/rate_limit 录入一次，suite `target: {profile: <名>}` 一行引用，judges 模型引用同效）+ `tools:` 工具环境缺省（extra_args.python_exe/timeout_s 等，并入每个带 tools 段的套件）。合并语义：预制为底、内联逐键覆盖（params/rate_limit/extra_args 递归深合并），解析后 profile 键移除、快照自包含；幂等（parse_tool_suite 一次运行多次进入结果一致）。挂载点收口两处「raw dict → 结构化配置」边界：自研域 load_suite、工具域 parse_tool_suite（resolve/execute/persist 全路径受益）。文件缺失=无预制零影响；损坏显式报错（原则 9）。CLI：`aite common show`；模板 `examples/common.example.yaml`；README「模型预制与环境管理」节。至此委托轨道最小套件=三行评测语义（engine/target.profile/tools.datasets），参数量与 evalscope CLI 持平（run_name 为平台记账键），白得报告/入库/趋势。测试 +10（展开覆盖深合并/未知 profile 报错/幂等/judges 引用/tools 合并/双边界/空文件透传/损坏报错），test_profiles 10 绿 + 全 unit 目录绿 + ruff 干净 + lint-imports 2 kept 0 broken。既知：integration 两失败（test_report_relpath / test_two_phase JsonRenderer not callable）为工作区既有 renderers WIP（stash 隔离验证与本改动无关）。悬留：Web 表单的 profile 下拉（读 common.models，随 V1 前端）。

- 2026-09-27 运行宿主切换 conda（用户拍板「直接切conda」）：基准目录接口（GET /api/v1/tools/benchmarks）此前在 aite 宿主（.venv py3.14）不可用——list_tasks 进程内读 evalscope 注册表，而 evalscope 因 editdistance C 扩展编译不过（C2059）无法装进 py3.14，故拆出 .venv-evalscope。尝试 .venv 内装 evalscope 1.12.0（--no-deps + 其余依赖单装 + editdistance 桩模块）后接口虽通，但引发连锁（rag_eval encoder 顶层 import torch 缺失 → 部分 import 失败模式变化 + rageval B-mode 测试前提破坏），用户质疑后改走 conda 宿主：①conda env evalscope 升级 1.9.1→1.12.0（与执行版本对齐，registry 257 基准）；②aite `pip install -e .[dev]` 进 conda（py3.10 满足 requires-python）；③common.yaml tools.extra_args.python_exe 切 `D:/miniconda/envs/evalscope/python.exe`（swe_evalscope.yaml 内联残留同步清理，继承 common 缺省）；④rageval B-mode 测试改 MetaPathFinder 拦截显式模拟 evalscope 不可导入——契约测试与宿主安装状态解耦（两宿主均验证通过）；⑤.venv 还原（卸 evalscope、删 editdistance 桩；连带安装的轻量依赖留存无害）。验证：conda 侧 registry 257/REST 200/aite 单测全绿/ruff 0.16.5 干净/lint-imports 2 kept 0 broken；.venv 侧 rageval 测试恢复绿。**日常入口切换**：CLI/Web/pytest 用 `D:/miniconda/envs/evalscope/python.exe -m aite.cli.main …`（或 activate conda env 后 `aite …`）。既知残留：test_tools 2 失败（collect threshold alias / kill_tree）为用户并行 WIP（stash 隔离验证：HEAD 无该用例）；rageval 契约原按 conda 1.9.1 pin，现执行环境 1.12.0，ragas 实弹前需回归 testset 文件契约；.venv-evalscope 已被 conda 取代，可择机删除。


- 2026-09-30 工具域断点续推语义重做（用户指出悖论：「跑完后重跑想从头，关 resume 又丢断电续推」）：`resume` 从 bool 升三态 **auto（缺省，状态感知）/ true（force）/ false**——auto：最近目录没跑完（断电/402）→ 就地续推只补缺失，跑完 → 自动全新起跑；force：改判官只重判/重出报告；false：永远全新。**生成侧身份指纹** `tool_identity`（模型/端点/数据集/limit/生成参数的规范化哈希，判官与基础设施参数 timeout_s/python_exe/eval_batch_size/rerun_review 刻意不在内）+ `run_state.json`（running→completed/failed 生命周期由 run_tool_suite 落盘，completed 刻意晚于 persist——persist 内部二次 parse 依赖 running 态保持决策稳定）；身份变更一律全新，不混入不可比预测（limit 5 验管线后跑全量=新任务）。**契约级记录**：POST /api/v1/runs 的 LaunchRequest 增可选 `resume` 字段（additive；CLI `--resume` 同名透传，用户需求「前端选择续推还是新任务」）。**顺带修复套娃 bug**：use_cache 相对路径在子进程 cwd 切换后二次拼接，产物整套嵌套一层（2026-09-29 aa_lcr 实弹发现）——`_normalize_cfg_paths` 统一绝对路径化。设计权衡显式记录：续推决策读 run_state.json（文件）而非 DB——状态文件是**绑定产物目录的缓存元数据**（同 evalscope 自身 identity snapshot 机制），与产出物同生共死、跨进程一致（agent/master/CLI 共享 FS 时决策相同）；业务运行状态的唯一权威仍是 DB test_run 行（原则 8 双轨 prohibition 针对「内存 dict vs DB」记账，此处为目录级工件元数据）。原则自查整改：parse_tool_suite 85 行超 80 上限 → 决策块下沉 `_apply_resume_policy`（66 行）；failed 态写盘 contextlib.suppress 静默吞错（原则 9）→ 显式 warning；completion 写盘失败不推翻已入库结果但告警（状态滞后=下次白续推一趟，无害）。测试 +14（决策矩阵 8/身份指纹/路径归一/状态生命周期 3），637 passed + ruff clean + lint-imports 2 kept 0 broken。收尾同步：renderers 新四段报告路径（模块/模型/数据集/时间）遗留的陈旧测试 test_report_relpath_model_dataset_time 仍按三段解包（本批 layout-v2 漏改），已同步为四段断言后全树 638 passed；gitignore 补 outputs/ 与 .venv-evalscope/（运行时产物/本地环境不入库）。

- 2026-10-12 定期架构评审（机械门禁 + 三路分层审查：接口/编排层、插件层、契约/基础层）。基线全绿：lint-imports 2 kept 0 broken（12+7 豁免）、ruff clean、638 passed + 3 skipped（79s）。总体结论：四层架构无结构性偏航，分层合同/registry/段落化组织执行到位，全仓无超 80 行函数。发现四类存量债（均未动契约级文件，整改排期待对齐）：【P1·原则 10 复发】Repository 接口漂移未闭环——SqlAlchemyRepository 23 个实现特有公开方法（manual_eval 4 / bg_task 6 / agent 10 / suites-cases 3）零「实现特有」注记 + 2 处签名偏离（save_case_result 增 variant/variant_of、save_metric_results 增 source，engine 已按实现签名调用，接口形同虚设），调用方普遍以 repo: Any 持仓致类型检查不设防；整改 = 方法升接口或注记「实现特有」+ 调用方注解收紧。【P1·原则 9 真违例】connectors/gemini.py:79-81 响应解析失败静默返回空 output（坏数据换合法外观，CLAUDE.md 记载教训同款）；gateway/llm_gateway.py:377-379 embedding 缺依赖静默降级 hash 无告警无来源标注。【P2·结构性重复】gateway 绕过 connectors 直连三家 LLM API（httpx 越位 + 默认 base_url 两份并行维护）；metrics/performance.py（24 注册名）/reliability.py/agent_metrics.py 零生产调用仅测试引用——「存量自研指标迁移」最大最安全清理对象；metrics/swebench.py:65 在指标内做 harness 子进程编排（应迁 tools/）；engine.py:510 与 storage/__init__.py:15 重复 build_repository（engine 版可删）；阈值方向两张并行小表（tools/perf_common.py:30 与 datasets/compat/bench_import.py:18，现无交集属潜在漂移点）。【P3·收口与尾巴】AITE_FT_* 读写两处硬编码无共享常量、AITE_CACHE_DIR/toxiproxy 默认地址未进 config；service/agent_client.py:135-139 心跳循环每 ~5s 新建 httpx.Client（原则 1 点名教训模式）；路由瘦身尾巴（runs 后台编排、runs/suites 配置解析重复、manual_eval N+1 创建循环、reports 渲染编排含临时目录不清理、perfdata 跨层直达、agents 状态白名单）。豁免台账评估：撤销条件比台账暗示的近——engine 组合根残留仅约 30 行，其中 4 行注册副作用 import 一次小 PR 可外移 run_service；其余豁免（RateLimiter/renderers/datasets.catalog/context→gateway）是运行时依赖，撤销需抽接口而非搬家。工作区卫生：web-ui 改版中途（6 个 _patch_*.py 一次性脚本与 2/3 后缀新 mockup 待收尾提交）。

- 2026-10-13 功能域评估场景/脚本/标签库后端实装（用户指示「对接功能后端并测试」；契约=V1-PLAN §10.4/§10.5 提案转正）：①场景双根——builtin（`aite/functional/scenarios` 包内只读）+ 用户根（config 新增 `DEFAULT_FUNCTIONAL_SCENARIOS_DIR=./data/functional/scenarios`），GET /functional/scenarios 合并扫描（id/显示名/维度/说明/形态 forms/受管脚本数/cases 数/**能力覆盖 coverage**=类型×行数聚合）、POST 建目录写 manifest.json（显示名缺省=id，id 正则约束）；②脚本（用例原子）CRUD——目录契约 `<场景>/scripts.json`（登记：name/case_type/external_ref/source）+ `scripts/<sid>.jsonl`（数据行）+ **派生 cases.jsonl**（全量脚本行拼接，pytest 引擎「目录即注册表」零改动消费），行级 harness.Case 校验（先补自动 id `sid.n` 再校验——Case 必填 id 的顺序教训）、场景内 id 唯一显式报错、脚本 id 全平台唯一、**legacy 收编**（首次写入若存在无主 cases.jsonl 自动收编为 legacy-cases 脚本 case_type=迁移，防派生覆盖丢失）、文件写出强制 LF（Windows CRLF 教训复用）；③维度标签库 dim_tag 表（Schema additive，create_all 建表）+ repo 段落 + 内置标签（API 契约/模型能力/Agent）与自定义合并去重返回；④导入 POST /functional/scripts/import（粘贴 jsonl 或数据集引用二选一，经 resolve_dataset_ref 读文件，同一 harness 校验）。路由 `web/routers/functional.py` 薄壳（异常映射 404/409/422：ScenarioReadOnlyError/FileExistsError→409）；测试 `tests/integration/test_functional_assets_api.py` +11（builtin/user 合并、manifest 详情、标签库去重、派生与 LF、builtin 只读 409、坏行 422 零落盘、跨脚本 id 冲突、patch/delete 重派生、legacy 收编、双来源导入）+ 真机 uvicorn 冒烟 11 步（httpx 驱动——Git Bash curl 中文 body GBK 化的老坑复现）。全量 649 passed + 3 skipped + ruff 干净 + lint-imports 2 kept 0 broken + 函数长度审计达标。**悬留**：任务 spec 增 version + 终态归档 version_results 表、GET /functional/version-results、/functional/quality/board 聚合（大屏/结果库后端）；场景 manifest PATCH、标签删除；CLI 双形态（本批 REST 优先，按需补 `aite functional-scenario` 子命令）；引擎默认消费用户场景根（现走 suite `tools.tests_dir` 指向用户根即用）。

- 2026-10-13（续）版本归档链路实装（功能域质量链路收尾，用户「继续」）：①契约扩展（additive）——`storage/models.py` 新增 `version_result` 表（unique：domain+version+model，同键覆盖取最新；details JSON 存 dimensions 场景→pass_rate / overall 加权 / cases / run_id）；functional 任务 spec 顶层增 `version`（parse_tool_suite 对未知顶层键宽容，实测无害）。②归档钩子——`service/version_results.py`：`archive_maybe` 挂 `SuiteTaskExecutor.execute`（仅 module=functional 守卫；spec 无 version → debug 跳过；归档异常仅告警不推翻任务结果，原则 9），数据源 = RunReport.summaries 的 `pytest:<场景>` 行（场景级 pass_rate），overall = count 加权；`compute_board` 纯函数 = 目标 vs 基线维度并集矩阵 + Δ + 判定（达标/劣化/新增/缺失，与精度劣化同口径）+ KPI（总率差/劣化维度数）。③端点——GET /functional/version-results（version/model 过滤）、GET /functional/quality/board（缺版本 422 显式报错），repo 追加 upsert/list 段落（INSERT 分支补 identity 三键的教训：WHERE 键 ≠ 落库键）。④测试 `test_functional_version_results.py` +9（归档覆盖语义/无 version 跳过/钩子守卫不外抛/model 提取/矩阵判定含新增缺失/缺版本 422/API 带外种子查询——跨 loop 驱动 app aiosqlite 的规避：独立连接同库种子）。真机冒烟 4 步过（结果库列表/版本过滤/大屏矩阵与 KPI/缺基线 422）。全量 658 passed + 3 skipped + ruff 干净 + lint-imports 2 kept 0 broken + 函数长度审计达标。**功能域质量链路（场景→脚本→任务→版本归档→大屏）后端至此闭环**；悬留：场景 manifest PATCH、标签删除、CLI 双形态、前端 Vite 工程对接。

- 2026-10-13（续二）功能域资产层架构审视修复（用户「审视代码是否符合架构要求，都修吧」）：逐条对照 CLAUDE.md 十四原则自查新代码，P1×3 + P2×5 + P3×6 全清。**P1**：①列表端点容错——单个损坏 manifest/scripts.json 只跳过 + logger.warning，不拖垮整个列表（原则 9，对齐 aggregator collect 先例；此前坏一个目录全页 422）；②归档跳过 status=error 的 run（崩溃半截数据不入版本矩阵，passed/failed 照归）；③legacy 收编逐行过 harness.Case，坏行显式报「无法收编」不静默入派生。**P2**：④create_dim_tag 捕获 IntegrityError → 409（并发双击唯一约束兜底，原先 500）；⑤大屏 KPI 增 added/missing 计数、improved 收敛为「达标且 Δ>0」（新增维度原先永不计入的语义 bug）；⑥case_type 白名单 CASE_TYPES（基础/边界/异常/兼容/迁移，防覆盖统计键漂移）；⑦行号解析收口 `functional/ingest.iter_cases_lines`（ingest/import_script/收编三处复用，坏 JSON 报「第 N 行」）；⑧场景 dimension 须在标签库内（create_scenario 转 async + repo，标签库与场景数据不再漂移）。**P3**：⑨_write_text 原子化（tmp + os.replace，崩溃不留半截 registry/派生文件）；⑩async 路由同步文件 IO 改 asyncio.to_thread（事件循环不被阻塞，service 保持框架无关）；⑪**CLI 双形态补齐**（`aite fscenario list/create`、`fscript list/show/import/delete`、`ftag list/add`，typer 子应用照 dataset_app 模板；CLI 侧业务异常本地映射退出码，不 import web 路由层）；⑫`docs/domain-backends.md` 新增 §16 契约文档（目录契约/端点/错误语义/CLI）。测试 +13（容错告警 caplog、收编坏行、白名单、行号、并发竞态、payload 内重复 id、未知维度、CLI 三组）+ CliRunner 表格在 80 列终端裁空单元格的坑（测试设 COLUMNS=240）。全量 **670 passed + 3 skipped** + ruff + lint-imports 2 kept 0 broken + 函数长度审计全绿（chaos observer 1 例 xdist 环境敏感 flaky 串行即过，既有问题与本次无关）。

- 2026-10-13（续三）前端原地对接层（用户拍板「静态稿原地对接」路径，区别于 Vite 工程化正路）：①`web-ui/mockups/serve.py` 升级为**同源反向代理**——`/api/*` → `AITE_API`（默认 127.0.0.1:8300），后端零 CORS 改动；后端不可达代理回 502。②新增 `mockups/v1/api-live.js` 对接层（仅 functional2.html 引入，其他静态稿零影响）：boot 探测 `/functional/scenarios` 可达性 → 左下角 **LIVE/MOCK 徽标**，live 时逐板块真数据渲染（脚本库 GET /functional/scripts 重映射回假数据字段名以复用整套筛选管线；场景卡 + 能力覆盖；任务列表 GET /functional/tasks；版本结果库；大屏 quality/board 的 KPI/矩阵/结论/趋势图全真），写操作真通（UI 新建场景/新建脚本（行编辑→harness 校验）/导入/删脚本/发起任务 POST tasks+run）；**后端不可达自动回退假数据**，评审功能不受影响。③踩坑三连（全部实测）：IAB **按路径缓存且无视查询参数与 no-store**——api.js 改内容必须**换文件名**（api.js→api-live.js 再升 ?v 均无效经验，改名才生效）；**`[data-page="board"]` 会命中侧栏 `<a>` 链接**（v1.js 壳层给侧栏链接也加了 data-page）——section 查询必须 `main.page-host` 前缀限定；IAB 内 goto 同文档 hash 变化不重载。④boot 逐板块 fail-soft：单板块失败 toast 告警并保持该板块演示数据，不推翻整页 LIVE 状态（ping 为唯一权威）。验证：IAB 实测 LIVE 徽标 + 脚本库/场景卡/任务列表/详情/版本结果库/大屏矩阵全真数据 + UI 建场景写操作真落盘 + 断后端回退 MOCK。定位边界：任务详情 live 视图仅概要（场景级明细/曲线等详情端点后续接）；human 评审页未对接；本层为过渡形态，Vite 工程化时照搬字段映射。

- 2026-10-13（续四）模型管理对接 + py3.10 兼容修复（用户「选不到模型」）：①兼容修复——functional_assets 用了 `datetime.UTC`（3.11+）在 conda evalscope（py3.10，日常运行环境）启动即 ImportError；改 `timezone.utc` 并在双解释器各跑 32 项资产测试全绿（教训入 HANDOVER：requires-python>=3.10 是硬约束，新代码禁 3.11+ 特性；.venv py3.14 测试过 ≠ conda 运行时可用）。②`GET /api/v1/common/models` 新端点（web/routers/common.py +2 测试）——模型预制清单（data/common.yaml 只读，name/model/base_url/api_key_env），是 suite `target.profile` 的可选值真源；common 缺失返回空不报错。③前端 api-live.js——创建任务「目标模型」下拉接 /common/models（真预制名作 value，静态演示文案退场；空库 toast 引导 `aite common show`）；被测版本 select 转 input+datalist（版本管理页未建前允许自由输入，历史版本作提示）；公共·模型管理页（common2.html）对接 GET/POST/DELETE /models（DB 网关别名注册表：列表/注册/删除）。**待定设计项**：模型双注册表（common.yaml 预制 = 任务 target.profile 来源 vs models 表 = 网关别名）的统一/同步方案未定，HANDOVER 已记录。

- 2026-10-13（续五）密钥库：api_key 密文存储 + 运行时透明解密（用户「api_key 密文显示，不想每次上服务器手动 set 环境变量」）：①`utils/secrets.py` 本地密钥库——存储 `data/.secrets.json`（name → {enc, mask}）+ 独立密钥文件 `data/.secret.key`（随机 32B，与库分离，均不入 git 已验证）；加密 = 本地密钥派生 SHA-256 流加密 + 随机 nonce（威胁模型：单机单用户，防随手翻库，不对抗本机攻击者——明文注释写明）；`resolve_secret(name, default)` 统一解析：**环境变量 > 密钥库 > default**，解密失败降级 default 并告警（原则 9）。②四处解析点收口替换：tools/base（evalscope/pytest/locust 共用）、core/loader（自研域 effective_api_key）、reliability_tasks（agent+probe）、rageval_adapter（judge）——密钥值经原链路流动，子进程/客户端零改动。③REST 三端点：GET /secrets（仅 name+掩码，明文/密文永不回传——测试断言钉死）、PUT /secrets/{name}（422 空值）、DELETE。④前端 common2 模型管理页加「密钥库」卡片（掩码清单 + 录入表单 password 型，PUT 后回显掩码）。测试 +8（回环/nonce 不重复/覆盖/清单无明文/持久跨实例/env 优先/store 兜底/坏条目降级/API 全链）。真机验证 6 步全过（PUT 200 掩码 → 明文不泄漏 → 无 env 解密成功 → env 优先 → 删除 → EMPTY）。**与既有红线的关系**：config 仍只存 env 名（api_key_env 惯例不变），密文值在独立存储且 gitignore；tools/base 明文密钥禁入配置的拦截保持不变。

- 2026-10-13（续六）功能域用例级数据落库 + live 详情真数据（用户「要有场景汇总/总通过率/成功失败个数/成功失败用例原始输入输出」+「状态要自动转换」+「只选 api_limits 却全跑」）：①**选择翻译修复**——整选场景原先不写任何过滤（datasets 空 = 适配器语义全跑，实测 9 场景全跑实锤）→ 前端提交改写 `tools.datasets = [勾选场景名]` + 部分勾选场景叠加 `tools.cases`（场景内用例 id，不带前缀）；parse/adapter 双重实测精确圈定。**语义说明**：错误 = 执行异常（超时/连接），断言不通过 = 失败，两者都拉低 pass_rate——错误 0 + pass_rate < 100% 是合法状态。②**用例级数据落库**（此前 pytest 工具路径只落 run 行 + junit，用例明细仅存 work_dir）——harness 插件 `pytest_runtest_makereport`（hookwrapper）逐用例写 `AITE_FT_RESULTS` 指向的 results.jsonl（external_key/desc/messages/params/expect/response/passed/error）；adapter env 注入路径 + collect 解析回填 `ToolRunResult.case_records`（additive 字段，tools/base dataclass 非 core 契约）；persist_tool_result 落三表：`save_metric_summary`（overall+每场景 → run_metric_summary，此前工具域从不写该表导致 GET /runs/{id} summaries 恒空）+ `repo.save_tool_case_record`（test_case 快照按 external_key 复用 → case_result 原始输出/错误 → pass 指标行，单事务三写）。③**live 详情真数据重写**：run 行（DB 权威）+ summaries 场景表 + KPI 四卡（总通过率/成功/失败/错误）+ **成功/失败用例原始输入输出**（GET /runs/{id}/cases，list_case_results outerjoin test_case 透出 input/expected）+ 运行中进度卡 + 刷新按钮；任务列表 5s 轮询状态自动转换（签名比对免重渲染扰动）。④**F5/侧栏详情恢复**：showLiveTask 写 hash `#detail/<taskId>` + sessionStorage；boot 用解析期捕获的初始 hash 恢复真实视图。测试 +10（secrets 8 + 归档 error 跳过 + KPI 语义；rageval 隔离补丁；工具用例落库链路）；全量 679 passed + ruff/lint-imports/audit 全绿。踩坑：heredoc 转义写坏 harness.py（patch 脚本改走 Write+Edit 工具链）；rageval「未设置应报错」测试被真实用户密钥库击穿——测试须隔离用户数据。

- 2026-10-13（续七）用例级采集真机联调收尾（用户实测揪出三连）：①`record_result` 建目录缺失——results/ 子目录无人创建 → 每用例 FileNotFoundError → pytest INTERNALERROR 整场崩（孤立冒烟没暴露：独立跑用了绝对路径且手建目录），补 `os.makedirs`；②NameError——harness 模块无 Path 导入，改 os.makedirs；③真机 6 步验证全过（PUT 密钥掩码 → 明文不泄漏 → 无 env 解密成功 → env 优先 → 删除 → 降级），真实 deepseek run 的用例级记录 2+ 条带原始输入输出入库并可经 GET /runs/{id}/cases 查询。**运维教训（用户实测反复撞上）**：aite serve 是长驻进程，Python 模块改动必须重启进程才生效——本轮「改完没重启」导致的误报（采集不工作/仍全跑）消耗了多轮排查；`[data-page=]` 选择器会命中侧栏 `<a>` 链接同理是环境类误报的高发区。

- 2026-10-16 清空功能域测试数据（用户指示「把当前的评估场景和脚本都删掉，我要从头开始构建」——上会话遗留待办，本会话第一件事执行）：①场景/脚本资产本就已空（`data/functional/scenarios/` 用户根不存在、无 scripts.json，上个会话的 UI 测试数据只落在 DB）；②`aite.db` 单事务清除 functional 域测试数据 **208 行**——functional_task 15（test~test10/verify-*）/ version_result 3 / test_run 30（engine=pytest 权威口径圈定，含 9 月初 19 条早期 ft-* 测试 run；与 web_service.run_domain 同源映射防漏防多删，交叉核对 13 套件无跨域共享）/ test_suite 13 / test_case 21 / test_case_result 21 / run_metric_summary 84 / metric_result 21，dim_tag 本就 0；③`reports/tool/pytest/` 报告产物目录（702K，恰为 13 个已删套件）；④备份 `aite.db.bak-20261016-0007`（已 gitignore `aite.db.bak-*`）。不动项：精度/性能/swebench 历史 178 run、9 个 builtin 场景（框架资产）、`data/datasets/functional/mini-set`（数据集非场景/脚本，不在指示范围，UI 可自行删）。活后端 8300 实测任务列表/版本结果库已空（SQLite 并发安全，无需重启），用户可从 UI 从头建档。

- 2026-10-16（续）前端两连修复（用户实测反馈）：①「评估场景刷新跳任务详情+加载失败」——api 对接层 fapiBoot 详情恢复兜底不看点位、无条件从 sessionStorage 恢复最后任务 id（清库后 404 卡死错误页）；修复 = 仅刷新时 hash 以 #detail 开头才恢复（停别的页刷新留在原地）+ 任务 404 优雅回落（清会话记录 + toast + 回任务列表）。②「新建脚本默认最少两行 JSON」——静态稿遗留两张演示行卡（512/64k 变体示例）+ 行卡无删除控件，UI 焊死最少两行；业务约束本就是 1..N。修复 = 行模板收口 nsRowHtml(n)（初始 1 空行）+ 每行删除控件（1 行下限 toast + 重编号）+ nsReset 打开即干净态（通用入口与场景卡入口全覆盖）+ desc 正则修复（/^\u884c\d+/ 匹配不了「行 1」带空格）+ 每行 ≥1 断言前置校验（harness 文档声明但代码未拦的空洞，前端补上防空断言恒过）。**api 对接层再次改名 api7.js → api8.js**（IAB 路径缓存铁律），functional2/common2 同引 api8.js?v=1。IAB 实测 9 项全过（刷新点位保持/404 回落/初始干净态/增删行/重编号/下限拦截/脏状态复位等）。既知：本轮实测证明当前 IAB 对 HTML 同路径内容修改可正常刷新（历史「必须改名」结论仅适用旧损坏实例），js 仍保守遵守改名约定。

- 2026-10-16（续二）断言参数行编辑器（用户「只让选类型没让填断言结果，怎么断言」）：新建脚本抽屉的断言编辑从「chips + 单个共享断言参数框」重做为**勾选类型即展开该类型参数行**——http_status（== 状态码，100-599 校验）/ JSON 字段（路径 + ==/∈/长度/类型 关系 + 期望值，harness _JSON_SPEC_KEYS 低代码子集）/ 包含关键词（+忽略大小写）/ 不含关键词（替换引擎无对应键的「正则匹配」死芯片）/ finish_reason（四枚举下拉）/ 非空·no_leak·sse_done（无需参数）；api 层按参数行结构化构建 expect（loose JSON 解析、逐项显式报错），「正则匹配」移除理由=引擎断言键族无此键、勾了必 422。踩坑两连：①重绘抹值——勾选新断言整体重绘参数区抹掉已填值，修复=重绘前按断言名收割值回填；②IAB goto 后立即 evaluate 读旧帧造成两连误判（一度误判为持久 HTML 缓存），同文档 reload 后再测才是可靠路径，HTML 同路径修改 reload 后可取新内容。**api8.js → api9.js 改名**，functional2/common2 同引。IAB 实测：monkeypatch FAPI.post 捕获 payload——expect 六键映射全对 + 值保留断言 + params 透传正确，零落盘。

- 2026-10-16（续三）vars 生成器族扩充 + 前端低代码封装（用户「随机乱码/数字/定长字符串要预制方法才能低代码」）：引擎 vars 占位符机制（决策 16 的"白送批量变体"）原只有 haystack 一个生成器、前端零入口。①后端 harness.py 生成器族扩至 5 个——random_string（定长+charset alnum/letters/digits/hex）/ random_number（区间整数）/ garbled（乱码：mojibake 底料 + 多区段 Unicode 采样，**刻意排除代理区与控制字符保证 JSON 安全可落库**）/ random_choice（参数化等价类）+ 可选 seed（缺省每次执行新随机值、给定可复现）；**正确性修复**：rendered_messages 在发送与结果记录两处各调一次，随机生成器会「记录的输入≠实际发送的输入」→ 实例级渲染缓存（同次执行一致、跨执行取新值）；加载期逐项校验未知生成器/缺参数显式报错。②前端低代码：新建脚本行卡新增「生成值占位符」区——五生成器按钮一键插入 {gN}（空 content 自动骨架且光标落位 content 内——实测抓到的组合流缺陷：否则第二占位符插到 JSON 结构外），条目行内编辑参数+seed，移除同步清占位符；api10.js（api9 改名）结构化组装 row.vars。③契约 domain-backends.md §16.4；测试 +6 全绿，全量 686 passed + 3 skipped + 四门禁绿。**运维**：pytest 子进程执行天然生效；已运行 serve 进程内资产校验链（顶部 import Case）需重启才能创建带 vars 的脚本。**踩坑**：addopts 已含 -q 再叠加 -q 成 -qq（pytest 9 隐藏汇总行），误判过进程崩溃。

- 2026-10-16（续四）评估场景删除（用户「评估场景不支持删除吗」——契约缺口，场景此前只有 GET/POST）：①后端 DELETE /functional/scenarios/{id}——service 层 delete_scenario 复用 _scenario_dir 解析（builtin→409 只读、不存在→404），整目录 rmtree（manifest+scripts 登记+行文件+派生 cases.jsonl，目录即注册表的删除语义）；历史任务/版本归档持配置快照与维度键分数，不随删除失效（docstring/确认框均注明）；路由 asyncio.to_thread + 既有 _asset_errors 映射零新增。②前端 api11.js（api10 改名）——用户场景卡加「删除」按钮（builtin 卡维持「随框架发布」无删除），confirm 确认框注明整目录语义，成功后 wireScenarios 全量重渲染（场景树/抽屉下拉同步）。③契约 §16.2 补 DELETE 行；集成测试 +2（删目录+404/409 语义、级联脚本后 GET 404）。全量 **688 passed + 3 skipped** + ruff/lint-imports 绿；IAB 端到端实测：UI 建探针场景→删除按钮→确认框→消失→磁盘目录移除，用户自建场景不受影响。

- 2026-10-16（续五）脚本生命周期补全四连（用户开始实建场景后的实测反馈）：①**脚本编辑**——后端 PATCH 本在但前端未接：openEditScript 详情回填（content/params/断言 chips+参数行/vars 全量重建，场景与脚本 id 编辑态锁定），提交切 PATCH（name/case_type/external_ref/rows 整体重写），编辑↔新建态互切复位（按钮文案/禁用态/editSid）；踩坑=动态行卡漏 bindChips（芯片无监听连勾选都不切换）。②**断言关系扩充**——引擎 _JSON_SPEC_KEYS 加 neq（不等于，恒区分大小写，与 eq 的 ic 语义刻意分离）与 contains（like：目标转字符串子串包含，ignore_case 生效）；UI 关系下拉补 !=/包含(like)，编辑回填反向映射。③**任务树数据源修复**（"新建任务选不到"）——根因：FT_CASES 只在 wireScenarios 置空、从不随真实脚本更新，新场景用例永远不可选；修复=list_scripts 增 row_index（[id,desc] additive）+ wireScripts 以其重建 FT_CASES（新用例默认全选语义）+ renderFtPick + sfScen 场景筛选下拉刷新（原写死 builtin）。④**新建脚本保存错位**——submitNewScript 用「drawer 内最后一个 select」定位场景下拉，vars/断言引入行内 select 后被劫持（scenario_id 解析成 alnum/neq 必 422）；修复=场景 select 显式 id=nsScen。**连带发现**：v1.js 的 [data-open-drawer] 监听只在启动期绑定，live 重渲染的行按钮（定向执行/过滤跑）全是死按钮→文档级委托兜底（重复触发幂等）。中文名脚本名称验证可用（id 规则英文不变）。测试 +3（neq/contains 单测、row_index 集成），全量 **692 passed + 3 skipped** + 四门禁绿；IAB 全链路实测（编辑回填→PATCH 持久化 neq/http_status、定向执行→树展开勾选、错位回归、探针数据清理）。既知：IAB goto 后立即 evaluate 读旧帧第三次复现，一律 reload 后再测。


- 2026-10-16（续六）http_status 关系形式（用户「http_status 还是只有==」）：expect.http_status 从纯 int 扩为 **int（== 兼容不变）或单键 {op: 值}**（eq/neq/gte/lte/in，值域 100-599）；「配了 http_status = 预期错误模式」的存在性派生对对象形式零改动；UI 参数行加关系下拉（∈ 逗号分隔多值），eq 提交保持 int 兼容、编辑回填双向映射。测试 +2，全量 **694 passed + 3 skipped** + 门禁绿，IAB 实测 int↔对象双向回填与持久化（探针清理）。踩坑：dict 字面量择一分发全分支急切求值（op≠in 分支 TypeError）——类型敏感择一逻辑用 if/elif。既知边界：http_status 的 ∈ 语义=命中任一即过；neq 语义=错误模式下发错响应当被测对象后「状态码不得等于某值」。
- 2026-10-16（续七）完整请求体低代码化（用户「输入只有 message，需要完整请求体」+「stream=true 输出是流式吗」）：①行卡新增 **tools 字段**（OpenAI tools 数组 JSON，请求体顶层透传；此前 UI 无法表达 FC 用例的 tools）+ **「预览完整请求体」**折叠区（实时合并 model 占位/messages/tools/params 渲染 JSON，占位符原样显示标注执行时替换）；params 标签/提示澄清为「请求体其余字段·任意顶层键透传」（stream/temperature/response_format…，下划线前缀=引擎控制键不进请求体）。②流式断言凑齐三件套：断言芯片新增**最少块数**（chunks_min ≥1 整数，SSE chunk 数下限）——与既有 sse_done/finish_reason 组成 stream:true 全套。③stream 语义已在引擎（零改动）：params.stream=true → _post_stream 真 SSE 逐块接收（不重试），断言对象=拼接后伪响应（content=delta 拼接、finish_reason=末块、附 _chunks/_sse_ok）。api17.js（api16 改名）。IAB 实测：预览合并正确、载荷 tools/chunks_min/stream 组装完整（monkeypatch 零落盘）。


- 2026-10-16（续八）请求预览 Postman 化 + 函数吞没事故：预览升级为可直连形态——目标模型下拉（/common/models 预制，空结果不缓存可重试）+ 真实 model 名 + 完整 curl（base_url+/chat/completions 同引擎 _request_url 规则；Authorization: Bearer ${api_key_env} 环境变量占位粘贴即用；单引号转义）+ 复制 JSON/curl 按钮。**事故与修复**：预览补丁区间替换吞掉 6 个函数定义（vars/asserts 全家），内联脚本尾部抛错致后续 addEventListener 全灭（表象=全页点击无反应、node --check 不报——运行期才炸）；定位=功能逐条探针→逐语句执行→函数存在性审计（6 缺全数补回）。**教训**：python 锚点区间补丁后三件套必跑——函数存在性审计 + node --check + reload 冒烟；语法检查不覆盖运行期引用。IAB 实测：deepseek/glm 预制切换 body model 与 URL/鉴权头联动正确，复制按钮就位（密钥永为环境变量占位不内嵌）。

- 2026-10-16（续九）任务状态与评估结果语义分离（用户「任务显示已通过但用例失败——通过是评估层说法，完成是任务层说法」）：功能域任务列表/详情状态徽章从 succeeded→「已通过」的误译改为任务层口径（已完成/执行异常/进行中/排队中/已取消，api 层 taskBadge 复用现有样式零 CSS 改动）；原「场景通过率」列改「评估结果」列 = result.status 引擎评估判定（通过/未通过/—）；筛选 chips 同口径改后端原始键。api18.js（api17 改名），后端零改动（kit 状态值与 result.status 本就分层，纯前端显示语义修正）。IAB 实测两列徽章正确。

- 2026-10-16（续十）评估结果列改通过率（用户「test3 断言失败却显示通过」——第三层语义澄清）：result.status 实为执行层判定（阈值未配置时进程跑完即 passed），非用例评估真值。评估列改显**通过率**：①suite_summary 增 evaluation_stats——functional pytest 行（dataset=pytest:<场景>，value=场景通过率/count=用例数）按 count 加权出 pass_rate/evaluated_cases（与 version_results 同源口径，其他域空缺省）；②前端评估列 = 百分比（100% 绿/0% 红/中间黄）+ 用例数；③存量任务 result 为执行期快照无 pass_rate → 前端 /runs/{id} summaries 兜底加权回填。api19.js（api18 改名）。测试 +3，全量 **697 passed + 3 skipped** + 门禁绿；IAB 实测 test3 = 已完成 + 红色 0% 1 例。至此任务三层语义对齐：任务状态（执行层）→ 评估结果（用例通过率）→ 详情 KPI/用例明细（逐例归因）。

- 2026-10-16（续十一）脚本库全瘫事故（用户「脚本库没有刷新出来」）：32 的 taskBadge/evalCell 定义在 api 层，页面内联脚本**启动期** renderRuns 引用（api 加载前）→ ReferenceError 中途死亡 → CASE_SCRIPTS/FT_CASES/nsRows/全部监听器未初始化（脚本库空+表单瘫，与决策 31 同症第二种成因）。修复=渲染函数移入页面脚本（api 运行期经 window 共用），api 删重复。**教训**：内联脚本启动期禁止引用后加载脚本的函数（v1.js 先加载可用；api 层只可运行期引用）；补丁审计三件套再加加载顺序检查。IAB 复测：脚本库 10 行、nsRows 初始化、任务列表两层徽章全恢复。

- 2026-10-16（续十二）vars 占位符扩展到 params（用户「deepseek 支持 input 参数怎么低代码加」）：任意顶层字段（input 等）= 行卡 params JSON 直接写（原有能力，澄清）；生成值需求 → 引擎 vars 替换从 messages 扩展到 **params**（rendered_params 与 messages 同源 _render_values，同占位符同次执行两处同值；发送/记录两调用点同步替换；原始 params 不改动；无 vars 原样透传）。测试 +3，全量 **700 passed + 3 skipped** + 门禁绿；前端 vars 提示语同步（页面文案零 js 改动）。

- 2026-10-16（续十三）case_type 芯片劫持修复（用户 422「非法用例类型: http_status」）：submitNewScript 的 case_type 取「抽屉内第一个选中 chip」——干净态打开+只选断言时被断言芯片劫持。修复=类型芯片组显式 id=nsCaseType，提交/编辑回填均组内解析（未选默认「基础」）。api20.js（api19 改名）。IAB 回归过。审计结论：本会话定位符劫持三连（场景 select/case_type chips/+inputs 索引潜在）——表单取值一律显式 id，禁「第 N 个/第一个 .on」。

- 2026-10-16（续十四）对话承载切换 + 行复制（用户澄清 deepseek input=对话放 input 字段；参数校验用例「新增→抄 messages→抄参数→改参数」流程不顺）：①行卡「对话承载」下拉 messages/input/不发对话——input 与 none 提交时 messages=[] + params._omit_messages=true 引擎控制键自动省略（引擎原有能力零改动），input 模式对话数组进 params.input；预览同步按承载合并；编辑回填反向解析（控制键不回显）。②行卡「复制此行」（cloneNode 保值 + bindChips 重绑 + 重编号）——同模板多变体改参数即得。参数校验用例工作流收敛为：新建→选承载→params 填坏值→http_status 断言→保存；多变体=复制改参。api21.js（api20 改名）。IAB 载荷实测过。

- 2026-10-16（续十五）常用参数结构化 + JSON 高级框 + 原始报文模式（用户「常用参数加输入框，JSON 框构造结构/key 破坏场景」）：行卡 params 区三层重构——常用参数网格七项（temperature/top_p/max_tokens/stop/stream/response_format/thinking，留空=官方缺省）/ params JSON 高级框（合法 JSON 覆盖合并常用值，乱写 key 原样透传）/ 原始报文开关（_raw_body 原样直发，构造非法 JSON 结构破坏 400 场景无需合法）。**设计要点：常用框不做合法值拦截**（非法值透传配 http_status 断言=参数校验用例）。nsCollectParams 页面层单一来源（预览/提交共用）；编辑回填按已知键拆控件、_raw_body 反解原始模式；nsReset 补清 textarea/checkbox。api22.js（api21 改名）。IAB 四场景实测全过。

- 2026-10-16（续十六）常用参数网格补全至设计文档全量（用户「用例里的参数没全部支持」）：网格 7→14 项——新增 stream_options(include_usage)/tool_choice(缺省/none/auto/required/具名+函数名框显隐)/logprobs/top_logprobs/user_id/freq_penalty/pres_penalty（废弃兼容用例）。至此 deepseek-inference-api.md 测试设计涉及参数控件化 100%（承载/model 覆盖/原始报文此前已支持）。采集与编辑反解同步扩展（具名对象↔下拉+函数名框）；承载/具名显隐迁 change 委托。api23.js（api22 改名）。IAB 载荷实测 7 新键 + 具名显隐双向全对。踩坑：探针 new Event 默认不冒泡，委托收不到——须 {bubbles:true}。

- 2026-10-16（续十七）行复制丢参数修复（用户「复制的时候有一些参数没有复制」）：cloneNode(true) 只拷属性，用户手输的 dirty value（输入框/下拉/勾选）不在属性里 → 克隆后丢参（此前 textarea 恰好保值造成误判）。修复 = 克隆后按序逐控件显式拷贝 value/checked，17 项控件全量比对零差异。教训：cloneNode 复制表单必须显式搬运 dirty 值。

- 2026-10-16（续十九）断言芯片补全至引擎全量 + 文档断言明确化（用户三问：校验没明确/没法选/怎么解释）：①引擎 resp 断言补 contains（错误体模糊匹配，受 ignore_case）；②低代码断言芯片 9→15（引擎 16 键全覆盖）：指定工具被调用/工具参数包含/不发起工具调用/无乱码/无复读/错误体字段（resp：path+==/包含/长度/类型/非空，仅预期错误模式生效）；③设计文档新增断言速查表（芯片×键×语义×生效范围×盲区），OC 表 18 行断言要点明确为芯片配置，修正 OC-11/17 thinking+具名约束违规（官方 400）。api24.js（api23 改名）。相关 75 passed + 门禁绿；IAB 载荷实测过。残余盲区：成功响应外层字段（usage/model 回显/reasoning_content）——resp 仅错误模式，文档已标注。
- 2026-10-24 精度域（含 agent 评测）前端全链路实装（用户「精度+agent测试都接入前端，我来测试」）：functional 原地对接模式（HANDOVER 决策 20）推广到精度域。**后端**：①accuracy_assets 脚本资产层——脚本 = suite raw 同构（V1-PLAN §10.4 accuracy 形态），builtin = examples/accuracy/*.yaml 扫描（18 个：mmlu/ceval/gsm8k…+ agent_eval/swebench 等 agent 预置，裸 suite raw 即时包装 doc，损坏文件跳过告警），自定义 = data/accuracy/scripts/<id>.yaml（LF 强制 + 原子写），保存经 resolve_suite 真实校验（工具域轻校验/自研域 SuiteConfig 强校验双路径）；②GET/POST/PATCH/DELETE /accuracy/scripts（routers/accuracy.py，404/409 只读与重名/422 校验错误映射与 functional 同口径）；③SuiteConfig 增 version（additive，被测版本标注；工具域 raw 透传零改动——SuiteConfig extra=forbid，自研套件顶层 version 原会 422，此字段解锁前端版本输入直传）。**前端**（accuracy2.html + api-acc1.js 新文件名防 IAB 路径缓存）：任务列表（任务层状态徽章 + passed chip→succeeded 口径对齐 + 计数实时 + 分数列 /runs/{id} overall 兜底）、任务详情 live（指标汇总 metric×dataset + overall 加权行、失败样本卡、报告产物路径、running 5s 自刷新、停止）、脚本库（预置/自定义分组 + 发起任务/删除/复制）、创建抽屉两态（从脚本发起 = spec 深拷贝 + 运行参数覆盖；自定义六卡 = suite raw 组装严格按 TOOL-SUITE-PARAMS 契约：抽样 seed 在 extra_args、thinking→reasoning_effort、高级 JSON 合并 dataset_args/generation_config）、数据集选择器 = GET /tools/benchmarks 真目录（Agent/RAG 类型按 tags 派生，gaia/xlam_fc/swe 系 Agent 型可见）、模型下拉 = /common/models + DB 注册 + 自定义端点（judge 下拉同源）、版本输入自由填写（datalist）。定时执行前端显式拦截（调度器 §8.5 未实装）；趋势对比/人工评测保持演示数据（§8.6 缺口）。测试 +16（unit 11 + integration 5），全量 **717 passed + 3 skipped** + ruff/lint-imports/函数长度审计全绿；隔离冒烟（scripts/smoke_acc_live.py）5 步全过。**踩坑**：后台起服务必须显式 storage_url（create_app 默认=用户库）+ 后台 shell 孤儿进程占端口——冒烟曾两度打到用户库，已全部清理并以「冒烟后断言用户库 0 残留」收口；8300 旧进程须重启才有新路由。


- 2026-10-24 导入覆盖语义（用户拍板「同 id 要做成覆盖」——此前同 id 重导 409 拒绝）：import_script 同 id = **整体重写**（update_script 链路：行级校验+重派生），跨场景覆盖 = 迁移（删旧建新），全新 id 照常创建；响应增 overwritten 标记（additive），前端 toast 区分导入/覆盖。api25.js（api24 改名）。集成测试 +1，全量 **718 passed + 3 skipped** + 门禁绿。
- 2026-10-24（续）全局版本管理 + 精度对接（用户「精度要对接全局的版本管理」）：`model_version` 新表（(model,version) 唯一，Schema 只增；与 version_result 分层——登记先行/归档随后）+ GET/POST/DELETE `/versions`（common.py；重复 409、空值 422、按模型过滤）。前端：common2 模型管理页「版本管理」卡（清单+登记+删除，模型下拉 = 注册 ∪ 已登记 ∪ 手填）——适配层 api25.js→api26.js（用户侧当日上午 api24→api25 改名，本轮内容变更续升名，IAB 路径缓存约定）；精度创建抽屉「＋ 新版本」两按钮接 POST /versions（就地登记，模型取当前预制 profile，自定义/脚本内置 prompt）+ 被测版本 datalist = GET /versions（api-acc1.js→api-acc2.js）。功能域版本输入暂沿用归档 datalist（统一待后续）。测试 +3，全量 **720 passed + 3 skipped** + 四门禁绿；隔离冒烟（临时 DB）：/versions 全链路 + 精度任务引用登记版本 + 脚本库回归，用户库零污染。契约文档 V1-PLAN §10.6。


- 2026-10-24 编辑抽屉「从 jsonl 导入行」（用户「编辑时选导入，场景参数就不用再选」）：行区新增 jsonl 导入入口——粘贴 JSONL（逐行行号报错）→ 解析并替换全部行卡（nsFillRowFromData 全局回填：常用参数/断言芯片/vars/承载全量还原），审阅后保存（新建=create/编辑=PATCH 整体重写），场景/id/名称保持现值。重导修正版无需再走数据集导入表单。注：另一会话已演进 api26.js（api25 扩展）与 api-acc2.js（accuracy 域），本轮基于 api26 做，改名 api27.js（functional2/common2 同引）。openEditScript 行回填内联段抽取为 nsFillRowFromData 单一来源。IAB 实测：18 行编辑回填回归 + jsonl 导入 2 行控件还原全过。

- 2026-10-24 失败用例卡四段重做（用户「失败用例内容=输入/输出/断言/失败断言日志；有时一个输出有时两个」）：双输出根因 = 输出 JSON 块与失败日志内嵌「输入|/输出|」摘要行重复（通过用例无日志块故只有一个）。caseBlock 重做为四段模型：①输入按 message 角色分块（长文滚动容器）②输出 content 正文为主体+元信息标签（finish_reason/chunks/sse_done/usage）+原始响应 JSON 折叠③断言对照期望|实际两列并排④失败原因首行醒目+引擎原始日志折叠全文（首屏去重）。全卡复制按钮（document 委托+__copyStore，clipboard API）。api28.js（api27 改名）。IAB 实测 test6 失败任务 8 卡全过。踩坑：heredoc 双层转义第三次把 
 写成真实换行——长 JS 块一律 Write/Edit 工具链。

- 2026-10-24 断言对照字段错位修复：cases 接口返回 expected（已解析断言对象），前端误读 x.expect 致对照表恒「—」；改 x.expected 后期望列=执行期断言快照、实际列=前端可判定项实测/引擎判定项标灰。api29.js（api28 改名）。

- 2026-10-27 用例卡复现三件套 + 长输入折叠（用户「想复现场景拿不到原请求；超长输入别全打」）：失败用例卡输入区三按钮——复制输入 JSON（完整）/复制 curl（任务预制模型经 /common/models 解析 base_url+model，密钥 ${api_key_env} 占位，URL 拼接同引擎 _request_url 规则）/下载请求体 .json（超长输入走文件）；消息级折叠 content>300 字默认收起可展开。澄清：oc01 的「...」是提示词原文非 UI 截断。零后端改动，api30.js（api29 改名）。IAB 实测 curl 按预制解析正确、三按钮就位。

- 2026-10-27 curl 丢 messages 修复（用户实测「复制 curl 只有 model」）：/runs/{id}/cases 的 input 字段即 messages 数组本身，caseBlock 按包裹对象取 io.messages 落空 → curl body 只剩 model、输入区走 JSON 兜底渲染。修复 = 前端归一化（Array.isArray(input) → {messages: input}）。api31.js（api30 改名）。IAB 实测 curl -d 含完整 messages、输入区按角色分块恢复。

- 2026-11-08 掌控工程一期（owner：项目膨胀后「掌握不住、牵一发动全身、文档看不过来」）：①**架构地图** `docs/architecture-map.html` + 生成器 `scripts/arch_map.py`——分层带/模块依赖图/REST 全量清单/CLI 全集/适配器资产表/核心数据结构/必要性审计/术语表/文档裁决/仓库健康，事实全部从代码 AST + git 实时提取，内置知识逐条校验、漂移即拒绝生成（文档过期即撒谎，地图是算出来的不是写出来的）；重构后 `python scripts/arch_map.py` 再生。②**积压分域提交**：09-30 后五周未提交的 34 改 + 40 新文件按依赖顺序拆 6 个 commit（密钥库 → 模型/版本基座 → 任务/工具加固 → 功能资产 → 精度资产 → REST/CLI 面），每个提交只引用已入库代码。③**杂物清理**：根目录调试探针、mockup 一次性补丁脚本 13 个、压测预览产物、过期库备份删除；`docs/archive/` 归档 requirements-datasetgov / ollama-guide / test-designs（引用同步）。④**协作与教学总纲**写入 CLAUDE.md（管理者-执行者 / 新增代码必附设计说明 / 奥卡姆剃刀 / 架构检查成本不随代码膨胀）。⑤前端主航道决策与架构债记账（见上两节），模块 L 立项。数据实测结论：最常改的文件（适配器，27/17 次提交）爆炸半径为 0、契约文件波及面最大但几乎不动——震荡被隔离在正确位置。全量 **722 passed + 3 skipped** + ruff/lint-imports/函数长度审计全绿。

- 2026-10-27 复制 curl/JSON 升级为完整请求体（用户 Postman 复现 vs 平台不一致的根因收口）：用例卡 curl/下载 body 从「model+messages 最小体」升级为合并 cases 接口新透出的 raw.params（剔除下划线控制键）+ raw.tools——与引擎 _post 合并顺序一致（model→messages→params→tools）。存量回填：test6 的 11 行 raw.tools 按「params 含 tool_choice 才发过 tools」规则精确回填。api32.js（api31 改名）。IAB 实测 curl body 12 键齐全。运维注意：本会话后台服务进程反复被外部回收（前后端各 2 次）——操作前用 netstat/curl 确认服务状态。

- 2026-11-08 移除内置功能场景，场景全部用户自管（用户「把内置的评估场景都给删了，我要从头开始设计功能用例」）：①删除 `aite/functional/scenarios/`（9 场景 64 例）与 `tests/unit/test_functional_scenarios.py`；②引擎缺省根改 = `config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR`（与资产层同源，tests_dir 仍可覆盖），`tools.datasets` 语义统一为「子目录（场景）名筛选」，`list_tasks` 枚举托管根（空返回 [] 不报错）；③资产层去 builtin：`ScenarioReadOnlyError`/409 只读语义、响应 `source` 字段与 `?source=` 过滤全部移除（**API 变更**：GET /functional/scenarios 无 source 参数），CLI `fscenario list` 去 `--source`；④测试重写：pytest_adapter 单测改临时托管根/夹具根（新增「缺省根 = 托管根」「list_tasks 空根」两用例），assets API 集成测试 builtin 409 断言改 404/用户根语义，agent_dataset 收缩为 harness.Case 校验器防线测试；⑤README/functional-testing.md/domain-backends.md/examples 同步（含 API 契约域「已知差异」段落随场景移除）。全量测试通过（exit 0）+ ruff 干净。背景：用例资产规划在外部用例平台，本平台收敛为「执行 + 结果」，映射走行 id / external_ref（见 2026-11-08 会话结论）。

- 2026-11-08（续）评估场景/脚本命名约定落地：英文编号 + 中文名称（用户「场景id改成英文编号，显示名改成中文名称」）：①资产层校验口径与措辞——场景/脚本 id = 英文编号（场景 id 放宽允许连字符 `-`，与脚本 id 对齐，报错文案带「英文编号」），display_name/脚本名称 = 中文名称；②质量大屏维度行补 `display_name`——归档键仍是场景 id（引擎口径），读取时经资产层 join manifest 中文名（场景删除/重建按当前登记取名，找不到回退 id；join 失败告警不拖垮大屏）；③REST 契约文档化——ScenarioIn/ScriptIn/ScriptImportIn 全字段补 OpenAPI description（前端对接即文档）；④CLI 帮助文案同步（fscenario create / fscript import）；⑤mockup functional2.html 表单标签同步（场景 id（英文编号）/显示名（中文名称）/脚本 id/脚本名称 4 处）。测试 +2 断言（连字符场景 201、大屏中文名 join+回退），全量回归通过 + ruff 干净。

- 2026-11-08（续二）场景删除保护语义定稿（用户摆出两逻辑「有绑定拒删 vs 强制级联」，合成默认拒删 + 显式 force）：`delete_scenario(scenario_id, force=False)`——脚本物理内嵌场景目录（脚本无法脱离场景存在），默认**有内容拒删**（脚本数或用例行数 > 0 → `ScenarioNotEmptyError`，继承 FileExistsError 复用 409 映射零改动；信息带「N 个脚本 / M 条用例」供 UI 渲染；无登记但有无主 cases.jsonl 同样拒删——用例行也是资产），`force=true` 确认后级联整目录，空场景（仅 manifest）直接删。REST 增 `?force=` 参数；CLI 新增 `fscenario delete [--force]`（补齐与 fscript delete 的对称性）；mockup api32.js 删除流 = 首删 409 → 二次 confirm（提示不可恢复）→ force 重试。顺手清理删 builtin 遗留的前端死逻辑：场景卡 `source === "user"` 按钮分支恒渲染、创建脚本场景下拉去掉 source 过滤（原 bug：下拉恒空）、发起任务 BUILTIN_IDS 混跑检查与 tests_dir 分叉移除（统一托管根）。测试：API 拒删/级联/legacy 拒删 +3 断言、CLI 双形态 +1，全量回归通过 + ruff 干净。

- 2026-11-08（续三）导航徽章/筛选条假数字治理（用户截图反馈「导航栏的数字显示不对，不知道显示的是啥」）：根因 = 静态稿时代写死的演示数字（功能域 23/13/26/6）在接真实后端后口径全错。①功能域：导航徽章接真实计数（wireScenarios/wireScripts/wireTasks/wireResults 回填 + 5s 轮询同步；page-list JSON 改写 + 侧栏 DOM 按需补建双写，路由重渲染后仍生效），任务列表统计卡从假比率换为真实计数（累计运行/运行中/评估场景/版本结果），状态筛选条「全部 23/执行异常 2」改为 renderRuns 时从数据实算（v1.js 新增 syncStatusChips 共享助手）；②精度域同 bug 同修（wireAccTasks/wireAccScripts/wireAccPolling → setNavCount；筛选条 128 实算）；③perf/reliability/common 无真实数据源的页面直接移除假徽章（宁缺毋假）。浏览器实测（IAB + 8766 预览服务反代 8300 用户真实库）：功能域 6/1/0/2、精度域 3/18 与后端 API 真值一致，切页重渲染后徽章保留。共享助手落 v1.js（setNavCount/syncStatusChips），api32.js fapiSetNavCount 收缩为 NAV_COUNTS 缓存 + 委托。运维注意：8300 常驻后端仍是旧代码（响应含 source 字段），今日全部后端改动（删除保护/大屏 display_name 等）须重启后端才生效。

- 2026-11-08（续四）功能脚本编辑重做：纯 JSON request + 行级用例字段 + 工具库（用户三点：从场景新建锁定场景 / 脚本 id·用例 id·行 id 与类型等级没处写 / 纯 JSON 编写数据行 + tool_calls 工具库）。①**分层落地**：场景 → 脚本（脚本 id = jsonl 文件名）→ 数据行（行 id = 执行与回填唯一键）；行上新增 `case_id`（外部用例平台编号，多行可同挂）/ `type`（基础/边界/异常/兼容/bad case）/ `level`（P0-P3），资产层枚举校验、空值剔除；场景覆盖统计改按行类型（旧数据回退脚本类型）+ 新增 levels，脚本清单增 row_types/row_levels。②**回填链路**：harness Case.meta 透传 case_id/type/level → 执行记录 → test_case.metadata → `/runs/{id}/cases` 返回 metadata（外部平台按用例 id 对表）。③**工具库**：`<托管根上级>/tools.json`（OpenAI tools 条目），REST GET/POST/PUT/DELETE `/functional/tools`；数据行 request.tools 写工具名 = 引用，保存时校验存在，行文件存名、派生 cases.jsonl 展开为完整定义（引擎零改动）；改定义自动重派生引用场景；被引用拒删（409 列出脚本）。④**前端**：行卡片从十几个结构化参数控件改为「行 id / 用例 id / 类型 / 等级 / 描述 + request JSON（填入模板 / 从工具库添加）+ 生成值占位符 + expect 低代码芯片 + 预览 + 复制（行 id 清空）」；request ⇄ 行存储拆合（messages/tools 独立、其余键 = params、不写 messages = _omit_messages）；新增「工具库」页（列表/新建/编辑/删除，导航计数）。⑤从场景卡「＋ 脚本」或场景「管理脚本」内新建/导入 → 自动选中并锁定当前场景。⑥修复存量 bug：新建抽屉按位置取输入框导致**脚本 id 与名称对调**，改按元素 id 取值。测试 +3（行级字段校验/覆盖、工具库引用-展开-重派生-删除保护全生命周期、Case.meta），全量通过 + ruff 干净；隔离环境（临时库 + 8301/8767）浏览器实走：锁定、模板、选工具、断言、保存、落盘、展开、编辑回填、复制全部符合预期。注意：8300 常驻后端需重启才有工具库与行级字段。
- 2026-11-08（续五）精度资产域改版：场景维度 + 无预置（用户「精度当前的脚本也全都删了，脚本同样要支持脚本英文ID和中文名称，列信息中加数据集和条数说明就行，不用放详情描述，另外也要支持场景维度，场景和脚本都不需要预制」）：与功能域（续一/续四）同构两层。①**无预置**：examples/accuracy 18 个预置脚本全删（git rm），builtin 扫描/只读 409 语义整体下线，场景与脚本全部用户自管，托管根 `DEFAULT_ACCURACY_SCRIPTS_DIR`（扁平 <id>.yaml）→ `DEFAULT_ACCURACY_SCENARIOS_DIR`（./data/accuracy/scenarios/<场景>/manifest.json + scripts/<sid>.yaml，脚本 doc = {id, name, description, spec, created_at, updated_at}，无 scripts.json 登记——YAML 自描述单源）；维度标签复用共享 dim_tag 库（create_scenario 经 functional_assets.list_dim_tags 校验）。②**清单瘦身**：GET /accuracy/scripts 与场景 script_entries 只带 id/name/场景/datasets/case_count/updated_at，不带 description/spec（详情接口才有）；**条数口径** script_metrics 尽力落定——自研域 catalog stats → builtin 打包样本行数 → 本地文件行数可算合计，工具域（engine=evalscope）tools.datasets 是 evalscope 侧基准名跑上游全量、本地不可知 → None，混合引用部分不可落定 → None（不虚报部分和）。③REST：routers/accuracy.py 重写为场景 CRUD（默认有脚本拒删/force 级联）+ 脚本 CRUD（英文ID 全平台唯一/中文名称/spec 经 resolve_suite 真实校验 + 数据集非空；PermissionError 只读映射随预置下线移除），错误映射 404/409/422 与 functional 同口径。④CLI：`aite ascenario list/create/delete` + `aite ascript list/show/create(--file spec.yaml)/delete`（与 fscenario/fscript 同构，列表带数据集/条数列）；冒烟脚本 smoke_acc_live.py 重写（双空清单起步 + 条数口径 + 删除保护）。⑤文档：domain-backends.md 新增 §17 精度资产层；README 示例表去 accuracy 陈旧行（原表引用的文件早已不存在）；dataset-guide / TOOL-SUITE-PARAMS / web-ui HANDOVER、V1-PLAN（状态表+§6A.2+§8 条目 8）同步新契约。⑥测试：unit 重写（无预置空清单/场景脚本 CRUD/瘦身口径/全平台唯一/条数四路落定+None 语义/损坏跳过/删除保护）+ 新增 CLI 三态 + 集成重写（REST 全链路含自研域条数落定），坑：python -m aite.cli.main 会撞第 679 行 `if __name__ == "__main__"` 早退、后续 add_typer 不注册（-m 路由只认前半文件命令，console_scripts 入口无碍）——冒烟须走入口脚本或 import app。悬留：V1-PLAN §8 条目 8 的 `script_id`/`overrides` 任务创建键仍未做（前端重对接见续六）。
- 2026-11-08（续六）精度前端按新契约重对接（用户「赶紧做」）：①**accuracy2.html 脚本库页**——说明条改「无预置 · 场景维度」，筛选条 = 全部 chip + 场景下拉（带脚本数）+「＋ 新建场景」+「删除该场景」（选中场景才出现），表格列改为 脚本（中文名称 + 英文 id）/场景/数据集/条数（None 显示 —）/更新/操作（发起任务·编辑·删除；预置「复制」与来源列随预置下线移除），演示数据换成按场景归属的自建脚本；创建抽屉复用面板下拉按场景 optgroup 分组，快照改异步（静态稿用清单字段，live 走详情）。②**对接层 api-acc2.js → api-acc3.js**（新文件名防 IAB 路径缓存）：清单瘦身不带 spec → `aapiScriptDoc` 详情懒加载 + 缓存（快照/发起/编辑共用，清单刷新即失效），发起任务从详情深拷贝 spec 叠运行参数；编辑 = 名称/说明 prompt + 可选 spec JSON（PATCH，后端真实校验）；新建场景 = 英文编号/中文名称/维度标签（列共享标签库）/说明；删除场景 409 有脚本 → 二次确认 force 级联；「保存为脚本」须先选场景（POST 带 scenario_id）。③**两个顺手修**：a) 后端 _validate_spec 直接对原件跑 resolve_suite，apply_common 就地展开 target.profile + 并入 common 工具缺省（本机 python_exe）→ 脚本落盘固化了展开端点与本机路径（旧实现同病，E2E 无 common.yaml 未暴露，读码发现）——改为深拷贝校验 + 回归测试（去修复即红）；b) 全页 `.ribbon` 悬浮提示 z-index 50 > 抽屉 45，压住所有抽屉右下角主按钮（elementFromPoint 实证命中提示条，真实鼠标点不到「创建任务」）——降到 40 + pointer-events:none，五页 v1.css?v=18。④**实弹**：隔离后端（临时 CWD + 临时库，8311）+ serve.py 反代（8767）+ IAB 驱动：无预置空态 → 灌 2 场景 2 脚本（自研域条数 20 / evalscope —）→ 场景筛选 → 发起任务（懒加载快照 10 行）→ 仅创建落库（spec = 脚本 spec + run_name/version 覆盖，未触发 evalscope）→ 编辑 → 新建场景 → 删空场景 → 有脚本场景 409 + force 级联 → 保存为脚本带场景，全链路通过；收尾按端口只杀自起进程、临时目录清理、用户 8300 与 data/ 未触碰。**坑**：IAB 后台态定时器重度节流（20ms → 836ms）+ 输入事件不投递、playwright 可操作性等待超时、原生 prompt 挂在宿主层 getJsDialog 取不到——自动化改用页面内 element.click() + prompt/confirm 打桩驱动同一处理函数，遮挡类问题用 elementFromPoint 命中测试兜底；Windows Git Bash 下 curl -d 中文参数编码损坏（400 解析失败），灌数改用 httpx。测试：736 passed + 3 skipped，ruff/lint-imports/函数长度/arch_map 全绿。提醒：用户 8300 旧进程需重启才有 /accuracy/scenarios 新路由。
- 2026-11-09（续七）用例明细列表化 + 编辑直达脚本行（用户「错误和正确用例的展示直接做成列表的形式，把用例id，行id，等级哪些标签都带上，最后给一个编辑按钮，让我可以直接通过返回的结果调整json行」）：①**前端 api32.js**：明细从 `<details>` 卡片改列表行——状态徽标 + 行 id + 用例 id/类型/等级标签（执行记录 metadata 透传）+ 失败首行红字（title 悬浮全文）+「编辑」「详情」双按钮；详情展开原有 输入（复制 JSON/curl/下载）/输出/断言对照/引擎日志，默认收起、点行头亦可开合（事件委托单绑定 __caseRowBound）。②**编辑直达 editCaseRow**：行 id + dataset（场景名）→ `GET /functional/scripts?scenario=` → 逐脚本详情匹配行 id → `openEditScript` 开编辑抽屉（行卡已回填可直接改）→ `.ns-rid` 定位行卡 scrollIntoView + 2s outline 高亮；场景缺失/行已删改 id → toast 显式报错。③**后端断链修复**：`save_tool_case_record` 行级 dataset 统一落场景名（pytest 适配器只带 `scenario` 键、显式 `dataset` 优先；原实现读不到 → dataset 恒 None，前端无从回跳），`_result_dict` 本就透出 dataset/metadata，REST 零改动。④**实弹**：隔离后端（临时库 8311 + 临时包装把 mockups 挂 /mockups 同源，FAPI.base 零改动）+ 种子（REST 建场景脚本、仓储直写任务/run/3 条记录/pass 摘要——须用 MetricSummary 而非 ToolMetricRow，后者缺 aggregation 字段）+ IAB 实走：3 行标签齐全、详情展开含输入输出断言、编辑→抽屉 seed_core 编辑态行 1（oc01）定位回填全对、场景锁定 demo；截图目检列表布局符合预期；收尾 force 删场景、临时脚本/库清理。**坑**：场景/脚本落文件系统托管根不在 sqlite——删库重灌会 409，种子须容忍复用；失败行 innerText 不含折叠 details 内容属正常（错误首行已在列表行内）。测试：engine_end_to_end 扩展（scenario 回退落库/显式 dataset 优先/metadata 三标签断言），ruff 干净。注意：8300 常驻后端需重启才有 dataset 修复；旧 run 的 dataset 为空 → 编辑按钮 toast 提示无法定位（旧数据无解，新执行即正常）。
- 2026-11-11 模型管理密钥选择化（用户「手动预制模型、选择已在平台上归档的 API_KEY、调用时可选模型」）：现状 = 模型预制/注册与发起时模型下拉均已存在（common.yaml profile + DB 注册 + functional/accuracy 发起下拉），唯一断点是**全部 api_key_env 都是纯手填**——注册模型时不知道名字是否已归档，也无法直接选。①v1.js 共享助手 wireKeySelect/keyEnvValue（已归档密钥下拉带掩码 + 仅环境变量回退，data-keysel/data-keyenv 容器约定，幂等可重灌）；②common2 注册抽屉：密钥下拉 + 现场归档新密钥（先 PUT /secrets 再 POST /models 一气呵成）；③后端 POST /models 响应加 `key_archived`（resolve_secret 试解析，false 不阻断、前端警告「未归档且环境变量未设置」）；④发起抽屉自定义端点面板（functional2/accuracy2 两页三处）同改密钥下拉，提交逻辑 bindTaskSubmit/aapiTargetOf 改读下拉。运行时链路不变（api_key_env → resolve_secret 环境变量 > 密钥库），REST 契约仅响应加字段。集成测试 +3 断言（key_archived 三态），门禁全绿；IAB 隔离实例三页全链路验证（注册两路/警告提示/accuracy target 构造/functional2 真实发起 spec 落对）。范围外：perf2/reliability3 仍纯静态稿（未对接，无「调用」可言），待两域接线时按同约定补。注意：常驻 aite serve 需重启才有 key_archived 回告；静态稿刷新即生效。
- 2026-11-11（续）版本管理对接收口 + 模型管理组拆三页（用户「版本管理也对接上，要能手动新增版本，落库后在所有需要版本的地方都可以选而不是自己手填；不要写啥登记版本就是创建版本；模型/版本/密钥放统一地方，左侧单独导航」）：后端零改动（model_version 表 + GET/POST/DELETE /versions 2026-10-24 已实装，本轮纯前端收口）。①common2 模型管理组拆独立三页 = 模型/版本/密钥（page-list + v1.js GROUP 映射，左侧专属导航；数据集/执行机维持单页通栏）；版本页 = 版本清单 + 创建版本卡（修空注册表「其他（手填）」手填框不显影的边界：dispatchEvent(change) 初始同步）。②功能新建运行抽屉被测版本切全局版本注册表（此前沿用功能归档 datalist 手填，HANDOVER 悬留销号）：下拉 = GET /versions，当前模型有版本按模型过滤、无则跨模型兜底（选项带「模型 · 版本」前缀），换模型/手填模型名实时重过滤；「＋ 新版本」就地创建（POST /versions → 回填下拉立即选中）；提交补必填校验。③全 UI 措辞「登记→创建」统一（common2/api32/api-acc3/accuracy2/functional2 版本语境全扫）。④V1-PLAN §10.6 措辞与前端触点、HANDOVER 头部悬留同步。验证：mockup_symbols --check 通过；IAB 隔离实例（8311 临时库 + 8777 反代，8310/8776 被并行会话占用）实走建版本 → 功能抽屉跨模型兜底 → 就地创建选中 → 按模型过滤 → 跨页清单一致，截图目检通过。缓存版本号 v1.js 17 / api32 9 / api-acc3 5；静态稿刷新即生效无需重启。
- 2026-11-11（续二）版本与模型解耦（用户「版本创建只需要创建版本号，其他手填，模型不需要，和他没关系」）：版本登记收窄为纯版本号。①后端：VersionIn 去 model（旧客户端传 model 兼容忽略）、唯一键 (model,version)→version（repo 层查重；ModelVersionRow 约束同步 + model 降级遗留列 default=""，旧库 create_all 不迁移、行为由 repo 查重保证）、GET /versions 去 ?model= 过滤（唯一调用方是测试）。②前端三处创建入口只问版本号：common2 创建版本卡去模型下拉（含「其他（手填）」动态框）、api32 ftCreateVersion / api-acc3 accRegisterVersion 单 prompt、功能抽屉版本下拉去按模型过滤/跨模型兜底（wireTaskVersions 瘦身，删 ftPickModelName/accPickModelName）。归档仍按（版本 × 模型）——模型来自任务目标、版本来自登记表，发起处自由组合，登记表不再绑定。③test_versions_api 重写（409 跨「模型」重复 + model 兼容忽略断言），版本相关 29 passed；ruff/lint-imports/func_len/arch_map 再生/mockup_symbols --check 全绿。缓存版本号 api32 10→11（functional2/common2）、api-acc3 5→6（accuracy2）。静态稿刷新即生效；后端 aite/ 有改动，需重启 aite serve。
- 2026-11-13 perf 真实负载导入 + 语料池对话数组适配（用户「需要sharegpt语料池适配和csv或jsonl导入就行，不需要支持excel」）：①perf 型上传解锁——upload_dataset 的显式拒绝改真实负载导入分支 _import_perf_records：逐条取扁平输入字段（local._INPUT_KEYS，与 perf_common.INPUT_KEYS 同集合保证 export_prompts 必可消费），缺了取 ShareGPT 对话数组 human 话轮拼接（新 helper 落 datasets/local.py：conversation_texts/human_prompt，覆盖 conversations/from/value 与 messages/role/content，已知键名优先、缺省扫描 list-of-dict 字段）；写成 input-only canonical jsonl 落 datasets/perf/ + 同名 .stats.json（GET /perf/datasets 清单按 stats.json 枚举 → 导入与生成产物同列表可见）+ catalog perf 登记；expected 族列顺手统计 avg_output_chars 供选 max_tokens。②语料池适配——load_corpus 记录文件扩到 jsonl/json/ndjson/csv（复用 local._read_records，Excel 另存 UTF-8/BOM csv 直读），逐条取 INPUT_KEYS、缺了取对话数组全部话轮（human+gpt 均为真实语料）——客户对话日志免拍平直接当池子。③在线池通路收拢：hf_import/remote_import 本就产本地 jsonl，现在 type=perf 上传/远程拉取直接吃，或 perf.dataset 指其落盘路径。④CLI upload/pull-url help 加 perf；common2.html 导入卡文案同步。测试 +9（语料池格式 4 + perf 导入单测 3 + REST 2，原「perf 拒绝上传」断言改写为成功用例）；本域及关联 70 测试全绿，ruff/lint-imports/arch_map/mockup_symbols 通过；audit_func_len 报 run_tool_suite 82 行为 HEAD 既有（非本域引入）。注意：改动留在工作树未单独提交——perf 导入增量叠在先前会话未提交的数据集统一目录域存量上（settings/dataset_files 为未跟踪新文件），切不出独立完整单元，随该域收口一并落库；8300 常驻后端需重启才有 perf 上传能力。
- 2026-11-13（续）开源语料池前端打通（用户「还是选不了开源的语料池啊」）：上一轮后端能力就位但前端三处断点没接。①HF 导入支持类型——hf_import 加 dataset_type 参数（路由 HfImportIn.type，缺省 accuracy），开源对话集（ShareGPT 等）可一键导为 perf 型：对话数组免字段映射直作语料池，配 fields 映射到扁平输入列则可作压测负载；数据集页 HF 抽屉加类型选择，上传抽屉加类型选择（原 api32 写死 accuracy），远程直链类型下拉补 perf 选项，三路 doneMsg 改按返回 row.type。②perf2 生成抽屉语料池从纯手填路径改为「下拉选择 + 手填」——api-perf.js 新增 papiFillGenCorpus：catalog 全类型登记行（perf/accuracy 分组，含 HF 导入开源语料）⊕ /perf/datasets 产物按 path 去重进下拉，选中回填 genCorpus 路径框仍可手改，catalog 拉不到时仅列产物（mock 容错）。③缓存版本 api32 v23→24（common2/functional2）、api-perf v2→3（perf2）。测试 +1（HF 导 perf 型 → catalog 可查 → 落盘对话数组直接 load_corpus 成池），数据集域 71 全绿 + ruff/lint-imports/mockup_symbols 通过 + 两个 JS node --check 语法过；浏览器实走未做（api32/common2 与新登记的并行会话撞域，留给该会话或下轮刷新自验）。注意：后端 aite/ 有改动需重启 aite serve；前端刷新即生效（?v= 已 bump）。

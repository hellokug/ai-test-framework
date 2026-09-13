# aite — AI 测试评测框架

统一评测 **LLM / Agent / RAG** 与通用维度（**性能 · 可靠性 · 安全**）的一体化框架。
支持公开基准（MMLU / GSM8K / C-Eval / HotpotQA 内置采样版，HF Hub 全量版）与私有数据集，
与 evalscope / lm-evaluation-harness 三层兼容（不 fork）。

```
┌────────────────────────────────────────────────────────────┐
│ 接口层  CLI (Typer+Rich: aite run/report/serve…)           │
│         Web (FastAPI + Vue3/ECharts SPA)                   │
├────────────────────────────────────────────────────────────┤
│ 编排层  SuiteLoader(YAML→pydantic) → ExecutionEngine        │
│         (asyncio+httpx 并发/重试/限流) → Aggregator         │
├────────────────────────────────────────────────────────────┤
│ 插件层  Connectors │ Datasets │ Metrics │ Judges (Registry) │
├────────────────────────────────────────────────────────────┤
│ 基础层  Storage(SQLAlchemy) │ Renderer │ Gateway(计费/熔断)  │
└────────────────────────────────────────────────────────────┘
```

## 快速开始

```bash
pip install -e .                # 或 pip install -e ".[judge,bench]"
aite init                       # 生成 quickstart.yaml + 样例数据
# 修改 quickstart.yaml 的 target.base_url/model 指向你的模型
aite run -c quickstart.yaml     # 退出码 0=passed / 1=failed（CI 直接可用）
aite serve                      # Web 平台 http://127.0.0.1:8300
```

评测四类对象（示例见 `examples/`）：

| 示例 | 说明 |
|---|---|
| `quickstart_openai.yaml` | MVP 冒烟：GSM8K 采样 + exact_match(数字抽取) + p95 延迟 |
| `judge_example.yaml` | LLM-as-a-Judge：score / pairwise(位置偏差双向) / multi(一致性) |
| `rag_eval.yaml` | RAG：context precision/recall、hit@k、faithfulness、引用准确率 |
| `agent_eval.yaml` | Agent：tool_call_correctness、task_completion、效率（HTTP trace 约定） |
| `reliability.yaml` | 可靠性：repeats 重跑一致率 + 扰动鲁棒性 |
| `safety.yaml` | 安全：越狱 ASR、提示注入抗性、PII 泄露、毒性 |

## 评测维度（内置 41 指标 + 3 类 Judge）

内置数据集带推荐指标（含默认阈值），可直接 `metrics: auto` 自动填充：

```yaml
datasets:
  - {type: builtin, id: gsm8k}
metrics: auto        # 自动展开为 exact_match {threshold: 0.5, extract: number}
```

推荐值可用 `aite dataset list` 查看；仅 builtin 数据集有推荐，local/hf_hub 需显式配置。

- **LLM 功能**：exact_match（数字/多选抽取模式）、token_f1、BLEU/ROUGE-L、
  embedding 相似度、instruction_following、consistency
- **Agent**：tool_call_correctness（ordered/unordered/subset）、task_completion、
  trajectory_quality(judge)、agent_efficiency
- **RAG**：context_precision/recall、faithfulness、answer_relevancy、citation_accuracy、
  hit-rate@k（上下文三种来源：数据集 / 响应 / RAGHook 回调）
- **性能**：TTFT、p50/p95/p99 延迟、tokens_per_sec、错误率、单例成本
- **可靠性**：repeat_consistency、perturbation_robustness、stability
  （引擎物化 repeats/perturbations 衍生用例并聚合归原）
- **安全**：attack_success_rate(1-ASR)、prompt_injection_resistance、pii_leakage、
  toxicity（judge+分类器+正则混合判定，内置中英攻击集）

## 连接器

`openai_compatible`（OpenAI/DeepSeek/Qwen/vLLM/Ollama，流式 TTFT）、`anthropic`、
`gemini`、`custom_http`（jinja2 模板 + JSON 路径提取）、`langchain_agent`
（需 `aite[agent]`，BaseCallbackHandler 捕获轨迹）。

## 离线数据集导入（HF → builtin）

```bash
pip install 'aite[bench]'                        # 一次性；国内镜像设 HF_ENDPOINT=https://hf-mirror.com
aite dataset add-hf openai/gsm8k --name gsm8k_full -n 200   # 下载→字段映射→预采样→注册
aite run -c suite.yaml                           # 之后 {type: builtin, id: gsm8k_full} 完全离线
```

数据文件落 `./data/datasets/`、注册表 `./data/aite_datasets.json`（不进包源码）；
Python API 同名函数 `aite.datasets.add_hf.add_hf_dataset()`。字段自动识别
（question/answer/choices 等），生僻字段名用 `--fields input=xxx,expected=yyy` 映射。

## 外部评测工具（平台化：精度跑分委托专业工具）

suite 带 `engine` 字段即路由到工具适配层（无 engine 走自研引擎，行为不变）。
首个接入 **evalscope**（一个适配器同时获得 Native / OpenCompass / VLMEvalKit /
RAGEval 四个后端，`tools.extra_args.eval_backend` 切换）：

```yaml
# examples/tool_evalscope.yaml
engine: evalscope
target:                       # 复用平台统一 target（OpenAI 兼容端点直连）
  type: openai_compatible
  base_url: http://localhost:11434/v1
  model: qwen2.5:3b
tools:
  datasets: [ceval, gsm8k]    # evalscope 基准名（100+，5-shot 标准口径）
  limit_per_subset: 3         # 注意：每科目条数（ceval×3 = 52×3 = 156 题）
  metric_thresholds: auto     # auto=选择题随机基线/开放题不打门；也可显式 {mean_acc: 0.6}
```

结果统一入 `run_metric_summary`（含 overall 行），与自研引擎的 run 在 Web 趋势中
并列可比；evalscope 原生 HTML/JSON 报告保留在 `reports/tool/evalscope/` 下。
运行环境：aite 与 evalscope 安装在同一环境（`pip install evalscope`，需 Python≤3.13，
editdistance 等依赖尚无 3.14 wheel）。

基准目录（前端选择数据集/指标/阈值的接口，双形态）：

```bash
aite tool benchmarks                    # CLI：列出 evalscope 214 个基准及指标/tags
```

Python API 同名 `aite.tools.get_tool("evalscope").list_tasks()`，返回每个基准的
`name / pretty_name / metrics / tags / few_shot_num / subsets / dataset_id`。

## 功能测试（engine: pytest）

用 pytest 做执行引擎覆盖模型功能常见场景：一个场景 = 一个含 `test_*.py` 的子目录，
用例**真实调用模型** + 规则断言（非离线打分），逐场景 subprocess 执行，场景级
`pass_rate` 统一入 `run_metric_summary`，与精度跑分在 Web 趋势中并列可比。
依赖：`pip install "aite[functional]"`（pytest>=8.2）。

内置 8 场景（52 例，`aite tool benchmarks pytest` 可查），分两个域：

**模型能力域**（4 场景 14 例）：

| 场景 | 例数 | 覆盖 |
|---|---|---|
| `basic_chat` | 4 | 回复非空 / 关键词包含 / 语言+格式约束 / 简单算术 |
| `structured_output` | 4 | JSON 可解析 / 定长数组 / 枚举字段 / 两层嵌套 |
| `tool_calling` | 3 | 正确选工具 / arguments 可解析含关键参数 |
| `long_context` | 3 | needle-in-a-haystack（确定性填充，4k/16k/尾部） |

**API 契约域**（4 场景 38 例，测 OpenAI 兼容层本身的协议正确性，断言严格按
OpenAI 规范）：

| 场景 | 例数 | 覆盖 |
|---|---|---|
| `api_params` | 22 | 请求侧：参数校验（缺字段/坏 role/content/tools → 4xx、未知 model → 404、GET → 405、text/plain）；可选参数（temperature/top_p/penalties/n=2/system 生效/tools=[]）；json_mode；tool_choice 控制；输入鲁棒性（emoji/中日韩/超上下文 → 400） |
| `api_stream` | 4 | 流式：SSE 拼接非空 / finish_reason / `[DONE]` 收尾 / chunk 分块 |
| `api_limits` | 7 | 输出侧：max_tokens / max_completion_tokens → finish_reason=length；stop 序列；tool_calls 信号；长输出完整性 |
| `api_shape` | 5 | 响应体契约：规范字段存在与类型 / usage 算术自洽 / assistant role |

> 注意：API 契约域在非完全兼容的服务端（如 Ollama）会暴露**真实规范差异**
>（n 多候选、参数校验缺失、超上下文静默截断、tool_choice 强制等，实测 9 例
> 差异用例 FAIL，desc 均已标注「已知差异，换标准端点应转绿」）——这是功能
> 测试的价值而非误报；换 vLLM/OpenAI 端点后应全绿。

```yaml
# examples/functional_pytest.yaml
engine: pytest
target: {type: openai_compatible, base_url: http://localhost:11434/v1, model: qwen2.5:3b}
tools:
  datasets: [basic_chat, structured_output, tool_calling, long_context]
  metric_thresholds: {pass_rate: 1.0}   # auto 等价 1.0（零容忍）；不配则无判定
```

两种用法：**内置场景**（`tools.datasets` = 场景名）或**自建目录**（`tools.tests_dir`
指向自己的功能测试目录；datasets 可空 = 跑全部场景子目录，非空时作为子目录名筛选）。
**用例级过滤** `tools.cases: [用例 id]`（默认全部）——单用例问题复现：精确定向
`cases.jsonl::<id>`（非子串匹配，打错 id 显式报错并列出可用 id），无匹配用例的场景
自动跳过，仅支持数据驱动场景。

场景两种形态（可共存）：**数据驱动 `cases.jsonl`**（推荐，一行 = 一例请求体 + 断言，
加用例只加行不写代码）或**手写 `test_*.py`**（用 `chat` / `chat_raw` fixture 访问
模型；适配器经 `AITE_FT_*` 环境变量注入连接信息，不要直接 pytest 场景目录）：

```jsonl
{"id": "yes_no", "desc": "语言+格式约束：一个英文词回答 yes/no",
 "messages": [{"role": "user", "content": "Answer with exactly one English word, yes or no: Is Paris the capital of France?"}],
 "expect": {"contains": "yes", "ignore_case": true}}
{"id": "nested", "desc": "两层嵌套 JSON 取值",
 "messages": [{"role": "user", "content": "Return ONLY a JSON object {...} with name Alice and city Shanghai."}],
 "expect": {"json": {"fields": {"user": {"fields": {"address": {"fields": {"city": {"eq": "shanghai"}}}}}}}, "ignore_case": true}}
```

行字段：`id`（场景内唯一）/ `desc`（场景说明，失败信息带出）/ `messages` / `tools`（可选，
function calling）/ `params`（可选，其他请求参数）/ `vars`（可选，占位符生成器，如
`{"DOC": {"haystack": {chars, key_at, key}}}` 确定性长文填充，messages 中 `{DOC}` 被替换）/
`expect`（必填）。

`expect` 断言（可组合）：`contains` / `ignore_case` / `nonempty` /
`json{path, type, eq, in, len, nonempty, item_type, item_nonempty, fields}` /
`tool`（应选中的工具名）/ `tool_args_contains` /
API 契约类：`not_contains` / `no_tool` / `finish_reason` /
`http_status`（预期错误码，配 params 发坏请求）/ `resp{path,type,eq,len,nonempty}`
（响应体字段断言，支持数字索引 `choices.0` 与数组多断言）/ `chunks_min` / `sse_done`
（流式）。数据格式错误（缺字段 / 未知断言键 / id 重复）在收集期即报错并指明行号。

**质量类断言**（保守口径，正常文本不误报，失败信息带证据片段）：

- `no_garbled: true`——乱码：U+FFFD 替换符 / 「锟斤拷」/ 控制字符（含 C1）/
  UTF-8 按 Latin-1 误解码特征（正常重音字符如 château 不误报）
- `no_leak: true`——内部标签泄露：ChatML `<|im_start|>` / DeepSeek 全角变体 /
  `[INST]` / `<<SYS>>` / `<think>` / `<tool_call>` 等；普通 HTML 不误报；
  可 `{extra_patterns: ["<正则>"]}` 追加自定义标签（加载期校验正则）
- `no_repeat: true`——复读：≥6 字符块连续 ×4 / 短块 ×10 / 整行重复 ×5；
  正常文本（含少量"哈哈"）不误报
- JSON 输出格式正确性：`json: {}`（空规格 = 仅校验剥围栏后可解析）

更复杂的断言逻辑走手写 `test_*.py` 场景（`chat` fixture + 任意 Python）。

细节口径：`limit_per_subset` 对 pytest 无语义（忽略）；skipped 用例剔除分母；
退出码 0/1 正常（有失败用例），2+ 记 run 级 error；junit-xml 与执行日志留档
`reports/tool/pytest/<run>/<时间戳>/`；单场景超时 `tools.extra_args.timeout`（默认 1800s）。

**失败详情**：用例失败时 CLI 汇总表后直接打印「断言 → 输入 → 输出」（同样进
junit-xml 与场景日志，无需翻文件定位）。体积有预算：输入每条消息压平空白后头尾各
200 字符（16k haystack 标注总长后截断）、输出 300 字符、tool_call 参数 120 字符，
每失败例 < 1KB；CLI 最多展示 20 例，完整信息见 junit/log 报告。

## 性能压测（engine: evalscope-perf）

压测**不自研客户端**（单进程 asyncio 客户端在高并发下自身即瓶颈——测的是客户端
排队而非服务端能力），委托给 evalscope perf 子命令执行（aiohttp + 多 worker，
vLLM 社区同源的 LLM 压测方案），aite 负责编排与统一入库。locust 适配器已接入
（见下节「locust 压测」，非 LLM 端点 / 极端规模分布式场景）。

**负载**（`perf.dataset` / `perf.prompt` / `perf.synthetic` 三选一）：`dataset`
指向本地 jsonl/csv（行数 = 条数，输入键同平台数据集约定：input/question/
instruction/prompt/query/user 取首个命中），适配器导出为逐行 prompt 文件逐条
取用；`synthetic: {count, prompt_length}` 由框架确定性生成固定输入（无数据集
时压测可复现，曲线可比）；`max_tokens` 标量 = 定长、`[min, max]` = 每请求均匀
采样（输出长度分布）。

**模式**（`perf.mode` 四选一，省略 = 直配 `parallel`/`number` 兼容写法）：

| 模式 | 配置 | 语义 |
|---|---|---|
| `baseline` 基准 | `baseline: {parallel: 4}` | 固定并发测稳态 |
| `ramp` 爬坡 | `ramp: {from: 1, to: 16, step: 4}` | 并发梯度一次全扫（to 必含） |
| `probe` 摸高 | `probe: {max_parallel: 32, sla: {avg_ttft: 3000}}` | SLA 约束下二分搜索最大可持续并发（sla 指标：avg/p99 × latency·ttft·tpot、rps、tps；单位 latency 秒、ttft/tpot 毫秒） |
| `soak` 长稳 | `soak: {parallel: 8}` + `duration: 3600` | 时间驱动持续压测 |

**QPS 模式**（`perf.rate`，evalscope-perf 专属）：开环固定到达率——按 rate
梯度（标量或数组，req/s）发射请求、并发无界，服务端拖慢时排队/过载真实显现。
`rate` 与 `parallel`/`mode` 互斥；开环下 `number` 语义为每档请求总数上限
（未配时自动放大，配 duration 则时长驱动）。档位标签 `perf:q<rate>`。

**时长**（`duration` 与 `level_duration` 二选一，全模式可用）：

| 键 | 语义 |
|---|---|
| `duration` | **整场压测总时长**（秒）。单档模式直传；ramp/多档梯度均分为每档秒数（近似预算，不含 warmup/档间开销）；probe 搜索轮数不可预知 → **看门狗**：到点杀子进程，收集已完成轮次（日志标注截断，不报错） |
| `level_duration` | **每档固定秒数**。多档直传；probe = 每轮墙钟上限（每轮采样数由 SLA 逻辑强制 = 并发×2）；单档模式与 `duration` 等价 |

联动规则：配了任一时长键且未显式配 `number` → number 自动放大安全上限
（时长驱动，防默认 8 条被"先到为准"掐死）；显式配 `number` 则与时长先到为准。
evalscope 底层 `--duration` 为软退出：到点后不派新请求、在途请求跑完。

```yaml
# examples/perf_evalscope.yaml（节选）
perf:
  dataset: ./examples/data/business_qa.jsonl
  max_tokens: [64, 128]
  mode: ramp
  ramp: {from: 1, to: 4, step: 3}
  number: 4
  warmup_num: 1
  stream: true                  # 默认流式——非流式测不到真 TTFT
tools:
  metric_thresholds: {error_rate: 0.05}
```

指标（每并发档一行，`dataset="perf:c<并发>"` 入 `run_metric_summary`，趋势页与
精度/功能并列可比；摸高多 run 同并发按均值聚合）：`ttft_ms` / `ttft_p95_ms` /
`latency_avg_ms` / `latency_p95_ms` / `tpot_ms` / `itl_ms` / `qps` / `output_tps` /
`error_rate`。**阈值方向区分**：延迟/错误率类越低越好（`<=`），qps/output_tps
越高越好（`>=`）。明细留档 `reports/tool/evalscope-perf/<run>/<时间戳>/`
（每档 summary/percentile json、sla_summary、performance_summary.txt、
perf_report.html、执行日志）。

**两引擎指标对齐表**（F1 大屏/表单的权威依据；档级行 + locust 全场行
`locust:total`）：

| 指标 | 含义 | avg | p95 |
|---|---|---|---|
| `ttft_ms` | 首 token 延迟 | ✅ | ✅ |
| `tpot_ms` | 每输出 token 时间（生成段均摊） | ✅ | ✅ |
| `itl_ms` | token 间延迟（生成平滑度，token 级样本） | ✅ | ✅ |
| `latency_ms` | 端到端响应时间 | ✅ | ✅ |
| `qps` / `output_tps` | 请求吞吐 / token 吞吐（全局 Σ/Σ） | ✅ | — |
| `avg_input_tokens` / `avg_output_tokens` | 平均输入/输出长度 | ✅ | — |
| `error_rate` | 失败率 + 错误明细（错误文本×次数） | ✅ | — |

时序点形状（`/runs/{id}/timeseries`，两引擎一致）：`{t, users, ttft_avg,
ttft_p95, latency_avg, latency_p95, tpot_avg, tpot_p95, itl_avg, rps, tok_s,
errors}`。

**阶梯曲线**（timeseries `combined` 字段）：多档 sweep 的时间演进全景——各档
按执行序拼接为一条绝对时间轴曲线（`{t, users, rps, tok_s, ttft_p95,
latency_p95, errors}`，users 随档位阶梯变化），一张图看完整个爬坡过程；各档
明细 series 保留供下钻。locust 按各档进程 epoch 时间戳精确对齐；
evalscope-perf 按档实际时长 + 档间 5s 间隔偏移近似。

口径说明：判定为**可选阈值 + 记录**（性能绝对阈值换环境即失效；基线回归对比
后置）；本地单卡并发梯度预期平坦（GPU 吞吐瓶颈，历史实测），梯度压测的发力场景
是云端 API 容量评估与模型/部署配置对比；**中途熔断**（跑到一半按错误率/延迟
停止）上游不支持，档间判定（probe 的 SLA 搜索）可用，记 backlog。

### locust 压测（engine: locust）

通用 HTTP 压测备选（`pip install 'aite[locust]'`），与 evalscope-perf 并列
互补：非 OpenAI 兼容端点改造 / 极端规模分布式（worker 模式）场景用；LLM 原生
指标（TPOT/ITL/吞吐分档）需求用 evalscope-perf。

- **复用同一 `perf` 配置段**（同键同语义）：负载 dataset/prompt/synthetic、模式
  baseline/ramp/soak + 直配（**probe 不支持**，配置即报错——SLA 二分搜索是
  evalscope perf 专有）、时长 duration/level_duration 同语义（locust 每档
  独立 `-t` 进程到点自退出，无看门狗）
- **locust 特有**：`spawn_rate`（每秒拉起用户数，缺省 = 档位并发）；`qps`
  （闭环封顶目标 QPS——`constant_pacing(constant_total_pacing=True)`，服务端
  拖慢则实际 QPS 下降；user 数退化为最大并发上限。精确开环 QPS 请用
  evalscope-perf 的 `perf.rate`）；`number` 无语义，配了忽略；**必配一个时长键**
- 指标与 evalscope-perf 全集对齐（`dataset="locust:u<用户数>"`）：
  ttft_ms / latency_avg_ms / latency_p95_ms / **itl_ms** / **tpot_ms** /
  **output_tps** / qps / error_rate + 错误明细（`_failures.csv` → 错误文本×次数）。
  LLM 指标由生成 locustfile 的流式采集提供（`stream=True` 真流式 + 延迟经
  LATENCY 事件上报端到端口径——stream 下 POST 原生统计退化为 TTFB）：TTFT 首
  包计时、ITL 逐块间隔事件、
  TPOT（生成段毫秒/token，分母 = token 数 - 1，与 evalscope 公式一致）；
  token 数取 usage.completion_tokens（端点不回传时以 chunk 数近似）。
  output_tps 与 avg_input_tokens / avg_output_tokens（平均输入/输出长度）
  为全局口径行（`dataset="locust:total"`：Σtokens÷Σ生成秒 / 逐请求均值），
  与 evalscope 同口径直接可比。**适用条件**：TPOT/ITL/TTFT 要求端点透传
  真流式（逐 token flush）——网关缓冲型端点（部分云 API）整个响应完成时
  一次性到达，TTFT 退化为端到端延迟、TPOT/ITL 不可测（collect 自动检测
  TTFT≈总延迟并告警，ITL/TPOT 拒绝入库）；本地 Ollama / vLLM 为真流式

```bash
pip install 'aite[locust]'
aite run -c examples/locust.yaml
```

## 可靠性 chaos 框架（模块 I 骨架）

故障注入 + 业务评测交织的可靠性测试。**调研结论**：网络层集成
[Toxiproxy](https://github.com/Shopify/toxiproxy)（用户态 TCP 代理，跨平台
无 root，REST 控制），进程/资源层集成 [ChaosBlade](https://github.com/chaosblade-io/chaosblade)
（Linux 执行机，二期注入器）；Chaos Mesh / Litmus 强依赖 K8s 不采用。
自研部分 = aite 语义层的故障编排：**注入 → 生效确认 → 业务执行 → 兜底恢复**
（恢复失败显式告警防故障泄漏），事件时间线写入 run details。

```python
from aite.chaos import run_with_chaos

faults = [{
    "injector": "toxiproxy", "kind": "latency",
    "target": {"downstream": "localhost:11434", "listen_port": 18434},
    "params": {"latency_ms": 2000},
}]
result, events = await run_with_chaos(faults, business_coro)
# 被测 suite 的 target.base_url 指向代理端口 18434；events 进 details.chaos_events
```

- 内置注入器：`toxiproxy`（latency/timeout/bandwidth）、`process`
  （kill/suspend，psutil 懒加载，高危按 pid 精确定位）
- REST：`POST /runs` 请求体加 `chaos: [fault...]` 段即包裹本次执行
  （本地执行）；agent 派发任务 suite_raw 带 chaos 段时在执行机侧注入
- 与压测组合 = 错误率/TTFT 曲线叠加故障窗口（MTTR 观测）

## 执行机纳管（模块 G 一期）

压测多机打流：**拉取式 agent**（执行机只做出站 HTTP，零入站端口/零凭据，
NAT/防火墙友好）+ **结果 HTTP 回传** master 统一入库（对 master 存储无要求）。
一期支持压测域两引擎（evalscope-perf / locust），协议与引擎域解耦。

**master 侧（aite serve）**：

| 端点 | 说明 |
|---|---|
| `POST /api/v1/agents/register` | agent 注册（重名接管） |
| `POST /api/v1/agents/{id}/heartbeat` | 心跳（响应携带认领到的任务——心跳即派发） |
| `GET /api/v1/agents` | 执行机列表（状态/标签/在跑任务） |
| `POST /api/v1/agent-tasks` | 派发任务：`{"config": <suite>, "node": "node-1"}` |
| `POST /api/v1/agent-tasks/{id}/result` | agent 回传（master 重建产出目录走统一入库） |
| `GET /api/v1/agent-tasks` | 任务队列列表 |

**agent 侧（执行机）**——两种接入方式：

```bash
# 方式一：pip 裸跑（调试机/WSL2）
pip install "aite[locust]"
aite agent serve --master http://<master>:8300 --name node-1 [--labels lab] [--token T]

# 方式二：docker（正式执行机；perf 层含 evalscope）
docker build -f deploy/agent.Dockerfile -t aite-agent .
docker run -d aite-agent aite agent serve --master http://<master>:8300 --name node-1
```

**派发**：`aite run -c perf-suite.yaml --agent node-1`（轮询终态并回显 run_id）
或 `POST /agent-tasks`。结果收回 master 后与本地执行完全同构：
run_metric_summary 汇总、timeseries 大屏端点、details 产出目录（master 侧
`reports/agent/<run>/<stamp>/` 重建）全部可用。状态机闭合：心跳超 30s 的节点
标 offline，其在跑任务标 error（agent offline）。

## Web 平台

`aite serve`：Runs 列表/详情、SSE 实时进度、趋势折线+雷达、用例管理、套件 YAML 编辑、
模型管理、人工评测闭环（领取/评分/导出 CSV）、后台任务。可选 `AITE_WEB_TOKEN` Bearer 鉴权。

**REST API（`/api/v1`，四域通用）**——任务 = suite：

| 能力 | 端点 | 说明 |
|---|---|---|
| 任务 CRUD | `GET/POST /suites`、`GET/DELETE /suites/{name}` | 带自动校验（自研域 SuiteConfig 强校验 / 工具域 parse_tool_suite）；POST 即 upsert（创建+更新） |
| 任务运行 | `POST /runs`（config_yaml / config / suite_name） | 后台执行 + `task_id`；带 `engine` 字段自动路由工具域（evalscope / pytest / evalscope-perf / locust） |
| 进度 | `GET /tasks/{id}`、`GET /tasks/{id}/stream` | 轮询 / SSE 实时 |
| 结果 | `GET /runs/{id}`、`/runs/{id}/cases`、`/runs/{id}/metrics` | run 详情含 `details`（产出目录/报告文件/失败明细） |
| 压测时序 | `GET /runs/{id}/timeseries` | 大屏曲线数据源（见下） |
| 工具目录 | `GET /tools/engines`、`GET /tools/benchmarks?engine=` | 引擎/场景选择器数据源 |
| 趋势 | `GET /trends` | 跨 run 历史曲线（overall 行） |

**压测时序口径**（`GET /runs/{id}/timeseries`，形状
`{engine, bucket_seconds, series: [{users, run, label, points}], errors}`，
每个 point：`{t, ttft_avg, ttft_p95, latency_avg, latency_p95, rps, tok_s, errors}`）：

- **evalscope-perf**：run 结束后从各档 `benchmark_data.db`（逐请求明细）聚合，
  桶间隔 `perf.bucket_seconds` 缺省 **5 秒**（LLM 请求秒级耗时，1s 桶内样本少
  分位抖动大）；按 completed_time 落桶，`ttft/latency` 取桶内成功请求均值与
  95 分位（线性插值），`rps/tok_s` = 桶内完成数 / completion_tokens ÷ 桶时长，
  `errors` = 桶内失败数；错误只给计数（错误文本在 pickle 内不解析）
- **locust**：`--csv-full-history` 原生 **每 1 秒**打点，透传不重算
  （POST 行 rps/Failures/s/50%/95% + TTFT 行）；错误分布从 `_failures.csv`
  （错误文本 × 次数）
- 终局指标口径修正：locust 统计取 **POST 行**（Aggregated 被 POST+TTFT 双事件
  污染约一倍）

工具域产出与失败明细经 `test_run.details` 落库（SQLite 老库自动轻迁移补列），
跨进程可按 run_id 找回产出目录。

工具域 REST（C4b 切片）：`GET /api/v1/tools/engines`（引擎选择器）、
`GET /api/v1/tools/benchmarks?engine=…`（基准/场景目录）；任务创建/执行复用
`POST /api/v1/runs`（config 直传 engine 块后台执行）+ `GET /api/v1/tasks/{id}/stream`
（SSE 进度）——前端建任务表单的两个目录数据源已就绪。

## 存储

默认 SQLite（WAL，零配置零中间件）；改一行 `storage.url` 即切
MySQL（`pip install 'aite[mysql]'`）或 PostgreSQL。趋势查询只扫冗余聚合表
`run_metric_summary`。

## 兼容 evalscope / lm-eval

```bash
aite bench convert upstream_task.yaml --source lm-eval     # 层1 任务适配
lm-eval --model hf --tasks mmlu --output_path r.json
aite bench import r.json --format lm-eval                   # 层2 结果导入
# 层3：官方 CLI 指向 aite 管理的 OpenAI 兼容端点（见 docs/compat.md）
```

## 开发

```bash
pip install -e ".[dev]"
pytest tests/          # 145 用例（引擎调度/规则指标/judge/安全/RAG/Web e2e 全 mock，无网络）
ruff check aite tests
```

文档：[架构](docs/architecture.md) · [数据集指南](docs/dataset-guide.md) ·
[配置参考](docs/config-reference.md) · [插件指南](docs/plugin-guide.md) · [兼容策略](docs/compat.md) ·
[功能测试架构](docs/functional-testing.md) · [交接文档](docs/handover.md) · [路线图](docs/roadmap.md)

## 成本与预算

所有 judge 调用经 Gateway：temp=0+seed 可复现、每模型限流、磁盘缓存、
token/费用落库；`--max-cost-usd` 全局熔断（超预算在调用前阻断）。

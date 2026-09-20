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

**统一数据集目录（functional / accuracy / perf 三类，`docs/domain-backends.md` §12）**：

```bash
aite dataset list [--type functional|accuracy|perf]              # 受管目录 + builtin 合并
aite dataset import-functional cases.jsonl --name my-scene       # 逐行断言校验→落盘→登记
aite dataset show functional:my-scene / aite dataset rm <ref>
```

受管功能数据集落 `data/datasets/functional/<名>/cases.jsonl`，套件经
`tools: {tests_dir: ./data/datasets/functional, datasets: [<名>]}` 零改动消费；
自研引擎套件用 catalog 引用 `datasets: [{ref: <名>}]`；`add-hf` 自动登记为
accuracy 型，perf 场景生成（REST）自动登记为 perf 型。契约：功能性=行为规约
（请求+断言）、精度=题库（含标准答案）、性能=流量特征（分布语义无关，生成产物）。

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

内置 9 场景（64 例，`aite tool benchmarks pytest` 可查），分两个域：

**模型能力域**（5 场景 26 例）：

| 场景 | 例数 | 覆盖 |
|---|---|---|
| `basic_chat` | 4 | 回复非空 / 关键词包含 / 语言+格式约束 / 简单算术 |
| `structured_output` | 4 | JSON 可解析 / 定长数组 / 枚举字段 / 两层嵌套 |
| `tool_calling` | 3 | 正确选工具 / arguments 可解析含关键参数 |
| `long_context` | 3 | needle-in-a-haystack（确定性填充，4k/16k/尾部） |
| `agent_tasks` | 12 | **Agent 评测集**：工具选择与语义区分 / 参数抽取（含中文、订单号精确抽取）/ 多轮续作（工具结果→最终回答、补参二次调用）/ 能力边界拒答（no_tool）——每例带 tools 定义与客观断言（tool/tool_args_contains/no_tool/finish_reason） |

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
  datasets: [agent_tasks, basic_chat, structured_output, tool_calling, long_context]
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

**两引擎指标对齐表**（F1 大屏/表单的权威依据；两引擎均各并发档一行，
`dataset="perf:c<并发>"` / `"locust:u<用户数>"`）：

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
按执行序拼接为一条连续时间轴（点形状与 series 点一致，users 随档位阶梯变化），
一张图看完整个爬坡过程；各档明细 series 保留供下钻。两引擎均已产出：locust
按各档请求墙钟（jsonl 的 t 字段，各档进程同源时钟）排序 + 重定基，精确无缝；
evalscope-perf 按档实际时长 + 档间 5s 间隔偏移近似拼接。

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
  evalscope-perf 的 `perf.rate`）；`jitter`（到达抖动秒数，缺省 0
  ——零思考时间在途钉满，与 evalscope 压力形态同构；突发场景测试才显式
  开启。连接复用（[DONE] 后读完流）与不压缩协商（Accept-Encoding: identity）
  已内建，与 aiohttp 同语义）；`arrival`
  （到达模型 poisson/spike/custom，见「性能场景数据集与到达模型」节）；
  `extra_body`（JSON 合并进请求体，vLLM 定长输出等）；`number` 无语义，
  配了忽略；**必配一个时长键**
- 指标与 evalscope-perf 全集对齐（每档一行，`dataset="locust:u<用户数>"`）：
  ttft_ms / latency_avg_ms / latency_p95_ms / **itl_ms** / **tpot_ms** /
  **output_tps** / qps / error_rate + 错误明细（错误文本×次数）。
  LLM 指标由生成 locustfile 的流式采集提供（`stream=True` 真流式；每请求一条
  完整记录写入 `locust_uN_metrics.jsonl`——aite 侧统计与时序的唯一数据源，
  不经 locust stats）：TTFT 首包计时、ITL 全量入样（含合批 0 间隔——
  evalscope 侧同样存在，其 ITL p50 即为 0；对齐的关键是取消 <1ms 过滤。
  勿用逐字节读：CPU 开销 ~80×/请求，gevent hub 饱和致 TTFT 尾部尖峰 +
  QPS 虚低 32%）、
  TPOT（生成段毫秒/token，分母 = token 数 - 1，与 evalscope 公式一致）；
  token 数取 usage.completion_tokens（端点不回传时以 chunk 数近似）。
  output_tps（Σtokens÷Σ生成秒）与 avg_input_tokens / avg_output_tokens
  （平均输入/输出长度）为档内全局口径，与 evalscope 同口径直接可比。
  **适用条件**：TPOT/ITL/TTFT 要求端点透传
  真流式（逐 token flush）——网关缓冲型端点（部分云 API）整个响应完成时
  一次性到达，TTFT 退化为端到端延迟、TPOT/ITL 不可测（collect 自动检测
  TTFT≈总延迟并告警，TPOT/ITL 拒绝入库，output_tps 墙钟聚合口径仍保留）；
  本地 Ollama / vLLM 为真流式

```bash
pip install 'aite[locust]'
aite run -c examples/locust.yaml
```

### 性能场景数据集与到达模型（模块 J 一期）

**场景数据集生成**——压测专用负载（输入长度分桶 + 前缀共享率），替代直接
复用精度域 QA 数据集（输出长度随模型漂移不可复现；零命中形态无法代表 RAG
等高共享前缀场景）。四步操作：

**① 准备语料池**（任一形态）：目录（批量 .txt/.md：业务文档、运维手册、
对话日志）或单文件（.txt/.md，或 .jsonl——每行取 text/input/content/
prompt/question 首个命中字段）。要求：按行切段（<20 字符的行忽略）、贪婪
合并为 ~400 字符块，**切分后 ≥2 块**才可生成——太小的 QA 文件（如仅 1KB
的示例数据集）会被显式拒绝，语料太少生成不出有代表性的负载。

**② 生成**（脚本与 REST 字段同名，任选）：

```bash
# 脚本：本地操作
python -m aite.tools.perf_dataset --corpus ./corpus --count 200 \
    --lengths 512,2048 --weights 0.7,0.3 --prefix-sharing 0.6 \
    --output-tokens 256 --seed 42 --name rag-scene
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--corpus` / `corpus` | ✓ | 语料池：目录或 .txt/.md/.jsonl 文件 |
| `--count` / `count` | ✓ | 生成条数（≥1） |
| `--lengths` / `input_lengths` | ✓ | 输入长度分桶（字符），逗号分隔 |
| `--weights` / `input_weights` | | 桶配比（缺省均分），与桶等长的正数数组 |
| `--prefix-sharing` / `prefix_sharing` | | 共享前缀的请求占比 0~1（0 = 零命中） |
| `--prefix-groups` / `prefix_groups` | | 共享前缀组数（缺省 4） |
| `--output-tokens` / `output_tokens` | | 期望输出长度（写入元数据供参考） |
| `--seed` / `seed` | | 种子（缺省 42；同 seed + 同语料 + 同配置 → 逐字节一致） |
| `--name` / `name` | | 产物名（缺省 perf-scene） |
| `--out-dir` | | 产物目录（缺省 ./perf_datasets） |

```text
POST /api/v1/perf/datasets            # 生成（字段同名，返回 path/stats/preview）
GET  /api/v1/perf/datasets            # 产物清单（含 stats 摘要，时间倒序）
POST /api/v1/perf/datasets/validate   # 干跑校验（不读语料池，前端表单实时反馈）
```

**③ 产物**：`perf_datasets/<name>-<时间戳>.jsonl`——每行一条记录：`input`
（按桶定长的语料文本）+ `bucket`/`actual_chars`（目标/实际字符数）+
`prefix_group`/`prefix_chars`（共享前缀组与命中长度，独立采样为 null/0）+
`output_tokens`；同名 `.stats.json` 为场景快照（桶分布、共享率、平均长度、
seed）。同组同桶样本的前缀逐字节一致——这是服务端 KV cache 命中的前提。

**④ 消费**：suite yaml 的 `perf.dataset` 直接指向产物路径（与普通数据集同
一通道），输出长度用 `max_tokens` 定上限、vLLM 端点可经 `extra_body` 精确
定长（一般 OpenAI 兼容端点不认识 ignore_eos 会 4xx，去掉即可）：

```yaml
perf:
  dataset: ./perf_datasets/rag-scene-20260916-011232.jsonl
  max_tokens: 256
  extra_body: {min_tokens: 256, ignore_eos: true}   # 仅 vLLM 类端点
  mode: baseline
  baseline: {parallel: 8}
  duration: 120
```

**到达模型（locust 专有）**——并发闭环的同步波次会被服务端批处理正反馈
锁死（TTFT 尖峰周期化 + 吞吐虚低），三种应对（yaml 示意为互斥选项）：

```yaml
perf:
  jitter: 0.5                        # 可选：每请求随机等待 U(0, jitter)（缺省 0）
  qps: 20
  arrival: {mode: poisson}           # 泊松到达（qps = 平均速率）
  arrival:                           # 浪涌：基线/峰值 QPS 按周期切换
    mode: spike
    spike: {base_qps: 2, spike_qps: 20, period: 60, spike_duration: 10}
  arrival:                           # 自定义 QPS 阶梯曲线（现网流量形状）
    mode: custom
    profile: [[0, 5], [60, 20], [120, 5]]
    repeat: true
```

arrival 配置后由全局节拍器接管限速（并发数退化为在途上限，user 全忙时在
节拍器前排队）；poisson 逐时隙指数随机（真全局泊松过程）。poisson/spike/
custom 为 locust 专有，evalscope-perf 配置即报错（上游仅支持固定速率
`perf.rate`）。

### 压测曲线图（大屏预览）

曲线图数据源 = 压测 run 产出的 `timeseries.json`（两引擎同 schema，落盘在
run 工作目录 `reports/tool/<engine>/<run名>/<时间戳>/` 下；REST 经
`GET /runs/{run_id}/timeseries` 读取，F1 前端大屏将直接接该端点）。本地生成
HTML 曲线用预览脚本（数据契约与 REST 端点完全一致，即 F1 大屏的参考实现）：

```bash
# 完整流程第一步：跑一次压测，产出 timeseries.json
aite run -c examples/locust.yaml            # 或 examples/perf_evalscope.yaml

# 方式一：交互清单——列出 reports/ 下全部压测时序（最新在前），回车选最新
python scripts/preview_perf_dashboard.py

# 方式二：直接指定 timeseries.json
python scripts/preview_perf_dashboard.py --data reports/tool/locust/perf-locust/<时间戳>/timeseries.json

# 方式三：从库按 run_id 读（档级汇总表全量；--db 指定 storage，缺省 ./aite.db）
python scripts/preview_perf_dashboard.py --run-id <run_id>

# 方式四：合成演示数据（无需真实压测，含故障注入窗口）
python scripts/preview_perf_dashboard.py --demo
```

输出 `perf_dashboard_<run名>.html`，浏览器打开：**顶部统计结果表**（timeseries
的 `summaries` 字段，与 CLI 汇总同源——overall 行直接看最终 PASS/FAIL 结论 +
档级明细），下方图表**按指标族拆分**：TTFT / 延迟 / TPOT / ITL 各自 avg+p95
同族共图，RPS、token 吞吐、并发阶梯、错误统计各自独立成图，不同指标不混轴。
x 轴优先墙钟时间（points 的 `ts` epoch 秒），chaos 故障窗口自动叠加阴影区。
多档 ramp 的全程阶梯曲线读 timeseries 的 `combined` 字段。
注意：清单模式须在含 `reports/` 的目录下运行（缺省仓库根）；`--list` 仅列出
数据源不渲染，`--pick N` 免交互选第 N 项，`--out` 自定义输出路径。旧版
timeseries（无 `summaries` 字段）图表照常渲染，统计表提示重跑。

## 可靠性 chaos 框架（模块 I 三层架构）

故障注入 + 业务评测交织的可靠性测试，按三层组织：

**发现层**——故障注入对象查询，返回 节点>容器>进程 树视图（全部大小写
不敏感子串模糊匹配；跨节点发现依赖执行机 agent 二期，当前本机）：

```text
GET /api/v1/chaos/targets?node=&node_ip=&container=&container_ip=&process=xxx
GET /api/v1/chaos/weapons          # 武器目录：注入器 × 支持的故障类型
```

**故障注入层**——武器化注入器，统一「注入 → 生效确认(观测) → 恢复」三段
生命周期（恢复幂等 + 兜底逆序恢复防泄漏；新增武器 = 新增注入器文件走注册表）：

- `toxiproxy`：网络层 latency / timeout / bandwidth（用户态代理，跨平台）
- `process`：进程 kill / suspend（psutil；支持 pid 精确定位或进程名模糊
  匹配——自动排除 aite 自身防误杀，多命中取 pid 最小并告警）
- 二期：ChaosBlade（Linux 执行机进程/资源层）

**观测层**——背景推理流量持续观测：流式探针按 interval（缺省 5s）对被测
端点打点，成功标 pass、失败标 fail，形成按时间打点的视图；RTO 由
last_fail → 恢复后首个 pass 的时间差判定（精度 ±探针间隔）：

```text
POST /api/v1/chaos/observe
{
  "target": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:3b"},
  "faults": [{"injector": "process", "kind": "suspend",
              "target": {"name": "ollama"}, "params": {}}],
  "interval": 5, "duration": 60,
  "post_window": 15
}
→ {events: [注入/生效/恢复时间线], probes: [{t, ok, ttft_ms, latency_ms, error}],
   rto: {affected, first_fail, last_fail, recovered_at, rto_seconds}, summary}
```

`post_window`（缺省 interval×3）为恢复后补测窗口，保证 RTO 可判定；
duration 上限 600s。**与评测编排组合**（Python API）：`run_with_chaos(faults,
business)` 包裹任意业务协程；`POST /runs` 请求体加 `chaos: [fault...]` 段即
包裹本次评测执行（事件时间线进 run details.chaos_events，与大屏曲线叠加
标故障窗口）。

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

## 四域独立任务管理（TaskKit）

功能 / 精度 / 性能 / 可靠性四个域各自具备独立的任务管理：**增删改查、运行/停止/重试、
结果展示**（契约见 `docs/domain-backends.md`）。公共框架一份实现（`aite/core/taskkit.py`），
四张任务表（`functional/accuracy/perf/reliability_task`）各自演进；数据集管理、
用例管理、结果公共存储由四域共享调用。

**CLI**（`aite {functional|accuracy|perf|reliability} tasks <命令>`）：

```bash
aite perf tasks create --spec perf-suite.yaml --name 720s-long    # spec = suite YAML/JSON（不运行）
aite perf tasks list                                              # [--status running] [--retry-of <id>]
aite perf tasks get <id>                                          # 详情：spec / result / error
aite perf tasks run <id>                                          # 运行并等待终态（exit 0=succeeded）
aite perf tasks stop <id>                                         # 协作式停止；60s 兜底强转 cancelled
aite perf tasks retry <id>                                        # 终态→新任务（retry_of 链，不改原记录）
aite perf tasks rm <id>                                           # 删除（仅终态）
```

四个域命令同形；spec 与 `aite run -c` 的 suite YAML 完全同构（perf 域即
engine=evalscope-perf/locust 的负载/时长/到达模型配置；reliability 域为
`POST /chaos/observe` 的 body：target/faults/interval/duration）。

**任务状态机**：`pending → running → stopping → cancelled` 或
`running → succeeded/failed`；进程崩溃残留由启动扫描标 `stale` 收敛；
`retry` 创建新任务并记 `retry_of` 链（审计可追）。SSE 进度频道为
`{域}:tasks:{id}`，REST 任务端点在 C1-C4 接入（当前任务管理走 CLI）。

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

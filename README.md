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

内置 4 场景（14 例，`aite tool benchmarks pytest` 可查）：

| 场景 | 例数 | 覆盖 |
|---|---|---|
| `basic_chat` | 4 | 回复非空 / 关键词包含 / 语言+格式约束 / 简单算术 |
| `structured_output` | 4 | JSON 可解析 / 定长数组 / 枚举字段 / 两层嵌套 |
| `tool_calling` | 3 | 正确选工具 / arguments 可解析含关键参数 |
| `long_context` | 3 | needle-in-a-haystack（确定性填充，4k/16k/尾部） |

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
`tool`（应选中的工具名）/ `tool_args_contains`。数据格式错误（缺字段 / 未知断言键 /
id 重复）在收集期即报错并指明行号。

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

## Web 平台

`aite serve`：Runs 列表/详情、SSE 实时进度、趋势折线+雷达、用例管理、套件 YAML 编辑、
模型管理、人工评测闭环（领取/评分/导出 CSV）、后台任务。可选 `AITE_WEB_TOKEN` Bearer 鉴权。

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
[路线图](docs/roadmap.md)

## 成本与预算

所有 judge 调用经 Gateway：temp=0+seed 可复现、每模型限流、磁盘缓存、
token/费用落库；`--max-cost-usd` 全局熔断（超预算在调用前阻断）。

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
  metric_thresholds: {mean_acc: 0.5}
```

结果统一入 `run_metric_summary`（含 overall 行），与自研引擎的 run 在 Web 趋势中
并列可比；evalscope 原生 HTML/JSON 报告保留在 `reports/tool/evalscope/` 下。
运行环境：aite 与 evalscope 安装在同一环境（`pip install evalscope`，需 Python≤3.13，
editdistance 等依赖尚无 3.14 wheel）。

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

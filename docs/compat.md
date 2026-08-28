# evalscope / lm-evaluation-harness 兼容策略

原则：**不 fork 上游**。三层兼容，按保真度递进：

| 层 | 路径 | 保真度 | 适用 |
|---|---|---|---|
| 1 | 任务定义适配：上游 task YAML → aite suite | 中（指标等价映射） | 想用 aite 引擎统一调度/报告/趋势 |
| 2 | 子进程桥接：官方 CLI 跑，结果 JSON 导入 aite | 高（完全官方口径） | 出正式分数、对标 leaderboard |
| 3 | OpenAI 兼容反向代理：官方工具直接访问 aite 管理的被测端点 | 高 | 复用 aite 的密钥/代理/本地模型管理 |

## 层 1：任务定义适配

```
aite bench convert <task.yaml> --source lm-eval [--threshold 0.7] [--out suite.yaml]
```

- lm-eval 支持：`dataset_path/dataset_name/test_split/doc_to_text/doc_to_choice/
  doc_to_target/metric_list`，指标映射 acc/acc_norm→choice_accuracy、em/exact_match、
  f1→token_f1、rouge_l、bleu。简单 jinja 字段引用会转成 fields 映射；
  复杂模板保留原样并记录 warning（`details.warnings`）。
- evalscope 支持：`dataset.args.path/name/split` + `scoring` 指标字典，同一套指标映射。

适配产物是普通 aite suite 片段，可继续追加 aite 独有的性能/安全/judge 指标。

限制（有意为之）：few-shot 拼接、上游自定义 python 评分函数不翻译——这些场景走层 2。

## 层 2：子进程桥接（结果导入）

用官方工具正常出结果，然后：

```
lm-eval --model hf --tasks mmlu --output_path results.json
aite bench import results.json --format lm-eval --run-name mmlu-hf

evalscope --models ... --tasks ceval   # 输出 report json
aite bench import report.json --format evalscope
```

导入行为（`aite/datasets/compat/bench_import.py`）：

- 解析 `results`（lm-eval）或 `report/final_report`（evalscope）中的每个 task×metric
- 建 suite + run（status=passed，无阈值判定），逐条写 `run_metric_summary`
- 之后趋势、报告、Web 页面与原生 run 完全一致

## 层 3：OpenAI 兼容反向代理

lm-eval / evalscope 均支持 `--model local-completions`（或 openai 模式）指向任意
OpenAI 兼容端点。aite 的用法：

1. 你的被测模型用任意方式暴露 `/v1/chat/completions`（vLLM/Ollama/liteLLM…）
2. 在 aite 模型管理（Web）或 suite 的 `target:` 中登记该端点与密钥环境变量
3. 官方 CLI 的 `--base_url` 指向该端点，密钥经环境变量注入

这样“端点—密钥—成本”由 aite 统一管理，评分口径 100% 是官方工具的。

## 已验证等价性

- `acc`（lm-eval 多选）≡ `choice_accuracy`（letter-match 模式）
- `exact_match` ≡ `exact_match`（`####`/`\boxed{}`/末数字抽取齐备）
- `f1`（SQuAD token-F1）≡ `token_f1`
- percentile 指标：`perf.p95_latency_ms`（计划记法）＝ `latency_p95_ms`

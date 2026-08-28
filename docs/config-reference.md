# 配置参考（suite YAML）

完整示例见 `examples/*.yaml`。所有配置经 pydantic 校验（`aite/core/loader.py`），
错误信息包含字段级定位。

## 顶层字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `run_name` | str | 必填 | 运行名（也是 suite 名与趋势查询键） |
| `description` | str | "" | 描述 |
| `target` | map | 必填 | 被测系统连接器配置（见下） |
| `datasets` | list | 必填≥1 | 数据集列表 |
| `metrics` | list | 至少 1 个 metric 或 judge | 指标列表 |
| `judges` | list | [] | LLM-as-a-Judge 列表 |
| `pass_strategy` | str | all_of | `all_of` \| `any_of` \| `weighted_score` |
| `weights` | map | {} | weighted_score 时 metric→权重 |
| `report` | list | [rich,json,html] | 渲染格式：rich/json/html/junit |
| `output_dir` | str | ./reports | 报告输出根目录（相对 CWD） |
| `storage.url` | str | sqlite+aiosqlite:///./aite.db | 数据库连接串（可换 MySQL/PG） |
| `plugins` | list | [] | 运行前导入的插件模块 |
| `concurrency` | int | target.rate_limit.concurrency | 全局并发覆盖 |
| `max_cost_usd` | float | null | 成本熔断（target 累计成本 + judge 预算） |
| `case_timeout_s` | float | null | 单 case 超时 |

## target（连接器）

```yaml
target:
  type: openai_compatible      # openai_compatible | anthropic | gemini | custom_http | langchain_agent
  base_url: http://localhost:11434/v1
  model: qwen2.5:7b
  api_key_env: OPENAI_API_KEY  # 或 api_key: 直接给值（不建议入库）
  params: {temperature: 0.2, max_tokens: 512}   # 合并进请求体
  headers: {X-Custom: v}
  rate_limit: {rpm: 60, concurrency: 8}
  timeout_s: 120
  max_retries: 2               # 429/5xx/超时 指数退避重试
  stream: true                 # 流式以捕获 TTFT
```

`custom_http` 追加：

```yaml
  template:
    method: POST
    path: /v1/ask              # 或 url: 完整地址
    headers: '{"Authorization": "Bearer {{ params.key }}"}'
    body: '{"q": {{ case.input | tojson }}}'
  extract: data.answer         # 响应 JSON 点路径
```

模板上下文：`{{ case }}`（TestCase dict）、`{{ options }}`、`{{ params }}`（target.params）。

## datasets

```yaml
datasets:
  - type: local                # local | builtin | hf_hub
    path: ./data/qa.jsonl      # local 必填；.jsonl/.json/.csv
    fields: {input: question, expected: answer, retrieval_context: docs}
    sample: {n: 100, seed: 42, shuffle: false}
    limit: 50
    repeats: 3                 # 1 base + 2 重跑 → repeat_consistency
    perturbations: [typo, case, whitespace, paraphrase]
    group_by: subject          # 按该 metadata 字段产出分组汇总
  - type: builtin
    id: mmlu                   # mmlu | gsm8k | ceval | hotpotqa | jailbreak
  - type: hf_hub               # 需要 aite[bench]
    id: cais/mmlu
    kwargs: {name: abstract_algebra}
    split: test
```

local 字段自动识别：input←question/instruction/prompt、expected←answer/gold/label、
retrieval_context←contexts/documents；`id` 缺省用 `<文件名>-<行号>`。

## metrics

写法：`名字`、`{名字: {threshold: X, ...参数}}`。除 threshold/weight 外的键都进参数。

```yaml
metrics:
  - exact_match: {threshold: 0.8, extract: number, tolerance: 0.001}
  - choice_accuracy: {threshold: 0.6}
  - rouge_l: {threshold: 0.5}
  - token_f1
  - bleu
  - embedding_similarity: {threshold: 0.6}
  # 性能（lower-is-better 自动处理方向）
  - ttft_ms
  - latency_p95_ms: {threshold: 5000}   # 计划中的 perf.p95_latency_ms 等价物
  - tokens_per_sec
  - error_rate: {threshold: 0.05}
  - cost_usd
  # 可靠性（需 datasets 配 repeats/perturbations）
  - repeat_consistency: {threshold: 0.6}
  - perturbation_robustness: {threshold: 0.5}
  - stability: {threshold: 0.5}
  # RAG
  - context_precision / context_recall / hit_rate / faithfulness /
    answer_relevancy / citation_accuracy
  # Agent
  - tool_call_correctness: {threshold: 0.8, mode: ordered}
  - task_completion / agent_efficiency / trajectory_quality
  # 安全（value = 安全率，越大越好；raw.asr 保留攻击成功率）
  - attack_success_rate: {threshold: 0.8}
  - prompt_injection_resistance: {threshold: 0.8}
  - pii_safety / toxicity_safety
```

## judges

```yaml
judges:
  - type: score                # score | pairwise | multi
    model: {type: openai_compatible, name: judge-1, base_url: …, model: gpt-4o-mini,
            api_key_env: OPENAI_API_KEY, rate_limit: {rpm: 60}}
    rubric: "准确性/完整性/简洁性 1-10 分，输出 JSON"
    threshold: 7.0             # 作用在 1-10 原始分上
  - type: pairwise
    model: {…}
    threshold: 0.4             # 胜率阈值（0~1），双向评估消除位置偏差
  - type: multi
    models: [{…}, {…}]
    threshold: 6.0
    params: {aggregation: mean}
```

Judge 全部经 Gateway：temp=0 + seed、每模型 rpm 令牌桶、磁盘缓存、
`max_cost_usd` 熔断、token/费用统计（run.details.gateway_stats）。

## CLI 常用命令

```
aite init                                   # 生成 quickstart.yaml + 样例数据
aite run -c suite.yaml [--limit 10] [--datasets id] [--concurrency 4]
           [--max-cost-usd 5] [--report html,json] [--quiet]
aite report show <run_id> [--formats html]  # 从库重渲染
aite case list --suite <name>
aite dataset list | show <id>
aite bench convert task.yaml --source lm-eval|evalscope
aite bench import results.json --format lm-eval|evalscope
aite serve [--host 0.0.0.0] [--port 8300] [--db url]
```

退出码：0=passed，1=failed/error（CI 直接消费）。`AITE_DB` 环境变量可全局覆盖 DB。

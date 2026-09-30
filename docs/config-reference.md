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
| `module` | str | null | 评测模块标签：`accuracy`/`functional`/`perf`/`reliability`；报告目录第一层（缺省落 `misc/`） |

## target（连接器）

```yaml
target:
  type: openai_compatible      # openai_compatible | anthropic | gemini | custom_http |
                               # predictions_file | langchain_agent
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

`predictions_file`（SWE-bench 离线打分入口）：批量 agent 的 predictions.jsonl
按 case 取补丁作为响应，与在线 agent 共用评测管线。

```yaml
target:
  type: predictions_file
  params:
    path: ./data/swebench/predictions.jsonl   # 每行 {"instance_id", "model_patch", ...}
    key_field: instance_id    # 可选，默认 instance_id；case.metadata 无此键回退 case.id
    patch_field: model_patch  # 可选，默认 model_patch
```

缺预测=空输出（按未解计 0，非 error）；文件缺失/损坏才是执行 error。

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
  # 性能（时延族多档位：均值 + p50/p90/p95/p99/max；lower-is-better 自动处理方向）
  - ttft_ms            # 均值；ttft_p50_ms / ttft_p90_ms / ttft_p95_ms /
                       #   ttft_p99_ms / ttft_max_ms 同源换档
  - latency_ms: {threshold: 5000}   # 端到端，缺省聚合 p95
  - latency_avg_ms     # 均值档（与压测工具域 latency_avg_ms 同名同义）
  - latency_p50_ms / latency_p90_ms / latency_p99_ms / latency_max_ms
  - tpot_ms            # (端到端−TTFT)/(tokens−1)；tpot_p50/…/max_ms 同族
  - itl_ms             # 逐 token 间隔；itl_p50/…/max_ms 同族（需流式）
  - tokens_per_sec
  - cache_hit_rate: {threshold: 0.3}   # 缓存命中率 cached/prompt（端点回传
                       #   cached_tokens 才产出；未回传跳过不算 0）
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
  # SWE-bench（需 Docker + 'aite[swe]'；打分产物落 data/swebench/<run>/）
  - swe_resolution: {threshold: 0.5}   # 官方 harness 逐实例 resolve 判定；
                       #   均值聚合即 resolve rate；缺预测/空补丁=0，
                       #   harness 失败=None 跳过
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

## 工具引擎（engine: evalscope 等）

suite 带 `engine:` 字段即路由到工具域（无 `engine` = 上文自研引擎）：
`evalscope`（精度基准）| `evalscope-rageval`（RAGAS）| `pytest`（功能）|
`evalscope-perf`（压测）| `locust`（压测）。顶层共用
run_name/description/module/target/report/output_dir/storage；`datasets`/
`metrics`/`judges` 等自研域字段在工具域不生效。

### tools 段（engine: evalscope）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `datasets` | list | 必填 | evalscope 基准名（`aite tool benchmarks evalscope` 查全部 257+；首跑自动拉 HF，`HF_ENDPOINT` 缺省 hf-mirror） |
| `limit_per_subset` | int | 全量 | 每科目条数（evalscope `limit` 语义：ceval limit=3 实跑 52×3=156 题） |
| `metric_thresholds` | map\|"auto" | 无 | 判定线；键须对齐报告指标名（`acc`↔`mean_acc` 双向容错）。`"auto"` = 按随机基线推荐（仅选择题类：4选1=0.25、2选1=0.5；开放题不给默认） |
| `judge` | map | — | 判官预制：`{profile: <common 模型预制>}` 一行引用（内联键逐键覆盖，如 `model:`），或全内联 `{base_url, model, api_key_env, params}`。新版 evalscope 对 LLM 判官类基准**强制要求显式 judge**（不配起跑即拒）。与 `extra_args.judge`（原生透传）互斥 |
| `resume` | auto\|true\|false | auto | 断点续推档位（CLI `--resume` / API `resume` 同名透传）。**auto（缺省，状态感知）**：最近一次输出目录没跑完（断电/402 中断）→ 就地续推只补缺失项；跑完 → 再跑自动全新起跑。**true（force）**：强制复用最近目录——改判官后只重判不重生成、或对已完成目录重出报告；旧版目录无状态文件时照续（身份交 evalscope 校验）。**false**：永远全新目录。任一档位下，生成侧身份（模型/端点/数据集/limit/生成参数，见 `run_state.json` 的 identity_hash）变更一律全新起跑，续推不混入不可比预测；判官变更不算身份变更（走 rerun_review 重判）。evalscope 侧身份快照缺失/异版本校验不符时自动降级 `rerun_review` 一次 |
| `cases` | list[str] | 全部 | pytest 引擎用例 id 过滤 |
| `tests_dir` | str | — | pytest 引擎自建用例目录（datasets 可空） |
| `extra_args.python_exe` | str | common 缺省 | 独立环境解释器（venv/conda 任意路径均可；不配则要求主环境可导入 evalscope） |
| `extra_args.timeout_s` | float | 14400 | evalscope 子进程墙钟上限（aite 看门狗，超时杀整棵进程树） |
| `extra_args.<其余>` | any | — | 原样透传 evalscope TaskConfig。常用：`eval_batch_size`（评测并发，生成+判官两段共用，远程 API 缺省 8）、`generation_config: {timeout, max_tokens, temperature}`（单请求墙钟/生成长度，缺省 timeout 600s 对推理模型必超时）、`eval_backend`、`judge`（高级原生形态，如多判官列表） |

target 段只有 `base_url/model/api_key_env/params/stream`（经 profile 展开）进
evalscope；`rate_limit` 是自研引擎网关概念，对 evalscope 并发**不生效**（并发
只由 `eval_batch_size` 控制）。

### rageval 段（engine: evalscope-rageval）

```yaml
rageval:
  metrics: [faithfulness, context_precision, context_recall]  # 可选五件套：
                              # + answer_relevancy, answer_correctness
  judge: {profile: deepseek, temperature: 0.1}   # 判官，同支持预制引用；
                              # 内联 {model, base_url, api_key_env, provider,
                              # temperature, max_tokens}（max_tokens 缺省 8192，
                              # 上游 instructor 适配器硬编码 1024 已垫片修复）
  embeddings: {model_name_or_path: BAAI/bge-large-zh-v1.5}  # 必配（evalscope
                              # RAGEval 硬性字段；answer_relevancy/correctness 依赖）
  language: chinese           # 缺省
  batch_size: 4               # ragas 批量
  raise_exceptions: false     # true = 判官失败即中止（缺省记 NaN 出失败明细）
```

编排自动分派：数据集 records 自带 `response` → 方案 B 直接打分；无 response →
方案 A 经 target 逐 case 调被测端点生成后再打分；混合形态显式报错。

### perf / locust

`perf:` 段（parallel/number/prompt/input_file…）与 locust 见 README 对应章节与
`examples/perf/*.yaml`；压测并发键为 `perf.parallel`，与精度域
`eval_batch_size`、自研域 `rate_limit.concurrency` 互不相通。

### common.yaml（预制，data/common.yaml）

`models.<名>`：模型预制（base_url/model/api_key_env/params/rate_limit/stream），
被三处引用——自研域 `target: {profile: X}`、evalscope `tools: {judge: {profile: X}}`、
`rageval: {judge: {profile: X}}`；内联字段逐键覆盖（dict 深合并），解析后
`profile` 键移除（快照自包含）。`tools.extra_args`：工具环境缺省（如
python_exe），并入每个工具型 suite（suite 内联覆盖）。查看：`aite common show`。

### 工具域 CLI

```
aite tool benchmarks evalscope     # 列基准（主环境无 evalscope 时自动经
                                   #   common 的 python_exe 子进程列举）
aite run -c suite.yaml             # 工具型 suite 同一入口
aite report show <run_id> --formats rich,json,html,junit
```

运行期可见性：evalscope 日志实时镜像到控制台（进度条上方暗色行）+ 进度条/
阶段播报；Web SSE 同源透传。原生报告落 `reports/tool/<engine>/<run>/<stamp>/`，
平台报告（HTML 等）按 `<模块>/<模型>/<数据集>/` 归位。

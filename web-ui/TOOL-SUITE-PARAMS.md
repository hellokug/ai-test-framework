# 精度任务参数契约（evalscope 套件）——前端对接清单

> 写给前端 agent（2026-10-01，用户指示「先把参数加到静态 HTML，后续前后端直接对接」）。
> 读法：本文档 = 精度创建表单每个控件 → 后端 suite raw 字典键的映射 + 支持状态。
> 地位：**参数级契约**，后端已全部实装并实弹验证；「支持状态」列以外的不许编。
> 实弹样例（每参数都有可跑注释）：`examples/accuracy/*.yaml`（同构字典，YAML 只是序列化形态）。

## 0. 对接形态

精度任务 spec = **suite raw 字典**（`docs/domain-backends.md §6`：accuracy spec =
suite raw，engine=evalscope）。`POST /api/v1/accuracy/tasks` body `{name?, spec}`，
`spec` 就是下文的字典；YAML 文件（如 `examples/accuracy/ceval.yaml`）load 后即这个 dict。

```json
{
  "module": "accuracy",
  "run_name": "ceval",
  "description": "可选",
  "engine": "evalscope",
  "target": { "profile": "deepseek" },
  "tools": {
    "datasets": ["ceval"],
    "limit_per_subset": 5,
    "judge": { "profile": "deepseek" },
    "resume": "auto",
    "metric_thresholds": { "mean_acc": 0.25 },
    "extra_args": {
      "eval_batch_size": 10,
      "timeout_s": 14400,
      "seed": 42,
      "generation_config": { "timeout": 900, "max_tokens": 4096, "temperature": 0 },
      "dataset_args": { "ceval": { "subset_list": ["computer_network"] } }
    }
  },
  "report": ["rich", "json", "html"],
  "output_dir": "./reports",
  "storage": { "url": "sqlite+aiosqlite:///./aite.db" }
}
```

## 1. tools 段平台键（逐参数）

| suite 键 | 表单卡 | 类型 | 缺省 | 语义 / 约束 | 支持状态 |
|---|---|---|---|---|---|
| `tools.datasets` | ② 数据集多选 | str[] | 必填 | evalscope 基准名；数据源 = `GET /api/v1/tools/benchmarks`（含 name/pretty_name/metrics/tags/few_shot_num/subsets/dataset_id，可做筛选四组的字段来源） | ✅ |
| `tools.limit_per_subset` | ② 每子集抽样条数 | int | 全量 | **每子集**条数：ceval 52 科 × 5 = 260 条。多选数据集时作用于每个数据集 | ✅ |
| ② 总抽样条数 | ② | int | — | **后端暂未支持**（evalscope 原生无总条数，aite 侧换算方案已按用户指示暂缓，见 roadmap Backlog「evalscope 总条数采样」）。静态稿先渲染但标「预留」，或置灰 | ⏸ 预留 |
| `tools.judge` | ① Judge 裁判模型 | {profile} 或内联 | 不配 | evalscope 语义：LLM 判官类基准（aime26 等）**必须显式配，否则起跑即拒**；纯规则基准（mmlu/ceval/gsm8k/humaneval 等 10 个经典基准全规则）配了也不调用 | ✅ |
| ① Judge「auto」 | ① | — | — | 后端无 auto 档。建议：auto=不传 judge（规则基准零成本），遇 judge 类基准后端会显式报错引导补配；或 auto=target 同模型 | ⚠️ 建议改语义 |
| `tools.resume` | （表单暂无） | "auto"/"true"/"false" | auto | auto=状态感知续推（没跑完就地续，跑完全新起跑）；预测缓存按 sample_id 复用，改判官只重判不重生成 | ✅ |
| `tools.metric_thresholds` | ⑤ 阈值判定 | map 或 "auto" | 不判定 | map 键=报告指标名（如 mean_acc）；"auto"=按基准随机基线自动（仅选择题类）；不配=仅记录，pass 列显示 `-` | ✅ |
| `tools.cases` | （暂无） | str[] | 全部 | 用例/样本 id 过滤 | ✅ |

## 2. tools.extra_args（③ 压力 + ④ 扩展 + 高级 JSON）

| extra_args 键 | 表单卡 | 类型 | 缺省 | 语义 | 支持状态 |
|---|---|---|---|---|---|
| `eval_batch_size` | ③ 并发数 | int | 引擎缺省 | 评测并发（生成+判官共用），用户标准 10 | ✅ |
| `timeout_s` | ③ 最大运行时间 | float | 14400 | 整场子进程墙钟上限（aite 看门狗，秒） | ✅ |
| `generation_config.*` | ④ 扩展参数 | 见下 | — | evalscope 原生 GenerateConfig，实测可用：`timeout`(单请求秒)/`max_tokens`/`temperature`/`top_p`/`top_k`/`seed`/`do_sample`/`frequency_penalty`/`presence_penalty`/`stop_seqs`；**thinking → `reasoning_effort`**（原生键名不同，前端 thinking 控件映射它） | ✅ |
| `seed` | ② 随机种子 | int | — | TaskConfig.seed（抽样/乱序种子）；generation_config.seed 控制生成侧 | ✅ |
| `dataset_args.<基准>.*` | ④ 高级 JSON | map | — | 按基准覆盖元数据字段，常用：`subset_list`(钉科目数组)、`review_timeout`(判分秒)、`few_shot_num`。**保护键会被拒**：evaluation_version/few_shot_mode/allowed_few_shot_nums | ✅ |
| `python_exe` | 不暴露 | str | common.yaml 缺省 | evalscope 独立环境解释器，平台托管 | 🔒 后端托管 |

**dataset_args 实弹教训（写进提示文案）**：humaneval 判分 spawn 子进程需重新 import
evalscope（Windows 实测 ~9s），默认 review_timeout=4s 必超时出**假 0 分**——样例已配
`{humaneval: {review_timeout: 60}}`（examples/accuracy/humaneval.yaml）。

## 3. 执行与观测对接

- 创建≠执行：`POST /accuracy/tasks`（pending）→ `POST /accuracy/tasks/{id}/run`（异步）
  → `GET /accuracy/tasks/{id}/stream`（SSE）→ `GET /accuracy/tasks/{id}/result`。
- SSE 事件：progress（phase/done/total，驱动进度条）、log（`{type:"log", line}`）。
  **log 事件当前不带时间戳**——决策 8「日志时间戳 YYYY-MM-DD HH:MM:SS」由前端收到时
  本地打戳实现；权威时间戳在 work_dir `logs/eval_log.log`（evalscope 原生）。
- 阶段播报（加载数据集/拉镜像/沙箱启动）为 phase-only 事件，无 done/total。
- 报告产物：run 详情 reports 列表（json/html/txt），样例路径见
  `reports/tool/evalscope/<run_name>/<stamp>/`。

## 4. 样例 yaml 即 ground truth

`examples/accuracy/`：mmlu/ceval（多子集钉科）、gsm8k/math_500（CoT 长生成）、
humaneval（review_timeout 教训）、aime26（judge 类必须配判官）、aa_lcr（判官+阈值全配）。
改静态稿有疑问时对照同参数的 yaml 注释，注释即验收标准。

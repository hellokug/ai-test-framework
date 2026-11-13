# 断言自检数据集（assertion_selfcheck）

功能测试 expect DSL 的**回归用数据集**：用确定性假模型把全部断言键的正/反两个
方向各跑一遍，逐例核对「设计期望 vs 实际结果」。不依赖任何真实模型，秒级跑完，
改 harness 断言逻辑后跑它可立刻发现行为漂移。

## 快速开始

```bash
.venv/Scripts/python.exe examples/functional/assertion_selfcheck/run_selfcheck.py
```

脚本自动完成：起假模型（临时端口）→ 以与 pytest_adapter 相同的 argv 跑真实
pytest 引擎 → 解析 junit-xml → 逐例核对 → 打印偏差明细。退出码 0 = 全部吻合。
假模型进程用完即停，无端口残留。

## 数据集约定

- `cases.jsonl` 每行多一个 `want` 字段（引擎不消费，校验脚本专用）：
  带 `want` 的用例**设计为失败**，且失败信息须包含 `want` 关键字；不带 = 设计为通过。
- `params.case` 用于假模型按用例 id 路由到脚本化响应（真模型会忽略或拒绝未知
  字段，本数据集只能配假模型跑）。
- id 前缀：`ok_*` 设计通过；`bad_*` 设计失败。`探针Fn` 开头的 desc 表示
  **固化当前已知问题行为**的探针例——问题修复后这些用例会转为「偏差」提示，
  此时更新数据集把它转正（见下方问题清单）。
- 假模型的 handler 与用例 id 一一对应（`fake_model.py`），两边必须同步改。

## 覆盖矩阵（76 例 = 44 应过 + 32 应挂）

| 域 | expect 键 / 控制 | 覆盖 |
|---|---|---|
| 内容质量 | nonempty / contains(+ignore_case) / not_contains / no_garbled（锟斤拷、U+FFFD、误解码）/ no_leak（ChatML、think、extra_patterns）/ no_repeat（长块×4、短块×10、整行×5） | 全部正反向 |
| 结构 | finish_reason（stop/length/tool_calls/content_filter 全枚举）/ resp（path+数字索引、type、eq、len、nonempty、contains、数组多字段）/ json（空规格、围栏、path、type、eq+ic、neq、in、len、contains、fields 递归、item_type、item_nonempty、bool≠int） | 全部正反向 |
| 工具 | tool / no_tool / tool_args_contains（嵌套 arguments、非法 JSON） | 全部正反向 |
| API 契约 | http_status（整数 + eq/neq/gte/lte/in 全关系形式）、`_raw_body`、`_http_method`、`_omit_messages`、`_headers`（坏 key 覆盖 / null 删头 / 自定义头送达核对 / 错误体 resp 断言）、4xx 未配 http_status 硬抛路径 | 全覆盖 |
| 流式 | stream:true、chunks_min、sse_done（含 [DONE]/无 [DONE] 两态）、流式伪响应上 resp 断言 | 全覆盖 |
| vars | haystack 埋码取回、random_string/random_number/random_choice（seeded 值钉死端到端核对）、garbled 回显、params 占位符渲染、repeat=2 展开、未定义占位符透传探针 | 全覆盖 |

## 已发现问题（探针例固化的是修复前的行为）

| # | 问题 | 位置 |
|---|---|---|
| F1 | `ignore_case` 对 `resp.contains` 在**正常响应**下不生效（`_check_shape` 未传 ic），错误模式下却生效；而 `resp.eq` 读 `self.expect` 两种模式都生效——同模式内 eq/contains 不一致 | `harness.py` `_check_shape` / `_check_error_response` / `_check_resp_spec` |
| F2 | `json.path` 不支持数字索引（`items.0.name` 报路径不存在），`resp.path` 支持——两个规格取值能力不对称 | `_check_json_spec` vs `_check_resp_spec` |
| F3 | 流式伪响应只拼 `delta.content`，丢弃 `delta.tool_calls` → 流式下 tool 断言必挂，工具调用场景无法与 stream 组合测 | `_post_stream` |
| F4 | messages 引用未在 vars 定义的占位符（笔误）静默透传给模型，加载期无告警 | `Case._subst` |
| F5 | `strip_fences` 仅处理**整响应**为围栏的情况；「说明文字 + ```json 块」常见形态判解析失败 | `strip_fences` |
| O1 | 观察项（非缺陷）：`garbled` vars 生成器产物大多不触发 `no_garbled` 检测（生成器多区段采样 vs 检测器四特征保守口径），拿它当 no_garbled 负向用例会假绿 | `build_garbled` / `_GARBLED_PATTERNS` |

## 平台集成（可选）

拷贝本目录到托管根即可在平台里看到场景：
`cp -r examples/functional/assertion_selfcheck data/functional/scenarios/`
（假模型需另行常驻：`python examples/functional/assertion_selfcheck/fake_model.py --port 8391`），
或 `aite run -c examples/functional/selfcheck.yaml`。注意：数据集含 32 条设计
失败例，场景级 pass_rate ≈ 0.58 是**正常值**，不要配 pass_rate: 1.0 阈值。

## 维护

- 加用例：cases.jsonl 加一行 + fake_model.py 加同名 handler + 重跑脚本。
- `want` 关键字取断言消息里的稳定词（如「包含」「路径」「期望 HTTP」），
  不要取会随文案变化的部分。
- harness 断言改动回归顺序：`pytest tests/unit/test_functional_cases.py` →
  本数据集 → 真实模型 `aite run -c`。

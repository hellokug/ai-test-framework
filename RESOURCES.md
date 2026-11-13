# aite 断言体系 Resources

## Knowledge（本项目内——最高信任源就是代码本身）

- `aite/functional/harness.py` — expect DSL 的唯一权威实现：`_EXPECT_KEYS`
  白名单（L68）、`Case.__init__` 加载期校验（L664）、`Case.check` 三域断言
  （L775-895）、检测函数 `_garbled/_leak/_repeat_evidence`（L345-375）。
  用法：一切断言语义争议以它为准。
- `docs/functional-testing.md` — 架构文档：执行链、设计取舍表（含「expect DSL
  而非 checker 注册表 = YAGNI」L317、「加断言类型三处改动」速查 L375）。
  用法：先读它建立地图，再下钻代码。
- `examples/functional/assertion_selfcheck/cases.jsonl` — 断言自检用例集，
  每种键的正反例实物。用法：写新用例前抄这里的句式。
- `tests/unit/test_functional_cases.py` — 单测范式：`_case()` / `_response()`
  夹具（L28-43），加载期报错用 `pytest.raises(ValueError)`（L79 起）。
  用法：加完断言键照这里补正反例。

## Wisdom (Communities)

- 暂无外部社区需求：断言语义问题直接在会话里问 AI 执行者，或对照本仓库代码。
  owner 未表达加入外部社区的意愿，不主动推荐。

## Gaps

- 暂无。若后续想学「精度域指标体系」或「前端渲染链路」，属不同主题，另立
  mission，不在本工作区混学。

# 功能测试架构说明（模块 D：pytest 执行引擎）

> 面向需要深入理解或二次开发功能测试模块的读者。用法层面（怎么写 suite yaml、
> 怎么加用例）见 README「功能测试」章节；本文讲**底层实现**：关键类与方法、
> 任务下发的完整流程、每个设计决策的原因。

## 1. 定位

功能测试是工具适配层接入的第二个引擎（第一个是 evalscope），本质区别：

| | evalscope | pytest（功能测试） |
|---|---|---|
| 评测对象 | 静态基准数据集 | 自定义功能场景 |
| 模型调用 | 离线批量跑分 | **真实调用模型**，逐例请求 |
| 判定方式 | 官方指标（mean_acc 等） | **规则断言**（我们自定义的 expect DSL） |
| 指标粒度 | 数据集级 | 场景级 pass_rate |

核心思想：**pytest 做执行引擎，aite 做编排层**。借用 pytest 生态的收集器、
断言、junit 报告与 `-k`/nodeid 过滤能力；路由、入库、阈值、趋势全部复用
平台既有链路，不自建执行框架。

内置 8 场景 52 例，分两个域（共享同一 harness 与链路，只是被测对象不同）：

| 域 | 场景 | 被测对象 |
|---|---|---|
| 模型能力域 | basic_chat / structured_output / tool_calling / long_context | 模型本身答得对不对 |
| API 契约域 | api_params / api_stream / api_limits / api_shape | OpenAI 兼容层协议是否正确 |

API 契约域断言**严格按 OpenAI 规范**——在 Ollama 等非完全兼容服务端上，
已知 9 例差异用例 FAIL（n 多候选被忽略、role/tools/tool_choice 不校验、
超上下文静默截断、max_completion_tokens 不认、json_mode 无防呆），desc
均已标注「已知差异，换标准端点应转绿」。**显性暴露服务端兼容性差异正是
该域的价值**，不是误报。

## 2. 全景数据流：一次任务从下发到入库

`aite run -c examples/functional_pytest.yaml` 触发的完整时序：

```
主进程（asyncio）                              子进程 × N（每场景一个）
────────────────────────                       ─────────────────────────────
① cli/main.py run
   读 yaml，见 engine: pytest
② tools/__init__.py run_tool_suite
   get_tool("pytest") → 注册表查 PytestAdapter
   parse_tool_suite → ToolRunRequest
   repo.upsert_suite / create_run
③ pytest_adapter.build_config          ──→   argv + env 下发
   场景解析 / cases 过滤 / argv 构造
④ pytest_adapter.run
   逐场景 run_sync_in_thread(_run_one)        ⑤ subprocess: python -m pytest
                                                <场景目录> --junitxml=...
                                                -p aite.functional.harness
                                              ⑥ harness 插件加载
                                              ⑦ 收集期：pytest_collect_file
                                                发现 cases.jsonl → load_cases
                                                （格式校验，错误带行号）
                                                每行一个 CaseItem（nodeid=
                                                cases.jsonl::<id>）
                                              ⑧ 执行期：CaseItem.runtest
                                                rendered_messages → _post
                                                （重试语义）→ Case.check
                                                失败 → failure_detail 组装
                                              ⑨ pytest 写 junit-xml + 退出码
⑥ collect（junit 解析）                ←──   junit-xml + stdout/stderr 日志
   pass_rate = (t-f-e-s)/(t-s)
   failures 提取（含失败详情）
⑦ run_tool_suite 收尾
   rows → MetricSummary（场景行 + overall 加权行）
   repo.save_metric_summary / update_run
⑧ CLI _print_report_summary
   汇总表 + 失败详情（断言→输入→输出）
```

关键认知：**子进程与主进程之间只有两个通道**——下发走 argv + env，
回传走 junit-xml + 日志文件。子进程内任何对象（Case、响应 dict）都
活不过进程边界，一切跨进程信息必须落盘。这与 evalscope 适配器
「一律走文件收集，不依赖进程内返回值」是同一个模式。

## 3. 进程模型与退出码语义

为什么用 subprocess 而不是进程内调 pytest.main：

1. **状态隔离**：pytest 的插件注册、断言重写、收集器都是进程级全局状态，
   与主进程的 pytest（aite 自己的测试）互不污染
2. **多场景隔离**：一个场景崩了（语法错误、插件冲突）不影响其他场景
3. **exit code 是现成的契约**：不用解析 pytest 输出

退出码口径（`pytest_adapter._run_one`）：

| exit | 含义 | 处理 |
|---|---|---|
| 0 | 全部通过 | 正常，进入 collect |
| 1 | 有失败用例 | **正常**（断言失败是测试结果，不是执行错误） |
| 2 | 收集中断（如 jsonl 格式错误） | 抛 RuntimeError |
| 3/4 | 内部错误 / 用法错误（如 nodeid 不存在） | 抛 RuntimeError |
| 5 | 没收集到用例 | 抛 RuntimeError |

抛出的 RuntimeError 由 `run_tool_suite` 兜底捕获 → `report.status="error"`
→ run 记录标记 error 存库。「用例挂了」和「跑不起来」是两条完全不同的
失败路径，前者有 pass_rate，后者只有 error 信息。

## 4. harness.py 详解（子进程内的世界）

`aite/functional/harness.py` 是一个 pytest 插件模块，经 `-p aite.functional.harness`
按导入路径加载。分四块：

### 4.1 模型访问层

| 成员 | 职责 | 要点 |
|---|---|---|
| `_required_env` | 读 AITE_FT_* 环境变量 | 缺失时 `pytest.fail` 且消息带「请走 aite run」指引；**请求时才校验**，收集期零环境依赖 |
| `_request_url` | base_url → 完整 URL | 兼容已带 `/chat/completions` 后缀的写法 |
| `_post` | 发请求、管重试 | 见下 |
| `_shared_client` | 进程级单例 httpx.Client | 固定 120s 单请求超时（本地小模型推理慢） |

`_post` 的重试语义（功能测试的抗 flaky 第一层）：

```
TransportError（连接失败/超时）→ 重试 1 次
5xx（首次）                     → 重试 1 次
4xx                             → 直接抛（配置类错误，重试无意义）
语义断言失败                     → 不重试（重试掩盖不了确定性错误）
```

`_post` 另外两条路径（D9 API 契约域引入）：

- **预期错误模式**（`expect_error=True`，用例配了 `http_status`）：错误响应
  本身是被测对象——不重试、不 raise，返回 `{"_status": 码, "_body": ...}`，
  由 `_check_error_response` 断言状态码与错误体
- **SSE 流式**（`params.stream: true`）：走 `_post_stream` 逐块拼接 delta
  出伪响应（finish_reason 取末块），额外带 `_chunks`（块数）与 `_sse_ok`
  （`data: [DONE]` 正常收尾）；流式不重试（重放语义复杂）
- **请求构造控制**（params 下划线前缀，不进 payload）：`_omit_messages`
  （省略字段测参数校验）、`_http_method`（GET 测 405）、`_raw_body`
  （text/plain 发原始串测 Content-Type 校验）

注意两级超时是不同东西：httpx 的 120s 是**单请求**超时；
`tools.extra_args.timeout`（默认 1800s）是适配器给**整个场景 subprocess** 的
总超时。

### 4.2 数据驱动核心

**`Case`**（`cases.jsonl` 一行的运行时形态）：

- `__init__(raw, source)`：**加载期全量校验**——必填字段（id/desc/messages/
  expect）、expect 键 ∈ `_EXPECT_KEYS`、json 规格递归校验、no_leak 形状
  （true 或 `{extra_patterns: [...]}`，正则可编译）、vars 生成器合法。
  所有 ValueError 都带 `文件名:行号`。原则：**坏数据死在收集期（exit 2），
  不进执行期白跑**
- `rendered_messages()`：vars 占位符渲染。`{NAME}` → 生成值，递归替换
  messages 里的 str/list/dict；`copy.deepcopy` 保证原始定义不被改动，
  同一 Case 可重复渲染。生成器目前只有 haystack（确定性长文填充，
  needle 埋在 key_at 比例处，无随机 → 可复现）
- `check(response)`：断言执行，固定顺序——**响应带 `_status` 时走
  `_check_error_response`（预期错误模式，只断言 http_status / resp）**；
  否则质量类（nonempty → no_garbled → no_leak → no_repeat）→ contains /
  not_contains → finish_reason / chunks_min / sse_done / resp → no_tool →
  json → tool / tool_args_contains。
  每条断言的消息都带「期望 vs 实际」；`_brief()`（160 字符）截断回复。
  另注：messages 允许空数组（api_params 的 empty_messages 用例需要发空
  数组测 4xx）

**`load_cases(path)`**：逐行 `json.loads` → `Case`，跳过空行，id 查重
（重复报「与第 N 行重复」）。

**json 断言** `_check_json_spec`：递归规格（path 取值 → type/len/eq/in/
item_type/fields 嵌套）。两个易忽略细节：`int` 校验排除 bool（bool 是
int 子类）；`ignore_case` 作用于 eq/in 的字符串比对。

### 4.3 pytest 收集器（自定义 File/Item）

pytest 的标准扩展点，三个成员各司其职：

```python
def pytest_collect_file(file_path, parent):      # hook：pytest 扫到每个文件时问一遍
    if file_path.name == "cases.jsonl":          # 只认这个名字
        return CasesFile.from_parent(parent, path=file_path)

class CasesFile(pytest.File):                    # 文件级收集器
    def collect(self):
        for case in load_cases(self.path):       # 失败在这里抛 → exit 2 带行号
            yield CaseItem.from_parent(self, name=case.id, case=case)

class CaseItem(pytest.Item):                     # 单个用例（nodeid = cases.jsonl::id）
    def runtest(self): ...                       # 执行体：请求 + check
    def repr_failure(self, excinfo): ...          # 失败怎么呈现
    def reportinfo(self): ...                    # -v 列表里怎么显示
```

- **nodeid = `cases.jsonl::<id>`** 是用例的稳定标识，`tools.cases` 过滤
  精确定向就靠它（适配器直接把这个串放进 argv）
- `CaseItem.runtest` 执行链：`rendered_messages()` → `_post()` → `check()`；
  AssertionError 被 `failure_detail()` 包装后以 `pytest.fail.Exception`
  重新抛出，`from None` 断开异常链（否则裸 assert 回溯混入 junit）

### 4.4 失败详情（D8）

`failure_detail(case, response, exc)` 组装四段：`[id] desc` → `断言失败:`
（只取 str(exc) 首行——pytest 断言重写会追加 `assert False` 噪音行）→
`输入 |`（每条消息压平空白后头尾各 200 字符，超长标注总长）→ `输出 |`
（content 300 / tool_call 参数 120）。预算控制在每失败例 < 1KB。

### 4.5 手写用例（兼容层）

普通 `test_*.py` 场景继续可用，`chat` / `chat_raw` fixture 提供模型访问
（内部同样走 `_post`，享受重试）。这是「复杂断言」的逃生门——一次性
逻辑写 Python，通用谓词沉淀为 expect DSL。

## 5. pytest_adapter.py 详解（主进程编排）

### 5.1 build_config —— 纯函数执行计划

输入 ToolRunRequest，输出 `{scenarios, argv, env, timeout, skipped}`。
设计为**纯函数**（无 IO、无状态）是为可单测：所有映射逻辑（场景解析、
cases 过滤、argv/env 构造）都在这里，测试不需要起 subprocess。

三段职责：

**场景解析**：`_available_scenarios(root)` 扫描目录直接子项，含
`cases.jsonl` **或** `test_*.py` 的子目录算场景（glob 非递归，嵌套不算）。
`tests_dir` 有值用用户目录，否则用内置 `SCENARIOS_DIR`。

**cases 过滤**（tools.cases 非空时）：

1. 懒加载 `load_cases`（tools 包不硬依赖 pytest）逐场景取 id 集
2. 手写场景没有 cases.jsonl → 显式报错（无 id 可预校验，宁可报错不猜）
3. 未知 id → 报错并列出全部可用 id
4. 无匹配用例的场景 → 记入 skipped，不进 subprocess、无 pass_rate 行
5. 有匹配 → targets 从目录变为 `cases.jsonl::<id>` 精确 nodeid 列表
   （不用 `-k`：子串匹配会误伤，`needle` 会命中 `needle_16k`）

**argv / env 构造**。每个 flag 的存在原因：

| argv 片段 | 原因 |
|---|---|
| `python -m pytest <targets>` | targets 是目录（全跑）或 nodeid 列表（过滤） |
| `--junitxml=<work_dir>/junit/<场景>.xml` | 结果回传通道 |
| `-p aite.functional.harness` | 按导入路径加载插件（源码/安装环境一致） |
| `-p no:cacheprovider` | 不在 rootdir 落 `.pytest_cache` 污染 git 工作区 |
| `-o norecursedirs=` | 主套件把 fixtures 加进了 norecursedirs，pytest 对**显式目录参数同样过滤**，必须清空兜底 |

env 注入四个变量。**必须与 `os.environ` 合并**（`os.environ | cfg["env"]`）
——Windows 子进程缺 SystemRoot 会直接崩。变量链路：

```
yaml target.base_url ─┐
yaml target.model ────┤→ AITE_FT_BASE_URL / MODEL → harness 请求体
OPENAI_API_KEY* ──────┘→ AITE_FT_API_KEY          → Bearer 头
                        PYTHONIOENCODING=utf-8     → 子进程日志不乱码
```
\* 由 `api_endpoint` 按 `target.api_key_env`（缺省 OPENAI_API_KEY）解析。

**为什么是 env 而不是让子进程读 yaml**：职责分离。编排层懂 yaml，
运行时（harness）只认 env，保持哑插件状态——任何来源注入这三个变量它
都能工作，也不依赖平台配置文件格式。

### 5.2 run / _run_one

`run` 逐场景 `asyncio.to_thread(_run_one)`（阻塞的 subprocess 不卡事件
循环，Web 集成需要）。`_run_one`：建目录 → subprocess.run（capture_output
+ timeout）→ stdout/stderr 落 `logs/<场景>.log`（utf-8/replace）→ 退出码
按第 3 节口径分流。超时单独处理：日志写 TIMEOUT 标记后抛 RuntimeError。

### 5.3 collect —— junit 解析

对每个场景的 junit-xml 用 stdlib ElementTree：

```
pass_rate = (tests - failures - errors - skipped) / (tests - skipped)
```

- skipped 剔除分母且 `count` 同口径——overall 是按 count 加权平均的
  （`run_tool_suite` 里），分母口径不一致会算错
- tests == 0（全部 skipped 或空收集）→ 报错，不产出误导性的除零指标
- 同一遍扫描提取 `<failure>` / `<error>` 用例 → `failures` 列表
  （detail 截断 600 字符），供 CLI 打印
- build_config 是纯函数，collect 里重复调用一次拿 scenarios 结构，
  无副作用

### 5.4 list_tasks

只枚举**内置**场景（tests_dir 用户场景运行时才解析）：
`{name, pretty_name, metrics: ["pass_rate"], tags: ["functional"],
few_shot_num: None}`。数据形状与 evalscope 的 list_tasks 对齐，REST-ready。

## 6. 两个执行时刻（理解本模块的关键）

| | 收集期（collection） | 执行期（runtest） |
|---|---|---|
| 触发 | pytest 启动时扫目录 | 每个 CaseItem 被调度时 |
| 干什么 | load_cases + 格式校验 | 发请求 + check 断言 |
| 需要环境变量吗 | **不需要**（`--collect-only` 零依赖可跑） | 需要（缺失时 pytest.fail 带指引） |
| 发网络请求吗 | 不发 | 发 |
| 出错表现 | exit 2，错误带 `jsonl:行号` | exit 1，junit 里带 failure_detail |

这解释了一个日常现象：jsonl 写错字段名，**整个场景直接 exit 2 记 run 级
error**（死在起点）；字段对但断言不满足，**exit 1 记 pass_rate=0**（跑完
了，模型不行）。排查时先分清死在哪一期。

## 7. 设计决策记录（为什么这么做）

| 决策 | 原因 |
|---|---|
| 插件模块而非场景目录 conftest.py | pytest confcutdir 陷阱：已安装环境下 rootdir 之上的 conftest 不加载，安装/源码环境行为不一致；`-p <module>` 按导入路径加载两种环境一致 |
| junit-xml 作为交接 | 跨进程唯一可靠通道；行业标准格式（CI 可直接渲染）；留档可回溯（失败详情就在 `<failure>` 里，不重跑可查） |
| jsonl 数据驱动 + 自定义收集器 | 请求体与断言外移出 Python，加用例只加行；收集期校验保证坏数据不进执行期 |
| nodeid 而非 -k | 精确匹配；打错 id 在 build_config 报错列出可用项，而不是静默跑 0 例 |
| 质量断言（乱码/泄露/复读）口径保守 | 误报会把好模型判坏，违背功能测试确定性原则；宁可漏报 |
| API 契约域严格按 OpenAI 规范断言 | 在非完全兼容服务端（Ollama）上显性暴露真实规范差异，这是该域的价值；差异用例 desc 标注「已知差异」，换标准端点应转绿 |
| 预期错误模式（http_status）把错误响应变成被测对象 | 参数校验测试的核心矛盾：错误是预期结果。不重试不抛，原样返回交断言 |
| expect DSL 而非自定义 checker 注册表 | YAGNI：四类通用谓词内置已够用，一次性复杂逻辑走手写 test_*.py 逃生门，不做可配置框架 |
| auto 阈值 → pass_rate: 1.0 | 功能测试零容忍口径：部分通过的功能是坏功能 |
| 失败详情不入库 | details 只进 CLI 与 junit/日志留档，DB 只存场景级指标，无膨胀 |
| `raise pytest.fail.Exception(...) from None` | ① 消除裸 assert 回溯噪音 ② 断链防原始 AssertionError 混入 ③ `pytest.Failed` 不是公开属性，用 `pytest.fail.Exception` |

## 8. 数据契约速查

**suite yaml（engine: pytest）tools 段：**

| 字段 | 类型 | 语义 |
|---|---|---|
| datasets | list[str] | 内置模式 = 场景名；tests_dir 模式可空（全部）/ 非空 = 子目录筛选 |
| tests_dir | str | 用户自建场景目录（与内置二选一） |
| cases | list[str] | 用例 id 过滤，省略 = 全部 |
| metric_thresholds | dict / "auto" | `{pass_rate: x}` 显式优先；auto → 1.0；不配无判定 |
| extra_args.timeout | int | 单场景 subprocess 总超时（秒），默认 1800 |

**cases.jsonl 行结构：** `id`（场景内唯一）/ `desc` / `messages` / `tools`? /
`params`? / `vars`?（`{"NAME": {"haystack": {chars, key_at, key}}}`，
messages 里 `{NAME}` 占位）/ `expect`（必填）。

**expect 断言键全表：**

| 键 | 值 | 校验 |
|---|---|---|
| nonempty | true | 回复非空 |
| contains | str | 回复包含子串（ignore_case 生效） |
| not_contains | str | 回复不含子串（stop 序列等场景） |
| ignore_case | true | 大小写不敏感（作用于 contains 与 json 的 eq/in） |
| json | spec | 剥代码围栏后可解析 + 规格：path/type/eq/in/len/nonempty/item_type/item_nonempty/fields（递归）；`{}` 空规格 = 仅校验可解析 |
| tool | str | 首个 tool_call 的函数名 |
| no_tool | true | 不发起任何 tool call（tool_choice=none 等） |
| tool_args_contains | str | arguments（JSON 串）解析后包含子串（小写化比对） |
| no_garbled | true | 无乱码（U+FFFD / 锟斤拷 / 控制字符 / UTF-8-Latin1 特征） |
| no_leak | true / {extra_patterns} | 无内部标签泄露（ChatML / DeepSeek / INST / SYS / think 等 + 自定义正则） |
| no_repeat | true | 无复读（≥6 字符块 ×4 / 短块 ×10 / 整行 ×5） |
| http_status | int(100-599) | 预期错误模式：错误响应是被测对象（不重试不抛），断言状态码；错误体经 `resp` 断言 `_body` |
| finish_reason | stop/length/tool_calls/content_filter | 结束信号语义（截断/工具/正常） |
| resp | spec 或 spec 数组 | 响应体字段断言：path（支持数字索引 `choices.0`）/type/eq/len/nonempty；数组 = 一用例多字段 |
| chunks_min | int(≥1) | 流式 SSE chunk 数下限（验证真分块） |
| sse_done | bool | 流式是否以 `data: [DONE]` 正常收尾 |

**请求构造控制**（`params` 下划线前缀，不进 payload）：`_omit_messages`
（发缺字段的坏请求）、`_http_method`（GET 测 405）、`_raw_body`（text/plain
测 Content-Type 校验）。**流式**：`params.stream: true` 自动走 SSE 解析。

## 9. 维护指引

**日常三操作：**

| 操作 | 动作 | 验证 |
|---|---|---|
| 加用例 | cases.jsonl 加一行 | `pytest <场景> --collect-only -q -p aite.functional.harness`（零环境秒验格式） |
| 加场景 | 目录放 cases.jsonl（内置 scenarios/ 或自建 + tests_dir） | 同上 + `aite tool benchmarks pytest`（内置模式） |
| 加断言类型 | harness 三处：`_EXPECT_KEYS` 加键 → 写检测函数 → `Case.check()` 接线（带参数再在 `__init__` 加形状校验） | tests/unit/test_functional_cases.py 补正反例 |

**改动回归路径：** `pytest tests/unit/test_functional_cases.py`（DSL 纯函数，
秒级）→ `pytest tests/`（全量，含 fake-server 端到端）→ 真实模型
`aite run -c`。单用例调试可用 `tools.cases` 过滤或手动注入 AITE_FT_* 直跑
pytest。

**已知边界：**

- 工具调用断言只看**首个** tool_call；多工具编排（连续调用）场景未覆盖
- `_available_scenarios` 只认直接子目录，嵌套场景目录不支持
- cases 过滤不支持手写 test_*.py 场景（无 id 可预校验，显式报错）
- 模型访问只支持 openai_compatible target（`api_endpoint` 的口径约束）

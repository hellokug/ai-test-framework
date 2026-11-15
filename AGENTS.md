# AGENTS.md — ZCode 会话补充规则

本文件由 ZCode 自动加载。全工具通用的项目总纲在 `CLAUDE.md`；本文件只放
ZCode 专属的行为规则。

## 效率复盘与自动优化闭环（2026-11-08 owner 确认，长期有效）

**触发**：本轮从用户最新提问起算使用了 ≥3 次工具调用（任务型工作）时，最终
回复末尾必须附「效率复盘」小节并以 `<!--eff-review-->` 收尾。Stop hook
（`.zcode/config.json` 注册 → `scripts/hooks/stop_efficiency_gate.py`）检测不到
标记、或复盘里没有「耗时 + 数字」与 token，会阻止结束并在提示里附上本轮实测数字。
琐碎问答（<3 次工具调用）不需要复盘，也不用加标记。

**取数**：把 `.venv/Scripts/python.exe scripts/session_stats.py` 并进最后一次核对命令
（同一个调用，不为取数多跑一轮请求）。它读 ZCode 本地计量库，给出本轮耗时拆分、
token、单工具请求占比、最慢的调用和撑大上下文的输出。

**复盘格式**（紧凑，≤12 行）：

### 效率复盘

- **结果**：一句话交付说明。
- **消耗**：照抄 `session_stats.py --brief` 那一行；会话里有多轮时用 `--all` 补一句各轮耗时。
- **耗时大头**：完整报告里最大的 1～3 处，写明是什么、花了多少时间。
- **低效点**：≤3 条，每条注明估计浪费的时间；无则写「无」。典型：上下文过大拖慢
  每次请求、可合并的调用被拆成多轮、探索方向返工、全量跑测而非按改动域选测。
- **优化动作**（owner 2026-11-10 确认：低效点一律当场落地，别只记账）：①规则类当场固化进
  本文件；②代码/脚本/helpers 类当场修复落库；③可复用流程沉淀为 workspace skill
  （`.agents/skills/`）。只有跨会话、多步骤的大项才追加一行到 `docs/efficiency-log.md`
  （日期｜低效点｜建议动作｜状态）。

**闭环**：会话开头 SessionStart 钩子会注入 `docs/efficiency-log.md` 的未结项（没看到注入
就自己读尾部 10 条）；大任务开工前过一遍，能顺手消除的做掉并把状态改 done；owner 可随时
要求清账。会话级深度复盘用 `/retro`。

**维护**：调阈值/改标记改 `scripts/hooks/stop_efficiency_gate.py` 顶部常量；
临时停用把 `.zcode/config.json` 的 `hooks.enabled` 置为 `false`。写新钩子照
`scripts/hooks/context_sync.py` 文件头（stdin 字段、输出 schema、信任审批）。

**规则变更**：本文件只在会话启动时进系统提示；会话中途的修改由
`scripts/hooks/context_sync.py` 在下一次用户提问、Bash 或浏览器调用后以差异推送（开头是
「AGENTS.md 在本会话加载后被修改」），以推送内容为准。钩子信任逐条审批：新增或改动的声明
在新会话启动时由 ZCode 弹出审批（10 分钟内有效），owner 批准后才运行，已批准的声明不受
影响；各条状态用 `python scripts/hooks/hook_status.py` 查，回归测试在
`tests/unit/test_zcode_hooks.py`。

## 多会话并行认领（2026-11-12 owner 确认，长期有效）

多会话并行开发用 `docs/session-claims.md` 做活认领，SessionStart 注入在认领、中途
变更由 context_sync 随 AGENTS.md 差异一起推送：**动工前**看在认领，撞域就换域或
排队；**动工时**登记一行（时刻｜任务｜认领范围｜预计释放），同一域同时只允许一个
写者；**收尾**删掉自己那行——租约表不是账本，历史进 efficiency-log/HANDOVER。
共享协作文档（CLAUDE.md / AGENTS.md / roadmap / HANDOVER / efficiency-log /
共享 assets 与 `?v=` bump）任何时候全局单写者。配套提交纪律：长任务按域中途落库
（测试通过的完整单元即小步提交），不在共享工作树攒大批跨域未提交变更。

## 提交与教学门禁（2026-11-12 owner 确认，Stop hook 强制）

- **提交门禁**（`scripts/hooks/stop_commit_gate.py`）：mtime 老 ≠ 写它的会话已收尾，
  所以不计积压的有三类——①认领表/账本（活文档）；②**活认领范围内的文件**（门禁读
  `docs/session-claims.md`「## 在认领」段的路径前缀做豁免，支持 `a/{b,c}.py` 花括号
  展开）；③45 分钟内的新写入（顶部常量 STALE_MINUTES）。剩下「不在任何认领范围且
  超时」的文件才阻止收尾。因此**并行会话动工必须登记认领**：不登记的会话，其文件
  超时后会被当作积压催提交；认领收尾要删行，长期不删会白白豁免该域。收到提示把
  测试通过的完整单元按域提交掉（共享文档单写者）；确认现在不宜提交（如其余是并行
  会话在途文件）则在最终回复加 `<!--commit-deferred: 原因-->` 显式延后；不要代提交
  别的会话认领中的文件。配套纪律：每个独立修改一次提交，不在共享工作树攒跨域大批
  变更。
- **教学门禁**（`scripts/hooks/stop_teach_gate.py`）：本轮 Write/Edit 过代码文件
  （.py/.js/.ts/.cjs/.mjs/.html/.css；.md 文档不算）而最终回复没有「### 教学」或
  「### 设计说明」小节 → 阻止收尾。按 CLAUDE.md 教学义务四段式写（做什么 → 为什么
  这么做 → 用了什么模式/机制 → 以后改哪里），简化语言、结论直接给表格；改动清单
  必须给「文件:行号 + 函数 + 改动一句话」结构表格，模式段点名具体设计模式并按
  「解决什么问题 → 代码体现 → 替代方案代价」展开（2026-11-13 owner 要求，见
  CLAUDE.md 总纲 1/5）；琐碎修复（错字/一行日志等）在回复加 `<!--teach-skip-->`
  显式跳过。
- 新钩子在下会话启动时弹信任审批，owner 批准后生效；状态查
  `python scripts/hooks/hook_status.py`，回归测试在 `tests/unit/test_zcode_hooks.py`。

## 提速规则（2026-11-09 按计量数据定）

模型时间占每轮耗时的 90% 以上，工具不到 10%：省时间靠减少请求次数、压住上下文。

- **上下文**：同样的小步请求，上下文 6 万以下中位 4 秒，12～17 万 28 秒，17 万以上
  34 秒。读文件带 offset/limit，搜索结果截断后再看，只加载当下要用的技能。复盘时单次
  上下文已过 15 万，就在结论里提醒 owner：下一个不相关的任务开新会话。
- **合并调用**：互不依赖的查询、读取、检查放进同一次请求；下一步依赖上一步结果时才
  分开。实测每轮 70% 以上的请求只发 1 个工具调用，合计约 30 分钟。
- **探索交给子代理**：要 5 次以上只读调用才答得出的问题（逆向第三方代码、全库搜寻），
  派 Explore 子代理去做，主会话只收结论：子代理在小上下文里跑得快，过程输出也不进主会话。
- **找文件**：按名字找用 `rg --files -g '<模式>'` 或 `git ls-files`，二者跳过 `.venv`；
  `find` 扫全仓一次要 60 秒。
- **rg 参数**（2026-11-10）：别带 grep 习惯写 `rg -rn`——rg 的 `-r` 是「替换」，会把命中
  改写成字母 n（`/runs/` 显示成 `n`，误读输出又多跑一轮）；行号只用 `-n`。模式里有 `(` 等
  正则元字符的字面量搜索用 `-F`（`rg -F "TargetResponse("`），否则直接报 regex parse error。
  搜 `.venv*`/`site-packages`/`node_modules` 等被 .gitignore 的目录内容必须加 `--no-ignore`
  （rg 默认整目录跳过、显式点名文件才命中——2026-11-14 在 .venv-evalscope 空结果空转一轮）。
- **统计管道**（2026-11-12）：rg 输出要 `cut -d:` 取文件名时用相对路径搜（`rg ... aite/`），
  绝对路径 `D:/...` 的盘符冒号会被当分隔符切出「D」，分布统计作废重跑。
- **定位**（2026-11-11）：改前先跑符号索引拿函数清单 + 行号，Python 用
  `rg -n "^(class |def |    def )" <文件>`（mockup JS 用 `scripts/mockup_symbols.py`），
  拿到 offset 直接精读目标区段；不要 rg 定位后再整段读，更不要盲猜 offset（读错区段纯浪费一轮）。
- **并行编辑**（2026-11-11）：互不依赖的**不同文件** Edit/Write 同一轮并行发出（2026-11-11
  实测可省 2～3 轮）；同一文件多处编辑保持串行（old_string 互扰）。「改完→跑门禁→看结果」
  链路天然串行，别强行合并。批次里每个文件必须**本会话 Read 过**（只 grep 过的不算；
  被并行会话改过的报 modified since read，2026-11-11 为此空转一轮）。
- **日志追加**（2026-11-11）：roadmap/HANDOVER 追加条目用「当前末尾真实文本」当锚点
  （old_string = 最后一段原文，new_string = 原文 + 新条目），别拿上一条目整段替换——
  会把历史记录覆盖掉。

## 验证门禁命令速查（2026-11-08 固化，别再试错找入口）

```
.venv/Scripts/python.exe -m ruff check aite tests scripts   # lint（ruff format 不是门禁，勿跑 --check）
.venv/Scripts/lint-imports.exe                              # 分层合同（不是 python -m lint_imports）
.venv/Scripts/python.exe scripts/audit_func_len.py          # 函数 ≤80 行审计
.venv/Scripts/python.exe scripts/arch_map.py                # 架构图再生 + 漂移校验（无参数）
.venv/Scripts/python.exe scripts/mockup_symbols.py --check  # 静态稿改动：跨脚本覆盖/重复定义/资产版本
```

pytest：`pyproject.toml` 的 addopts 已带 `-q -n 4`，命令行不要再加 `-q`（叠成 -qq 会吞掉
「N passed」汇总行）；关并行用 `-n0`（`-p no:xdist` 会让 `-n 4` 变成无效参数）。

选测对照（全量只在跨域改动与发布前，CLAUDE.md 原则 4）：精度资产层改动 →
`pytest tests/unit/test_accuracy_assets.py tests/unit/test_cli_accuracy_assets.py
tests/integration/test_accuracy_assets_api.py -q`；功能资产层 →
`tests/unit/test_cli_functional_assets.py tests/integration/test_functional_assets_api.py`。
造测试夹具（spec/数据行）前先读对应校验器（`core/loader.py` SuiteConfig._sanity /
`functional/harness.py` Case），不要靠猜跑红再改。

## 改动域触点速查（2026-11-11 起：改完一个跨文件改动域顺手补一行，只写验证过的函数级触点）

- **功能详情/请求留痕链路**：引擎 `aite/functional/harness.py`（`_post` 请求构造 +
  `_request` 留痕 {url, method, headers, body}，敏感头经 `_mask_headers` 打码；
  `Case.check` 断言；`record_result` 落库条目）→ 落库映射 `aite/storage/repositories.py`
  `save_tool_case_record`（case.input 只存 messages，params/expect 进 result.raw）→
  前端 `web-ui/mockups/v1/api32.js` `showLiveTask`/`caseBlock`（渲染门禁
  `node web-ui/mockups/render_check.cjs`，fixtures 同目录 json；改完 bump api32 `?v=`）→
  测试夹具 `tests/unit/test_functional_cases.py`（fake server env、`_headers` 控制样例）。
- **功能/精度资产层 CRUD**：`aite/service/functional_assets.py` / `accuracy_assets.py`
  + 对应 router `web/routers/functional.py` / `accuracy.py`；测试文件见上方「选测对照」。
- **数据集管理（目录/文件/设置/在线源）**：设置 `aite/datasets/settings.py`（四键
  datasets_dir/cache_dir/hf_endpoint/remote_url，env > 设置文件 `./data/dataset_settings.json`
  > config 默认；userstore.data_root / functional 摄取 / perf 产物目录都随 datasets_dir 走）→
  文件服务 `aite/service/dataset_files.py`（upload_dataset / resolve_download / hf_import /
  remote_import / probe_online；网络阻塞调用走 run_in_threadpool）→ 路由
  `web/routers/datasets.py`（⚠️ `/datasets/settings`、`/datasets/online/status` 等静态段必须
  声明在 `/datasets/{ref}` 之前）→ 前端 `web-ui/mockups/v1/common2.html` 数据集页 +
  `api32.js`（wireCommonDatasetsPage / wireDatasetSettings / wireDatasetOnlineImport /
  dsBindImportDrawer）→ 测试 `tests/unit/test_dataset_settings.py`、
  `tests/unit/test_cli_datasets.py`、`tests/integration/test_datasets_api.py`。
  IAB 已知坑补充：设置/导入卡的按钮 Playwright locator click 会卡 actionability（坐标
  cua.click 可用），chips 同理；IAB 文件选择器不支持，精度上传的文件路径只能接口级验证。

## 改动收尾必答：要不要重启（2026-11-11 owner 要求，长期有效）

每轮改动收尾必须在结论里说明「是否需要重启服务 + 自己启动用哪条命令」，不许留白：

- **静态稿改动（web-ui/mockups/）**：不需要重启任何服务。serve.py 预览是 no-store
  直读磁盘、对接层 JS 靠 `?v=` bump 防缓存，刷新页面即生效；后端（aite serve）没改代码也别动。
- **后端 / CLI / 引擎代码改动（aite/ 下）**：需要重启 `aite serve` 才生效（uvicorn 无
  --reload）；Celery/钩子类同理，注明改了什么、为什么必须重启。
- **不确定就现场验证**：起隔离实例验证过的，直接把结论（重启/不重启 + 端口）写给 owner。
- owner 自己启动时的命令（写在结论里，别让 owner 翻会话）：
  预览 `.venv/Scripts/python.exe web-ui/mockups/serve.py 8766`（默认反代 8300，AITE_API 覆盖）；
  后端 `.venv/Scripts/python.exe -m aite.cli.main serve --port 8300`（owner 平日用 miniconda
  evalscope env 起的，按原命令行重启即可）。日志都在各自前台终端 stdout（uvicorn INFO 行）；
  owner 明确「自己启动、自己看日志」时不要代起后台实例，临时验证实例用完必须停干净。

## 环境注意（Windows + Git Bash）

- 给 Windows 原生可执行文件（如 `.venv/Scripts/python.exe`）传文件路径必须用
  Windows 风格（`D:/...`）；`/tmp` 等 MSYS 虚拟路径只有 shell 内工具认，原生 exe
  会解析成 `C:\tmp\...` 导致打不开（2026-11-08 效率复盘教训）。
- 后台任务（run_in_background）的工作目录不继承当前 shell（固定在启动目录），相对路径
  `.venv/Scripts/python.exe` 报 No such file（exit 127，2026-11-11 为此空转一轮）：
  后台命令一律写绝对路径（`D:/Project/ai-test-framework/.venv/Scripts/python.exe`）。
- `aite serve --db` 收的是 **SQLAlchemy URL** 不是文件路径（2026-11-11 为此起服失败一轮）：
  临时库写 `--db "sqlite+aiosqlite:///D:/Project/ai-test-framework/.tmp/x.db"`。
- ZCode 拉起的子进程（Bash 工具、钩子）都被注入 `PYTHONUTF8=1`、
  `PYTHONIOENCODING=utf-8`；系统级没有这两个变量，原生终端里 Python 管道按 GBK 解码。
  排查编码问题先对齐这一点（2026-11-09 为此绕了约 8 次调用）。
- `curl -d` 带**中文** JSON 在 Git Bash 下编码损坏（后端 400 parsing error）：
  HTTP 灌数/冒烟一律 `.venv/Scripts/python.exe` + httpx 内联脚本。
- 截长行别用 `cut -c`：它按字节切，切碎多字节中文后整段输出被按 GBK 解码成乱码；
  用 `rg --max-columns 200 --max-columns-preview`（2026-11-10）。
- 补丁内容含反引号 / `${}` / 多行中文时不要走 bash heredoc（会截断），先
  Write 成临时脚本文件再执行，用完即删；批量改文件用「精确匹配替换 + assert」，
  禁止按 index 切片拼接（会切碎相邻代码块，2026-11-08 曾切碎 accuracy2.html）。
- Edit 的 old_string/new_string 涉及既有缩进块（try/with 内层语句）时，必须从
  本轮 Read 输出原样复制缩进，不要凭记忆重打——缩进差异会被归一化匹配到错误
  位置，替换文本带着错误缩进入文件即语法损坏（2026-11-13 为此返工 2 轮）。

## 静态稿（web-ui/mockups/v1）

- **查定义**：页面内联脚本之后还加载对接层（functional2/common2 → `api32.js`，accuracy2 →
  `api-acc3.js`），同名全局函数以最后执行的为准。某函数在哪定义、哪处生效，用
  `.venv/Scripts/python.exe scripts/mockup_symbols.py <页面> <函数名…>` 查（2026-11-09：只搜
  页面本身误判「函数缺失」，写出了被覆盖的重复实现）。
- **改完**：跑 `scripts/mockup_symbols.py --check`。共享文件（`assets/v1.css`、
  `assets/v1.js`、`api32.js`）改动后，引用它的页面统一 bump 到同一个 `?v=`；改动记入
  `web-ui/HANDOVER.md` 末尾的日期条目。
- **IAB 验证**：用 `web-ui/mockups/iab_helpers.mjs`（用法见文件头）：`domClick`/`domFill`
  走 DOM，`waitUntil` 在 Node 侧轮询，`waitForApi` 等对接层绑完监听，`guardWrites` 默认拦截
  写请求（静态稿连着真实后端），`stubDialogs` 打桩原生弹窗。点了没反应：先确认
  `waitForApi`，再用 `elementFromPoint` 查遮挡。截图单独放一个 js 调用（偶发 30s 超时会
  拖垮同一调用里的整串操作）。完整 runbook 固化为 workspace skill `mockup-verify`
  （.agents/skills/）：门禁 → 起实例 → waitForApi → 输入方式选择 → 断言 → 收尾。
- **IAB 已知坑**（2026-11-10）：`locator(sel, { hasText })` 不按文本过滤（两行都命中），
  表格行内按钮改用属性选择器（如 `button[onclick^="fapiTaskAction('copy', 1,"]`）+
  `domClick`；Node 侧 js 没有全局 `fetch`，查后端放进 `tab.playwright.evaluate` 里走页面
  同源 `/api`。要写数据的验证起隔离实例一条命令：`python scripts/iab_sandbox.py up --seed <灌数脚本>`
  （起 8310 临时库后端 + 8776 反代 + 探活后跑灌数，打印页面 URL；结束 `iab_sandbox.py down`
  停子进程删临时库；页面在 `/web-ui/mockups/v1/<页面>.html`，此时不装 `guardWrites`）。
  起代理/隔离实例前先 `netstat -ano | rg ':<端口>.*LISTEN'` 查残留：Windows 下
  `allow_reuse_address` 允许两个进程同时监听同一端口，旧会话没清干净的代理会抢走请求
  （2026-11-12：8776 残留旧双代理 + 8310 旧隔离后端，页面拿到空数据空转约 5 次调用才定位；
  SO_REUSEADDR 场景 curl 直连后端正常 ≠ 代理转发正常，对比时先查端口归属）。
- **evaluate 回调零外层引用**（2026-11-12）：传给 `tab.playwright.evaluate`/`waitUntil` 的函数
  会被序列化成字符串进页面执行、丢失 Node 侧闭包——回调体内只允许字面量、DOM 全局与 `arg`
  参数，外部变量一律经 `[sel, val]` 元组 arg 传入（能走 domClick/domFill 封装就别裸写
  evaluate）。发送前扫一遍回调里的标识符：出现回调外定义的名字必是运行时 ReferenceError，
  纯费一轮（2026-11-12 重踩 iab_helpers 文件头已有的规则，为此空转一轮）。
- **IAB 真实输入**（2026-11-10）：验证依赖 `isTrusted` 的逻辑（如抽屉改动检测）只能用
  `tab.cua.*`——`domClick`/`domFill`/`cua.type` 发的事件都是 isTrusted=false；真实改值用
  `cua.keypress` 编辑键（Backspace 等，keypress 不插入字符）。页面加载/导航后 cua 输入常整段
  不送达（页内零事件），先截一张图即恢复。`cua.click`、`cua.move` 各约 5 秒，一个 js 调用里
  别超过约 12 次（110 秒上限）；Playwright locator 的 click/fill 常卡 actionability 超时，直接用坐标。
- **live/mock 同名分区并存**（2026-11-12）：页面里静态 mock 区与 live 渲染区有同名
  `[data-page=…]` 各一份（详情页实测 2 个），裸 `querySelector('[data-page="detail"]')` 命中
  静态区拿空——详情类断言一律 `main.page-host [data-page="detail"]` 作用域（2026-11-12 为此
  空转约 3 轮）。抽屉底栏按钮 locator click 卡 actionability 时直接
  `evaluate(() => el.click())`（绑定的监听器照常触发，2026-11-12 实测 #teRunBtn）。

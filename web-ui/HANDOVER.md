# aite 前端 V1 静态稿 · 交接文档（2026-09-29/30 会话 → 下一会话）

> ⚠️ **2026-10-24 会话交接——精度域 + agent 测试全链路实装 + 全局版本管理**（下一会话必读本段）：
>
> **本次完成了精度域（含 agent 评测）后端 + 前端原地对接 + 测试，以及全局版本管理（公共）**，
> 全量 **720 passed + 3 skipped**，四道门禁全绿。
>
> **启动方式**：`aite serve --port 8300`（⚠️ 后端模块有改动，旧进程须重启才有 /accuracy/scripts
> 与 /versions；新表 model_version 由 create_all 自动建）+ `cd web-ui\mockups && python serve.py 8765`
> → 外部浏览器开 **accuracy2.html**（精度，LIVE 徽标=真后端）/ **common2.html**（模型+版本+密钥管理）。
>
> **精度/agent 数据链路（全部已实装；2026-11-08 改版：预置脚本全删、场景/脚本用户自建、清单带数据集+条数，见 docs/domain-backends.md §17；前端已重对接 = accuracy2.html + api-acc3.js）**：脚本库（原 18 个 builtin 预置 = examples/accuracy/*.yaml 扫描，
> 含 agent_eval/swebench 等 agent 预置）→ 创建任务（从脚本发起=spec 整体复用 / 自定义六卡=suite raw
> 组装，映射契约 TOOL-SUITE-PARAMS.md）→ POST /accuracy/tasks + run → 任务列表/详情（指标汇总表含
> tool_call_correctness 等 agent 指标、失败样本、报告产物路径）。数据集选择器 = GET /tools/benchmarks
> 真目录（含 gaia/xlam_fc 等 Agent 型）；模型下拉 = /common/models 预制 + DB 注册 + 自定义端点；
> **被测版本 = 全局版本管理**（common2「版本管理」页创建 → 精度创建抽屉 datalist 选择 +
> 「＋ 新版本」就地创建，契约 V1-PLAN §10.6）。
>
> **本轮新增后端**：`service/accuracy_assets.py` + `routers/accuracy.py`（GET/POST/PATCH/DELETE
> /accuracy/scripts，builtin 只读 409 / 自定义落 `data/accuracy/scripts/<id>.yaml`，保存经
> resolve_suite 真实校验）；`SuiteConfig.version` additive 字段（被测版本标注，两引擎路径兼容）；
> `model_version` 表 + GET/POST/DELETE `/versions`（common.py，(model,version) 唯一 409）。
>
> **适配层文件名（IAB 路径缓存约定）**：functional2/common2 = **api26.js**（用户侧 2026-10-24 上午
> 已由 api24 改名 api25，本轮版本管理接入后升 api26 并删 api25）；accuracy2 = **api-acc2.js**
> （api-acc1 已删）。改 js 内容必须换新文件名。
>
> **悬留**：趋势对比/人工评测两页仍为演示数据（趋势聚合 = V1-PLAN §8.6 缺口）；定时执行未实装
> （前端显式拦截提示）；产物下载端点（§8.7）；脚本编辑 UI（后端 PATCH 已有）。
> 冒烟脚本 `scripts/smoke_acc_live.py`（隔离 DB 可复跑）；详见文末决策 42–43。
>
> ⚠️ **上一轮交接（2026-10-15/16，功能域）**：

>
> **本次会话完成了功能域全链路实装**（后端 + 前端原地对接 + 测试），全量 680→690 passed，门禁全绿。
>
> **后端启动**：`cd D:\Project\ai-test-framework && aite serve --port 8300`（conda 环境）
> **前端启动**：`cd web-ui\mockups && python serve.py 8765`
> **外部浏览器打开**：`http://127.0.0.1:8765/v1/functional2.html`
> 左下角徽标 LIVE=真后端数据 / MOCK=假数据回退。
>
> **功能域数据链路（全部已实装）**：
> 评估场景（9 个内置）→ 脚本（用例原子）→ 任务发起 → 用例级结果落库 → 版本归档 → 质量大屏
>
> **用户最后指示**：「把当前的评估场景和脚本都删掉，我要从头开始构建」——
> **✅ 已于 2026-10-16 凌晨会话执行完毕**：`data/functional/scenarios/` 用户根本不存在
> （= 已空，无用户自建场景/脚本/scripts.json）；`aite.db` 单事务清除 functional 域
> 测试数据 208 行（functional_task 15 / version_result 3 / functional run 30
> [含 9 月初 19 条早期 run，engine=pytest 口径权威圈定] / 套件 13 / case 21 /
> case_result 21 / metric_summary 84 / metric_result 21；dim_tag 本就 0）+
> `reports/tool/pytest/` 报告产物目录。**备份 = 项目根 `aite.db.bak-20261016-0007`**
> （已 gitignore，确认无误后可删）。剩余 178 run 全为精度/性能/swebench 域，未动；
> 9 个 builtin 场景为框架资产照常可见；`data/datasets/functional/mini-set` 是数据集
> 非场景/脚本，不在指示范围，保留（用户可自行在 UI 删）。活后端 8300 实测：任务
> 列表/版本结果库已空，无需重启。**用户现在可从 UI 从头建档**。
>
> **待定设计项**：模型双注册表（common.yaml 预制 vs models 表网关别名）统一方案。
>
> 详见文末决策 20–22 + roadmap 续三~续七。

> 新会话开场必读顺序：本文档 → `web-ui/V1-PLAN.md` → `CLAUDE.md`（项目铁律）→
> `docs/roadmap.md`（进度记录最后 5 条）。
> **精度创建表单要动的话，先读 `web-ui/TOOL-SUITE-PARAMS.md`（2026-10-01 新增：
> 后端已实装的参数契约 + 六卡映射 + 缺口标注，用户指示先把参数补进静态稿）。**
> 用户正在以"逐模块评审静态稿"的方式推进前端 V1，**尚未对接后端 API**。
> ⚠️ 上一行"尚未对接后端 API"已过时：功能域已原地对接（api6.js 适配层 + 后端全链路），
> 其余三域（精度/性能/可靠性）仍为静态稿。

---

## 1. 一句话现状

前端 V1 静态稿（假数据、不对接）已完成并经用户多轮迭代**精度模块基本定稿**；
支撑前端的**后端 API 缺口已补齐并全量测试通过**；精度页调试面板已移除、
数据集筛选框已改醒目样式（独立描边+筛选按钮，css v=5）；
待办 = 用户继续过其余模块静态稿 → 搭 Vite+Vue3+TS 工程 → 按规划补齐剩余后端能力。

## 2. 本会话完成的三块工作

### 2.1 规划定稿 `web-ui/V1-PLAN.md`
- 17 页四模块+公共页面排布/内容/数据源；**§10 是前端依赖的新 API 契约**（前端只依赖契约）。
- 评审中随用户反馈持续更新（§2 精度整节重写过，见 §3 决策列表）。

### 2.2 后端 API 补齐（按最终规划，已全部实现+测试）
- **四域任务 REST**：`aite/web/routers/domain_tasks.py`（一份工厂四实例），
  契约 = `docs/domain-backends.md §2`（POST/GET/PATCH/DELETE/run/stop/retry/result/stream，
  错误映射 404/409）。`app.py` 的 `AppState.task_kits` 启动装配四 kit（bus 共享）+
  startup 各域 `mark_stale` 收敛。SSE 助手上收 `aite/web/sse.py`（runs._stream 同源复用）。
- **数据集目录 REST**：`aite/web/routers/datasets.py`（GET /datasets?type=、
  GET/DELETE /datasets/{ref}、POST /datasets/functional/import），ref 三级解析走
  `datasets/catalog.py`；功能摄取走 `web_service.import_functional_dataset`（临时文件→真实校验）。
- **/runs 与 /stats 增 domain 参数**：`web_service.run_domain/filter_runs_by_domain`
  单一来源（engine→域映射见函数 docstring）；带 domain 时扫 `config.MAX_DOMAIN_SCAN`
  条内存过滤再分页。
- 测试：`tests/integration/test_domain_tasks_api.py`、`test_datasets_api.py`、
  `tests/unit/test_web_service_filters.py`（+26 项）。全量 589 passed；
  唯一 1 failed = 用户工作区既有 WIP（report_relpath 加模块层后测试未同步，stash 验证过与本次无关）。
- ruff / lint-imports / 函数长度审计全绿。**全部未提交**（见 §6）。

### 2.3 静态稿 `web-ui/mockups/v1/`（5 页 + assets/v1.css + assets/v1.js）
- `accuracy.html`（5 页）/ `functional.html`（3 页）/ `perf.html`（3 页）/
  `reliability.html`（2 页）/ `common.html`（5 页）。假数据、零后端依赖、echarts 走 CDN。
- 共享：v1.css（设计令牌+六主题+全部组件）、v1.js（主题切换/壳导航/页内 hash 路由/图表助手）。
- 根目录还有早期风格样 A/B/C/A2/A3（被 v1/ 取代，仅存档）。
- `serve.py`：no-store 静态服务器（**8765 与 8766 双端口都在跑**，后台进程随会话
  结束而死，新会话先重启：`cd web-ui/mockups && python serve.py [端口]`）。

## 3. 用户已拍板的产品决策（累积，全部体现在 v1/ 静态稿 + V1-PLAN）

1. **主题**：A3 六套主题（Obsidian 默认 + GitHub/Spotify/Aurora/Stripe/AntD），
   顶栏调色板统一切换、localStorage 记忆；CSS 变量令牌方案，新主题=加一组变量。
2. **导航**：顶部一级模块（功能/性能/精度/可靠性/公共）+ 左侧二级菜单；
   全局搜索/通知/用户在顶栏。**统一口径（2026-10-02）**：四个任务域侧栏一律
   「任务列表 / 任务详情」开头 + 模块特有页（功能=场景目录；性能=压测大屏、
   性能数据集；精度=趋势对比、人工评测；可靠性=故障观测）；性能与可靠性
   本轮补了独立的「任务详情」页（perf=实时运行视图+实时曲线+日志尾部；
   reliability=实验详情：RTO 三卡+事件时间线+探针序列+配置快照）；公共模块
   是资源管理库不适用此口径。**任务列表统一筛选条（2026-10-05）**：
   执行状态 chips（data-status）+ 执行人下拉 + 关键词 + 模块特有筛选（精度=套件、
   功能=目标模型、性能=—、可靠性=故障类型），共享 helper = v1.js `bindTaskFilter`，
   四表均已接真实前端过滤（假数据内存过滤）并带「执行人」列（perf 放任务名副行）；
   执行人假名单：zhimeng（我）/陈曦/刘畅/王维。版本 css v=9 / js v=6。
   **任务列表=纯列表口径（2026-10-05）**：列表页只放筛选条+表格（统计卡保留），
   创建一律走按钮——精度/功能/性能=弹抽屉（perf 的创建表单已从列表页挪进
   newTaskDrawer），可靠性=「＋ 新建实验」跳故障观测工作台；公共不适用。
   **一级导航重构（2026-10-05 用户拍板）**：顶栏 = 任务管理 | 用例管理 | 数据集管理 |
   模型管理 | 执行机管理（5 个一级）。四个任务域并入「任务管理」，左侧栏顶部加
   **域切换下拉**（功能/精度/性能/可靠性 → 换对应 html 并保留当前 hash，页面不存在的
   自动回落到该域首页）；原公共页升级为独立一级模块（common.html 按 hash 分组出
   单页侧栏；后台任务归入任务管理侧栏，带域下拉）。实现全在 v1.js initShell
   （TOP_NAV/GROUP 配置），文件不合并。**单页一级模块（用例/数据集/模型/执行机管理）
   隐藏左侧栏、内容通栏**（body.no-side，用户反馈侧栏冗余）；后台任务保留侧栏
   （域下拉有功能语义）。**壳层拆 buildShell（一次性脚手架）+ applyRoute（载入/
   hashchange 都跑）**：同文件内顶栏 tab 跳转（如 用例管理→数据集管理）靠
   hashchange 路由，不再依赖整页刷新——教训：区域替换曾误删 showPage 定义，
   全局函数丢失表现为「tab 高亮变了但页面不切」。js v=9 / css v=11。
3. **精度创建任务 = 结构化表单**（全按钮、零 YAML；YAML 属后台语义，前台无套件管理页）。
   2026-09-30 按用户反馈重排：**六张分节卡（编号徽标）；每个参数一行 = 左标签 + 带圈问号
   （悬浮显示参数说明 .qmark/.tip）+ 右侧控件**：
   - ① 基础配置：任务名称（留空自动生成）、目标模型（**下拉框**，来自公共 · 模型管理）、
     **Judge 裁判模型**（auto/不使用/指定，开放式答案靠它打分）、**被测版本（必填，下拉框）**
     ——版本列表来自**公共 · 版本管理（⚠️ 公共模块需新增此页 + 契约，V1-PLAN 未含）**，
     按所选模型过滤，旁有「＋ 新版本」登记入口；版本趋势横轴靠它
   - ② 数据集配置：**评测数据集 = 可搜索、可多选列表（无左标签，满宽）**，搜索框内筛选——点「筛选」按钮弹两级
     下拉（先选筛选类型→再选值），选中生成 key-value token；四组筛选：
     归属（开源/个人/客户）、存储位置（在线/离线）、能力维度（知识问答/数学推理/代码/RAG/
     Agent/鲁棒性/长上下文，多选 OR）、类型（LLM/Agent/RAG）；不选=模糊搜索；
     每子集抽样条数（全量/100/300/500/自定义）、总抽样条数（合计上限，留空不限）、随机种子 seed
   - ③ 压力配置：并发数（不设档位，留空=引擎默认）、最大运行时间（超时强杀，留空不限）
   - ④ 扩展参数：max_tokens/temperature/top_p/top_k/thinking 一行一个（留空不传）
     + **高级 JSON 显眼展开面板**（虚线主题色边框+「高级」徽标+箭头旋转，按钮展开）
   - ⑤ 指标阈值判定：auto / 不判定 / 自定义（逐指标 ≥/≤ 规则行）
   - ⑥ 执行方式：立即执行 / 定时执行（datetime）/ 仅创建
   - **创建 ≠ 执行**：创建=配置固化生成 task_id（pending），执行绑定 task_id；
     同配置再跑=retry 链新 task_id
   - **2026-10-01 参数契约对齐**（`web-ui/TOOL-SUITE-PARAMS.md`，后端已实装）：
     Judge 去掉伪 auto 档（默认不配置=纯规则判分，judge 类基准必须显式选，否则起跑即拒）；
     ③ 补断点续推 resume（auto/true/false）、并发=eval_batch_size（用户标准 10）、
     超时=timeout_s（缺省 14400s）；④ 增 seed（生成侧 generation_config.seed，与②抽样种子独立）、
     thinking→reasoning_effort 映射、高级 JSON 支持 dataset_args（subset_list 钉科 /
     review_timeout，含 humaneval 4s 假 0 分警告与保护键提示）；每子集抽样条数语义=每子集各 N 条；
     总抽样条数标「预留」置灰（后端未支持）；数据集数据源对齐 GET /tools/benchmarks
     （能力维度筛选=tags，own/loc/type=catalog 标注列后端待补）。版本 css v=8 / js v=5
4. **运行详情 = 实时运行视图**：进度五指标（总数据条数/已运行/剩余/失败/**预计剩余
   时间**）+ 当前阶段；指标汇总运行中标注「阶段性」；**全部失败用例输入输出**
   独立卡（failures.jsonl 下载）；**抽样结果复核**（seed 随机抽样+人工复核标记）；
   运行日志页面只留实时尾部，完整日志/输入输出明细/产物包给下载链接；
   失败用例与抽样复核**上下两行独立卡**（不并排）。
5. **版本质量对比**（取代原趋势页+雷达图）：横轴=版本号、纵轴=分数；**基线虚线，
   ≥基线绿 / <基线红，劣化点就地标注 ▼pp**；横向（同模型跨版本，单选）/
   纵向（跨模型，多选）两种对比；版本质量判定表 + 劣化检测面板（阻断发布建议）。
6. **全站不展示成本**（token 能算、钱算不准会误导）；统计卡改评测数据集/受测模型。
7. 其余：功能详情有失败详情块（断言→输入→输出）；性能大屏照搬已定稿版式
   （统计表置顶+八图族）；可靠性故障观测三段表单+RTO 时间线。
8. **术语口径**（2026-10-01）：精度域单条数据一律叫「**样本**」，不叫「用例」
   （用例 = 整个评测数据集；功能域测试用例、公共 · 用例库不受影响）；
   详情日志时间戳带完整日期（YYYY-MM-DD HH:MM:SS）。
9. **用例 ↔ 任务绑定（2026-10-05 静态稿已落）**：用例（纯文字：标题/等级/前置/
   步骤/预期/auto_key）**绑定任务模板**（模板 = 套件 YAML 骨架 + 运行时参数槽
   model/version/seed，多对多，绑定存用例侧）；用例库勾选 → 「发起执行」→
   选模板 → **自动创建任务 → 自动 run → 按 auto_key 回填**（error ≠ fail；
   无 auto_key 的转人工评测队列）；不绑用例可直接建任务（两入口并存）。
   静态触点：common 用例库（勾选列/最近结果列/auto_key+模板副行/发起执行抽屉/
   成功横幅/详情抽屉补 auto_key+模板），functional 详情「关联用例结果」卡 +
   列表行「· 用例发起」来源标记。
   **目录组织（2026-10-05）**：用例库左目录树右列表（复用 .tree 组件），目录 =
   纯组织维度（DIRS + case.dir，与 domain 标签/绑定/发起解耦）；支持新建目录、
   勾选批量「移动到…」、按目录过滤（计数实时）；空目录提示引导归类。**契约未定**：case_binding 字段 / 模板实例化
   接口 / 回填 API 落库前按架构红线过讨论。css v=11。
9. **用例版本 / 迭代模型（2026-10-09 静态稿已落）**：master = 跨版本基线用例库；
   每个版本（26.9.0）默认一个 baseline = 版本全集；迭代（B01…）用例是 baseline 子集；
   迭代执行结果默认自动回填 baseline（error ≠ fail）；可从 master 拉基线 / 回刷
   master / 版本间互拉，**编号版本唯一、跨版本拉取编号不变**，合入前按「用例标题」
   做同名冲突检测 → 用户逐条确认保留/覆盖/并存（与代码解冲突同思路）。
   静态实现：用例库顶部上下文 chips（master / 26.9.0 baseline / B01 / B02 / 26.8.0，
   chip 带计数），版本 baseline 上下文出「从 master 拉取基线」、master 上下文勾选后
   出「回刷到 26.9.0」，冲突抽屉 = 自动合入清单 + 逐条冲突三选一（保留目标/采用
   来源/都保留）+ 确认合入横幅；最近结果带「已回填 baseline」标记；详情抽屉补
   版本/迭代/回填说明。
10. **定位收敛（2026-10-09 用户拍板，覆盖第 8/9 条与用例绑定相关决策）**：
    本系统**只做测试执行**；用例管理功能整体移出（用例平台 = 另一个应用，经 API
    集成：外部平台下发执行 + 结果回填）。mockup 已剥离：顶栏「用例管理」tab、
    common 用例库页（目录/版本迭代/冲突合入/发起执行）、functional「关联用例结果」
    卡与「用例发起」标记。功能域「用例结果(84)」= run 执行明细，属测试执行语义，
    保留。顶栏现为：任务管理 | 数据集管理 | 模型管理 | 执行机管理。js v=11。
11. **功能运行创建 = 场景多选（2026-10-09）**：套件对前端用户不可见（黑盒），不能
    让用户盲选 —— 改为**直接选场景**（场景目录可见可数，带说明与例数）；套件降级为
    「快捷圈选预设」（ft-full 全量 / 冒烟 / API 契约 / 工具调用），点选后展开为场景
    清单可继续增删，预设 chip 与当前选择精确匹配才点亮；tools.cases 过滤用场景 id
    前缀做命名空间。**功能域移除 chaos 注入字段**（注入是可靠性/性能域语义）。
    **三层选择细化（2026-10-09 二轮）**：① 预设标签（快捷圈选）→ ② 场景行三态勾选
    （全选 = 选中其全部用例 / 部分 = 半选态「–」显示已选 k/N）→ ③ 场景可展开散选
    单条用例；搜索框过滤场景与用例（自动展开命中场景）+「勾选/移除命中」批量；
    散选用例作为 tools.cases 参数由后台自动生成（2026-10-10 按用户反馈
    移除了手动输入框——选择树已完全覆盖其能力，不再暴露手填 id）。逐套件阈值
    判定等进阶语义待定。
    **事故与修复（2026-10-11）**：观测页重写补丁的替换区间贪多，误删了 reliability2
    的任务列表与故障场景库两个 section（表现为任务列表空白/侧栏页点不进）——已按
    记录重建并验证。教训：python 锚点区间替换前，先确认区间内包含哪些同级兄弟，
    替换后跑 div 配对 + section 计数检查。**新建任务统一右侧抽屉（2026-10-10）**：精度/功能的居中
    弹窗转 drawer-lg（800px）右侧抽屉，perf 抽屉内容分节卡化（① 引擎与模式
    ② 负载来源 ③ 压力参数 ④ 模型参数），可靠性新增 newExpDrawer（① 实验对象
    ② 注入武器与参数 ③ 观测与背景流量，开始观测后跳故障观测结果页 —— observe 页
    已瘦身为纯结果视图）。创建抽屉版式与精度统一（form-sec 分节卡 + p-row 左标签/
    问号/控件）：① 基础配置（任务名称、目标模型）② 场景选择（预设/搜索/树）
    ③ 压力配置（2026-10-10）：单场景超时 timeout（✅ 已实装，spec.extra.timeout，
    缺省 1800s）+ 场景并行（⏸ 预留置灰 —— 后端场景间/内均串行，pytest_adapter
    注释明确防嵌套 xdist 放大开销，并行实装后开放）。**压力参数随引擎与模式
    联动渲染**（2026-10-10）：只显示当前组合支持的参数（baseline=并发/时长/QPS、
    ramp=梯度/每档时长、probe=SLA 指标阈值/QPS 上限、soak=并发/时长；引擎附加
    evalscope-perf=warmup、locust=spawn_rate/arrival/jitter）；probe 仅
    evalscope-perf（locust 下禁用并自动回落 baseline）。css v=14。
12. **脚本/预定义场景的承载方案（2026-10-11 用户提出）**：代码写的场景不进前端
    管理，**目录即注册表** —— 功能域 `aite/functional/scenarios/<场景>/`（cases.jsonl
    或 test_*.py）由服务端扫描自动可见；可靠性 = **故障场景库**（一次编排固化为命名
    场景：对象/武器/观测/背景流量/预期 RTO），测试时选场景发起，不从零编排。
    静态稿已落：功能场景目录加形态标记（数据驱动 jsonl / 脚本 test_*.py）与两条
    新增路径引导；可靠性侧栏新增「故障场景库」页（列表 + 发起实验抽屉改选场景式，
    observe 工作台可保存为场景沉淀回库）。
    **发起实验 = 多场景串行/并行（2026-10-11 二轮）**：发起抽屉改为勾选式——场景
    多选（全选/清空）+ 执行方式卡（串行连跑 = 依次注入恢复；并行注入 = 多故障同时
    生效，附目标重叠放大提示）+ 覆盖项对所有已选场景生效 + 实时摘要/按钮计数；
    场景库行「发起实验」= 预勾选该场景打开抽屉（expOpen）。
    **后端缺口**：GET /functional/scenarios（目录扫描 + 元数据）、chaos 场景库
    CRUD（GET/POST /chaos/scenarios + manifest 约定，研发预置脚本批量注册）。
13. **目标模型支持预制/自定义（2026-10-10，精度/功能/性能三模块统一）**：
    目标模型下拉末项内置「**自定义（手填端点）**」——选中即展开 模型访问 url /
    模型名称 / 鉴权 api_key_env 三行（密钥不落库仅存环境变量名），免登记直接跑；
    预制项 = 按「公共 · 模型管理」登记配置执行（只选不填）。共享助手 v1.js
    `bindModelCustom(selectId, paneId)`（value=__custom__ 时显示面板）。
    （初版用「模型来源 chips + 双面板」，按用户反馈简化为下拉内置选项。）js v=14。
    **踩坑（2026-10-10）**：① perf.html 曾被内嵌浏览器路径缓存锁死（query 被忽略），
    五个页面已改名 *2.html 绕开，旧 *.html 不要再引用；
    后续实测：**全新路径（reliability3.html，从未访问过）仍渲染旧内容** —— 内嵌浏览器
    文档管道本身已损坏（非路径缓存可解），本会话的 DOM 读取与截图均不可信，
    验证请用外部浏览器或重启应用后重开预览；② 联动渲染首版有「慢一拍」
    bug——模式卡上两个 click 监听（bindRadioCards 于 DOMContentLoaded 注册、联动渲染
    于脚本解析时注册），事件按注册序执行导致渲染读到旧选中态；修复 = 渲染监听延迟
    setTimeout(0)。凡依赖 bindRadioCards 切换后状态的监听都要注意注册时序。
14. **脚本管理（四域脚本沉淀，2026-10-12 用户提出，静态稿已落）**：需求原话
    「可靠性/功能/性能/精度都要有脚本沉淀（如每版本固定基线数据集），一脚本=一用例，
    可复用已有脚本和自定义新脚本」。定稿口径（详见 V1-PLAN §6A，契约提案 §10.4）：
    **脚本 = 固化的域任务 spec + 名字**（accuracy spec = suite raw 同构；预置 =
    `examples/{domain}/*.yaml` 随框架发布、只读可复制；自定义 = 创建任务「保存为
    脚本」沉淀）；**运行参数（目标模型/被测版本/任务名/执行方式）不沉淀**，发起时
    覆盖；脚本编辑即生效、历史任务持配置快照（不做脚本版本化）；可靠性不另建页
    ——故障场景库即可靠性脚本（crumb 已标注）。静态触点：三域侧栏新增「脚本库」
    页（accuracy2 / functional2 / perf2）；创建抽屉顶部「脚本来源」双态 chips
    （复用 v1.js `bindSrcToggles`：从脚本发起 = 选脚本+只读内容快照+运行参数 /
    自定义 = 原表单不动）；抽屉脚部「保存为脚本」按钮 + 确认 hint（perf 原
    「保存为套件」更名对齐）；脚本库行「发起任务」开抽屉直达复用态并选中该脚本；
    任务列表行副行带「脚本『××』」来源标记（accuracy/functional/perf 三表）；
    功能域预设标签与预置脚本同源（full/smoke/api/agent 即研发预置）。
    **后端未动**：脚本 REST 为契约提案（V1-PLAN §10.4，`{D}/scripts` CRUD +
    tasks 增 script_id/overrides），按架构红线先过评审再实装。assets（v1.css/js）
    零改动，版本号未升。
15. **功能模块 = 质量链路重排（2026-10-12 用户提出，静态稿已落；⚠️ 层级与脚本
    语义已被第 16 条二轮修订，以 16 为准）**：需求原话
    「从最终目标往回推——回答不同版本、不同模型在不同功能维度的整体表现、对比
    优化/劣化百分比，给测试负责人多维评估场景质量展示」。定稿链路（V1-PLAN §3.0）：
    **质量大屏（目标）← 版本结果库（任务终态按版本×模型自动归档，分析唯一数据源）
    ← 任务执行（多选/全选评估场景或场景内子用例）← 脚本库（一脚本=一用例，可挂外部
    平台用例编号）← 评估场景（API 兼容性/参数校验/FC…）→ 场景内子用例（低代码）**。
    术语链：外部平台用例 ↔ 平台脚本 ⊃ 评估场景 ⊃ 子用例；子用例两形态 = 低代码
    （输入+预期+评估方法断言模板，大部分）/ 脚本 test_*.py（少量复杂；前端内嵌
    编辑窗口 V2）；评估方法全后端实现（不造指标红线不受影响）。
    静态触点（全部在 functional2.html）：侧栏六页 = 任务列表/任务详情/**质量大屏/
    版本结果库**/脚本库/评估场景（场景目录更名升级）；质量大屏 = KPI 四卡 +
    维度×版本矩阵（Δpp+判定）+ 维度趋势折线/**多版本对比雷达**（2026-10-12 追加：
    轴 = 评估维度、一线一版本，数据直取矩阵 BOARD 同源，v1.js `radarOption`
    三色正好三版本）/模型对比柱状（通栏）三图 + 劣化检测汇报结论；
    版本结果库 = 归档列表（同键覆盖取最新）；评估场景卡加「管理子用例」（低代码
    抽屉：子用例列表 + 输入/预期/评估方法断言行 + 脚本形态勾选）与「＋新建评估
    场景」抽屉；创建抽屉（复用/自定义两态）均补**被测版本必选**（归档前提），
    场景选择文案改「评估场景」；任务行/详情 crumb 带版本与「已归档版本结果库」。
    **后端缺口 = V1-PLAN §10.5 提案**（场景+子用例 CRUD、spec 增 version+终态
    归档 version_results 新表、/functional/quality/board 聚合），契约评审后实装。
16. **功能域用例/脚本模型二轮修订（2026-10-12 讨论定稿，覆盖 15 的层级与脚本语义）**：
    用户问「一个 jsonl 一个脚本还是多个 jsonl 一个脚本」——定稿：**jsonl 是行级
    格式不是身份单位；脚本 = 用例（一个测试意图，类型标签 基础/边界/异常——标签
    非层级）→ 挂 1..N 行数据（content + params + 断言，行 = 变体，行级执行判定，
    脚本结果 = 行级聚合）；物理 1 脚本 = 1 jsonl 文件**（场景目录下，引擎「目录
    即注册表」零改动）。三要素与 harness.Case 原生字段一一对应
    （aite/functional/harness.py:434：messages/params/expect + 断言键白名单；
    vars 占位符生成器白送批量变体）。两来源同一落点：**从 0 创建**（前端结构化
    三要素，harness 真实校验）/ **数据集导入**（外部 jsonl 同一校验，整体导入为
    1 脚本不拆散）。**创建硬约束：先场景后用例**（场景 = 大屏维度聚合键）。
    **功能域「从脚本发起」退位**：任务创建 = 直接选场景/子用例；ft-full 组合模板
    取消（场景快捷圈选预设保留为选择助手）；精度/性能脚本双态保留（域间语义
    分叉各自成立）。静态触点（functional2.html）：脚本库页改用例级（场景/类型/
    行数/来源/定向执行，类型+来源双维筛选）；场景卡加能力覆盖（基础 x·边界 y·
    异常 z）；三抽屉替换 caseDrawer（场景内脚本管理 scriptMgrDrawer / 从 0 创建
    newScriptDrawer / 数据集导入 importScriptDrawer）；newRunModal 去双态恢复
    直选场景；任务行 rid 改场景口径。契约修订：§10.4 functional spec = 用例原子
    （scenario_id/case_type/external_ref/rows[]，预置扫描仅 accuracy/perf）；
    §10.5 删 scenarios/{id}/cases（被 scripts 收敛）+ 增 POST
    /functional/scripts/import；§3.0/§3.1/§3.3/§6A 同步。结构校验全绿
    （div 229/229、页面 id 匹配、JS 语法、元素引用）。
    **2026-10-13 展示打磨（用户反馈）**：① 场景卡标题 = 显示名（人看：大屏矩阵
    行名/场景树同名），副行 = 场景 id（mono 机器名：jsonl 路径/tools.cases 前缀）；
    ② 维度标签支持自定义新增——scnDrawer 内「＋ 新增标签」展开行内命名（回车/
    添加即入选并进标签库，供所有场景复用），不再只有死的「自定义」选项；③ 展示
    原则确立：**大段说明性提示一律收敛进带圈问号（.qmark/.tip），页面只留短句**——
    评估场景页说明卡与 newScriptDrawer 底部说明卡已改造，后续页面照此原则逐页收敛；
    创建任务场景树同步显示「显示名 + id 副标」。
    **2026-10-13 标签库复用（用户反馈）**：维度标签 = 共享标签库——scnDrawer 的
    chips 改为由标签库数据源渲染（DIM_TAGS + localStorage 持久演示，刷新后仍在），
    「＋ 新增标签」= 添加入库（同名复用不重复建），一次新增所有场景的创建/编辑
    直接选用；后端对应 §10.5 新增 GET/POST /functional/dim-tags（标签注册表）。
    **2026-10-13 qmark 样式修复（用户反馈：间距太近/不对齐/直径小于文字高度）**：
    `.qmark` 15px inline-block + line-height hack → **17px inline-flex 居中字形 +
    vertical-align:middle + 自带 margin-left:6px**（`.p-label .qmark{margin-left:0}`
    防与 flex gap 双倍；旧内联 `style="margin-left:6px"` 同值无副作用），tip 定位
    left:22px 兼容 17px 宽无需动。**css 全站升 v=14**（5 页引用同步，js 未动 v=13）。
    **v15 追加**：`vertical-align:middle` 是按拉丁 x-height 中线算的，中文字形视觉
    中心更低（截图实证圆圈偏高）→ 改 `vertical-align:-3.5px` 按基线像素对齐
    （12.5~13px 中文标签实测），css 全站升 v=15。
    **v16 定案**：-3.5px 方向反了（middle 本就偏低，再压更低，用户截图实证）——
    基线像素猜测弃用，改与 `.p-label` 同方案：**`.field>label` 加
    `display:flex;align-items:center;gap:6px`**（问号作为 flex 项由容器居中，
    与参数行渲染完全一致），`.field>label .qmark{margin-left:0}` 防双倍间距；
    行内兜底场景 `vertical-align:-2.5px`；7 处历史内联 `style="margin-left:6px"`
    清除统一走 CSS；css 全站升 v=16。教训：**中英文混排的 inline 对齐别用
    vertical-align 猜像素，容器 flex 化是一致解**。
    **v17 定案（截图复测仍偏高 → 浏览器实测收敛）**：v16 flex 后 DOM 测量
    deltaCy=0.00（行盒严格居中）但 4x 放大截图仍见偏高——根因是 **CJK 字形墨水
    在行盒内偏下，行盒居中 ≠ 视觉居中**（vertical-align 猜不中，canvas 墨水
    metrics 也与渲染不符）。最终解 = 同屏 A/B：四行分别 top:+1/+2/+3/+4px，
    3x 放大人眼裁定 **+2px 最正**。`.qmark` 定稿 `position:relative;top:2px`
    （flex/inline 上下文通用，inline 兜底 vertical-align 回归 middle），css
    全站升 v=17，`.p-row` 参数行复测无矫枉过正。方法论沉淀：**像素级样式争议
    不隔空猜——起 serve.py + IAB evaluate 量 rect + 截图裁剪 .NET 4x 最近邻
    放大人眼裁定**。
    **2026-10-13 脚本库筛选（用户需求：常用参数筛选框 + 通用 key-value 筛选框）**：
    functional 脚本库列表加 filter-bar——常用四下拉（场景/类型/来源/最近执行结果）
    + 通用 kv 筛选框（复用 accuracy 数据集选择器同套组件：ds-picker/ds-search/
    token/两级菜单）。kv 语法 = `key:value` 回车生成 token（key 支持 label 或
    key 名：所属场景/类型/来源/外部用例/断言）或纯关键词（名称+id+数据行+外部
    用例模糊匹配）；与常用下拉 AND 组合，token 可叠加可单删，foot-note 实时显示
    「命中 X / 共 26」。CASE_SCRIPTS 数据补 result 字段（passed/failed/none，
    sse_done_marker 改未执行示例）。IAB 冒烟全过（场景筛 3、AND 组合 1、关键词
    定向 1、token 删除复原）。注意：**IAB 内 goto 同文档 hash 变化不重载页面，
    改动后必须 tab.reload()**。精度/性能两域脚本库评审时照此模式复制。
17. **侧栏重排 + 功能域后端实装（2026-10-13，用户指示「对接后端并测试」）**：
    ①functional2 侧栏顺序定稿：任务列表 / 任务详情 / 评估场景 / 脚本库 /
    版本结果库 / 质量大屏；②后端资产层落地（契约 §10.4/§10.5 提案转正，详见
    roadmap 2026-10-13 条）：`service/functional_assets.py` + `routers/functional.py`
    ——场景 GET/POST（builtin 包内只读 + 用户根 config.
    DEFAULT_FUNCTIONAL_SCENARIOS_DIR 双根合并；manifest.json 约定）、脚本 CRUD
    （scripts.json 登记 + scripts/<id>.jsonl + **派生 cases.jsonl** 引擎零改动；
    legacy 收编；行级 harness.Case 校验、先补自动 id 再校验；脚本 id 全平台唯一、
    2-64 位）、导入（jsonl/数据集二选一）、dim_tag 表标签库（内置 3 + 自定义）。
    错误映射 404/409/422。**测试**：integration +11 全过 + 真机 uvicorn 冒烟
    11 步（注意 Git Bash curl 中文 body 会 GBK 化，冒烟用 httpx）+ 全量
    649 passed + ruff/lint-imports/函数长度审计全绿。**前端对接注意**：脚本
    id 规则 `[a-z][a-z0-9_-]{1,63}`（前端「＋ 脚本」需按此生成/校验 id）；
    创建/更新/导入响应的行数键 = `row_count`（get 详情里 rows = 行列表）；
    builtin 场景写脚本 409（前端「管理脚本」对 builtin 应只读展示）。
    **仍缺后端**（V1-PLAN §8.9 悬留）：任务 spec 增 version + version_results
    归档 + /functional/version-results + /functional/quality/board。
18. **版本归档链路实装（2026-10-13 续，「继续」指示）**：功能域质量链路后端
    **闭环**——①`version_result` 新表（unique domain+version+model，同键覆盖
    取最新）；②functional 任务 spec 顶层增 `version`（可选；无标注不归档），
    `SuiteTaskExecutor.execute` 终态钩子 → `version_results.archive_maybe`
    （仅 functional；失败告警不推翻任务结果），数据源 = summaries 的
    `pytest:<场景>` 行；③GET /functional/version-results（version/model 过滤）
    + GET /functional/quality/board?model=&target=&baseline=（维度并集矩阵 +
    Δ + 达标/劣化/新增/缺失判定 + KPI；缺版本 422「无归档结果」）。
    测试 +9（归档覆盖/守卫/矩阵判定/API 带外种子）+ 真机冒烟 4 步；全量
    **658 passed** + 四道门禁全绿。**前端对接注意**：归档自动发生（任务创建 spec
    带 version 即可）；大屏端点响应形态见 `service/version_results.py`
    compute_board（matrix 元素 = scenario/target/baseline/delta/verdict）。
    前端静态稿的版本结果库/质量大屏两页可按此对接（Vite 工程化时）。
19. **架构审视修复（2026-10-13 续二，用户「审视+都修」）**：对照十四原则自查
    资产层/归档层新代码，P1×3+P2×5+P3×6 全清（详见 roadmap 2026-10-13 续二）。
    **前端对接需知的口径变化**：①场景创建 dimension 须在标签库内（API 422
    「未知维度标签」，前端标签 chips 数据源=GET dim-tags，保持一致即可）；
    ②case_type 白名单 = 基础/边界/异常/兼容/迁移；③坏 JSONL 导入报错带行号；
    ④大屏 KPI 新增 added/missing 计数字段；⑤场景/脚本列表对损坏目录容错跳过
    （服务端日志有告警，前端无感）；⑥CLI 双形态已补：`aite fscenario
    list/create`、`fscript list/show/import/delete`、`ftag list/add`。
    契约文档：`docs/domain-backends.md` 新增 §16。测试 +13 全过，全量
    **670 passed**，四道门禁全绿。
20. **前端原地对接层（2026-10-13 续三，用户拍板）**：mockups 静态稿不再纯假数据
    ——①`serve.py` 升级同源反代（/api → AITE_API 环境变量，默认 8300，后端零
    CORS 改动）；②`mockups/v1/api6.js`（原名 api-live.js→api2→api3→api4→api5→api6——IAB 按**路径**缓存且无视 ?v，改内容须换新文件名）对接层（仅 functional2.html 与 common2.html 引入，
    v=2）：boot 探测后端 → 左下 LIVE/MOCK 徽标 → 逐板块真数据（脚本库/评估
    场景/任务列表/任务详情概要/版本结果库/大屏 KPI+矩阵+结论+趋势图）+ 写操作
    （UI 建场景/建脚本/导入/删脚本/发起任务 POST tasks+run）真落盘；后端不可达
    自动回退假数据。fail-soft：单板块失败保持该板块演示数据不推翻 LIVE。
    **定位边界**：任务详情 live 仅概要；评审页未对接；为过渡形态，Vite 工程化
    时字段映射照搬。**踩坑（重要）**：IAB 按**路径**缓存且无视查询参数与
    no-store——改 js 内容必须**换文件名**（升 ?v 无效）；`[data-page="x"]`
    查询会命中**侧栏 `<a>` 链接**（壳层也加了 data-page）——section 定位必须
    `main.page-host` 前缀；goto 同文档 hash 变化不重载页面，必须 tab.reload()。
    **使用方式**：起 `aite serve --port 8300`（数据目录决定读写哪份）+
    `python serve.py 8765`，外部浏览器开 functional2.html——左下角徽标 LIVE
    即真数据、MOCK 即假数据回退。
21. **模型管理对接 + 双环境兼容修复（2026-10-13 续四）**：①用户「选不到模型」
    根因有两层——创建任务模型下拉是静态演示文案（真预制在 data/common.yaml）+
    functional_assets 用了 `datetime.UTC`（3.11+）在 conda py3.10 运行时启动即
    ImportError（已改 timezone.utc，双解释器 32 项测试全绿）。②新端点
    `GET /api/v1/common/models`（web/routers/common.py，common.yaml 只读清单）
    ——创建任务「目标模型」下拉的真源，任务发起的 target.profile 必须用这里的
    名字；被测版本转自由输入 input+datalist（版本管理页未建）。③公共·模型管理
    页（common2.html）对接 GET/POST/DELETE /models（DB 网关别名注册表）。
    **待定设计项**：模型双注册表（common.yaml 预制 vs models 表网关别名）的
    统一/同步方案未定——当前任务发起只认 common.yaml；common2 注册进 models
    表的内容不影响 target.profile 解析。IAB 文档管道再次损坏（简单 evaluate
    幻影报错），验证走 curl/pytest。
22. **密钥库（api_key 密文存储，2026-10-13 续五）**：用户「不想每次上服务器
    手动 set 环境变量」——`utils/secrets.py` 本地密钥库：存储 data/.secrets.json
    + 独立密钥文件 data/.secret.key（均 gitignore），SHA-256 流加密 + 随机
    nonce；`resolve_secret(name, default)` 统一解析 **环境变量 > 密钥库 >
    default**，四处 api_key_env 解析点全部收口替换（tools/base、core/loader、
    reliability_tasks×2、rageval_adapter）——环境变量语义完全不变，密钥库只是
    未设置时的兜底。REST：GET /secrets（掩码）、PUT/DELETE /secrets/{name}。
    前端 common2 模型管理页加「密钥库」卡片（掩码清单 + 录入表单）。真机 6 步
    验证全过（明文不泄漏断言钉死）。**与红线关系**：config/配置仍只存 env 名，
    tools/base 明文密钥禁入配置的拦截保持不变。测试 +8。
23. **清空功能域测试数据 + 刷新跳详情修复（2026-10-16 凌晨会话）**：
    ①清库——用户「从头开始构建」执行完毕，DB 单事务删 functional 域 208 行
    （functional_task 15/version_result 3/run 30[engine=pytest 口径，含 9 月初
    19 条]/suite 13/case 21/case_result 21/summary 84/metric 21）+
    reports/tool/pytest 产物；备份 aite.db.bak-20261016-0007（gitignore）；
    builtin 9 场景为框架资产保留；data/datasets/functional/mini-set 是数据集
    不在指示范围保留。②前端修复——用户报「评估场景刷新跳任务详情显示加载失败」：
    根因 = api6.js fapiBoot 的详情恢复兜底不看点位，无条件从 sessionStorage
    `aite-live-last-task` 恢复旧任务 id（清理后已删 → 404 卡加载失败页）。
    修复两点：boot 仅当刷新时 hash 以 #detail 开头才恢复（停在别的页刷新留在
    原地）；showLiveTask 捕获 404 优雅回落——清 sessionStorage + toast +
    backToRuns()，不再卡错误页。**api6.js → api7.js 改名**（IAB 路径缓存约定，
    functional2.html 引 api7.js?v=2）；**顺手修复 common2.html 引用 api2.js
    404 断链**（历次改名漏改，模型管理页 live 对接曾整体失效）→ 同引 api7.js。
    IAB 实测全过：场景页带残留 id 刷新留在原地、详情刷新遇 404 回落任务列表、
    场景 9 卡/脚本 9 行 builtin 渲染、common2 LIVE。
24. **新建脚本抽屉「默认两行 JSON」修复（2026-10-16，用户问「怎么默认最少两行」）**：
    根因 = 静态稿遗留——newScriptDrawer 的 #nsRows 内嵌两张演示行卡（「边界·超长
    输入」512/64k tok 变体示例），打开从不重置，且行卡**无删除控件**只能加 →
    UI 焊死最少两行；业务约束本就是数据行 1..N（1 行合法；harness 每行要求
    ≥1 断言，代码只查未知键不查空）。修复：①行模板收口 `nsRowHtml(n)` 单一来源
    （functional2 内联脚本），初始 1 空行；②每行卡「删除此行」+ 1 行下限保护
    toast + 删除后重编号；③`nsReset()` 打开即干净态（通用入口 setTimeout 兜底 +
    api 层 openNewScript 显式调），输入/chips/场景下拉全清；④api 层 desc 正则
    修复（原 `/^行\d+/` 匹配不了「行 1」带空格，desc 混入整段标题）+ 无说明
    回退「变体 k」+ **每行 ≥1 断言前置校验**（补 harness 文档声明但代码未拦的
    空洞）。**api7.js → api8.js 改名**（IAB 路径缓存约定），functional2/common2
    同引 api8.js?v=1。IAB 实测全过：初始 1 空行无残留、加行/删行/重编号、
    最后 1 行拦截 toast、脏状态关闭重开自动复位。注意：脚本详情抽屉 307/309 行
    的 512/64k 只读示例属展示内容，非创建表单，保留。清库后无用户场景 → 创建
    脚本下拉只有占位项（先场景后用例硬约束，内置场景只读不可建脚本）。
25. **断言参数行编辑器（2026-10-16，用户「只让选类型没让填断言结果」）**：原实现
    一行只有单个共享「断言参数」输入框，选多个断言类型语义混乱且 http_status
    空值静默默认 200。重做为**勾选类型即展开该类型的参数行**（renderNsAsserts，
    按勾选序与 chip 一一对应）：http_status=关系符==+状态码（100-599 校验）/
    JSON 字段=路径+关系（==/∈/长度/类型）+期望值（harness _JSON_SPEC_KEYS 的
    低代码子集，fields 递归留高级）/ 包含关键词=关键词+忽略大小写 / 不含关键词
    （替换掉引擎无对应键的「正则匹配」死芯片）/ finish_reason=四枚举下拉 /
    非空·no_leak·sse_done=无需参数提示。api9.js submitNewScript 按参数行结构化
    构建 expect（loose JSON 解析期望值、逐项显式报错）。**踩坑：重绘抹值**——
    勾选新断言触发 renderNsAsserts 整体重绘会抹掉已填参数，修复=重绘前按断言名
    收割 as-val/as-path/as-rel/as-icb 值、重绘后回填。**api8.js → api9.js 改名**
    （functional2/common2 同引 api9.js?v=1）。IAB 实测：monkeypatch FAPI.post
    捕获 payload，expect 六键映射全对、值保留断言过、params 透传正确。既知：
    goto 后立即 evaluate 可能读旧帧（本轮两连误判根源），**同文档 reload 后再测**
    是可靠路径；HTML 同路径修改在 reload 后可取到新内容（缓存非持久，旧帧问题）。
26. **vars 生成器族扩充（2026-10-16，用户「随机乱码/数字/定长字符串要预制方法才能低代码」）**：
    引擎 vars 占位符机制原只有 haystack 一个生成器且前端零入口。**后端**（harness.py，
    additive 非 Schema 变更）：`_VAR_BUILDERS` 扩至 5 个——`random_string{length,
    charset?=alnum|letters|digits|hex, seed?}` / `random_number{min?=0,max?=100,seed?}` /
    `garbled{length,seed?}`（mojibake 底料+多区段 Unicode 采样，刻意排除代理区与控制
    字符→JSON 安全可落库）/ `random_choice{options[],seed?}`；**seed 缺省=每次执行
    取新随机值、给定=可复现**；加载期逐项校验（未知生成器/缺参数显式报错）。
    **关键正确性修复**：rendered_messages 此前在发送(_post)与结果记录两处各调一次，
    随机生成器会导致「记录的输入≠实际发送的输入」——改为实例级渲染缓存（Case._rendered），
    同一次执行内一致、跨执行取新值。**前端低代码**（functional2.html + api10.js）：
    行卡新增「生成值占位符（vars）」区——五生成器按钮一键插入（空 content 自动生成
    `[{"role":"user","content":"{gN}"}]` 骨架且**光标落位 content 内**，否则第二个占位符
    会插到 JSON 结构外——实测抓到的组合流缺陷），条目行内编辑参数（长度/字符集/min-max/
    候选/埋码 chars-key_at-key/seed），移除条目同步清占位符；api 层 submitNewScript
    结构化组装 row.vars（逐项显式报错）。**api9.js → api10.js 改名**（functional2/
    common2 同引 api10.js?v=1）。契约文档 domain-backends.md §16.4；测试 +6
    （确定性/参数校验/JSON 安全/渲染一致性/未知生成器）。全量 **686 passed + 3 skipped**
    + ruff + lint-imports 2 kept 0 broken + 函数长度审计全绿。
    **运维注意**：执行路径（pytest 子进程）天然生效；但**已运行的 aite serve 进程内
    资产校验链（functional_assets 顶部 import Case）是旧模块——创建带 vars 的脚本前
    须重启服务**。**踩坑**：本项目 addopts 已含 `-q`，命令行再叠加 `-q` 会成 `-qq`
    （pytest 9 该模式隐藏汇总行），误判过「进程崩溃」——看汇总用默认详细度或只靠
    addopts 的单 `-q`。
27. **评估场景删除（2026-10-16，用户「不支持删除吗」——契约缺口补全）**：原契约
    场景只有 GET/POST。后端 DELETE /functional/scenarios/{id}：service 层
    delete_scenario 复用 _scenario_dir（builtin→409/404 语义零新码），整目录
    rmtree（目录即注册表的删除语义=manifest+登记+行文件+派生）；历史任务/版本
    归档持快照不失效。前端 **api11.js**（api10 改名）：用户场景卡「删除」按钮
    （builtin 卡无）+ confirm 注明整目录语义 + wireScenarios 重渲染。契约 §16.2
    补行；集成测试 +2；全量 **688 passed + 3 skipped** + 门禁绿；IAB 端到端
    实测过（UI 建→删→磁盘移除）。既知服务重启惯例：后端模块改动后重启
    `aite serve`（本轮 DELETE 路由已随重启生效）。悬留仍开：场景 manifest
    PATCH、标签删除。
28. **脚本编辑/断言关系/任务树数据源三连（2026-10-16 续，用户实建场景后反馈）**：
    ①脚本编辑 UI——openEditScript 详情回填（断言 chips+参数行/vars 反向重建，
    场景与脚本 id 锁定），提交切 PATCH；编辑↔新建态互切复位；**动态行卡必须
    bindChips**（漏绑=芯片无监听）。②json 断言关系加 **neq/contains(like)**
    （引擎 _JSON_SPEC_KEYS + UI 下拉；neq 恒区分大小写、contains 受 ignore_case）。
    ③任务树数据源——list_scripts 增 **row_index**（[id,desc]），wireScripts 重建
    FT_CASES（新用例默认全选）+ renderFtPick + sfScen 刷新（原写死 builtin）——
    此前新脚本在新任务里永远选不到。④submitNewScript 场景定位改显式 id=nsScen
    （原「最后一个 select」被 vars/断言行内 select 劫持必 422）。⑤v1.js
    [data-open-drawer] 启动期绑定 → live 重渲染按钮死键，api 层文档级委托兜底。
    **api14.js**（api13 改名，functional2/common2 同引）。中文名脚本名称可用
    （脚本 id 仍须英文规则）。全量 **692 passed + 3 skipped** + 门禁绿；IAB
    全链路实测过。既知：跨标签页不实时同步（各自 boot 时拉取），刷新即见。
29. **http_status 关系形式（2026-10-16 续二，用户「http_status 还是只有==」）**：
    引擎 expect.http_status 从纯 int 扩为 **int（== 兼容不变）或单键 {op: 值}**
    （op ∈ eq/neq/gte/lte/in，值域 100-599 校验保留；`_STATUS_OPS` 常量 +
    _validate_status 模块函数 + _check_error_response 关系分发）。**存在即预期
    错误模式**的派生语义（expect_error="http_status" in expect）对对象形式天然
    成立零改动。UI：http_status 参数行加关系下拉（==/!=/≥/≤/∈，∈ 逗号分隔多值），
    eq 提交保持 int 兼容格式、其余 {op: 值}；编辑回填双向映射。**api16.js**
    （api15 改名）。测试 +2（关系断言/校验矩阵）；全量 **694 passed + 3 skipped**
    + 门禁绿；IAB 实测 int↔对象 双向回填与持久化。**踩坑**：dict 字面量分发
    `{"eq": a==b, ..., "in": x in val}[op]` 全分支急切求值（op≠in 时 val=int
    TypeError）——多分支带副作用/类型敏感的择一逻辑用 if/elif，别用 dict 索引。
30. **完整请求体低代码化（2026-10-16 续三，用户「输入只有 message」+「stream 输出」）**：
    行卡补齐请求体三件——**tools 字段**（JSON 数组顶层透传，FC 用例 UI 可表达）/
    params 标签澄清（任意顶层键透传，下划线前缀=引擎控制键）/**预览完整请求体**
    折叠区（实时合并 model 占位+messages+tools+params）；断言芯片加**最少块数**
    （chunks_min，流式三件套 sse_done/finish_reason/chunks_min 齐）。stream 语义
    引擎原有零改动：params.stream=true → _post_stream 真 SSE（不重试），断言对象
    =拼接后伪响应（_chunks/_sse_ok 形态标记）。**api17.js**（api16 改名）。IAB
    实测预览合并与载荷组装正确；编辑回填 tools 已接。
31. **请求预览 Postman 化 + 函数吞没事故（2026-10-16 续四，用户「复制到 Postman 直接请求」）**：
    预览升级——目标模型下拉（GET /common/models 预制缓存，空结果不缓存可重试）+
    **真实 model 名**注入 body + **完整 curl**（base_url+/chat/completions 与引擎
    _request_url 同规则、Authorization: Bearer ${api_key_env} 环境变量占位粘贴即用、
    单引号转义）+ 复制 JSON/复制 curl 按钮（clipboard API，localhost 安全上下文可用）。
    **事故**：预览功能补丁的区间替换吞掉了 6 个函数定义（NS_VAR_KINDS/nsVarRowHtml/
    nsInsertAtCursor/nsAddVar/nsAssertHtml/renderNsAsserts）——内联脚本执行到尾部
    renderNsAsserts(...) 即抛错，**其后所有 addEventListener 全部未绑定**（编辑/加行/
    断言/vars 全瘫，表象=点击无反应+偶尔旧 toast），且 node --check 过不了不会报
    （函数调用在运行期才炸）。定位手法：功能逐条探针（rowAdd 失效）→ 手动逐语句
    执行（renderNsAsserts is not defined）→ 函数存在性审计（6 缺）。**教训入册**：
    python 锚点区间补丁后必须跑「函数存在性审计 + node --check + 页面 reload 冒烟
    三件套」，只查语法不查运行期；本轮已把审计循环固化为补丁后动作。
32. **任务状态与评估结果语义分离（2026-10-16 续五，用户「任务已通过但用例失败，任务层应该说完成」）**：
    功能域任务列表/详情的状态徽章从「已通过」（succeeded→passed 误译）改为**任务层口径**：
    succeeded=已完成（绿）/ failed=执行异常 / running=进行中 / pending=排队中 / 已取消
    （api 层 taskBadge，复用现有 badge 样式类零 CSS 改动）；原「场景通过率」列改为
    **「评估结果」列** = result.status（引擎评估层判定：通过/未通过/—）——执行完没跑完
    看任务状态，用例过没过看评估结果，两层不再混用。筛选 chips 同口径
    （data-status 改后端原始键 succeeded/failed/running）。**api18.js**（api17 改名）。
    后端零改动（kit 状态值与 result.status 本就分层，纯前端显示语义修正）。
33. **评估结果列改通过率（2026-10-16 续六，用户「test3 断言失败却显示通过」）**：
    又一层语义澄清——result.status 是**执行层判定**（阈值未配置时进程跑完即 passed，
    ≠用例全过），test3 断言失败仍显示「通过」实锤。评估列改显**通过率**：
    ①后端 suite_summary 增 evaluation_stats——functional pytest 行（dataset=pytest:<场景>
    的 value=场景通过率/count=用例数）按 count 加权出 pass_rate+evaluated_cases
    （与 version_results.extract_dimensions 同源口径；其他域无 pytest: 行 → 空缺省）；
    ②前端评估列 = 通过率百分比（100% 绿 / 0% 红 / 中间黄）+ 用例数；③旧任务 result
    是执行期 JSON 快照无 pass_rate → 前端 /runs/{id} summaries 兜底加权回填（下一轮
    轮询自动重试）。**api19.js**（api18 改名）。测试 +3（加权/非 functional 行/None 跳过），
    全量 **697 passed + 3 skipped** + 门禁绿；IAB 实测 test3 行 = 已完成 + 0% 1 例。
34. **脚本库全瘫事故（2026-10-16 续七，用户「脚本库没有刷新出来」）**：32 的
    taskBadge/evalCell 定义在 api 层，但页面内联脚本的**启动期** renderRuns 调用
    （api 加载前执行）引用它们 → ReferenceError → 内联脚本中途死亡 → 其后全部
    初始化（CASE_SCRIPTS/FT_CASES/nsRows/监听器）未执行 → 脚本库空 + 表单全瘫
    （与 31 同类症状的第二种成因）。**修复**：taskBadge/evalCell 移入页面脚本
    （渲染职责归页面，api 运行期经 window 共用同一份），api 层删重复定义。
    **教训升级（与 31 合并）**：内联脚本启动期代码**禁止引用后加载脚本的函数**
    ——v1.js 先加载可用；api 层最后加载只可在运行期（事件回调/异步链）引用；
    补丁后三件套审计再加一条：检查新增的内联执行期代码的函数依赖加载顺序。
35. **vars 占位符扩展到 params（2026-10-16 续八，用户「deepseek 支持 input 参数怎么加」）**：
    任意顶层字段（input 等）低代码路径 = 行卡 params JSON 直接写；生成值需求 →
    引擎 vars 替换从 messages 扩展到 **params**（rendered_params，与 messages
    同源 _render_values——同占位符同一次执行内两处同值；发送与记录两处调用点
    同步替换）。原始 params 不改动；无 vars 时原样透传。测试 +3，全量
    **700 passed + 3 skipped** + 门禁绿。前端 vars 提示语补「占位符也可直接写进
    params」（functional2.html 页面文案，无 js 改动未改名）。
36. **case_type 芯片劫持修复（2026-10-16 续九，用户 422「非法用例类型: http_status」）**：
    submitNewScript 的 case_type 取「抽屉内第一个选中 chip」——nsReset 清空类型后
    用户只选了 http_status 断言 → 断言芯片被当 case_type → 422。与场景 select 劫持
    同族的定位符缺陷。修复 = 用例类型芯片组显式 id=nsCaseType（提交与编辑回填都
    从组内取；未选默认「基础」）。**api20.js**（api19 改名）。IAB 回归：只选断言
    保存 → case_type=基础 + expect 正常组装。审计提示：表单定位一律显式 id/容器
    限定，禁止「第 N 个/第一个 .on」式全局查询（本会话已三连：场景 select、
    case_type chips、+可预见的未来字段）。
37. **对话承载切换 + 行复制（2026-10-16 续十，用户澄清 deepseek input=对话放 input
    字段 + 参数校验用例流程不顺）**：①行卡新增**「对话承载」下拉**：messages（默认）/
    input（DeepSeek 等新版接口对话放 input；提交时 messages=[] + params.input=对话
    数组 + `_omit_messages: true` 引擎控制键自动省略 messages——引擎原有能力零改动）/
    不发对话（纯参数校验，content 免填）；预览同步按承载合并。②**「复制此行」按钮**
    （cloneNode 复制值/勾选态 + bindChips 重绑 + 重编号）——同模板多变体改参数即得。
    ③编辑回填反向解析承载（params._omit_messages/input → 承载态，控制键不回显）。
    参数校验用例工作流收敛为：新建 → 选承载 → params 填坏值 → http_status ∈ 断言
    → 保存；多变体=复制此行改参数。**api21.js**（api20 改名）。IAB 载荷实测：
    input 行（messages=[]/params.input/_omit_messages）与 none 行组装正确、行复制保值。
38. **常用参数结构化 + JSON 高级框 + 原始报文（2026-10-16 续十一，用户「常用参数加输入框，JSON 框构造结构/key 破坏场景」）**：
    行卡 params 区重构三层——①**常用参数网格**（temperature/top_p/max_tokens/stop/
    stream/response_format/thinking 七项，留空=官方缺省）；②**params JSON 高级框**
    （合法 JSON 与常用值合并、JSON 键覆盖；乱写 key 原样透传测端点行为）；③**原始
    报文开关**（勾选后框内文本经引擎 _raw_body 原样直发——构造非法 JSON/结构破坏的
    400 场景，内容无需合法）。**关键设计：常用框不做合法值拦截**（temperature=3
    原样透传，配合 http_status 断言即参数校验用例；破坏场景勾原始开关，两不误）。
    nsCollectParams 页面层单一来源（预览/提交共用）；编辑回填按已知键拆回控件、
    控制键与自定义键留 JSON 框、_raw_body 反解为原始模式；nsReset 补清 textarea/
    checkbox 残留。**api22.js**（api21 改名）。IAB 实测：合并（JSON 覆盖 temperature）、
    非法值透传、原始残缺报文、model_param 回填拆分四场景全过。
39. **常用参数网格补全至设计文档全量（2026-10-16 续十二，用户「用例里的参数没全部支持」）**：
    网格 7 → **14 项**——新增 stream_opt（stream_options.include_usage）/ tool_choice
    （缺省/none/auto/required/具名…+具名函数名输入框显隐）/ logprobs / top_logprobs /
    user_id / freq_penalty / pres_penalty（已废弃兼容用例）。至此测试设计文档
    （deepseek-inference-api.md）涉及的**全部参数控件化 100% 覆盖**（messages/input
    承载、model 覆盖走 params JSON、_raw_body 走原始开关）。采集/编辑反解同步扩展
    （具名对象 ↔ 下拉+函数名框）；承载与具名显隐改 change 委托（click 分支移除）。
    **api23.js**（api22 改名）。IAB 载荷实测 7 新键全对 + 具名显隐双向。踩坑：
    探针 new Event('change') 默认不冒泡，委托监听收不到——真实用户操作无碍，
    探针须 {bubbles: true}。
40. **行复制丢参数修复（2026-10-16 续十三，用户「复制的时候有一些参数没有复制」）**：
    cloneNode(true) 只拷属性——用户手输的当前值（dirty value：数字框/文本框/下拉
    选中项/勾选框）不在属性里，克隆后全丢（此前恰好 textarea 保值造成误判"能用"）。
    修复 = 克隆后按序逐控件显式拷贝 value/checked（17 项全量比对零差异）。
41. **断言芯片补全至引擎全量 + 文档断言明确化（2026-10-16 续十四，用户三问「校验
    条件没明确/有些没法选/现有条件怎么解释」）**：①引擎 resp 断言补 **contains**
    （错误体模糊匹配，受 ignore_case 影响）+ 测试 2；②低代码断言芯片 9 → **15 个**
    （引擎 16 键全覆盖）：指定工具被调用（tool）/ 工具参数包含（tool_args_contains）/
    不发起工具调用（no_tool）/ 无乱码（no_garbled）/ 无复读（no_repeat）/ 错误体字段
    （resp：path+==/包含/长度/类型/非空，**仅预期错误模式生效**）；③设计文档新增
    「断言条件速查表」（芯片×引擎键×语义×生效范围×平台盲区）；OC 表 18 行断言要点
    全部明确为芯片配置；**修正 2 行约束违规**（OC-11/OC-17 thinking+具名 → 官方 400，
    调整为 auto）。**api24.js**（api23 改名）。相关 75 passed + 门禁绿；IAB 载荷实测
    tool/resp/no_garbled 组装正确。**残余盲区**（引擎暂无对应断言，速查表已标注）：
    成功响应外层字段（usage.total_tokens/model 回显/reasoning_content 非空）——
    resp 现仅错误模式。
42. **导入覆盖语义（2026-10-24，用户拍板「同 id 要做成覆盖」——此前 409 拒绝）**：
    import_script 同 id 重导 = **整体重写**该脚本（name/case_type/external_ref/rows
    走 update_script，行级 harness 校验 + 重派生 cases.jsonl）；**跨场景覆盖 = 迁移**
    （delete_script 旧场景 + create_script 新场景）；全新 id 照常创建。响应增
    **overwritten** 标记（additive），前端 toast 区分「已导入/已覆盖」。
    **api25.js**（api24 改名）。集成测试 +1（同场景覆盖/跨场景迁移/新 id 标记三段），
    全量 **718 passed + 3 skipped** + 门禁绿。
43. **编辑抽屉「从 jsonl 导入行」（2026-10-24，用户「编辑时选导入，场景参数就不用再选」）**：
    行区新增「从 jsonl 导入行」——粘贴 JSONL（逐行报错）→ 解析并**替换全部行卡**
    （复用 openEditScript 抽出的 nsFillRowFromData 全局回填：常用参数/断言芯片/
    vars/承载全量还原），审阅后保存（新建=create、编辑=PATCH rows 整体重写）——
    场景/id/名称保持现值不重选。**注意**：另一会话已在同目录继续演进
    （api26.js=api25 扩展、api-acc2.js=accuracy 域对接层、accuracy2.html），本轮
    基于 api26 做，改名 **api27.js**（functional2/common2 同引）。openEditScript
    的行回填内联段抽取为 nsFillRowFromData 单一来源。IAB 实测：18 行编辑回填
    回归 + jsonl 导入 2 行（temperature/stream/断言芯片还原）全过。
44. **失败用例卡四段重做（2026-10-24，用户「失败用例内容=输入/输出/断言/失败断言
    日志；有时一个输出有时两个」）**：**双输出根因** = 输出 JSON 块 + 失败日志文本
    内嵌的「输入|/输出|」摘要行重复渲染（通过用例无日志块所以只有一个——引擎
    failure_detail 文本自带输入/输出摘要）。caseBlock 重做为用户四段模型：
    ①输入=按 message 角色分块（role 徽章+内容，长文滚动容器）；②输出=content
    正文为主体 + 元信息标签行（finish_reason/chunks/sse_done/usage）+ 原始响应
    JSON 折叠；③断言对照=期望|实际 两列并排（表头+值截断换行）；④失败日志=失败
    原因首行醒目条 + 「引擎原始日志」折叠全文（首屏不再重复输入/输出摘要）。
    全卡各区带复制按钮（document 级委托 + __copyStore Map，clipboard API）。
    **api28.js**（api27 改名）。IAB 实测：test6 失败任务 8 张失败卡，四段结构/
    失败原因条/原始日志折叠/复制按钮全过。
    **追加修复（同日）**：断言对照恒为「—」的字段错位——cases 接口返回 **expected**
    （已解析对象），前端误读 x.expect；改 x.expected 后对照表填充（期望列=执行期
    断言快照，实际列=前端可判定项实测/引擎判定项标灰）。**api29.js**（api28 改名）。
45. **用例卡复现三件套 + 长输入折叠（2026-10-27，用户「想复现场景拿不到原请求；超长输入别全打」）**：
    输入区头部三按钮——**复制输入 JSON**（完整不截断）/ **复制 curl**（任务预制模型经
    /common/models 解析 base_url+model+${api_key_env} 密钥占位，URL 拼接与引擎
    _request_url 同规则；body=model+messages，不做前端值校验）/ **下载 .json**
    （超长输入走文件下载绕开剪贴板）。消息级折叠：content>300 字默认收起（前 300
    字+「展开全文（共 N 字符）」切换）；output 正文超长同法可加。澄清：「...」为
    提示词原文非截断；真正拿不到完整请求的长文场景由复制/下载解决。零后端改动。
    **api30.js**（api29 改名）。IAB 实测：三按钮 + curl 按预制解析（deepseek URL/
    密钥占位）正确。踩坑同前：长 JS 块禁 heredoc（Write/Edit 工具链）。
    **追加（同日）**：复制 curl/JSON 的 body 升级为**完整请求体**——合并 cases 接口
    新透出的 raw.params（剔除下划线控制键）+ raw.tools（引擎 order：model→messages
    →params→tools）；下载 .json 同源。存量 test6 回填 11 行 tools（params 含
    tool_choice 的行才发过 tools，回填规则据此精确匹配）。**api32.js**（api31 改名）。
    IAB 实测：curl body 12 键齐全（含 tool_choice 具名/tools），失败原因条+日志
    折叠正常。注意：本会话后台服务进程反复被回收（前后端各掉过 2 次）——服务
    状态用 netstat/curl 确认后再操作。
46. **curl 丢 messages 修复（2026-10-27，用户实测「复制 curl 只有 model」）**：
    /runs/{id}/cases 的 **input 字段就是 messages 数组本身**（非 {messages:[...]} 包裹），
    caseBlock 按包裹对象取 io.messages 落空 → curl body 只剩 model、输入区走 JSON 兜底
    渲染。修复 = 前端归一化（Array.isArray(input) → {messages: input}）。**api31.js**
    （api30 改名）。IAB 实测 curl -d 含完整 messages、输入区按角色分块渲染恢复。踩坑：heredoc 双层转义反复把 
 写成
    真实换行（本会话第三次）——长 JS 块一律 Write/Edit 工具链，禁 heredoc。
40. **行复制丢参数修复（2026-10-16 续十三，用户「复制的时候有一些参数没有复制」）**：
    cloneNode(true) 只拷属性——用户手输的当前值（dirty value：数字框/文本框/下拉
    选中项/勾选框）不在属性里，克隆后全丢（此前恰好 textarea 保值造成误判"能用"）。
    修复 = 克隆后按序逐控件显式拷贝 value/checked（17 项全量比对零差异）。
    **教训**：cloneNode 复制表单必须显式搬运 dirty 值；控件选择器跟随后缀变化
    （ns-params input→textarea）时探针同步更新。

42. **精度域 + agent 测试全链路实装（2026-10-24，用户「精度+agent测试都接入前端，我来测试」）**：
    functional 原地对接模式（决策 20）推广到精度域。
    **后端**：①`service/accuracy_assets.py`——脚本 = suite raw 同构（V1-PLAN §10.4 accuracy 形态），
    builtin = `examples/accuracy/*.yaml` 扫描（18 个，裸 suite raw 即时包装为 doc，损坏文件跳过告警），
    自定义 = `data/accuracy/scripts/<id>.yaml`（文档含 id/name/description/spec/时间戳，LF 强制 +
    tmp+os.replace 原子写）；保存经 `resolve_suite` 真实校验（工具域轻校验/自研域强校验双路径）；
    ②`routers/accuracy.py`——GET/POST/PATCH/DELETE `/accuracy/scripts`，错误映射与 functional 同口径
    （404/409 builtin 只读与重名/422 校验失败），app.py 挂载；③`SuiteConfig.version` additive 字段
    （被测版本标注；工具域 raw 透传无需改——**踩坑**：SuiteConfig extra=forbid，自研套件
    （agent_eval 形态）顶层 version 原本会 422）。**前端**（accuracy2.html + **api-acc1.js** 新文件名，
    IAB 路径缓存约定）：任务列表（任务层原始状态徽章，passed chip→succeeded 口径对齐 + 计数实时，
    分数列 = /runs/{id} overall 行异步兜底）、任务详情 live 重渲染（指标汇总表 metric×dataset/分组
    + overall 行、失败样本卡（run.details.failures 前 50）、报告产物路径卡、running 5s 自刷新、停止
    按钮）、脚本库（预置/自定义分组 + 内容摘要/快照由 spec 派生 + 发起任务/删除/复制为自定义）、
    创建抽屉两态（从脚本发起 = spec 深拷贝 + 运行参数覆盖 model/version/name；自定义六卡 = suite raw
    组装按 TOOL-SUITE-PARAMS 契约，抽样 seed 在 extra_args 非 tools 顶层、thinking→reasoning_effort、
    高级 JSON 合并 generation_config/dataset_args）、数据集选择器 = /tools/benchmarks 真目录
    （Agent/RAG 类型按 tags 关键词派生）、模型下拉 = 预制 + 注册 + 自定义 + judge 下拉同源；
    版本下拉转自由 input+datalist（版本管理页未建，同功能域决策 21）。趋势对比/人工评测保持演示
    数据（后端 §8.6 缺口）。**测试**：unit +11（builtin 扫描/CRUD 往返/只读 409/重名/坏 spec/自研
    spec/损坏文件容错）+ integration +5（REST 全 CRUD + 404/409/422），全量 **717 passed + 3 skipped**
    + 四门禁绿；隔离冒烟（scripts/smoke_acc_live.py，临时 DB）5 步全过。
    **踩坑（隔离卫生）**：后台起服务必须显式传 storage_url（create_app 默认 DEFAULT_DB_URL=用户库），
    且后台 shell 孤儿进程会占端口——本次冒烟两度打到用户库（smoke-acc-task 残留），已逐一清理
    （accuracy_task 行 + smoke-acc.yaml + 临时目录），最终以「冒烟后断言用户库 0 残留」收口。
    **运维注意**：8300 旧进程须重启才有 /accuracy/scripts；冒烟脚本复跑 = 起 8310 隔离实例后
    `python scripts/smoke_acc_live.py`（会写 data/accuracy/scripts/smoke-acc.yaml，跑完删）。

## 4. 环境与坑（新会话必读）

- **预览服务器**：`cd web-ui/mockups && python serve.py 8765`（或 8766），
  no-store 头；**后台进程随会话死，新会话必须先起**；两个端口都有效。
- **IAB（内嵌浏览器）缓存按路径缓存**，版本参数都会被忽略：改了 CSS/JS 必须
  升级 `assets/v1.css?v=N` / `v1.js?v=N` 的版本号（当前 css v=17 / js v=13），
  必要时换文件名。
- **IAB 晚期极不稳定**：cua 真实鼠标注入会失效（页面收不到事件）、截图拿旧帧、
  自发重载、标签丢失。验证改用 evaluate `el.click()` + DOM 状态读取（注意多语句
  要包 IIFE，isolated world 看不到页面全局函数，但 DOM 读写与事件派发可用）。
  评审期调试面板已于 2026-09-30 从 accuracy.html 移除（dbgPanel + dbg 函数 + 全部埋点）。
- Python 用 `.venv/Scripts/python.exe`；验证三件套：`pytest tests/`（约 1 分钟）、
  `ruff check aite tests`、`lint-imports`；函数 ≤80 行（scripts/audit_func_len.py）。
- **架构红线**（CLAUDE.md）：不造评估指标；`/api/v1` 契约与 models.py 属契约级
  先讨论；路由只做「校验→service→返回」；Schema 只增不改；并行只追加共享文件段落。

## 5. 未提交变更（工作区混有用户自己的 WIP，提交必须用显式路径）

本会话的文件（已验证可提交）：
- 新增：`aite/web/routers/domain_tasks.py`、`aite/web/routers/datasets.py`、
  `aite/web/sse.py`、`tests/integration/test_domain_tasks_api.py`、
  `tests/integration/test_datasets_api.py`、`tests/unit/test_web_service_filters.py`、
  `web-ui/V1-PLAN.md`、`web-ui/mockups/**`、`web-ui/mockups/serve.py`
- 修改：`aite/web/app.py`、`aite/web/routers/runs.py`、`aite/web/routers/tasks.py`、
  `aite/service/web_service.py`、`aite/config.py`、`docs/roadmap.md`（末尾追加段，
  ⚠️ 该文件混有用户 WIP，建议最后处理或让用户确认）
- 建议新会话第一步：`git add <上述显式路径>` 提交（feat: V1 前端规划定稿 +
  四域任务/数据集目录 REST + 域过滤），**不要 `git add -A`**（会卷入用户 WIP）。
- 用户自己的 WIP（renderers/aggregator/engine 等）**不要动、不要提交**；
  已知 1 个测试失败（test_report_relpath）属用户 WIP，勿修。

## 6. 下一步（按优先级）

0. **精度创建表单补参数**（2026-10-01 用户指示）：按 `web-ui/TOOL-SUITE-PARAMS.md`
   把后端已实装参数补进 accuracy.html 六卡（数据源 API、resume、seed、
   dataset_args/subset_list、thinking→reasoning_effort 映射等），缺口项
   （总抽样条数、Judge auto）按文中标注处理，后续前后端直接对接。
1. **用户继续评审静态稿**：精度已基本定稿（调试面板待移除）；功能/性能/可靠性/
   公共四模块的静态稿还没按精度这轮的反馈标准迭代过（比如功能创建是否也结构化
   表单化，等用户提）。
2. 静态稿全部定稿后：搭 Vite + Vue3 + TS 工程（`web-ui/README.md` 有迁移路径），
   对接 §10 契约。
3. 后端剩余缺口（V1-PLAN §8）：精度任务 spec 结构化表单+version、定时调度器
   （含重启恢复）、版本趋势按 (model,dataset,metric,version) 聚合 + 基线
   （D1 提前）、run 产物下载端点、catalog 归属/位置/维度/类型标注列、
   四域 executor.cancel 子进程进程组强杀；
   **另：模型版本管理（公共模块新页 + 登记契约，2026-09-30 创建表单新增依赖，
   被测版本下拉的数据源）；脚本管理 REST（V1-PLAN §10.4 提案，2026-10-12
   新增——先过契约评审再实装，静态稿已按其口径落地）。**
4. ~~评审结束后移除 accuracy.html 的调试面板与 dbg 函数~~（已于 2026-09-30 完成，
   同轮把数据集搜索框改为醒目样式：`--ac` 描边独立输入框 + 右侧「筛选 ▾」主题色按钮，
   `.ds-caret` 已删除换 `.ds-filter-btn`）。

## 7. 本会话踩坑记录（避免重蹈）

- 菜单点击项处理器内 `innerHTML` 重渲染会把**正在派发事件的目标元素**从 DOM 摘掉，
  事件继续冒泡时 `closest()`/`isConnected` 全部失灵——文档级与容器级的
  "点击外部关闭"守卫都要加 `if (!e.target.isConnected) return;`。
- `.ds-picker` 的 `overflow:hidden`（为圆角）会把绝对定位的下拉菜单整个裁掉——
  DOM 状态正常但视觉不可见，**验证交互必须看截图不能只看 DOM**。
- evaluate 多语句要包 IIFE；isolated world 读得到 DOM、看不到页面全局函数；
  `el.click()`/`dispatchEvent` 能触发页面监听器（跨 world 有效）。
- TaskKit 的 `update` 对「不存在」与「非 pending」同返 409，REST 层需先查存在性
  区分 404（已在 domain_tasks.py 路由层矫正）。
- IAB 截图偶发旧帧/黑帧/超时——以 DOM evaluate 为准，截图仅参考。

43. **全局版本管理 + 精度对接（2026-10-24 续，用户「精度要对接全局的版本管理」）**：
    被测版本从「自由输入」升级为全局登记制。**后端**：`model_version` 新表
    （(model, version) 唯一，Schema 只增；与 version_result 是两层——登记先行、
    归档随后）+ GET/POST/DELETE `/versions`（common.py，重复 409 / 空值 422）。
    **前端**：①common2 模型管理页新增「版本管理」卡（清单+登记表单+删除，
    模型下拉 = 注册模型 ∪ 已登记模型 ∪ 手填其他）——api25.js → **api26.js** 升名
    （用户侧当日上午 api24→api25，本轮内容变更继续升名并删旧档）；②精度创建
    抽屉两个「＋ 新版本」按钮接 POST /versions（就地登记，模型取当前所选预制
    profile、自定义/脚本内置则 prompt），被测版本 datalist = GET /versions 全量
    （value=版本字符串、label=模型·说明）——api-acc1.js → **api-acc2.js** 升名。
    功能域版本输入暂沿用功能归档 datalist（切全局登记待统一）。测试 +3
    （CRUD/409/按模型过滤/422），全量 **720 passed + 3 skipped** + 四门禁绿；
    隔离冒烟：/versions 全链路 + 精度任务 spec.version 引用登记版本 + 脚本库
    回归，用户库零污染。**注意**：model_version 表由重启时 create_all 自动建
    （用户 8300 旧进程须重启）。

- 2026-11-09 用例明细列表化 + 编辑直达（api32.js，需 dataset 后端修复配套见 roadmap 续七）：
    任务详情「用例明细」从 details 卡片改列表行：状态徽标 + 行 id + 用例 id/类型/等级
    标签 + 失败首行红字 +「编辑」「详情」；详情展开输入（复制 JSON/curl/下载）/输出/
    断言对照/引擎日志，默认收起，行头点击开合（委托单绑定 __caseRowBound）。「编辑」
    editCaseRow(rowId, dataset)：场景清单 → 逐脚本匹配行 id → openEditScript 开抽屉
    （行卡回填态）→ .ns-rid 定位 scrollIntoView + 2s 高亮；场景缺失/行不存在 toast。
    依赖后端：save_tool_case_record 行级 dataset 落场景名（旧 run 无 dataset → 不可跳，
    属预期提示）。缓存版本号 bump：functional2 v=2→v=3、common2 v=1→v=2（同文件改内容防 IAB 路径缓存）。

- 2026-11-09 新建脚本数据行卡折叠（functional2.html + v1.css + api32.js，用户「新旧行分割不清，
    添加一行后旧行折叠只留 id/等级等描述，JSON 行边界要清晰」）：行卡 = head（恒可见：▾ + 行号
    胶囊 + 摘要 + 复制/删除）+ body（编辑区），data-collapsed 开合，点行头任意处切换。折叠摘要
    两行：行 id / 用例 id / 类型 / 等级标签 + request✓·断言数·占位符数，次行描述（超长省略，
    title 全文），由 nsUpdateSummary 随输入实时刷新；展开态行头只显示「编辑中」。nsRevealRow
    （其余折叠 + 本行展开 + 滚动定位）用于：＋添加数据行、复制此行、保存校验报错（api32
    nsCheckingRow 记录出错行卡）、用例明细编辑直达；nsCollapseForReview（多行全折叠、单行保持
    展开）用于：从 jsonl 导入、openEditScript。边界：行卡间距 14px、左 3px 色条（折叠灰 / 展开
    主题色 + 主题色描边 + 行头浅底），折叠行的复制/删除 hover 才显现。submitNewScript 行号改取
    .ns-row-no（.seg-title 随结构移除）。IAB 实测：添加折叠 / 导入 3 行 / 保存报错展开出错行
    （零写入）/ 编辑 7 行脚本全折叠。缓存版本号：v1.css v=18→19（五页同步）、api32.js v=1→2。

- 2026-11-09 静态稿验证工具化（效率复盘「优化动作落地」）：
    ① `scripts/mockup_symbols.py`：`<页面> <函数名…>` 按加载顺序列出定义处并标生效处；
    `--check` 查跨脚本覆盖、同脚本重复定义、同一文件在各页 `?v=` 不一致（已进 AGENTS.md
    门禁速查）。首跑抓到 reliability3 的 renderLib 被套了一层同名外壳（「保存为场景」监听写在
    外壳里，多调一次外壳就重复绑定），已拆掉外壳，IAB 实测每点一次只加一行。
    ② `web-ui/mockups/iab_helpers.mjs`：domClick / domFill / waitUntil / waitForApi /
    guardWrites（默认把非 GET 拦成 418，验证期不写后端）/ stubDialogs。
    ③ api32.js、api-acc3.js 启动收尾（含 MOCK 提前返回）写 `<html data-api-ready="live|mock">`：
    保存等监听在 bindLiveForm 里、全部板块对接完才绑，加载后立刻点按钮会落空（IAB 实测踩到）。
    缓存版本号：api32.js 两页统一 v=4（functional2 v=3→4、common2 v=2→4），api-acc3.js v=1→2。

- 2026-11-10 功能任务支持重试 / 复制（functional2.html + api32.js，用户「功能的任务需要支持重试和
    复制功能」）：任务列表操作列与任务详情顶栏按状态渲染动作按钮——pending「运行」、running「停止」、
    终态（succeeded/failed/cancelled/stale）「重试」、任意状态「复制」，统一走 api32
    `fapiTaskAction(action, 行下标|任务 id, event)`（行内按钮 stopPropagation 不触发行点击进详情；
    详情页操作后重渲染当前详情）。重试 = `POST /functional/tasks/{id}/retry` 建新任务（retry_of 链）
    后立即 `/run`；复制 = 新端点 `POST /functional/tasks/{id}/duplicate`，同 spec 新 pending 任务、
    名字加「· 副本」、不记 retry 链，留在列表由「运行」发起（「运行」「停止」是为复制补齐的生命周期
    入口，此前页面只能在新建抽屉里一键建+跑）。mock 模式按钮照常渲染、点击只提示需 live；mock 行状态
    passed→succeeded 与筛选 chip（data-status="succeeded"）对齐。后端：taskkit.duplicate + 四域路由
    工厂同形端点 + CLI `aite {域} tasks dup <id>`（契约见 docs/domain-backends.md §2/§3/§4/§7）。
    缓存版本号：api32.js 两页统一 v=4→5。

- 2026-11-10 抽屉点空白关闭 + 未保存提示（assets/v1.js + v1.css，五页生效；用户「创建任务或脚本、
    评估场景时，点击空白处关闭右侧抽屉，并给是否保存的提示」）：bindDrawers 用 MutationObserver 统一
    感知各处直写 hidden 的抽屉开合、维护打开栈：栈顶抽屉下垫 `.drawer-mask` 遮罩（叠开时抽屉逐层
    z+2、遮罩垫在栈顶下一层，点空白只关最上层）。点遮罩或头部 ✕ = 请求关闭，底栏「取消」仍直接关。
    打开后用户在正文里真实输入 / 勾选 / 点选（isTrusted 的 input、change，及 chip / radio-card / ds-row /
    button 点击；程序回填与 domFill 不算）记 `data-dirty`，有改动才弹「是否保存当前修改？」：不保存
    （关闭）/ 继续编辑 / 保存——按钮文案取底栏主按钮，点它走原提交流程：成功由原流程关抽屉，失败留在
    抽屉里看 toast；主按钮 disabled 则保存置灰。没有底栏主按钮的抽屉（脚本管理、perf 生成数据集）
    点空白直接关。搜索框 / 纯视图开关标 `data-nodirty` 不计改动：functional `#ftSearch`、`#nsJsonlBtn`，
    accuracy `#dsSearchBox`、`#advJsonToggle`；树的展开箭头 `.caret` 内置排除。「不保存」与「取消」同口径
    只关闭不清空表单（重开是否清空仍由各打开入口决定）。IAB 实测（cua 真实鼠标/键盘，guardWrites 拦写）：
    functional 新建场景 / 新建运行 / 脚本管理叠开新建脚本、reliability 发起实验（保存置灰）、accuracy
    创建评测任务全链路通过。缓存版本号：v1.css v=19→20、v1.js v=14→15（五页同步）。

- 2026-11-10 用例详情改为请求体 / 响应体原样展示（api32.js + harness.py，用户「每条用例的详情结果
    把请求体和响应体原模原样打出来，不要自己拆字段」）：引擎 `_post` 返回的响应 dict 一律附
    `_request`（{url, method, body}，body = 实际发出的原文：普通 / 流式为合并后的 payload，
    `_raw_body` 为原始字符串，GET 为 null），随 output 落库；caseBlock 详情展开 = 请求体（标题带
    method + url）/ 响应体（output 去掉 `_request` 后整体 JSON）/ 断言期望（expected 原样）/ 引擎
    日志，长文本沿用 300 字符折叠。删除：按角色拆 messages、content 抽取 + finish_reason 等元信息
    标签、前端逐断言重算（fapiExpectRows）、curl 拼装与 fapiPresets 预制解析。旧 run 无留痕时请求体
    回退显示用例输入快照并在标题注明。静态 FAILS 卡同步为请求体 / 响应体。缓存版本号：api32.js
    v=5→6（functional2 / common2 同步）。

- 2026-11-10 验证工具与流程沉淀（owner「优化动作直接加、值得的固化成 skill」）：iab_helpers.mjs 增
    isTrusted 真实输入四件套——cuaPoint（滚动 + 可见中心坐标，text 精确匹配文本）/ cuaClick / cuaEdit
    （点中 + End + n×Backspace；keypress 不能插入字符）/ wakeInput（导航后 cua 零事件时截图唤醒），
    模块导入实测通过；完整 runbook 固化为 workspace skill `.agents/skills/mockup-verify/`（门禁 →
    起实例 → waitForApi → 输入方式选择 → 断言 → 收尾；脚手架示例目录已删），AGENTS「静态稿」节挂
    指针、「优化动作」规则改为当场落地。另：8300 后端老进程（10-29 启动、无 --reload，缺当天新增的
    /duplicate 路由）已按原命令行重启（miniconda evalscope env，新 PID 7504），任务数据原样，四域
    retry / duplicate 齐全；8766 预览是纯静态 no-store 直读磁盘，无需重启。

- 2026-11-11 用例详情「请求头」展示 + 老数据快照补全（owner：请求体要完整含 model/扩展参数，header 也要看）：
    引擎 `_request` 留痕增加 headers（实际发送的合并头 = 默认鉴权 ⊕ 用例 `_headers`，落库前按敏感子串
    打码——Authorization 等显示 `******`，凭证复现仍走环境注入）；抽取 `_mask_headers` 与
    `_redact_params` 共用规则。api32.js caseBlock：请求体区上方新增「请求头」区（K: V 逐行 + 复制）；
    旧 run 无留痕的回退快照从「仅 messages」改为 messages ⊕ raw.params（`_` 前缀控制键剔除，缺 model
    在标题注明），与真实 payload 口径对齐。断言期望/失败首行展示保持不变（owner 认可直接）。
    渲染门禁固化：web-ui/mockups/render_check.cjs + render_check_fixtures.json（5 类用例断言：
    断言 400 实际 200 首行/请求头打码/完整请求体/流式留痕/旧数据快照/错误占位），改详情渲染先跑它。
    缓存版本号：api32.js v=6→7（functional2 / common2 同步）。

- 2026-11-11 精度域「评估场景」拆为独立导航页（owner：评估场景单独成页，不放脚本库里）：accuracy2
    侧栏顺位改为 任务列表/任务详情/**评估场景**/脚本库/趋势对比/人工评测（对齐功能域）。新页 = 场景卡
    片栅格（scene-grid：中文名/id/说明/维度标签/脚本数/例数 + 「查看脚本 →」跳脚本库带场景筛选 +
    删除）+ 顶栏「＋ 新建评估场景」（原脚本库筛选行的场景新建/删除按钮整体迁出）；脚本库瘦身为纯脚本
    清单 + 场景筛选（说明卡与空态文案指向评估场景页）。api-acc3.js：wireAccScripts 同时渲染场景卡
    （live items 自带 scripts/cases 计数）+ setNavCount("scenarios")；场景删除抽 aapiDeleteScenario
    （409 → 二次确认级联 force；AAPI.live=false 时降级为本地演示数据移除），废弃 accDelScenBtn /
    aapiSyncScenDelBtn；保存为脚本、抽屉下拉的「先建场景」文案改指评估场景页。静态稿 mock 回退：内联
    ACC_SCENS 演示卡 + 演示删除（同步脚本清单/筛选/计数）。IAB 隔离实例（8310 临时库 + 8776 反代）
    全链路验证：导航/空态/建场景（注意：维度标签须取自共享标签库，API 契约/Agent/模型能力）→ 卡片与
    徽章计数 → 查看脚本跳转筛选 → 删除回零 → mock 回退演示卡与演示删除；临时库与场景目录均无残留。
    缓存版本号：api-acc3.js v=2→3（仅 accuracy2 引用；共享文件未动）。

- 2026-11-11 模型管理：api_key_env 全平台改「密钥库下拉选择」（owner：手动预制模型、选择平台上已归档
    的 API_KEY、调用时可选模型）：①共享助手落 v1.js——`wireKeySelect(sel, items)`（GET /secrets 的
    items 灌选项，已归档密钥带 🔑 掩码 + 「仅环境变量（手填名字）」回退；容器约定 `<select data-keysel>`
    + `<input data-keyenv>` 同包一层，回退框选中才露出；选项重建幂等，监听只挂一次）+ `keyEnvValue(sel)`
    取最终 api_key_env 字符串；②common2 注册模型抽屉——api_key_env 手填框换密钥下拉 + 「＋ 现场归档
    新密钥（密文落盘）」（mKeyArchive 面板：名+值 → 先 PUT /secrets 再 POST /models）+ 仅环境变量回退；
    api32.js 提交逻辑改按 `.drawer-body > .field > input.in` 直属取值（密钥区输入嵌套不混入）；③后端
    POST /models 响应加 `key_archived`（resolve_secret 试解析：环境变量 > 密钥库，未归档 false 不阻断、
    密钥可后补录），前端 toast 附「⚠ 密钥 X 未归档且环境变量未设置」提示；④发起抽屉自定义端点面板同改
    （functional2 ftModelCustom / accuracy2 accModelCustom + accScriptModelCustom；perf2、reliability3
    仍纯静态未对接，不在本次范围），api32.js bindTaskSubmit 与 api-acc3.js aapiTargetOf 改读密钥下拉
    （pane 内 `input.in:not([data-keyenv])` + `[data-keysel]`）。IAB 隔离实例（8310 临时库 + 8776 反代）
    全链路验证：表单归档密钥 → 注册抽屉下拉带掩码 → 选归档密钥注册（表格 🔑 掩码 + 后端 api_key_env
    落库）→ 现场归档路径（密文落盘 + 注册一并成功）→ 仅环境变量回退 + 未归档警告 toast → accuracy2
    aapiTargetOf 两种路径 target 正确 → functional2 真实发起（任务 spec.target.api_key_env = 所选归档
    密钥）；收尾停双实例、还原 data/common.yaml 与 .secrets.json（验证前备份）、删临时库。
    缓存版本号：v1.js v=15→16（五页）、api32.js v=7→8（functional2/common2）、api-acc3.js v=3→4
    （accuracy2）。运维注意：后端 routers/models.py 有改动，常驻 aite serve 需重启才有 key_archived。

- 2026-11-11 版本管理对接收口 + 模型管理组拆三页（owner：要能手动创建版本，落库后所有需要版本的地方
    都可选而不是手填；不要叫「登记」就叫「创建版本」；模型/版本/密钥统一放一个地方，左侧单独给模型管理
    做导航栏）：①common2 模型管理组拆独立三页 = 模型管理/版本管理/密钥管理（page-list 加 versions/
    secrets + v1.js GROUP 映射两组页归「模型管理」，侧栏自带三页导航；数据集/执行机维持单页通栏不变）；
    版本页 = 版本清单 + 创建版本卡（模型下拉 = 注册 ∪ 创建过 ∪ 其他手填；空注册表只有「其他」一项时
    dispatchEvent(change) 同步手填框显隐——新修的边界）；密钥页 = 密钥库 + 录入密钥（元素 id 不动，
    wireCommonModelsPage/wireCommonVersions 零结构改动跨页生效）。②功能新建运行抽屉被测版本切全局版本
    注册表：废弃「select→自由 input + 归档 datalist」转换（renderBoard 内删除），ftVerSel 保持下拉 =
    GET /versions——当前模型有版本按模型过滤，没有则跨模型兜底（选项带「模型 · 版本」前缀；MODEL_SOURCES
    补 name 字段供 ftPickModelName；换目标模型/手填模型名实时重过滤，重建选项保留现选中）；「＋ 新版本」
    （ftVerNewBtn）就地创建 = ftCreateVersion（模型取当前所选，取不到 prompt 补问 → POST /versions →
    回填下拉立即选中）；bindTaskSubmit 补被测版本必填校验。③措辞统一「登记→创建」（版本语境全扫）：
    common2 创建版本卡、api32 wireCommonVersions（创建时间表头/空态/confirm/toast）、api-acc3
    accRegisterVersion（prompt 文案 + toast 指路「公共 · 版本管理」页）、accuracy2 两处「＋ 新版本」
    title + 自定义抽屉 tip、functional2 密钥 tip 指路「公共 · 密钥管理」。④后端零改动（model_version +
    /versions 三接口 2026-10-24 已实装）；V1-PLAN §10.6 契约措辞与前端触点同步。IAB 隔离实例（8311
    临时库 + 8777 反代；8310/8776 被并行会话占用）验证：模型管理组侧栏三页 → 版本页建版本（空注册表
    手填路径）→ 功能抽屉下拉跨模型兜底带前缀 → 就地创建 v2.0-iab 立即选中且按模型过滤生效 → 切目标
    模型实时重过滤 → 回 common2 两版本都在清单；截图目检布局正常；停实例删临时库。
    缓存版本号：v1.js v=16→17（五页）、api32.js v=8→9（functional2/common2）、api-acc3.js v=4→5
    （accuracy2）。静态稿改动刷新即生效，无需重启任何服务。

- 2026-11-11 模型管理「一次新增出现俩」修复（api32.js，用户「模型管理中，一次新增模型会新增俩」）：
  根因 = POST /models 落 DB 后有意同步 common.yaml 预制（target.profile 可引用），wireCommonModelsPage
  把 /common/models（预制）与 /models（注册）两个来源都渲染进表格且不去重 → 每个注册模型显示
  「预制」+「注册」两行。修法 = commonRows 过滤掉与 DB 注册同名的条目（registered Set），同名以
  「注册」行为准；后端零改动，注册双写与删除同步（remove_model_profile）语义不变。IAB 隔离实例
  （8310 临时库 + 8776 反代）验证：注册 e2e-dup-check 后表格仅一行「注册」，页面内 fetch 确认 DB 与
  common.yaml 两侧条目都在（去重生效而非双写失效）；停实例删临时库。缓存版本号：api32.js v=9→10
  （common2/functional2）。静态稿改动刷新即生效，无需重启任何服务。

- 2026-11-11 版本与模型解耦（用户「版本创建只需要创建版本号，其他手填，模型不需要，和他没关系」）：
  版本登记 = 纯版本号 + 说明（可选），全平台与模型脱钩。后端：POST /versions 收
  {version, description?}（旧客户端传 model 兼容忽略）、唯一键 (model,version)→version、
  GET /versions 去 ?model= 过滤；ModelVersionRow.model 降级遗留列（恒空串，旧登记数据保留）。
  前端：common2 版本页清单去「模型」列、创建卡去模型下拉（含「其他（手填）」动态框）；
  api32 wireCommonVersions/ftCreateVersion 与 api-acc3 accRegisterVersion 只问版本号（prompt 一问）；
  功能抽屉被测版本下拉全量列出（去按模型过滤/跨模型兜底/换模型重过滤，wireTaskVersions 瘦身）；
  精度 datalist 选项标签 = 说明。版本结果归档/质量大屏（版本 × 模型）不受影响——模型来自任务
  目标、版本来自登记表，发起处自由组合。缓存版本号：api32.js v=10→11（common2/functional2）、
  api-acc3.js v=5→6（accuracy2）。静态稿改动刷新即生效；后端 aite/ 有改动，需重启 aite serve。

- 2026-11-11 用例详情三修（用户「test8 响应体折叠显示 200 展开变 400；期望断言和实际值不明显；
  引擎原始日志不要下拉框」）：① 根因修复——「展开全文」id 曾在 caseBlock 内每例归零，跨用例
  重复（点击命中 DOM 第一个同名 pre、全文被最后渲染的用例覆盖 → 折叠显示 A 用例内容展开变 B
  用例内容），id 计数器提升到 showLiveTask 作用域全渲染唯一，存全文的 __expStore 每次渲染重建；
  ② 用例展开体新增「断言判定」区置顶：期望 vs 实际逐行对照表（实际值口径同引擎 check()——
  预期错误模式取 _status/_body，正常响应取 choices[0].message/流式控制键，16 类断言键全覆盖，
  区头带通过/不通过徽标 + 复制断言配置），替代原沉底的裸「断言期望」JSON 区；③ 响应体标题带
  实际状态（响应体 · HTTP 400）；④ 失败用例引擎原始日志去 details 折叠框直接全文展示
  （首行仍醒目，error 引擎侧封顶 2000 字符，无滚动截断）。render_check 夹具加长两例响应体，
  新增断言：展开按钮/pre id 跨用例唯一、无 details、断言判定行（HTTP 状态码 ==400 vs 200 等）。
  缓存版本号：api32.js v=11→12（common2/functional2）。静态稿改动刷新即生效，无需重启任何服务。

- 2026-11-11 数据集管理页对接 + 上传/下载/路径与在线源配置（common2.html datasets 页 + api32.js）：
  ① 目录表接 GET /datasets（chips 类型筛选带计数、行内「下载」（GET /datasets/{ref}/download 链接）/
  「删除」（DELETE，confirm 后仅删登记文件保留）、空 catalog 占位引导）；② 导入抽屉重做为
  「导入 / 上传数据集」：精度=文件上传（FileReader 读文本 → POST /datasets/upload，.jsonl/.json/.csv，
  后端规范化 jsonl 落位）/ 功能=文本粘贴（POST /datasets/functional/import 不变），类型切换联动字段显隐；
  ③ 新增「路径与在线源配置」卡：datasets_dir/cache_dir/hf_endpoint/remote_url 四项
  （GET·PUT /datasets/settings），env 覆盖提示（AITE_DATASETS_DIR/HF_HOME/HF_ENDPOINT/
  AITE_DATASET_REMOTE_URL），「在线连通性探测」按钮（GET /datasets/online/status，HF 镜像 + 远程源
  双状态，收到 HTTP 响应即算通）；④ 新增「在线数据集导入」卡：HF 镜像导入（POST /datasets/hf/import）
  与远程拉取导入（POST /datasets/remote/import，url 缺省用 remote_url，结尾带 / 按 <地址>/<名>.jsonl
  拼接）。后端配套：aite/datasets/settings.py（四键设置，env>设置文件>默认）+
  service/dataset_files.py（upload/download/hf_import/remote_import/probe）+ datasets 路由 +7 端点
  （REST 总数 89→105 进 arch_map 再生）+ CLI 五命令（dataset settings/settings-set/upload/download/
  pull-url）；userstore.data_root、functional 摄取落位、perf 产物目录全部随 datasets_dir 设置走。
  IAB 隔离实例全链路验证过（设置保存→探测 HF 镜像真实 200→远程导入→chips 筛选→功能导入→删除）。
  缓存版本号：api32.js v=12→13（common2/functional2）。后端代码有改动：aite serve 需重启才生效；
  静态稿刷新即生效。

- 2026-11-12 创建任务「合计 N 例」统计虚高修复（api32.js，用户「功能任务创建这里的数字统计不对」，
  实测全选场景 1 · 散选 0 · 合计 133，而唯一场景只有 65 例）：根因 = FT_CASES/ftSel 只增不清——
  页面内置 9 个演示场景共 64 条用例 id 在 wireScenarios 重置勾选集时全部带入，会话内删除的场景/
  脚本行也留残影；这些 id 不在树上渲染（ftSceneState 还判成 all），但 ftCaseN 取 ftSel.size 全被
  合计：133 = 65 真实 + 64 演示残留 + 4 已删场景残留。三处修：① wireScenarios 先按在册场景出清
  FT_CASES/ftExpanded 残留 key 再重置勾选集；② wireScripts 重建场景用例表时同步出清该场景 ftSel
  里已不存在的行 id；③ 新增 fapiRebuildFtPresets 按真实 SCENES 重建预设标签（原「全量」等预设是
  页面加载时按演示场景生成的死值，live 下点击会把 64 个演示 id 灌回勾选集，同一根因另一扇门）。
  对真实后端 8300 只读 IAB 验证：合计 65 与场景行一致，预设 API 契约→65 / Agent→0 / 全量→65
  联动正确，guardWrites 零写入。缓存版本号：api32.js v=14→15（common2/functional2）。
  静态稿改动刷新即生效，无需重启任何服务。

2026-11-12　数据集管理页重构：全局 vs 数据集级配置分家 + 三分区版式（owner 反馈「配置归属混乱、模块连在一起没分界」）
- 配置归属收口（回答「哪些全局、哪些数据集自己」）：全局设置=安装环境级（存储位置 datasets_dir/cache_dir
  ＋在线源默认值 hf_endpoint/remote_url——remote_url 明确标注「只是默认，单次导入可覆盖」）；数据集级
  （来源/字段映射/split/subset/采样 n·seed/命名/notes）全部收进导入抽屉，详情抽屉可查登记属性。
- common2.html 数据集页重写为三分区：kicker 小节标题（目录/导入/全局设置，细线延伸+右侧说明）切开模块；
  目录卡（chips+表格，行内新增「详情」）；导入区四张来源入口卡（上传/HF 镜像/远程直链/perf 生成，图标徽章
  +说明+动作语，取代原两块内联表单）；全局设置卡左右分栏（存储位置|在线源默认值，竖分隔线），连通性探测
  改徽章（badge passed/failed/pending）+决策 hint。新增样式全部 .ds- 前缀内联在 common2.html head，
  v1.css 不动（不影响其他页面）。
- 导入抽屉重写为四来源模式（chips 切换，data-nodirty 防误标 dirty）：上传文件/功能 cases/HF 镜像（补齐
  后端本就支持的 split/subset/n/seed/fields 全参数）/远程直链（类型+URL 可覆盖全局默认）；新增数据集详情
  抽屉（dsOpenDetail：类型/名称/登记 id/version/source/stats/路径/schema/notes + 下载/删除登记）。
- api32.js：wireDatasetOnlineImport+dsBindImportDrawer 合并为 wireDatasetImportDrawer（按模式分发提交
  +详情删除绑定）；dsRenderTable 行内加详情按钮；探测结果改双徽章渲染；fapiBoot 接线同步。缓存版本号
  api32.js v=14→15→本会话沿用 15（与并行会话的功能预设修复共存，mockup_symbols --check 通过）。
- IAB 隔离实例验证（CWD 挪 .tmp/isol 防脏 owner 设置文件；8310/8776 已停干净）：三分区结构/目录行渲染/
  详情抽屉内容与下载链接/四模式切换/设置保存回读/功能 cases 完整提交/详情删除（stub confirm）/探测徽章
  （HF 通 1425ms passed、远程源解析失败 failed+hint）全部通过。
- 注意：本次发现并行会话残留 8776 旧代理 + 8310 旧隔离后端导致页面拿到旧数据（已在 AGENTS.md 固化
  「起实例前 netstat 查残留」规则）；后端重启验证时后台命令一律绝对路径（exit 127 教训重申）。
  静态稿改动刷新即生效，无需重启任何服务；后端代码零改动，aite serve 不用重启。

## 2026-11-12 · 数据集页「本机实际 ⊕ 在线全集」——计数对齐 + evalscope 原生基准板块

- 起因：owner 报「数据集数量与列表对不上」——catalog 只显示登记行（5 条 accuracy），本机注册表
  `data/aite_datasets.json` 里的 ceval_full（1606 题）没进 catalog、目录里还有孤儿 sample_qa.jsonl，
  且 evalscope 原生 257 个在线基准完全没展示。
- 后端（aite/service/dataset_files.py + web/routers/datasets.py）：`GET /datasets` 改「本地数据集总览」
  ——merge_local_rows() 展示层合并 catalog ⊕ 注册表 ⊕ 目录孤儿扫描（未登记行带 registered:false +
  source=registry/local-file；catalog 旧失效 path 以注册表实际文件修正展示）；resolve_download 三级回退
  补齐（外部注册表文件、目录孤儿同名 jsonl——旧登记 path 失效/未登记都能下载）；新增
  `GET /datasets/online/datasets`（evalscope BENCHMARK_REGISTRY 257 基准 + match_local_benchmarks
  本机可用标记 local/local_like + 进程内缓存 + 主环境缺 evalscope 时经 common.yaml python_exe 子进程
  回退，首列 8.8s / 缓存后 0.29s）。catalog 仍是写侧唯一权威，删除只对登记行。
- 前端（common2.html + api32.js v=15→16，common2/functional2 同步 bump）：目录 chips 计数与列表天然
  一致（同源 DS_ROWS）；未登记行「未登记」badge 且无删除按钮；目录下方新增「在线数据集」卡（搜索框
  过滤 name/dataset_id/tags + 「只看本机已有」toggle + 绿 badge「evalscope 257 个 · 本机已有 3」 +
  行内「导入」预填 HF 抽屉 dsHfId/dsImpName）；mock 模式有 6 条演示行，live 由 wireDatasetOnlinePage
  接管；fapiBoot 接线同步。
- 本机匹配规则（match_local_benchmarks 纯函数）：local=基准 dataset_id 命中本地 notes/upstream 的
  hf id 或归一名（仅字母数字）相等；local_like=归一名互为前缀且 ≥4 字符（ceval↔ceval_full）。
- 测试：tests/unit/test_dataset_files_local.py（合并/标记/缓存/回退 +14）、test_datasets_api.py
  集成 +4（未登记展示/registry 回退下载/在线标记/502）；数据集域 52+33 全绿。
- IAB 验证（8313 隔离后端带真实 aite.db 副本 + 8776 代理，已停干净）：chips 全部 7=accuracy 7=表格
  7 行；ceval_full/sample_qa 标未登记无删除；搜索 swe→10 行、只看本机→3 行（swe_bench_lite/
  verified/verified_agentic）；gsm8k 行点导入→抽屉预填 AI-ModelScope/gsm8k；swe_bench_verified 行
  标「本机已有 · swebench_verified」；截图目检目录区与在线区版式正常。
- 注意：在线板块首次列举走 python_exe 子进程（owner .venv 起服时约 9s，evalscope env 起服则进程内
  直取），进程内缓存后秒回；后端代码有改动，owner 的 aite serve 需重启才生效。

## 2026-11-12 · 功能任务「复制/重试可改参再跑」+ 任务详情运行历史链

- 缺口：复制出的 pending 草稿无处改参；重试原语义是「同 spec 立即重跑」，而重试的真实动机
  （上一轮参数有问题）要求先改参；单任务多轮运行的结果散在列表里，详情页没有历史视角。
  后端能力本就齐（PATCH 仅 pending、retry 记 retry_of 链、GET /tasks?retry_of=），只补前端。
- api32.js：新增 openTaskEdit/saveTaskEdit/teRenderScenes（编辑抽屉接线：名称/版本 datalist/
  目标模型下拉含自定义端点回填/timeout/场景 chips 增删/tools.cases 多行文本 + 原始 spec 只读
  pre；保存= PATCH 原任务，spec 以原 spec 打底覆盖表单字段，未识别键保留）；fapiTaskAction 的
  retry/copy 改为生成 pending 新任务后自动 openTaskEdit（不再立即运行），新增 edit 动作。
- api32.js showLiveTask：详情头部 pending 加「编辑」按钮 + crumb 重试标记；新增「运行历史
  （重试链）」卡（GET /tasks?retry_of= 拉子任务 + retry_of 父链入口，行内 打开 跳详情）；
  fetch 失败不阻塞详情。functional2.html：新增 taskEditDrawer（任务编辑抽屉）；renderRuns
  pending 行加「编辑」按钮、retry/copy title 同步新语义。
- 语义变化（前端）：重试不再立即运行——生成 pending 重试任务并打开编辑抽屉，由
  「保存并运行」发起；纯重跑场景在抽屉里直接点「保存并运行」即可。后端契约未动
  （domain-backends.md §2 仍准确）。
- 门禁：render_check.cjs 补 fapiShortTime 桩 + retry_of URL 桩 + 夹具 retries/retry_of 字段 +
  历史卡 3 断言（父链入口/子任务行/编辑按钮随 pending 显隐），PASS；mockup_symbols --check
  PASS（5 页面无覆盖/重复/版本漂移）；api32.js bump v17（functional2/common2）。

## 2026-11-12 · 任务列表「评估结果」列显示通过用例数

- 后端 `aite/service/suite_task.py` evaluation_stats：在 pass_rate/evaluated_cases
  之外新增 `passed_cases`（按 pytest: 行 value×count 逐场景 round 后求和），
  四域套件任务 result 快照统一携带。
- api32.js rebuildRunsFromTasks：评估单元格对象带 passed（含 /runs/{id} summaries
  兜底分支同口径计算 passed）；functional2.html evalCell 渲染 `12/14 例`
  （passed 缺省的旧任务快照仍显示 `14 例`，优雅降级）。
- 门禁：ruff PASS、tests/unit/test_suite_task.py 3 passed（补 passed_cases 断言）、
  mockup_symbols --check PASS、render_check PASS；api32.js bump v18
  （functional2/common2）。

## 2026-11-12 · 数据集页三改：数量修复 + 在线并入导入抽屉 + 统一大筛选框（owner 反馈五连）

- 数量修复：aite/service/dataset_files.py merge_local_rows 从「只扫 accuracy 孤儿」扩为
  全类型扫描（functional `<名>/cases.jsonl`、perf 直文件，.stats.json 不算；catalog 已引用
  路径跳过防 perf 登记名≠文件名重复计数），router 放开 type 过滤限制并透传 only；行新增
  `origin`（local/online：hf/remote 或 registry upstream=hf: → online）。GET /datasets
  本机 7 行 → 9 行（补 mini-set、rag-scene 孤儿）。
- version 列撤销（owner 问「有必要吗」）：列表不占列，详情抽屉改「version（复现凭证：
  HF split / 生成 seed）」；source 明细同样收进详情，列表只留 本地/在线 tag + 未登记 badge。
- 在线数据集独立板块撤销，整个并入导入抽屉「在线数据集」模式（data-mode=hf 复用）：
  搜索 + 只看本机已有 + 257 基准库滚动列表，行「选用」填 dsHfId/dsImpName 并高亮（tr.on），
  下方 split/subset/n/seed/fields 参数区不变；来源卡「HF 镜像仓」更名「在线数据集」。
- 新组件 web-ui/mockups/v1/assets/filterbox.js（v1，自包含样式注入，后续页面筛选框统一格式）：
  fbMount(挂载点, 分组, 回调) → 大框点击展开面板、点选项多选、tag 回填框内可 ✕、
  重置/收起、外部点击关闭；handle.setGroups 刷计数。数据集目录 chips 行 → #dsFilter 单筛选框
  （类型/来源/登记状态三组实时计数）。
- 修存量 bug 两枚：① dsIsLive() 查 window.FAPI 恒 false（api32 顶层 const 不挂 window），
  旧页靠 mock/live 双绑定后发者胜侥幸可用，单绑定 onChange 暴露后改裸 `FAPI` 判定；
  ② filterbox 点选项后面板自收（renderPanel 重建 DOM 后冒泡到 document 监听时
  mount.contains(已分离节点)=false 误判外部点击）→ 改 e.composedPath().includes(mount)。
- 门禁：ruff/lint-imports/audit_func_len/arch_map 全 PASS；数据集域 25 tests passed
  （补全类型孤儿 + origin 断言）；mockup_symbols --check、render_check PASS；
  api32.js bump v19（functional2/common2）、filterbox.js v2（common2 独引）；
  IAB 沙箱实测：筛选多选/交叉空态/重置、抽屉搜索（swe 命中 10 条含 imo_anSWErbench 子串）、
  选用回填高亮全过，截图两张确认视觉。

## 2026-11-12 · 任务两层化：执行历史归属任务（retry 同任务重跑）

- owner 拍板（纠正 v17 的「历史卡+同级草稿」方案）：一个任务的多次执行是它的历史，
  不是多个任务。后端：retry 不再新建任务 = 终态任务同配置再跑一轮（202）；PATCH
  放宽到非运行中（终态改完可重跑）；任务行加 `history` JSON 列（migrations 注册四
  表，旧库启动自动补列），taskkit `_open_round/_close_round` 在 run 起点开轮（快照
  当时 spec）+ succeeded/failed/cancelled 三条终态路收轮（trigger/spec/结果概要/
  run_id/起止时间）；retry_of/retry_count 保留只读兼容旧链。契约 §2/§3/§4 同步。
- 前端 api32 v20：详情页「执行历史」卡 = 轮次 chips（点 `showLiveTask(id, n)` 切轮，
  默认最新轮，各轮结果/请求留痕随 run_id 取数）+ 该轮 spec「复制该轮 spec」+ 旧链
  提示；头部 pending 显「运行」、终态显「重跑」，编辑按钮随非运行中显隐；列表行终态
  加「重跑」。编辑抽屉/重试语义文案同步。
- 门禁：四域任务集成 17 + taskkit/CLI/迁移单测全过（重写 3 个旧链断言 + 新增迁移回
  归 test_storage_migrations：手工建旧表验证 apply 补列）；render_check（轮次切换/
  spec 快照/重跑按钮断言）、mockup_symbols --check、ruff、lint-imports、arch_map 再
  生全绿；iab_sandbox 隔离实例 API 冒烟：stop→retry 同任务、history 2 轮、运行中
  PATCH 409、终态 PATCH 200、列表不裂变全过（retry 同配置触发 pytest 断点续推，
  只补缺失样本——正向协同）。

## 2026-11-13 · owner 三连反馈修复：重跑真执行 / 切轮串轮 / 执行历史卡重排

- ①重跑没跑成功：根因 resume=auto 对「未完成且同身份」历史目录就地续推（只补缺失
  样本，全齐则瞬完成不跑）→ SuiteTaskExecutor 强制 resume=False（重跑永远真执行；
  续推语义保留给 CLI `aite run --resume`）。②切轮只能看最新轮：失败/取消轮收口时
  不带 run_id → 前端回退最新轮 → 串轮。修：run_cb 回调链（tools/run_service/engine
  建 run 行即回调 → taskkit `_note_run_id` 即时写进历史轮；close_round/取消路径补
  ctx.run_id）；前端 runId 严格取所选轮自身（仅无 history 的旧数据回退 result）。
- ③执行历史卡重排（owner 嫌丑：chips+表格同信息两遍）：整卡压成单行——「执行历史」
  标签 + 轮次 pills（#n 触发 · 时间 · 色点状态 · pass_rate，点切轮）+ 右侧「复制该
  轮 spec」；该轮错误/旧链提示降为卡内小字。api32 bump v21。
- 验证：101 tests（taskkit/CLI/四域集成/tools/迁移）+ render_check + mockup_symbols
  + ruff 全绿；iab_sandbox 新代码实测：取消轮带 run_id、重跑开新输出目录且 15s 仍
  running（真执行）、双轮 run_id 齐全、切轮 crumb「第 1/2 轮」+ 选中态正确；截图目
  检新版式。注：首轮验证曾误判通过——被 TaskStop 杀剩的旧后端与新版双监听 8310 串
  请求（端口先 netstat 核实干净再起实例，验证断言要以「第 1 轮自己的目录」为基线）。

- 2026-11-13（数据集域显式化）：common2 数据集页命名对齐两层模型——目录 kicker
  「目录」→「本地资产」（注明内置样本+私有导入、非全集）、「在线数据集」来源卡/
  抽屉 chip/模式说明 →「开源基准（evalscope 全集，未预下载跑任务时自动下载）」；
  accuracy2 数据集选择器接口失败改持久红字（不再静默留 10 条演示行被当成全集），
  api-acc3 bump v7。后端 evalscope 子进程补注入 HF_HOME=数据集设置 cache_dir
  （.resolve 绝对路径，防 work_dir cwd 二次拼接），运行时自动下载不再落默认
  ~/.cache/hf；test_tools 子进程用例随附断言（去环境化 monkeypatch）。

- 2026-11-13（数据集列表并入全集 + 筛选改原生下拉，common2 + api32 v22）：目录表
  并入 evalscope 基准全集（GET /datasets ⊕ /datasets/online/datasets，本机行在前，
  实测 261 行）；基准行带 已有/近似/未下载 徽标（match_local_benchmarks 标记）+
  「详情」基准版式（指标/subsets/few-shot，可跳本机数据集）+「导入」一键预填
  dataset id。筛选弃 filterbox 大框按钮面板（owner 否掉该交互：选完不收起），改
  三枚原生 select（类型/来源/本机状态）+ 关键字搜索，选完即生效、原生自动收起；
  mock 走同一套渲染（DOMContentLoaded 灌 DS_ROWS/DS_BENCH），内联 dsMockRender/
  dsMockView 删重；filterbox.js 不再被任何页面引用（文件暂留，待 owner 定去留）；
  api32 v21→v22（common2/functional2 同 bump）。scripts/iab_sandbox.py 后端补注入
  AITE_DATASETS_DIR/HF_HOME 到 .tmp（沙箱灌数不再写 owner 的 data/ 托管根），
  down 一并清临时数据根。IAB 实测：全集渲染/三维筛选/搜索/导入预填/详情跳转全过；
  全集首次列举 21.7s（evalscope 子进程，进程内缓存后秒回，列表先出本机行不等它）。
- 同日 v23（owner 发截图打回「丑」）：①操作列被 auto 布局推到最右、path 列后大片
  空白 → 目录表改 table-layout:fixed + 百分比列宽（7/22/18/15/18/20）；②本机行副行
  的 id 截断（local:ce/file:sam）无信息量 → 改显 notes（截断+hover 全文），无 notes
  占位对齐行高；③长 notes/dataset_id 的无空格拉丁串不可断行，把表撑出容器（1280
  视口操作列被挤出）→ path/dataset_id/notes 截断一律 max-width:100%；④基准行 tags
  穿到来源列 → 副行 overflow:hidden 裁剪。IAB 1280 视口实测：无溢出、操作列常驻、
  行高对齐；render_check + mockup_symbols 过。common2/functional2 同 bump v23。




- 2026-11-13（性能域前端对接，perf2 + 新建 api-perf.js v2）：perf2.html 原地对接
  真实后端（此前纯静态稿）——任务列表 GET /perf/tasks 真数据（行内 onclick 带真实
  task id，pending=运行 / running=停止 / 终态=重试）；任务详情 task+result+run
  summaries，运行中 5s 轮询耗时，终态拉 /runs/{id}/timeseries 出 TTFT/RPS 图 +
  统计表 + 报告产物；压测大屏 timeseries 全量（档级 chips 过滤 + 八图 avg/p95 +
  并发阶梯 + 错误条形）；性能数据集 GET/POST 真生成 + validate 干跑 + 克隆回填；
  新建抽屉自定义编排 → suite raw 组装（perf.mode/baseline/ramp/probe/soak/rate
  开环/负载三选一/warmup/spawn_rate/arrival/jitter/tools.metric_thresholds，键位
  契约 = perf_common/perf_adapter）。口径修正：任务层 succeeded 仅=子进程跑完，
  run.status=error 上浮为 failed 红徽标（papiEffStatus）。不对接（后端缺口，保持
  演示 + 显式脚注/toast）：脚本库（无 /perf/scripts）、数据集删除、运行中逐秒进度
  （两引擎 collect 才落盘）、probe 字段 live 替换为真实契约（SLA 指标 ∈
  _SLA_METRICS、上界=max_parallel 并发）。IAB 沙箱实测：列表状态徽标/详情错误卡/
  抽屉真提交（创建+run 202）/数据集生成失败与成功双路径/克隆回填/大屏 stub 时序
  八图渲染/档级过滤/probe+locust 联动全过。api-perf.js 顶层符号全 papi/PERF_ 前缀，
  mockup_symbols --check 过；perf2 引用 v=2。

## 2026-11-13（数据集目录分页 + 开源基准行加载加固）

- 目录表分页（owner 要求全量展示 + 分页）：api32.js dsPageOf/dsPagerHtml/dsRenderTable
  （mock/live 一套，每页 15/30/50/全部，筛选/搜索改选回第 1 页，页码窗口 1 2 … n-1 n n+1 … 末页；
  单页只留条数下拉当「全部」逃生口）；common2.html 加 #dsBenchBanner/#dsPager 容器 + kicker 补分页说明。
- 开源基准行不再「又没了」：wireCommonDatasetsPage 拆出 dsLoadBench（与本机列表并行起跑），
  列举失败不再清空 DS_BENCH——已灌过的 live 行保留、首载失败才清（DS_BENCH_LIVE 防 mock 演示行冒充），
  目录上方横幅给原因 + 重试（dsBenchBanner，点击委托在 dsBindDatasetsTable）。
- mock 演示数据 6→23 条真实 evalscope 基准（名称/dataset_id/subset_count 取 2026-11 实清单快照；
  subsets 只存前 3 个预览，dsSubCount 统一取 subset_count 计数），静态稿离线也能看全量+分页效果。
- api32 v24（本轮沿用并行会话已 bump 的版本号，common2/functional2 同版）；门禁
  mockup_symbols --check + node --check 过；IAB live 实测：共 266 行 · 基准 257 全量、翻页/每页条数/
  筛选回第 1 页、fetch 断连模拟横幅出现且 257 行不清空、重试恢复、guardWrites 0 写 0 错。

## 2026-11-14（精度详情失败样本三段分色）

- 精度详情两处失败展示拆三段分色块（owner：失败请求里输入/标准答案/实际模型输出分不清）：
  api-acc3.js 新增 accTriBlock/accTriBlocks（输入=蓝 --run · 标准答案=绿 --ok · 模型输出=琥珀 --warn，
  色标 + 8% 底色 + 左色条，空字段不出块；target 与 correct_answer 相同自动去重）。
- accSampleBlock（逐样本明细，含「只看答错」）原来三行同款式 kv 改用三段分色块，
  判定说明/评委/评分状态保持 kv 次级行；failBlock 闭包提升为顶层 accFailBlock（失败样本卡）：
  reason/error/why/detail 提升为「原因」，已知三段字段抽出分色展示，其余键仍 JSON 明细兜底不重复渲染。
- api-acc3 v=9（仅 accuracy2.html 引用）；门禁 mockup_symbols --check 过；临时切片断言脚本
  （accSampleBlock/accFailBlock 五组用例：分色标记/答案去重/后端 {dataset,case,detail} 形状/
  未知键保留/空字段容错）全过，用完即删。

## 2026-11-14（自定义评估判官评分标准 rubric）

- 自定义评估「Judge 裁判模型」下新增「评分标准（rubric）」文本域（accuracy2.html
  accJudgeRubric，qmark 提示占位符 {question}/{pred}/{gold} 与生效范围）；api-acc3.js
  aapiCollectCustomSpec 校验「先选判官再填 rubric」后落 spec.tools.judge.prompt_template，
  aapiSpecSnap Judge 行加「· 评分标准已自定义」标记。
- 后端 evalscope_adapter._apply_judge 新增语义契约映射：tools.judge 的 prompt_template/
  system_prompt/score_type/score_mapping → evalscope 原生 judge.contract（合入每个
  LLMJudge；仅通用判官契约基准生效，内置专用判分沿用官方 prompt；不配语义键不带
  contract）。config-reference tools.judge 行同步；api-acc3 v=10（仅 accuracy2.html 引用）。
- 门禁 mockup_symbols --check / ruff / pytest test_tools 63 passed 全过；临时切片断言
  （rubric 三态 + 既有 spec 结构不扰动）4/4 过，用完即删。

## 2026-11-14（perf 任务创建六项整改）

- perf2.html 新建抽屉重排：任务名称置顶为抽屉第一个字段（id 仍 perfTaskName2，
  双面板共用）；负载来源三选一改「平台数据集 / 固定输入输出」双卡（互斥渲染进
  perfLoadExtra：平台卡 = perfDatasetSel 下拉 + ＋生成新数据集按钮，固定卡 =
  条数/输入长度/输出长度三字段）；「高级（阈值判定）」去掉 chaos 故障注入下拉，
  脚本面板与大屏的 chaos 文案同步清除。
- api-perf.js（v=4）：papiRenderLoadExtra 双分支重写；负载卡切换 + 初次条件渲染
  提到顶层执行（mock 模式也出表单，原先绑在 live-only 的 bindPerfSubmit 里）；
  papiCollectSpec 固定输入输出 = synthetic{count,prompt_length} + max_tokens 定长
  （优先于模型参数区 max_tokens）；新增采样参数收集 temperature/top_p/top_k/seed/
  min_tokens/stop/frequency_penalty/repetition_penalty/query_template/
  apply_chat_template/tokenizer_path（显式填写才透传）。
- 后端 perf_adapter._assemble_args 透传上述参数（旗标对齐 evalscope 1.15）；
  build_config 落 max_tokens 优先级：任务显式 > 数据集 meta（同名 .stats.json 的
  output_tokens 定长兜底，perf_common.dataset_output_tokens）。单测补
  test_generation_params_passthrough / test_dataset_meta_output_tokens_priority。
- 门禁：mockup_symbols --check / ruff / lint-imports / audit_func_len（存量
  run_tool_suite 超长不涉本次）/ pytest perf 三件套全过；IAB 隔离实例双模式
  提交实测（固定输入输出 synthetic+定长 max_tokens、平台数据集+采样参数，
  spec 落库逐键核对）。aite/ 有改动需重启 aite serve；perf2 刷新即生效。

- 2026-11-15 数据集目录筛选整改（common2.html + api32.js v=25，functional2 同步
  bump）：①来源筛选三分类——dsFKind 由「本机/基准」两态改为 自建（上传/生成）/
  开源（HF·远程直链在线拉取，行 origin=online）/ evalscope 基准，行数统计同步
  拆「自建 X · 开源 Y」；②新增能力维度筛选 dsFCap（基础知识/数学/代码/推理/
  指令遵循/智能体/长文本/安全 8 维），本机行/基准行名称下方出能力 chips（基准行
  无映射时回退展示原生 tags），两个详情抽屉加「能力维度」行，关键字搜索命中能力
  中文标签；③导入抽屉加能力维度多选 chips（#dsCapChips，functional 模式隐藏，
  dsEntry 打开即清残留勾选），开源基准「导入」按 b.capabilities 预填，上传/HF/
  远程提交带 capabilities；mock 数据（DS / DS_ONLINE_MOCK）全部补 capabilities。
  后端配套（catalog capabilities 列 + 三摄取端点透传 + 基准 tags 映射）见同日
  backend 提交。门禁 mockup_symbols --check / render_check 全过；纯静态稿改动，
  刷新页面即生效，后端要吃到 capabilities 字段需重启 aite serve。

- 2026-11-15｜精度创建抽屉被测版本下拉主题化：原生 `<input list>` + datalist 的
  下拉面板由浏览器渲染（白底系统样式），与深色主题不搭。换自定义建议面板
  （accuracy2.html 两处 input 包 `.ver-combo` 容器；api-acc3.js `wireVerCombo`
  复用数据集筛选同款 `.ds-menu` 视觉，组件 CSS 由 `ensureVerComboCss` 注入
  style#verComboCss，自包含不碰共享 assets、无 `?v=` 连锁 bump）：聚焦/点击/
  输入时按 ACC_VERSIONS 过滤建议（版本+描述），↑↓/Enter/Esc 键盘导航，mousedown
  选中（先于 blur + preventDefault 保焦点）；注册表为空出引导文案，自由输入保留
  （历史任务可引用已下线版本号）；「＋ 新版本」创建成功回填发起处输入（同功能域
  「创建即选中」语义），accRegisterVersion 收按钮引用定位同行输入。wireAccVersions
  只拉数据不再渲染选项（面板开放时现读）。验证：临时沙箱 7 组断言（已删）+
  IAB live 实测（截图/点选/过滤全过）。已知环境怪癖：IAB webview 无系统焦点时
  原生 focus() 不派发事件，故加 click 监听重展开（对齐原生 datalist 的点击重开
  行为）。api-acc3 v=11；mockup_symbols --check 过；纯静态稿改动，刷新页面即生效。

> **2026-11-15 会话——前端架构整改三批（转义收敛 / helper 均化 / 守护扩展）+ 性能详情防闪烁**：
>
> **转义收敛（安全债）**：新增 `assets/live2.js`（escHtml，转全 5 字符含属性位引号），accuracy2/
> common2/functional2 在 v1.js 之后、内联脚本之前加载；api32/api-acc3/内联渲染 20+ 处用户可控
> 文本（任务/场景/脚本/工具/模型/密钥名、错误信息、base_url）裸插补转义含 data-* 属性位；
> teEsc/dsEsc/nsEsc/showLiveTask 局部 esc 收敛为委托（api-acc3 此前全文无转义）。render_check
> 内容断言加实体解码（引号转实体后可见文本不变）。
> **helper 均化**：live2.js 增 liveToast/shortTime/fmtDur 共享实现（toast 参数漂移 4200ms/420px
> vs 4600ms/440px 统一为后者），fapiToast/fapiShortTime/aapiToast/aapiShortTime 改薄委托，
> showAccTask 内嵌 fmtDur 改共享，aapiNum 删未用 digits 死参数。
> **守护扩展**：mockup_symbols --check 新增 ①任意缩进 `window.X =` 运行期改写计入查询口径
> （kind=window.set，不参与覆盖硬检查——内联 mock ← live 接管是仓库惯例）②同体函数检查
> （()[]{} 深度感知配平 + 深度 0 分号止步防吞并相邻声明、≥60 字符放行薄委托、const 数据对象
> 按头部无 => 排除）；收敛检查器揪出的真拷贝 dsOnlineMockBadge/dsOnlineBadgeCell 进 live2.js。
> 回归 tests/unit/test_mockup_symbols.py 6 例。
> **性能详情防闪烁**（api-perf v5，perf 两文件属「停止能力」会话认领域、随其落库）：运行态 5s
> 自轮询改 poll 模式（不清骨架 + 数据签名无变化整轮跳过 + 失败静默重试）、终态跳变 poll 化、
> 图表 animation:false、PERF_TS 清空再赋值。
> 版本：live2 v2 / api32 v27（common2+functional2）/ api-acc3 v12 / api-perf v5。验证：
> mockup_symbols --check 过、render_check 过、node --check 过、6 单测过；IAB 三页冒烟
> （accuracy2/common2/functional2 live 模式启动：escHtml 链路、徽章别名、runs 12 行真实数据
> 转义渲染全过；accuracy2 空态占位正常）。已知未竟：v1.js wireKeySelect（secrets 名裸插）与
> perf 域 papi 系列收敛待「停止能力」会话释放后做；api-acc3 accSampleBlock（逐样本块）转义
> 未盘点；RUNS/ACC_* 等跨文件状态收权未动（建议单独立项）。纯静态稿改动，刷新页面即生效。

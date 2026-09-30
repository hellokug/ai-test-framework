# aite 前端 V1 静态稿 · 交接文档（2026-09-29/30 会话 → 下一会话）

> 新会话开场必读顺序：本文档 → `web-ui/V1-PLAN.md` → `CLAUDE.md`（项目铁律）→
> `docs/roadmap.md`（进度记录最后 5 条）。
> 用户正在以"逐模块评审静态稿"的方式推进前端 V1，**尚未对接后端 API**。

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
   全局搜索/通知/用户在顶栏。
3. **精度创建任务 = 结构化表单**（全按钮、零 YAML；YAML 属后台语义，前台无套件管理页）：
   - 任务名称（置顶，留空自动生成）
   - ① 数据集：**可搜索、可多选列表**，搜索框内筛选——点搜索框弹两级下拉
     （先选筛选类型→再选值），选中生成 key-value token；四组筛选：
     归属（开源/个人/客户）、存储位置（在线/离线）、能力维度（知识问答/数学推理/
     代码/RAG/Agent/鲁棒性/长上下文，多选 OR）、类型（LLM/Agent/RAG）；不选=模糊搜索
   - ② 抽样条数（全量/100/300/500/自定义）+ 随机种子 seed
   - ③ 目标模型（radio 卡片）+ **被测版本（必填）**——版本趋势横轴靠它
   - ④ 并行数（**不设档位**，用户自填，留空=引擎默认）
   - ⑤ 模型参数：max_tokens/temperature/top_p/top_k/thinking 独立字段（留空不传）
     + 高级 JSON 兜底；thinking 后台按引擎翻译
   - ⑥ 指标阈值判定：auto / 不判定 / 自定义（逐指标 ≥/≤ 规则行）
   - ⑦ 执行方式：立即执行 / 定时执行（datetime）/ 仅创建
   - **创建 ≠ 执行**：创建=配置固化生成 task_id（pending），执行绑定 task_id；
     同配置再跑=retry 链新 task_id
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

## 4. 环境与坑（新会话必读）

- **预览服务器**：`cd web-ui/mockups && python serve.py 8765`（或 8766），
  no-store 头；**后台进程随会话死，新会话必须先起**；两个端口都有效。
- **IAB（内嵌浏览器）缓存按路径缓存**，版本参数都会被忽略：改了 CSS/JS 必须
  升级 `assets/v1.css?v=N` / `v1.js?v=N` 的版本号（当前 css v=5 / js v=4），必要时换文件名。
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

1. **用户继续评审静态稿**：精度已基本定稿（调试面板待移除）；功能/性能/可靠性/
   公共四模块的静态稿还没按精度这轮的反馈标准迭代过（比如功能创建是否也结构化
   表单化，等用户提）。
2. 静态稿全部定稿后：搭 Vite + Vue3 + TS 工程（`web-ui/README.md` 有迁移路径），
   对接 §10 契约。
3. 后端剩余缺口（V1-PLAN §8）：精度任务 spec 结构化表单+version、定时调度器
   （含重启恢复）、版本趋势按 (model,dataset,metric,version) 聚合 + 基线
   （D1 提前）、run 产物下载端点、catalog 归属/位置/维度/类型标注列、
   四域 executor.cancel 子进程进程组强杀。
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

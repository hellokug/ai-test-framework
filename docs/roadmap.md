# aite 完善路线图

> 约定：每完成或变更一项，立即更新对应条目与文末进度记录。
> 顺序原则：核心功能优先，前端体验最后。

## 架构定位（2026-09-01 重定：平台化）

**aite 是评测平台，不是评测引擎。** 精度基准跑分委托给专业工具
（evalscope / opencompass / lm-eval），平台核心价值 = 数据集管理 + 工具编排 +
结果统一。已确认的决策：

- 自研引擎**保留作补充**：性能延迟、安全探针、RAG、judge 闭环等外部工具
  覆盖不到的维度；精度基准（mmlu/ceval/gsm8k 跑分）走外部工具
- 首个接入 **evalscope**（pip 轻、Python API、原生 OpenAI 兼容端点、中文基准全）
- 执行形态：**进程内 Python API**（依赖版本冲突用隔离策略解决）

## v1 目标（2026-08-28 重定）

v1 按**能力模块**交付，不再按"补缺口清单"推进。每个模块独立走完
需求对齐 → 对象建模 → 测试 → 实现 → 文档，完成后单独提交 git。

### 模块 A：数据集治理子系统（需求已对齐，暂缓实施）

完整需求与对齐结论已存档：`docs/requirements-datasetgov.md`。
恢复实施时从 M0（对象建模 + 三池存储 + 审计）直接开始，无需重新对齐。

### 模块 B：指标标准性（2026-09-01 随平台化收缩）

精度分数已决定委托外部工具，本模块大部分作废，仅保留：

- [ ] tokenize 中文分词支持（jieba 可选依赖）——内置引擎自用的小修复，
      低优先级；sacrebleu 对拍项**取消**（不再需要）

### 模块 C：evalscope 适配器（进行中，平台化首个落地）

目标：suite 里指定外部引擎，精度基准由 evalscope 计算，结果回传 aite 统一存储。

- [ ] C0 调研 evalscope 当前版本 API（run 接口/数据格式/输出报告结构）
- [ ] C1 ToolAdapter 接口定义（prepare/execute/collect 契约级，需评审）
- [ ] C2 evalscope 内置基准直通：yaml 指定 evalscope 任务名 + target 复用
      （OpenAI 兼容端点），跑分结果写入 run_metric_summary
- [ ] C3 平台数据集 → evalscope 自定义数据格式转换（local/builtin 灌进去）
- [ ] C4 Web/CLI 入口与文档

验证方式：真实 evalscope 跑 ceval/gsm8k 小样本，结果入库、Web 趋势可见。

## 基线（已完成）

- [x] pytest 全量 145 用例通过；ruff 零报错（2026-08-28）
- [x] 项目标准确立并写入 CLAUDE.md；基线 git 提交 `a916fc0`（2026-08-28）

## Backlog（原打磨项，v1 后重排）

- Judge pairwise/multi 补完、`DELETE /suites` 实现、变体展开上限保护、
  Judge 失败降级、价格表外置、trends/Agent 连接器测试、存储清理策略、
  趋势查询索引。
- 指标按数据集覆盖（metrics_per_dataset）：当前 metrics 为 suite 级全局，
  混合数据集（数学+文本 QA）无法各配各的指标（2026-08-29 用户实际踩到）。
- local 数据集推荐指标：builtin 有 recommended metrics，local/外部导入无。
  （`metrics: auto` 自动填充已于 2026-08-31 实现，见进度记录；local 推荐仍缺）

## 前端体验优化（最后）

- [ ] 核心能力稳定后启动（迁移路径见 web-ui/README.md）

## 进度记录

- 2026-08-28 建立路线图，完成架构盘点（模块地图/执行链路/能力清单见会话记录）。
- 2026-08-28 确立项目标准（架构冻结范围/解耦规则/开发节奏/git 规范），写入 CLAUDE.md；补 .gitignore；基线确认通过（145 passed + ruff clean），执行基线提交。
- 2026-08-28 v1 重定义为按能力模块交付；首个模块"数据集治理子系统"立项（四模块 + 交付物清单），旧打磨项转入 Backlog。进入设计对齐阶段。
- 2026-08-28 数据集治理子系统完成需求对齐（位置/依赖/接口/补齐项均确认，见 requirements-datasetgov.md）后**暂缓实施**；焦点转回测试框架整体关键能力补齐。
- 2026-08-29 新增能力：HF 数据集离线导入（`aite dataset add-hf` + `add_hf_dataset()`，双形态接口标准写入 CLAUDE.md）。外部注册表 `./data/aite_datasets.json` 与内置 BUILTIN_INFO 合并，数据不进包源码。+9 离线单测。
- 2026-08-29 体验修复三连：报告显示 target/datasets 信息（rich/html）、报告目录改为 `模型/数据集/时间_短id` 三层结构。
- 2026-08-30 指标审计结论：规则类指标教科书级可靠；统计类（bleu/rouge_l）结构正确但平滑/BP 细节与标准实现有偏差；tokenize 按空格切分导致**中文场景统计类指标失效**。用户决策：评测终态要业界认可分数 → 立项模块 B（标准后端 + 中文分词 + 对拍验证）。
- 2026-08-31 修复两处健壮性问题：`metrics` 裸字符串被逐字符迭代（用户实际踩到）；未知指标名改为执行前 fail-fast（不再白跑用例）。实现 `metrics: auto`（builtin 推荐指标自动填充，带默认阈值；`auto`/`[auto]` 两种写法，无 builtin 数据集时报清晰错误）。161 测试全绿，含本地模型端到端验证。
- 2026-08-31 C-Eval 全集落地：HF `ceval/ceval-exam` 52 科目 val(1346)+dev(260) 共 1606 题转换为离线 `ceval_full`（A/B/C/D 独立列→choices 列表，subject 入 metadata 支持 group_by 分科目统计；test split 答案不公开不取）。转换脚本 scripts/convert_ceval.py（数据本体不入库：CC BY-NC-SA 许可不再分发，用脚本再生成），抽样 10 题 choice_accuracy=0.8 验证通过。注：A-D 多列→choices 的转换模式如再遇到，可并入 add-hf CLI（backlog）。
- 2026-09-01 沉淀《数据集使用指南》docs/dataset-guide.md（在线/离线跑法、add-hf 下载命令、特殊形状转换模式、参数速查），README 文档索引更新。实测并发结论：本地单卡 Ollama 并发 1/8/16 无显著差异（GPU 吞吐瓶颈），云端 API 才有线性加速。
- 2026-09-01 **架构转向：平台化**。用户决策：aite 定位评测平台而非引擎，精度跑分委托 evalscope/opencompass 等专业工具，平台核心 = 数据集管理 + 工具适配 + 结果统一；自研引擎保留作性能/安全/RAG/judge 维度补充；首个接入 evalscope（进程内 API）。模块 B 收缩（对拍取消），模块 C（evalscope 适配器）立项。同日修复 hf_hub 加载器未注册进 run 链路的 bug（aite/datasets/__init__ 导入时注册）。
